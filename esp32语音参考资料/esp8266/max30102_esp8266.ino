/*
 * MAX30102 心率血氧监测节点 — ESP8266 固件
 *
 * 功能：
 *  - MAX30102（硬件 Wire，默认 SCL=D4 / SDA=D3，可在配置页修改；接反会自动交换）
 *  - 默认开启热点（开放网络，captive portal），配置 WiFi 与上传服务器
 *  - 保存后接入局域网，热点关闭；通过新 IP 访问 Web 界面
 *  - Web 界面端口 5535；80 端口做透明跳转，直接输入设备 IP（不带端口）也能打开：
 *      http://设备IP                  → 自动跳转到 :5535（AP 模式跳 192.168.4.1:5535）
 *      http://设备IP:5535            首页（大屏）
 *      http://设备IP:5535/setup      WiFi/服务器配置
 *      http://设备IP:5535/ecg        实时心电/脉搏波界面（无数据时显示"无监测"）
 *      http://设备IP:5535/history    24 小时历史记录（每分钟 1 点，曲线图）
 *      http://设备IP:5535/api/wave   实时波形数据接口
 *  - 数据以 JSON 上传服务器（HTTP POST 或 TCP 行，默认端口 12090）
 *    服务器不可达时按指数退避重试（5/10/20/40/60s），不阻塞主循环
 *  - 接入局域网后可用 mDNS 域名访问：http://max30102.local（免查 IP）
 *  - 在线保障：一旦入网绝不主动离开局域网（空载/长时间无测量也保持在线，每 30s 一次保活流量）；
 *    掉线由 SDK 自动重连 + 20s 后升级重连；开机没连上则开热点并后台持续重连，连上自动切回 STA；
 *    已入网后连续 5 分钟连不上才额外开备用热点（STA 侧继续重连，不会退回纯热点模式）
 *  - 串口（115200）调试命令：cfg / save / wipe / reset
 *  - 轮询服务器下发文字/位图，通过软件 I2C 显示到 SSD1306 OLED
 *    （OLED 独立引脚，默认 SCL=D1 / SDA=D2，与传感器不共用）
 */

#include <ESP8266WiFi.h>
#include <WiFiUdp.h>
#include <ESP8266WebServer.h>
#include <ESP8266mDNS.h>
#include <DNSServer.h>
#include <EEPROM.h>
#include <Wire.h>
#include <time.h>
#include "SoftI2C.h"
#include "SSD1306Soft.h"
#include "MAX30102.h"
#include "pages.h"

#define FW_VERSION      "1.4.1"
#define WEB_PORT        5535         // 设备 Web 界面端口（WiFi 配置 / 实时大屏 / 心电波形）
#define CAPTIVE_PORT    80           // AP 热点模式下的 captive portal 跳转端口
#define EEPROM_SIZE     512
#define CFG_MAGIC       0x4D415831UL   // "MAX1"

struct __attribute__((packed)) Config {
  uint32_t magic;
  char     ssid[33];
  char     pass[65];
  char     host[64];
  uint16_t port;
  char     path[48];
  uint8_t  proto;      // 0=HTTP POST, 1=TCP
  uint8_t  scl;        // MAX30102 SCL (GPIO)
  uint8_t  sda;        // MAX30102 SDA (GPIO)
  uint8_t  oScl;       // OLED SCL (GPIO)
  uint8_t  oSda;       // OLED SDA (GPIO)
  uint16_t interval;   // 上传间隔（秒）
  uint8_t  pad[8];
  uint32_t crc;
};

static Config cfg;
static bool cfgValid = false;
static bool apMode = false;       // 纯热点配置模式（开机就没连上局域网）
static bool backupAp = false;     // 备用配置热点：STA 掉线过久时临时开启，仍持续重连局域网
static bool staFailed = false;   // 上次 STA 连接失败（用于配置页提示）
#define MDNS_NAME "max30102"

static ESP8266WebServer server(WEB_PORT);
static ESP8266WebServer cap(CAPTIVE_PORT);   // AP 模式下 captive portal（把手机自动弹出页转到 :5535）
static DNSServer dnsServer;
static MAX30102 pulse;
static SSD1306Soft oled;

// ---------------- 测量与历史 ----------------
#define HIST_N 240
static struct { uint32_t t; int16_t hr; int16_t spo2; } hist[HIST_N];
static int histHead = 0, histCount = 0;

// 24 小时历史：每分钟 1 点，共 1440 点（重启后清零）
#define HIST24_N 1440
static uint8_t hist24HR[HIST24_N];    // 0 = 该分钟无有效心率
static uint8_t hist24Sp[HIST24_N];
static uint32_t hist24Min = 0;        // 开机以来累计的分钟数（写入游标）
static uint32_t lastHist24Ms = 0;
static uint32_t lastHist24SampleMs = 0;
static uint16_t h24Sum = 0, h24Cnt = 0, s24Sum = 0, s24Cnt = 0;   // 分钟内累加器

// 各 HTTP 接口共用的发送拼装缓冲：ESP8266WebServer 回调串行执行，不会重入。
// 合并成一份可省下约 2.3KB 静态内存（分开声明会各自占用 768B）。
static char txBuf[768];

static uint32_t bootMs = 0;
static uint32_t lastUploadMs = 0;
static uint32_t lastPollMs = 0;
static uint32_t lastHistMs = 0;
static uint32_t lastOledMs = 0;
static uint32_t lastUpOkMs = 0;
static bool     upOk = false;
static char     upStatus[48] = "未上报";
static uint8_t  upFailCount = 0;         // 连续上报失败次数（用于指数退避）
static uint32_t upBackoffUntil = 0;      // 退避截止时刻（此期间不再尝试上报）
static uint32_t loopMaxStallMs = 0;      // 主循环最长卡顿（诊断：网络阻塞会直接反映在这里）
static uint32_t loopStallAtSec = 0;      // 上述卡顿发生的时刻（开机秒数）

// ---------------- 复位/内存诊断 ----------------
// ESP8266 每次复位都会在串口打印复位原因，但用户看不到串口。这里把原因写进
// RTC 用户内存（512B，软件复位 / 看门狗复位 / 异常复位后数据保留，仅掉电丢失），
// 于是反复重启的历史可以被记录并读出，从而区分：
//   - RTC 数据仍在、原因是 Exception/WDT → 软件问题（内存耗尽 / 看门狗）
//   - RTC 数据丢失（magic 不匹配）      → 断电或欠压复位（供电不足）
#define RTC_MAGIC      0x4D415852UL   // "MAXR"
#define RTC_MAXRST     20             // 最多记录 20 次复位
struct __attribute__((packed)) RstRec {
  uint32_t upSec;      // 该次运行了多久（秒）
  uint32_t heap;       // 复位前空闲堆
  uint32_t heapMin;    // 该次运行期间最低空闲堆
  uint16_t reason;     // 复位原因码（= rst_info.reason）
  uint16_t _pad;
};                     // 16 字节
struct __attribute__((packed)) RtcData {
  uint32_t magic;
  uint32_t bootCount;      // 累计启动次数
  uint32_t hist24Min;      // 24h 历史游标（跨复位延续，避免重启后索引错乱）
  uint32_t lastMinEpoch;   // 最后一点的 Unix 分钟（用于判断断电时长）
  uint16_t rstHead;        // 复位记录环形游标
  uint16_t _pad2;
  RstRec   rst[RTC_MAXRST];
  uint32_t crc;
};                     // 344 字节
static RtcData rtc __attribute__((aligned(4)));   // RTC 读写要求 4 字节对齐
static bool rtcValid = false;

static uint8_t  lastRstReason = 0;
static char     lastRstStr[32] = "?";
static uint32_t minHeapSeen = 0xFFFFFFFF;
static uint32_t minHeapAtSec = 0;
static uint32_t warnHeapCount = 0;       // heap 跌破告警线的次数

#define HEAP_WARN_LEVEL   14000            // 空闲堆低于此值视为危险（String/lwIP 分配可能失败）

// 服务器下发消息
static char     msgText[192];
static uint32_t msgUntilMs = 0;
static uint8_t  msgPages = 1, msgPage = 0;
static uint32_t msgFlipMs = 0;
static uint8_t  msgBitmap[1024];
static bool     msgHasBitmap = false;
static uint32_t lastMsgId = 0;
static char     apSsid[24];

// ---------------- 前置声明 ----------------
void handleRoot(); void handleSetup(); void handleDash(); void handleSaved();
void handleScan(); void handleSave(); void handleReset(); void handleLive();
void handleHistory(); void handleStatus(); void handleSendMsg(); void handleNotFound();
void handleEcg(); void handleWave(); void handleHistPage(); void handleHist24(); void capNotFound();
void handleRstLog();
void startAP(); void startSTA(); void doUpload(); void pollMessage(); void startBackupAp();
void applyServerJson(const char *json, int len);
void oledShowVitals(); void oledTick(); const char *gpioToD(uint8_t g);
void serialCmd(); void printCfg(); void printRstLog();

// ---------------- 工具 ----------------
static uint32_t cfgChecksum(const Config &c) {
  const uint8_t *p = (const uint8_t *)&c;
  uint32_t s = 0, n = sizeof(Config) - 4;
  for (uint32_t i = 0; i < n; i++) s += p[i];
  return s;
}

static bool loadConfig() {
  EEPROM.begin(EEPROM_SIZE);
  Config tmp;
  EEPROM.get(0, tmp);                    // 先读入临时结构体，避免污染内存中的默认配置
  if (tmp.magic != CFG_MAGIC || tmp.crc != cfgChecksum(tmp)) return false;
  cfg = tmp;                             // 校验通过才覆盖
  cfg.ssid[32] = 0; cfg.pass[64] = 0; cfg.host[63] = 0; cfg.path[47] = 0;
  if (cfg.port == 0) cfg.port = 12090;
  if (cfg.interval < 1 || cfg.interval > 3600) cfg.interval = 5;
  return true;
}

static void saveConfig() {
  cfg.magic = CFG_MAGIC;
  cfg.crc = cfgChecksum(cfg);
  EEPROM.put(0, cfg);
  bool committed = EEPROM.commit();
  // 立即回读校验，确保真正落盘（避免"保存后重启配置丢失"）
  Config tmp;
  EEPROM.get(0, tmp);
  bool same = (tmp.magic == CFG_MAGIC && tmp.crc == cfgChecksum(tmp) &&
               strncmp(tmp.ssid, cfg.ssid, sizeof(tmp.ssid)) == 0 &&
               strncmp(tmp.host, cfg.host, sizeof(tmp.host)) == 0);
  Serial.printf("[CFG] 保存%s (commit=%d 回读%s) ssid=%s host=%s:%u\n",
                (committed && same) ? "成功" : "失败",
                committed ? 1 : 0, same ? "一致" : "不一致",
                cfg.ssid, cfg.host, (unsigned)cfg.port);
}

// ---------------- RTC 诊断数据（跨复位保留）----------------
static uint32_t rtcChecksum() {
  const uint8_t *p = (const uint8_t *)&rtc;
  uint32_t s = 0, n = sizeof(RtcData) - 4;
  for (uint32_t i = 0; i < n; i++) s += p[i];
  return s;
}

static void rtcLoad() {
  memset(&rtc, 0, sizeof(rtc));
  if (!ESP.rtcUserMemoryRead(0, (uint32_t *)&rtc, sizeof(rtc))) return;
  rtcValid = (rtc.magic == RTC_MAGIC && rtc.crc == rtcChecksum());
  if (!rtcValid) memset(&rtc, 0, sizeof(rtc));   // 掉电：从头开始
}

static void rtcSave() {
  rtc.magic = RTC_MAGIC;
  rtc.crc = rtcChecksum();
  ESP.rtcUserMemoryWrite(0, (uint32_t *)&rtc, sizeof(rtc));
}

// ---------------- 24h 历史 Flash 落盘（磨损均衡环形 64 扇区）----------------
// 4M1M 分区（物理地址 = 映射地址 - 0x40200000）：文件区 0x300000..0x3FA000 从未挂载（完全
// 闲置），EEPROM 在 0x3FB000。取 0x3BA000 起 64 个 4KB 扇区（256KB）做环形：
// 每 5 分钟写一个扇区，单扇区擦写 4.5 次/天，按 NOR 保守 1 万次擦写寿命也有 6 年，
// 典型 10 万次 = 60 年。断电/重启最多丢最近 5 分钟数据。
#define HFS_BASE    0x3BA000u
#define HFS_SLOTS   64
#define HFS_MAGIC   0x53343248u          // "H24S"
#define HFS_SLOT_SZ 4096
// 槽内布局: [0]=magic(4) [4]=ver(2) [6]=res(2) [8]=hist24Min(4) [12]=crc(4)
//           [16..1455]=hist24HR[1440]  [1456..2895]=hist24Sp[1440]  共 2900B < 4KB
static uint32_t hfsLastSlot  = HFS_SLOTS - 1;  // 最近写入的槽位（无有效数据时下一次写 0）
static uint32_t hfsSavedMin  = 0;              // 已落盘的游标
static uint32_t hfsRestoredMin = 0;            // 本次开机从 Flash 恢复到的游标
static uint32_t hfsWrites    = 0;              // 累计落盘次数

static uint32_t hfsCrc32(const uint8_t *p, size_t n, uint32_t seed) {
  uint32_t c = seed;
  while (n--) {
    c ^= (uint32_t)(*p++);
    for (uint8_t i = 0; i < 8; i++) c = (c & 1) ? (c >> 1) ^ 0xEDB88320u : (c >> 1);
  }
  return c;
}

// 扫描全部槽位，取游标最大且校验通过的一份恢复；返回恢复到的游标（0=无可恢复数据）
static uint32_t hfsLoad() {
  uint8_t hdr[16];
  int bestSlot = -1;
  uint32_t bestMin = 0;
  for (uint32_t s = 0; s < HFS_SLOTS; s++) {
    ESP.flashRead(HFS_BASE + s * HFS_SLOT_SZ, (uint32_t *)hdr, 16);
    uint32_t magic, mn;
    memcpy(&magic, hdr, 4);
    if (magic != HFS_MAGIC) continue;
    memcpy(&mn, hdr + 8, 4);
    if (mn > bestMin || bestSlot < 0) { bestMin = mn; bestSlot = (int)s; }
  }
  if (bestSlot < 0) return 0;
  uint32_t addr = HFS_BASE + (uint32_t)bestSlot * HFS_SLOT_SZ;
  ESP.flashRead(addr + 16,   (uint32_t *)hist24HR, 1440);
  ESP.flashRead(addr + 1456, (uint32_t *)hist24Sp, 1440);
  ESP.flashRead(addr + 2896, (uint32_t *)hdr, 4);
  uint32_t crcStored, crcCalc;
  memcpy(&crcStored, hdr, 4);
  crcCalc = hfsCrc32((const uint8_t *)hist24HR, 1440, 0xFFFFFFFFu);
  crcCalc = hfsCrc32((const uint8_t *)hist24Sp, 1440, crcCalc);
  if (crcCalc != crcStored) {                   // 数据损坏：宁可清零也不恢复错数据
    memset(hist24HR, 0, 1440);
    memset(hist24Sp, 0, 1440);
    hfsLastSlot = HFS_SLOTS - 1;
    return 0;
  }
  hfsLastSlot = (uint32_t)bestSlot;
  return bestMin;
}

static void hfsSave() {
  uint32_t slot = (hfsLastSlot + 1) % HFS_SLOTS;
  uint32_t addr = HFS_BASE + slot * HFS_SLOT_SZ;
  uint8_t hdr[16];
  uint32_t magic = HFS_MAGIC;
  uint16_t ver = 1, res = 0;
  uint32_t crc = hfsCrc32((const uint8_t *)hist24HR, 1440, 0xFFFFFFFFu);
  crc = hfsCrc32((const uint8_t *)hist24Sp, 1440, crc);
  memcpy(hdr, &magic, 4);
  memcpy(hdr + 4, &ver, 2);
  memcpy(hdr + 6, &res, 2);
  memcpy(hdr + 8, &hist24Min, 4);
  memcpy(hdr + 12, &crc, 4);
  ESP.flashEraseSector(addr / HFS_SLOT_SZ);
  ESP.flashWrite(addr,        (uint32_t *)hdr,      16);
  ESP.flashWrite(addr + 16,   (uint32_t *)hist24HR, 1440);
  ESP.flashWrite(addr + 1456, (uint32_t *)hist24Sp, 1440);
  ESP.flashWrite(addr + 2896, (uint32_t *)&crc,     4);
  hfsLastSlot = slot;
  hfsSavedMin = hist24Min;
  hfsWrites++;
}

// 上一次复位原因的中文描述（用于页面展示）
static const char *rstReasonName(uint8_t r) {
  switch (r) {
    case REASON_DEFAULT_RST:      return "上电/正常启动";
    case REASON_WDT_RST:          return "硬件看门狗复位";
    case REASON_EXCEPTION_RST:    return "异常复位(程序崩溃)";
    case REASON_SOFT_WDT_RST:     return "软件看门狗复位(主循环卡死)";
    case REASON_SOFT_RESTART:     return "软件主动重启";
    case REASON_DEEP_SLEEP_AWAKE: return "深度睡眠唤醒";
    case REASON_EXT_SYS_RST:      return "外部复位/掉电";
    default:                      return "未知";
  }
}

// ---------------- 串口调试命令（115200）----------------
//   cfg   查看当前配置与来源
//   save  把当前配置写入 EEPROM 并回读校验（用于验证持久化）
//   wipe  清除配置（下次启动进入热点配置模式）
//   reset 重启设备
void printRstLog() {
  Serial.printf("[RST] RTC 记录%s，累计启动 %lu 次\n",
                rtcValid ? "有效" : "已丢失(掉电复位)", (unsigned long)rtc.bootCount);
  Serial.printf("[RST] 本次复位: %s (code=%u)\n", lastRstStr, (unsigned)lastRstReason);
  if (!rtcValid) return;
  uint16_t n = rtc.rstHead < RTC_MAXRST ? rtc.rstHead : RTC_MAXRST;
  Serial.printf("[RST] 最近 %u 次运行记录（由新到旧）:\n", (unsigned)n);
  for (uint16_t i = 0; i < n; i++) {
    uint16_t idx = (uint16_t)((rtc.rstHead - 1 - i + RTC_MAXRST * 2) % RTC_MAXRST);
    const RstRec &r = rtc.rst[idx];
    Serial.printf("  #%u 运行 %lus 后 %s | heap=%lu heapMin=%lu\n",
                  (unsigned)(i + 1), (unsigned long)r.upSec,
                  rstReasonName((uint8_t)r.reason),
                  (unsigned long)r.heap, (unsigned long)r.heapMin);
  }
}

void printCfg() {
  Serial.printf("[CFG] 有效=%d ssid=%s pass=%s host=%s:%u path=%s proto=%u 间隔=%us\n"
                "[CFG] 引脚 SCL=%s SDA=%s OLED SCL=%s SDA=%s\n",
                cfgValid ? 1 : 0, cfg.ssid[0] ? cfg.ssid : "(空)",
                cfg.pass[0] ? "(已保存)" : "(无)", cfg.host, (unsigned)cfg.port, cfg.path,
                (unsigned)cfg.proto, (unsigned)cfg.interval,
                gpioToD(cfg.scl), gpioToD(cfg.sda), gpioToD(cfg.oScl), gpioToD(cfg.oSda));
}

void serialCmd() {
  static char line[80];
  static uint8_t n = 0;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c != '\n') { if (n < sizeof(line) - 1) line[n++] = c; continue; }
    line[n] = 0; n = 0;
    if (!strcmp(line, "cfg")) {
      printCfg();
    } else if (!strcmp(line, "save")) {
      saveConfig();
      printCfg();
    } else if (!strcmp(line, "wipe")) {
      cfg.magic = 0;
      EEPROM.put(0, cfg);
      EEPROM.commit();
      cfgValid = false;
      Serial.println("[CFG] 已清除，重启后进入热点配置模式");
    } else if (!strcmp(line, "reset")) {
      Serial.println("[SYS] 重启中...");
      delay(200);
      ESP.restart();
    } else if (!strcmp(line, "rst")) {
      printRstLog();
    } else if (!strcmp(line, "heap")) {
      Serial.printf("[MEM] 空闲堆=%lu 最低=%lu(第%lu秒) 碎片率=%u%% 告警次数=%lu\n",
                    (unsigned long)ESP.getFreeHeap(), (unsigned long)minHeapSeen,
                    (unsigned long)minHeapAtSec, (unsigned)ESP.getHeapFragmentation(),
                    (unsigned long)warnHeapCount);
    } else if (line[0]) {
      Serial.println("[SYS] 命令: cfg | save | wipe | reset | rst | heap");
    }
  }
}

const char *gpioToD(uint8_t g) {
  switch (g) {
    case 16: return "D0"; case 5: return "D1"; case 4: return "D2"; case 0: return "D3";
    case 2: return "D4"; case 14: return "D5"; case 12: return "D6"; case 13: return "D7";
    case 15: return "D8"; default: return "?";
  }
}

// ---------------- OLED 显示 ----------------
void oledShowText(const char *text, bool clearFirst = true) {
  if (!oled.ok()) return;
  if (clearFirst) oled.clear();
  // 自动分行：size=1 时 21 列 x 8 行
  int col = 0, row = 0;
  oled.drawText(0, row * 8, "MSG:", 1);
  col = 4;
  const char *p = text;
  while (*p && row < 8) {
    if (*p == '\n') { row++; col = 0; p++; continue; }
    if (col >= 21) { row++; col = 0; continue; }
    char s[2] = {*p, 0};
    oled.drawText(col * 6, row * 8, s, 1);
    col++; p++;
  }
  oled.display();
}

void oledShowBitmap() {
  if (!oled.ok() || !msgHasBitmap) return;
  oled.setBitmap(msgBitmap);
  oled.display();
}

void oledShowVitals() {
  if (!oled.ok()) return;
  oled.clear();
  char line[26];
  oled.drawText(0, 0, "MAX30102 MONITOR", 1);
  int hr = pulse.getHR(), sp = pulse.getSpO2();
  if (pulse.fingerOn()) {
    snprintf(line, sizeof(line), hr > 0 ? "HR %3d" : "HR  --", hr);
    oled.drawText(0, 14, line, 2);
    snprintf(line, sizeof(line), sp > 0 ? "SP %3d%%" : "SP  --", sp);
    oled.drawText(0, 34, line, 2);
  } else {
    oled.drawText(0, 14, "HR  --", 2);
    oled.drawText(0, 34, "SP  --", 2);
  }
  if (!pulse.fingerOn())      oled.drawText(0, 55, "PLEASE PUT FINGER", 1);
  else if (apMode) { snprintf(line, sizeof(line), "AP:%s", apSsid); oled.drawText(0, 55, line, 1); }
  else                        oled.drawText(0, 55, WiFi.localIP().toString().c_str(), 1);
  oled.display();
}

void oledTick() {
  if (!oled.ok()) return;
  uint32_t now = millis();
  if (msgUntilMs && now < msgUntilMs) {
    // 消息显示期间：多页文字自动翻页
    if (msgHasBitmap) return;
    if (msgPages > 1 && now > msgFlipMs) {
      msgPage = (msgPage + 1) % msgPages;
      msgFlipMs = now + 4000;
      // 按页截取显示
      char page[192];
      const char *src = msgText;
      int idx = msgPage * (21 * 8);
      int j = 0;
      while (src[idx] && j < (int)sizeof(page) - 1) page[j++] = src[idx++];
      page[j] = 0;
      oledShowText(page);
    }
    return;
  }
  if (msgUntilMs && now >= msgUntilMs) {
    msgUntilMs = 0;
    msgHasBitmap = false;
    oledShowVitals();
    lastOledMs = now;
  }
  if (now - lastOledMs > 1000) {  // 每秒刷新一次实时数据
    oledShowVitals();
    lastOledMs = now;
  }
}

// ---------------- 服务器消息解析 ----------------
// 从 JSON 中提取字符串字段到 out（处理常见转义），返回是否找到
static bool jsonGetString(const char *json, int len, const char *key, char *out, int outLen) {
  char pat[24];
  snprintf(pat, sizeof(pat), "\"%s\"", key);
  const char *p = NULL;
  for (int i = 0; i <= len - (int)strlen(pat); i++) {
    if (strncmp(json + i, pat, strlen(pat)) == 0) { p = json + i + strlen(pat); break; }
  }
  if (!p) return false;
  while (*p == ' ' || *p == ':') p++;
  if (*p != '"') return false;
  p++;
  int j = 0;
  while (*p && j < outLen - 1) {
    if (*p == '\\' && *(p + 1)) {
      p++;
      switch (*p) {
        case 'n': out[j++] = '\n'; break;
        case 'r': break;
        case 't': out[j++] = ' '; break;
        case 'u': p += 4; break;  // 中文需 bitmap 下发，此处跳过
        default: out[j++] = *p;
      }
      p++;
    } else if (*p == '"') break;
    else out[j++] = *p++;
  }
  out[j] = 0;
  return true;
}

static bool jsonGetUint(const char *json, int len, const char *key, uint32_t *out) {
  char pat[24];
  snprintf(pat, sizeof(pat), "\"%s\"", key);
  const char *p = NULL;
  for (int i = 0; i <= len - (int)strlen(pat); i++) {
    if (strncmp(json + i, pat, strlen(pat)) == 0) { p = json + i + strlen(pat); break; }
  }
  if (!p) return false;
  while (*p == ' ' || *p == ':') p++;
  if (!isdigit((unsigned char)*p)) return false;
  *out = (uint32_t)strtoul(p, NULL, 10);
  return true;
}

static int hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

void applyServerJson(const char *json, int len) {
  uint32_t id = 0;
  bool hasId = jsonGetUint(json, len, "id", &id);
  char text[192];
  bool hasText = jsonGetString(json, len, "text", text, sizeof(text));
  char hex[40];
  bool hasBm = false;
  if (jsonGetString(json, len, "bitmap", hex, sizeof(hex))) hasBm = strlen(hex) >= 4;

  if (!hasText && !hasBm) return;                        // 无有效载荷
  if (hasId && id != 0 && id == lastMsgId) return;       // 重复消息（相同 id）跳过

  if (hasBm) {
    // bitmap 字段可能是大十六进制串，直接定位解析
    const char *bp = NULL;
    for (int i = 0; i <= len - 9; i++) {
      if (strncmp(json + i, "\"bitmap\"", 8) == 0) { bp = json + i + 8; break; }
    }
    if (bp) {
      while (*bp == ' ' || *bp == ':') bp++;
      if (*bp == '"') {
        bp++;
        for (int i = 0; i < 1024; i++) {
          while (*bp == '\\' || *bp == '\n' || *bp == '\r') bp++;
          int h = hexVal(*bp), l = hexVal(*(bp + 1));
          if (h < 0 || l < 0) break;
          msgBitmap[i] = (uint8_t)((h << 4) | l);
          bp += 2;
        }
        msgHasBitmap = true;
      }
    }
  } else {
    msgHasBitmap = false;
  }

  if (hasText) {
    strncpy(msgText, text, sizeof(msgText) - 1);
    msgText[sizeof(msgText) - 1] = 0;
    for (char *q = msgText; *q; q++) {
      if (*q == '"' || *q == '\\') *q = '\'';
      if ((unsigned char)*q < 0x20 && *q != '\n') *q = ' ';
    }
    msgPages = 1 + (strlen(msgText) / (21 * 8));
    msgPage = 0;
  } else {
    msgText[0] = 0;
    msgPages = 1;
  }

  if (hasId) lastMsgId = id;
  msgUntilMs = millis() + 30000;   // 显示 30 秒
  msgFlipMs = millis() + 4000;
  Serial.printf("[MSG] 服务器下发: %s%s\n", msgText, msgHasBitmap ? " (+bitmap)" : "");
  if (msgHasBitmap) oledShowBitmap();
  else oledShowText(msgText);
}

// ---------------- 数据上传 ----------------
void buildBody(char *body, int len) {
  uint32_t ts = 0;
  time_t now = time(nullptr);
  if (now > 1600000000) ts = (uint32_t)now;
  // ICU 服务器兼容格式：device_id + pr_hr/ecg_hr + sp_o2
  // 同时保留 hr/spo2/id 原字段，供原测试服务器兼容
  int hr = pulse.getHR(), sp = pulse.getSpO2();
  snprintf(body, len,
           "{\"device_id\":\"%06X\",\"source\":\"esp8266\",\"t\":%lu,\"uptime\":%lu,\"hr\":%d,\"spo2\":%d,\"pr_hr\":%d,\"ecg_hr\":%d,\"sp_o2\":%d,\"finger\":%d,\"rssi\":%d,\"heap\":%lu,\"id\":\"%06X\"}",
           (unsigned)ESP.getChipId(),
           (unsigned long)ts, (unsigned long)((millis() - bootMs) / 1000),
           hr, sp, hr, hr, sp,
           pulse.fingerOn() ? 1 : 0,
           WiFi.RSSI(), (unsigned long)ESP.getFreeHeap(), (unsigned)ESP.getChipId());
}

static bool lastConnectDnsFail = false;   // 上次连接失败是否因域名解析不了（由 connectTarget 设置）

// 上报失败 → 指数退避（5s→10s→20s→40s→60s→120s→300s 封顶）。
// 关键：目标服务器若是“黑洞丢弃”型不可达（SYN 无响应、不返回 RST），
// 每次尝试都会占住一个 lwIP TCP 连接槽直到超时；频繁重试会把连接槽耗尽，
// 导致 Web 服务抢不到连接而“假死”。因此长时间不可达时必须退到 5 分钟级别。
static void markUploadFail(const char *why) {
  upOk = false;
  if (lastConnectDnsFail && upFailCount < 7) upFailCount = 7;   // 域名解析不了：直接退到最长间隔，少做无谓尝试
  if (upFailCount < 8) upFailCount++;
  uint32_t backoff = 5000UL << (upFailCount > 6 ? 6 : (upFailCount - 1));
  if (backoff > 300000UL) backoff = 300000UL;
  upBackoffUntil = millis() + backoff;
  if (upFailCount >= 4) snprintf(upStatus, sizeof(upStatus), "服务器不可达，已降低上报频率");
  else                  snprintf(upStatus, sizeof(upStatus), "%s", why);
}

static void markUploadOk(const char *why) {
  upOk = true; lastUpOkMs = millis();
  snprintf(upStatus, sizeof(upStatus), "%s", why);
  upFailCount = 0; upBackoffUntil = 0;
}

// 连接目标服务器：IP 字面量直接连；主机名解析结果缓存 10 分钟，
// 避免每次上报都做一次阻塞式 DNS（实测域名解析会让主循环卡 2 秒 → 丢样本）
static bool connectTarget(WiFiClient &cl) {
  lastConnectDnsFail = false;
  if (!cfg.host[0]) return false;
  IPAddress ip;
  if (ip.fromString(cfg.host)) return cl.connect(ip, (uint16_t)cfg.port) != 0;
  static IPAddress resolvedIp;
  static uint32_t  resolvedAt = 0;    // 解析成功时刻
  static uint32_t  dnsFailAt  = 0;    // 解析失败时刻
  uint32_t now = millis();
  // 解析失败也要缓存：否则每一轮上报/轮询都会再做一次阻塞式 DNS（实测可卡主循环数秒，
  // 反复失败还会持续消耗 lwIP 内存），这是"跑一段时间就重启"的重要诱因。
  if (dnsFailAt && now - dnsFailAt < 60000UL) { lastConnectDnsFail = true; return false; }
  if (!(resolvedAt && (now - resolvedAt) < 600000UL)) {
    IPAddress tmp;
    if (WiFi.hostByName(cfg.host, tmp)) { resolvedIp = tmp; resolvedAt = now; dnsFailAt = 0; }
    else { resolvedAt = 0; dnsFailAt = now; lastConnectDnsFail = true; return false; }
  }
  return cl.connect(resolvedIp, (uint16_t)cfg.port) != 0;
}

void doUpload() {
  if (apMode || !cfgValid) return;
  if (WiFi.status() != WL_CONNECTED) { snprintf(upStatus, sizeof(upStatus), "WiFi断开"); return; }

  char body[256];
  buildBody(body, sizeof(body));

  WiFiClient cl;
  if (cfg.proto == 1) {
    // TCP 模式：发送一行 JSON，随后读取服务器下发行
    cl.setTimeout(1500);
    if (!connectTarget(cl)) {
      cl.stop();                     // 显式释放连接槽：目标黑洞丢弃时 SYN 会一直占位
      markUploadFail("TCP连接失败"); return;
    }
    cl.print(body); cl.print("\n");
    uint32_t t0 = millis();
    String resp = "";
    while (millis() - t0 < 1000) {
      yield();
      while (cl.available()) {
        char c = cl.read();
        if (c == '\n') {
          if (resp.length() > 2) applyServerJson(resp.c_str(), resp.length());
          resp = "";
        } else if (c != '\r') resp += c;
      }
    }
    cl.stop();
    markUploadOk("TCP已发送");
    return;
  }

  // HTTP POST 模式
  cl.setTimeout(1500);
  if (!connectTarget(cl)) {
    cl.stop();
    markUploadFail("连接失败"); return;
  }
  cl.printf("POST %s HTTP/1.1\r\nHost: %s:%u\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n",
            cfg.path, cfg.host, (unsigned)cfg.port, (int)strlen(body));
  cl.print(body);

  // 读取响应（状态行 + body），body 可能携带下发消息
  String resp = "";
  uint32_t t0 = millis();
  bool statusOk = false;
  while (millis() - t0 < 2500) {
    yield();
    while (cl.available()) resp += (char)cl.read();
    if (resp.length() && millis() - t0 > 300 && !cl.available() && !cl.connected()) break;
  }
  cl.stop();
  if (resp.length() > 12 && resp.startsWith("HTTP/1.")) {
    int code = resp.substring(9, 12).toInt();
    statusOk = (code >= 200 && code < 300);
  }
  if (statusOk) {
    markUploadOk("上报成功");
    int bodyPos = resp.indexOf("\r\n\r\n");
    if (bodyPos > 0 && resp.length() - bodyPos > 20) {
      applyServerJson(resp.c_str() + bodyPos + 4, resp.length() - bodyPos - 4);
    }
  } else {
    markUploadFail("HTTP错误");
  }
}

void pollMessage() {
  if (apMode || !cfgValid || cfg.proto != 0) return;
  if (WiFi.status() != WL_CONNECTED) return;
  WiFiClient cl;
  String url = String(cfg.path) + "/message";
  cl.setTimeout(1500);
  if (!connectTarget(cl)) { cl.stop(); return; }
  cl.printf("GET %s HTTP/1.1\r\nHost: %s:%u\r\nConnection: close\r\n\r\n",
            url.c_str(), cfg.host, (unsigned)cfg.port);
  String resp = "";
  uint32_t t0 = millis();
  while (millis() - t0 < 1500) {
    yield();
    while (cl.available()) resp += (char)cl.read();
    if (resp.length() && millis() - t0 > 200 && !cl.available() && !cl.connected()) break;
  }
  cl.stop();
  int bodyPos = resp.indexOf("\r\n\r\n");
  if (bodyPos > 0 && resp.length() - bodyPos > 20) {
    applyServerJson(resp.c_str() + bodyPos + 4, resp.length() - bodyPos - 4);
  }
}

// ---------------- Web 服务 ----------------
void handleRoot() {
  if (apMode) {
    server.send_P(200, "text/html", SETUP_HTML);
  } else {
    server.sendHeader("Location", "/dash");
    server.send(302, "text/plain", "");
  }
}

void handleDash() { server.send_P(200, "text/html", DASH_HTML); }
void handleSetup() { server.send_P(200, "text/html", SETUP_HTML); }
void handleSaved() { server.send_P(200, "text/html", SAVED_HTML); }
void handleEcg()   { server.send_P(200, "text/html", ECG_HTML); }
void handleHistPage() { server.send_P(200, "text/html", HIST_HTML); }

// 实时脉搏波形接口：/api/wave?from=<已收样本数>
// 返回 {"s":传感器,"f":指尖,"l":实时,"n":累计,"hr":..,"spo2":..,"w":[滤波后AC样本...]}
void handleWave() {
  uint32_t from = (uint32_t)server.arg("from").toInt();
  uint32_t total = pulse.waveTotal;
  uint32_t lastSeen = pulse.lastWaveMs;
  bool present = pulse.present;
  bool finger = pulse.fingerOn();
  bool live = present && finger && (millis() - lastSeen) < 1000;
  if (from >= total) from = 0;                       // 客户端游标失效（缓冲被清空）→ 全量重发
  uint32_t avail = total - from;
  if (avail > pulse.WAVE_N) { from = total - pulse.WAVE_N; avail = pulse.WAVE_N; }

  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.send(200, "application/json", "");
  char head[112];
  snprintf(head, sizeof(head),
           "{\"s\":%d,\"f\":%d,\"l\":%d,\"n\":%lu,\"frm\":%lu,\"hr\":%d,\"spo2\":%d,\"ac\":%d,\"w\":[",
           present ? 1 : 0, finger ? 1 : 0, live ? 1 : 0,
           (unsigned long)total, (unsigned long)from, pulse.getHR(), pulse.getSpO2(),
           pulse.getAcAmp());
  server.sendContent(head);
  // 批量拼接后再发送：逐样本 sendContent 会为每个样本产生一次 TCP 小包
  //（满缓冲时最多 400 次），把主循环拖到几秒级并触发看门狗；合并后只需几次发送。
  char *sbuf = txBuf;
  size_t sl = 0;
  char nb[16];
  for (uint32_t i = 0; i < avail; i++) {
    int16_t v = pulse.wave[(from + i) % pulse.WAVE_N];
    int nl = snprintf(nb, sizeof(nb), i ? ",%d" : "%d", (int)v);
    if (nl <= 0) continue;
    if (sl + (size_t)nl > sizeof(txBuf) - 2) { server.sendContent(sbuf, sl); sl = 0; }
    memcpy(sbuf + sl, nb, (size_t)nl); sl += (size_t)nl;
  }
  if (sl) server.sendContent(sbuf, sl);
  server.sendContent("]}");
  server.sendContent("");
}

// 80 端口跳转：AP/备用热点模式 → 192.168.4.1:5535；STA 模式 → 本机 IP:5535
// 这样在浏览器里直接输入设备 IP（不带端口）也能打开界面，免去记端口的麻烦
void capNotFound() {
  String base;
  if (apMode || backupAp) base = String("http://192.168.4.1:") + String(WEB_PORT);
  else                    base = String("http://") + WiFi.localIP().toString() + ":" + String(WEB_PORT);
  String path = cap.uri();
  if (path.length() == 0) path = "/";
  cap.sendHeader("Location", base + path, true);
  cap.send(302, "text/plain", "");
}

// 备用配置热点：局域网长时间连不上时开启，让用户仍能进配置页；STA 侧继续重连，绝不主动离开局域网
void startBackupAp() {
  if (backupAp) return;
  backupAp = true;
  WiFi.mode(WIFI_AP_STA);
  if (!apSsid[0]) snprintf(apSsid, sizeof(apSsid), "MAX30102-%06X", (unsigned)ESP.getChipId());
  WiFi.softAPConfig(IPAddress(192, 168, 4, 1), IPAddress(192, 168, 4, 1), IPAddress(255, 255, 255, 0));
  WiFi.softAP(apSsid);          // 开放热点
  dnsServer.start(53, "*", IPAddress(192, 168, 4, 1));
  cap.onNotFound(capNotFound);
  cap.begin();
  Serial.printf("[NET] 局域网持续掉线，已开启备用配置热点 %s（http://192.168.4.1:%u）；"
                "STA 侧继续重连，联网恢复后会自动重新在线\n", apSsid, (unsigned)WEB_PORT);
}

void handleScan() {
  // 同步扫描（页面按钮触发）
  int n = WiFi.scanNetworks();
  String out = "[";
  for (int i = 0; i < n; i++) {
    if (i) out += ",";
    String s = WiFi.SSID(i);
    s.replace("\\", "\\\\"); s.replace("\"", "\\\"");
    out += "{\"ssid\":\"" + s + "\",\"rssi\":" + String(WiFi.RSSI(i)) +
           ",\"secured\":" + (WiFi.encryptionType(i) == ENC_TYPE_NONE ? "0" : "1") + "}";
  }
  out += "]";
  WiFi.scanDelete();
  server.send(200, "application/json", out);
}

void handleStatus() {
  char out[700];
  snprintf(out, sizeof(out),
    "{\"ap\":%d,\"bak\":%d,\"mode\":\"%s\",\"ip\":\"%s\",\"staip\":\"%s\",\"apssid\":\"%s\",\"stafail\":%d,"
    "\"ssid\":\"%s\",\"pass_saved\":%d,\"host\":\"%s\",\"port\":%u,\"path\":\"%s\","
    "\"proto\":%u,\"interval\":%u,\"scl\":%u,\"sda\":%u,\"oscl\":%u,\"osda\":%u,"
    "\"sensor\":%d,\"oled\":%d,\"fw\":\"%s\","
    "\"heap\":%lu,\"frag\":%u,\"boots\":%lu,\"rst\":\"%s\",\"rtcOk\":%d}",
    apMode ? 1 : 0, backupAp ? 1 : 0,
    apMode ? "AP" : (backupAp ? "STA+备用热点" : "STA"),
    (apMode || backupAp) ? WiFi.softAPIP().toString().c_str() : WiFi.localIP().toString().c_str(),
    WiFi.localIP().toString().c_str(),
    apSsid, staFailed ? 1 : 0,
    cfg.ssid, cfg.pass[0] ? 1 : 0, cfg.host, (unsigned)cfg.port, cfg.path,
    (unsigned)cfg.proto, (unsigned)cfg.interval,
    (unsigned)cfg.scl, (unsigned)cfg.sda, (unsigned)cfg.oScl, (unsigned)cfg.oSda,
    pulse.present ? 1 : 0, oled.ok() ? 1 : 0, FW_VERSION,
    (unsigned long)ESP.getFreeHeap(), (unsigned)ESP.getHeapFragmentation(),
    (unsigned long)rtc.bootCount, lastRstStr, rtcValid ? 1 : 0);
  server.send(200, "application/json", out);
}

// 复位诊断接口：/api/rstlog
// 返回累计启动次数、本次复位原因、历史上每次运行了多久 / 被什么原因终止 / 当时的堆水位
void handleRstLog() {
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.send(200, "application/json", "");
  uint16_t n = rtcValid ? (rtc.rstHead < RTC_MAXRST ? rtc.rstHead : RTC_MAXRST) : 0;
  char head[224];
  snprintf(head, sizeof(head),
    "{\"rtcOk\":%d,\"boots\":%lu,\"cur\":%u,\"curStr\":\"%s\","
    "\"heap\":%lu,\"heapMin\":%lu,\"frag\":%u,\"stall\":%lu,\"warn\":%lu,\"n\":%u,\"rec\":[",
    rtcValid ? 1 : 0, (unsigned long)rtc.bootCount,
    (unsigned)lastRstReason, lastRstStr,
    (unsigned long)ESP.getFreeHeap(),
    (unsigned long)(minHeapSeen == 0xFFFFFFFF ? ESP.getFreeHeap() : minHeapSeen),
    (unsigned)ESP.getHeapFragmentation(), (unsigned long)loopMaxStallMs,
    (unsigned long)warnHeapCount, (unsigned)n);
  server.sendContent(head);
  if (n) {
    char *rb = txBuf;
    size_t rl = 0;
    for (uint16_t i = 0; i < n; i++) {
      uint16_t idx = (uint16_t)((rtc.rstHead - 1 - i + RTC_MAXRST * 2) % RTC_MAXRST);
      uint32_t up = rtc.rst[idx].upSec, hp = rtc.rst[idx].heap, hm = rtc.rst[idx].heapMin;
      if (hm == 0xFFFFFFFF) hm = hp;      // 该次运行还没产生快照
      uint16_t rs = rtc.rst[idx].reason;
      char line[112];
      int nl = snprintf(line, sizeof(line),
                        "%s{\"up\":%lu,\"heap\":%lu,\"heapMin\":%lu,\"r\":%u,\"why\":\"%s\"}",
                        i ? "," : "", (unsigned long)up, (unsigned long)hp,
                        (unsigned long)hm, (unsigned)rs, rstReasonName((uint8_t)rs));
      if (nl <= 0) continue;
      if (rl + (size_t)nl > sizeof(txBuf) - 2) { server.sendContent(rb, rl); rl = 0; }
      memcpy(rb + rl, line, (size_t)nl); rl += (size_t)nl;
    }
    if (rl) server.sendContent(rb, rl);
  }
  server.sendContent("]}");
  server.sendContent("");
}

void handleLive() {
  uint32_t upAgo = lastUpOkMs ? (millis() - lastUpOkMs) / 1000 : 0;
  uint32_t retryIn = (upBackoffUntil > millis()) ? (upBackoffUntil - millis() + 999) / 1000 : 0;
  char out[720];
  snprintf(out, sizeof(out),
    "{\"t\":%lu,\"hr\":%d,\"spo2\":%d,\"finger\":%d,\"sensor\":%d,\"oled\":%d,"
    "\"wifi\":%d,\"rssi\":%d,\"heap\":%lu,\"ip\":\"%s\",\"uptime\":%lu,"
    "\"srv\":\"%s\",\"srvOk\":%d,\"up\":%lu,\"upAgo\":%lu,\"ir\":%lu,\"ac\":%d,\"retryIn\":%lu,"
    "\"stall\":%lu,\"stallAt\":%lu,\"msg\":\"%.40s\","
    "\"frag\":%u,\"heapMin\":%lu,\"heapMinAt\":%lu,\"warn\":%lu,\"boots\":%lu,"
    "\"rst\":%u,\"rstStr\":\"%s\",\"rtcOk\":%d,"
    "\"hRest\":%lu,\"hSaved\":%lu,\"hSlot\":%lu,\"hWrites\":%lu}",
    (unsigned long)(millis() / 1000), pulse.getHR(), pulse.getSpO2(),
    pulse.fingerOn() ? 1 : 0, pulse.present ? 1 : 0, oled.ok() ? 1 : 0,
    (WiFi.status() == WL_CONNECTED) ? 1 : 0, WiFi.RSSI(),
    (unsigned long)ESP.getFreeHeap(),
    apMode ? WiFi.softAPIP().toString().c_str() : WiFi.localIP().toString().c_str(),
    (unsigned long)((millis() - bootMs) / 1000),
    upStatus, upOk ? 1 : 0,
    (unsigned long)(lastUpOkMs ? 1 : 0), (unsigned long)upAgo,
    (unsigned long)pulse.getIrDC(), pulse.getAcAmp(), (unsigned long)retryIn,
    (unsigned long)loopMaxStallMs, (unsigned long)loopStallAtSec,
    msgUntilMs && millis() < msgUntilMs ? msgText : "",
    (unsigned)ESP.getHeapFragmentation(),
    (unsigned long)(minHeapSeen == 0xFFFFFFFF ? ESP.getFreeHeap() : minHeapSeen),
    (unsigned long)minHeapAtSec,
    (unsigned long)warnHeapCount, (unsigned long)rtc.bootCount,
    (unsigned)lastRstReason, lastRstStr, rtcValid ? 1 : 0,
    (unsigned long)hfsRestoredMin, (unsigned long)hfsSavedMin,
    (unsigned long)(hfsLastSlot + 1), (unsigned long)hfsWrites);
  server.send(200, "application/json", out);
}

void handleHistory() {
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.send(200, "application/json", "");
  String chunk = "{\"n\":";
  chunk += histCount;
  chunk += ",\"span_s\":";
  chunk += (unsigned long)((millis() - bootMs) / 1000);
  chunk += ",\"data\":[";
  server.sendContent(chunk);
  char *hbuf = txBuf;
  size_t hl = 0;
  bool first = true;
  for (int i = 0; i < histCount; i++) {
    int idx = (histHead - histCount + i + HIST_N * 2) % HIST_N;
    char line[48];
    int nl = snprintf(line, sizeof(line), "%s[%lu,%d,%d]", first ? "" : ",",
                      (unsigned long)hist[idx].t, hist[idx].hr, hist[idx].spo2);
    if (nl <= 0) continue;
    first = false;
    if (hl + (size_t)nl > sizeof(txBuf) - 2) { server.sendContent(hbuf, hl); hl = 0; }
    memcpy(hbuf + hl, line, (size_t)nl); hl += (size_t)nl;
  }
  if (hl) server.sendContent(hbuf, hl);
  server.sendContent("]}");
  server.sendContent("");
}

// 24 小时历史数据接口：每分钟 1 点
// 返回 {"span":开机以来分钟数,"n":有效点数,"start":首点分钟序号,"up":开机秒数,"h":[hr...],"s":[spo2...]}
void handleHist24() {
  uint32_t span = hist24Min;
  uint32_t count = span < HIST24_N ? span : HIST24_N;
  uint32_t startM = span - count;
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.send(200, "application/json", "");
  char head[96];
  snprintf(head, sizeof(head), "{\"span\":%lu,\"n\":%lu,\"start\":%lu,\"up\":%lu,\"h\":[",
           (unsigned long)span, (unsigned long)count, (unsigned long)startM,
           (unsigned long)(millis() / 1000));
  server.sendContent(head);
  // 同样必须批量发送：1440 点逐点 sendContent 会产生近 3000 次 TCP 写，直接把设备拖垮
  char *sbuf = txBuf;
  size_t sl = 0;
  char nb[12];
  for (uint32_t i = 0; i < count; i++) {
    int nl = snprintf(nb, sizeof(nb), i ? ",%u" : "%u", (unsigned)hist24HR[(startM + i) % HIST24_N]);
    if (nl <= 0) continue;
    if (sl + (size_t)nl > sizeof(txBuf) - 2) { server.sendContent(sbuf, sl); sl = 0; }
    memcpy(sbuf + sl, nb, (size_t)nl); sl += (size_t)nl;
  }
  if (sl) server.sendContent(sbuf, sl);
  server.sendContent("],\"s\":[");
  sl = 0;
  for (uint32_t i = 0; i < count; i++) {
    int nl = snprintf(nb, sizeof(nb), i ? ",%u" : "%u", (unsigned)hist24Sp[(startM + i) % HIST24_N]);
    if (nl <= 0) continue;
    if (sl + (size_t)nl > sizeof(txBuf) - 2) { server.sendContent(sbuf, sl); sl = 0; }
    memcpy(sbuf + sl, nb, (size_t)nl); sl += (size_t)nl;
  }
  if (sl) server.sendContent(sbuf, sl);
  server.sendContent("]}");
  server.sendContent("");
}

void handleSave() {
  bool hadPass = cfg.pass[0] != 0;
  Config nc = cfg;  // 保留旧值
  strncpy(nc.ssid, server.arg("ssid").c_str(), 32); nc.ssid[32] = 0;
  String pass = server.arg("pass");
  if (pass.length() > 0) { strncpy(nc.pass, pass.c_str(), 64); nc.pass[64] = 0; }
  else if (!hadPass) nc.pass[0] = 0;
  // 主机地址清洗：允许直接粘贴 http(s)://host:port/path，自动取出主机名与端口
  String hostRaw = server.arg("host");
  hostRaw.trim();
  int scheme = hostRaw.indexOf("://");
  if (scheme >= 0) hostRaw = hostRaw.substring(scheme + 3);
  int slash = hostRaw.indexOf('/');
  if (slash >= 0) hostRaw = hostRaw.substring(0, slash);      // 去掉路径
  uint16_t portFromHost = 0;
  int colon = hostRaw.indexOf(':');
  if (colon >= 0) {
    portFromHost = (uint16_t)hostRaw.substring(colon + 1).toInt();
    hostRaw = hostRaw.substring(0, colon);
  }
  strncpy(nc.host, hostRaw.c_str(), 63); nc.host[63] = 0;
  nc.port = (uint16_t)server.arg("port").toInt();
  if (portFromHost) nc.port = portFromHost;
  if (nc.port == 0) nc.port = 12090;
  strncpy(nc.path, server.arg("path").c_str(), 47); nc.path[47] = 0;
  if (!nc.path[0]) strncpy(nc.path, "/api/vitals", 47);
  nc.proto = (uint8_t)server.arg("proto").toInt();
  nc.scl = (uint8_t)server.arg("scl").toInt();
  nc.sda = (uint8_t)server.arg("sda").toInt();
  nc.oScl = (uint8_t)server.arg("oscl").toInt();
  nc.oSda = (uint8_t)server.arg("osda").toInt();
  nc.interval = (uint16_t)server.arg("interval").toInt();
  if (nc.interval < 1) nc.interval = 5;
  cfg = nc;
  saveConfig();
  Serial.printf("[CFG] 已保存: ssid=%s host=%s:%u proto=%u SCL=%s SDA=%s OLED(SCL=%s SDA=%s)\n",
                cfg.ssid, cfg.host, cfg.port, cfg.proto, gpioToD(cfg.scl), gpioToD(cfg.sda),
                gpioToD(cfg.oScl), gpioToD(cfg.oSda));
  server.send(200, "text/plain", "OK");
  delay(500);
  ESP.restart();
}

void handleReset() {
  cfg.magic = 0;
  EEPROM.put(0, cfg);
  EEPROM.commit();
  server.send(200, "text/plain", "OK");
  delay(500);
  ESP.restart();
}

void handleSendMsg() {
  // 本地测试：/api/sendmsg?text=hello 或 &bitmap=hex...
  String q = server.arg("text");
  if (q.length()) {
    q.replace("+", " ");
    applyServerJson(("{\"id\":0,\"text\":\"" + q + "\"}").c_str(), 64 + q.length());
  }
  String bm = server.arg("bitmap");
  if (bm.length() >= 4) {
    applyServerJson(("{\"id\":0,\"bitmap\":\"" + bm + "\"}").c_str(), bm.length() + 16);
  }
  server.send(200, "text/plain", "OK");
}

void handleNotFound() {
  if (apMode) {
    // AP 模式下 5535 的未知路径 → 跳回同主机首页（相对跳转，避免把局域网 IP 强行跳成 192.168.4.1）
    server.sendHeader("Location", "/", true);
    server.send(302, "text/plain", "");
  } else {
    server.send(404, "text/plain", "Not Found");
  }
}

void registerRoutes() {
  server.on("/", handleRoot);
  server.on("/dash", handleDash);
  server.on("/setup", handleSetup);
  server.on("/saved", handleSaved);
  server.on("/scan", handleScan);
  server.on("/save", HTTP_GET, handleSave);
  server.on("/save", HTTP_POST, handleSave);
  server.on("/reset", handleReset);
  server.on("/api/live", handleLive);
  server.on("/api/history", handleHistory);
  server.on("/api/status", handleStatus);
  server.on("/api/rstlog", handleRstLog);   // 复位原因/内存诊断
  server.on("/api/sendmsg", handleSendMsg);
  server.on("/ecg", handleEcg);          // 实时脉搏波 / 心电数据界面
  server.on("/api/wave", handleWave);    // 脉搏波增量数据接口
  server.on("/history", handleHistPage); // 24 小时历史记录界面
  server.on("/api/hist24", handleHist24);// 24 小时历史数据接口（每分钟 1 点）
  server.onNotFound(handleNotFound);
  server.begin();
}

// ---------------- 启动流程 ----------------
void startAP() {
  apMode = true;
  WiFi.mode(WIFI_AP_STA);   // STA 使能便于扫描周围网络
  uint32_t id = ESP.getChipId();
  snprintf(apSsid, sizeof(apSsid), "MAX30102-%06X", (unsigned)id);
  WiFi.softAPConfig(IPAddress(192, 168, 4, 1), IPAddress(192, 168, 4, 1), IPAddress(255, 255, 255, 0));
  WiFi.softAP(apSsid);   // 开放热点
  delay(100);
  dnsServer.start(53, "*", IPAddress(192, 168, 4, 1));
  cap.onNotFound(capNotFound);          // captive portal：80 端口任意请求 → :5535 主界面
  cap.begin();
  registerRoutes();
  if (staFailed) Serial.println("[AP] 提示: 上次 WiFi 连接失败，请核对 SSID / 密码 / 信号后重新保存");
  Serial.printf("\n[AP] 热点已开启: %s（开放网络）\n"
                "[AP] 手机连接后自动弹出配置页；手动访问 http://192.168.4.1:%u\n"
                "[AP] 配置页 /setup · 实时大屏 /dash · 脉搏波形 /ecg · 24h 历史 /history\n",
                apSsid, (unsigned)WEB_PORT);
  if (oled.ok()) oledShowVitals();
}

void startSTA() {
  apMode = false;
  WiFi.mode(WIFI_STA);
  WiFi.setAutoConnect(false);            // 不自动使用 SDK 残留凭据
  WiFi.setAutoReconnect(true);           // 底层自动重连，减少业务层干预
  WiFi.setSleepMode(WIFI_NONE_SLEEP);    // 关闭 modem sleep：避免周期性休眠导致掉线
  WiFi.begin(cfg.ssid, cfg.pass[0] ? cfg.pass : NULL);
  Serial.printf("[STA] 连接 WiFi: %s", cfg.ssid);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(200);
    Serial.print(".");
    if (millis() - t0 > 45000) {          // 45s：给路由器 DHCP 留足时间，避免误判回退热点
      Serial.printf("\n[STA] 连接失败 (status=%d)，转入热点配置模式\n", (int)WiFi.status());
      staFailed = true;
      startAP();
      return;
    }
  }
  Serial.printf("\n[STA] 已连接, IP: %s, RSSI: %d\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  configTime(8 * 3600, 0, "ntp.aliyun.com", "pool.ntp.org");
  if (MDNS.begin(MDNS_NAME)) {
    MDNS.addService("http", "tcp", WEB_PORT);
    Serial.printf("[STA] 局域网域名: http://%s.local:%u\n", MDNS_NAME, (unsigned)WEB_PORT);
  }
  registerRoutes();
  // 80 端口做透明跳转：直接输入设备 IP（不带端口）也能打开界面
  cap.onNotFound(capNotFound);
  cap.begin();
  if (oled.ok()) oledShowVitals();
  String ip = WiFi.localIP().toString();
  Serial.printf("[STA] 访问地址: http://%s/  或  http://%s:%u/  （域名 http://%s.local:%u/）\n",
                ip.c_str(), ip.c_str(), (unsigned)WEB_PORT, MDNS_NAME, (unsigned)WEB_PORT);
  Serial.printf("[STA] 实时大屏 /dash · 脉搏波形 /ecg · 配置页 /setup · 24h 历史 /history\n");
}

void initSensor() {
  // 按 cfg 引脚探测。ESP8266 软件 I2C 首次事务偶发 NACK，需同方向重试；
  // 连续失败再延时交换 SDA/SCL（接线反了自动纠正）。
  bool ok = false;
  for (int pass = 0; pass < 2 && !ok; pass++) {
    if (pass) {
      delay(200);   // 等总线/从机从错误方向脉冲中恢复
      uint8_t t = cfg.scl; cfg.scl = cfg.sda; cfg.sda = t;
    }
    Wire.begin(cfg.sda, cfg.scl);
    Wire.setClock(100000);
    Wire.setClockStretchLimit(50000);
    for (int r = 0; r < 3 && !ok; r++) {
      if (r) delay(100);
      ok = pulse.begin(Wire);
    }
  }
  if (ok) {
    Serial.printf("[I2C] MAX30102 就绪 (SDA=%s SCL=%s)\n", gpioToD(cfg.sda), gpioToD(cfg.scl));
  } else {
    Serial.println("[I2C] 未找到 MAX30102！请检查接线（VIN->3V3, GND->GND, SCL/SDA）");
  }
}

void setup() {
  bootMs = millis();
  Serial.begin(115200);
  delay(100);
  Serial.printf("\n\n=== MAX30102 ESP8266 固件 v%s ===\n", FW_VERSION);
  Serial.println("[SYS] 串口命令: cfg(查看配置) save(保存并校验) wipe(清除配置) reset(重启) rst(复位记录) heap(内存)");
  WiFi.persistent(false);   // 凭据只由本固件 EEPROM 管理，避免 SDK 残留凭据在开机时自动连接旧网络

  // ---- 读取本次复位原因 + 续写 RTC 诊断记录 ----
  // 运行期每 60 秒把"本次运行"的实时状态（已运行秒数/空闲堆/最低堆）刷进 RTC 当前槽；
  // 一旦复位，下一个启动就能读到上一次运行运行了多久、被什么原因终止。
  rst_info *ri = ESP.getResetInfoPtr();
  lastRstReason = ri ? (uint8_t)ri->reason : 0;
  snprintf(lastRstStr, sizeof(lastRstStr), "%s", ESP.getResetReason().c_str());
  rtcLoad();
  if (rtcValid) {
    // 用本次读到的复位原因，给"上一次运行"的记录定性
    RstRec &prev = rtc.rst[rtc.rstHead % RTC_MAXRST];
    prev.reason = lastRstReason;
    if (prev.upSec > 3) rtc.rstHead = (uint16_t)((rtc.rstHead + 1) % RTC_MAXRST);
    rtc.bootCount++;
  } else {
    rtc.bootCount = 1;
    rtc.rstHead = 0;
  }
  RstRec &cur = rtc.rst[rtc.rstHead % RTC_MAXRST];
  cur.upSec = 0; cur.heap = ESP.getFreeHeap(); cur.heapMin = 0xFFFFFFFF;
  cur.reason = 0; cur._pad = 0;
  hist24Min = rtcValid ? rtc.hist24Min : 0;      // 24h 历史游标跨复位延续
  // 从 Flash 恢复历史数据（掉电后 RTC 会丢，但 Flash 还在）
  {
    uint32_t fmin = hfsLoad();
    hfsRestoredMin = fmin;
    if (fmin > hist24Min) hist24Min = fmin;
    hfsSavedMin = fmin;
    Serial.printf("[HFS] Flash 历史: %s（游标 %lu 分钟）\n",
                  fmin ? "已恢复" : "无有效数据", (unsigned long)fmin);
  }
  rtcSave();
  Serial.printf("[RST] 复位原因: %s (%s, code=%u) | RTC 记录%s | 第 %lu 次启动\n",
                lastRstStr, rstReasonName(lastRstReason), (unsigned)lastRstReason,
                rtcValid ? "有效" : "丢失(掉电)", (unsigned long)rtc.bootCount);
  Serial.printf("[MEM] 启动空闲堆: %lu 字节\n", (unsigned long)ESP.getFreeHeap());

  // 默认配置（EEPROM 无有效数据时）
  memset(&cfg, 0, sizeof(cfg));
  cfg.port = 12090;
  strncpy(cfg.path, "/api/vitals", sizeof(cfg.path) - 1);
  cfg.proto = 0;
  cfg.scl = 2;    // D4 (GPIO2) MAX30102 SCL
  cfg.sda = 0;    // D3 (GPIO0) MAX30102 SDA
  cfg.oScl = 5;   // D1
  cfg.oSda = 4;   // D2
  cfg.interval = 5;

  cfgValid = loadConfig();
  Serial.printf("[CFG] 配置%s (ssid=%s host=%s:%u)\n", cfgValid ? "已加载" : "为空，首次启动",
                cfgValid ? cfg.ssid : "-", cfgValid ? cfg.host : "-", (unsigned)cfg.port);

  // OLED（独立软件 I2C）
  bool oledOk = oled.begin(0x3C, cfg.oSda, cfg.oScl);
  Serial.printf("[OLED] SSD1306 %s (SDA=%s SCL=%s)\n", oledOk ? "就绪" : "未检测到",
                gpioToD(cfg.oSda), gpioToD(cfg.oScl));

  // 传感器
  initSensor();

  if (cfgValid && cfg.ssid[0]) startSTA();
  else startAP();

  lastUploadMs = millis();
  lastPollMs = millis();
}

void loop() {
  // 主循环卡顿监测（诊断用：网络阻塞/连接超时会直接反映成一次大卡顿）
  {
    static uint32_t prevMs = 0;
    uint32_t nowMs = millis();
    if (prevMs != 0) {
      uint32_t dt = nowMs - prevMs;
      if (dt > loopMaxStallMs) { loopMaxStallMs = dt; loopStallAtSec = nowMs / 1000; }
    }
    prevMs = nowMs;
  }

  // 堆水位监测（诊断用：空闲堆持续走低或跌破告警线是"跑一段时间后重启"的头号嫌疑）
  {
    static uint32_t lastHeapChk = 0;
    if (millis() - lastHeapChk > 2000UL) {
      lastHeapChk = millis();
      uint32_t h = ESP.getFreeHeap();
      if (h < minHeapSeen) {
        minHeapSeen = h;
        minHeapAtSec = millis() / 1000;
        if (h < HEAP_WARN_LEVEL) {
          warnHeapCount++;
          Serial.printf("[MEM] 警告: 空闲堆跌至 %lu 字节（第 %lu 秒，碎片率 %u%%）\n",
                        (unsigned long)h, (unsigned long)(millis() / 1000),
                        (unsigned)ESP.getHeapFragmentation());
        }
      }
    }
  }

  // 传感器采样
  pulse.task();

  // 串口调试命令
  serialCmd();

  // Web / DNS
  server.handleClient();
  cap.handleClient();          // 80 端口：AP 模式做门户跳转，STA 模式做透明跳转
  if (apMode) {
    dnsServer.processNextRequest();
  } else {
    MDNS.update();
  }

  // 历史记录（每 2 秒一点，供大屏近 8 分钟曲线）
  if (millis() - lastHistMs > 2000) {
    lastHistMs = millis();
    hist[histHead].t = millis() / 1000;
    hist[histHead].hr = (int16_t)pulse.getHR();
    hist[histHead].spo2 = (int16_t)pulse.getSpO2();
    histHead = (histHead + 1) % HIST_N;
    if (histCount < HIST_N) histCount++;
  }

  // 24 小时历史（每分钟 1 点，环形覆盖最近 1440 分钟）
  // 每 2 秒采样累加，整分钟提交平均值：只要持续在测，曲线就是连续平滑的；
  // 有效读数不足半分钟（频繁拿起放下 / 瞬间掠过）则记 0（前端留空），避免孤立点连成直线
  if (millis() - lastHist24SampleMs > 2000UL) {
    lastHist24SampleMs = millis();
    int hr = pulse.getHR();
    int sp = pulse.getSpO2();
    if (pulse.fingerOn()) {
      if (hr > 0 && hr <= 255) { h24Sum += hr; h24Cnt++; }
      if (sp > 0 && sp <= 100) { s24Sum += sp; s24Cnt++; }
    }
  }
  if (millis() - lastHist24Ms > 60000UL) {
    lastHist24Ms = millis();
    uint32_t idx = hist24Min % HIST24_N;
    hist24HR[idx] = (uint8_t)(h24Cnt >= 15 ? (h24Sum + h24Cnt / 2) / h24Cnt : 0);
    hist24Sp[idx] = (uint8_t)(s24Cnt >= 15 ? (s24Sum + s24Cnt / 2) / s24Cnt : 0);
    h24Sum = s24Sum = h24Cnt = s24Cnt = 0;
    hist24Min++;
  }

  // RTC 诊断快照：每 60 秒把本次运行的状态写进 RTC（复位后即可回溯"上次运行了多久、被什么终止"）
  {
    static uint32_t lastRtcSnap = 0;
    if (millis() - lastRtcSnap > 60000UL) {
      lastRtcSnap = millis();
      RstRec &r = rtc.rst[rtc.rstHead % RTC_MAXRST];
      r.upSec = millis() / 1000;
      r.heap = ESP.getFreeHeap();
      r.heapMin = (minHeapSeen == 0xFFFFFFFF) ? r.heap : minHeapSeen;
      rtc.hist24Min = hist24Min;
      time_t now = time(nullptr);
      if (now > 1600000000) rtc.lastMinEpoch = (uint32_t)(now / 60);
      rtcSave();
    }
  }

  // Flash 历史落盘：每 5 分钟写一次（游标有变化才写），断电/重启最多丢 5 分钟
  {
    static uint32_t lastHfsMs = 0;
    if (millis() - lastHfsMs > 300000UL) {
      lastHfsMs = millis();
      if (hist24Min != hfsSavedMin) hfsSave();
    }
  }

  // 定时上传（失败后按指数退避跳过若干轮；interval=0 表示关闭上报）
  if (!apMode && cfgValid && cfg.interval > 0 &&
      (int32_t)(millis() - upBackoffUntil) >= 0 &&
      millis() - lastUploadMs > (uint32_t)cfg.interval * 1000UL) {
    lastUploadMs = millis();
    doUpload();
  }

  // 轮询服务器下发消息（HTTP 模式）
  // 同样受上报退避约束：服务器不可达时不再每 10 秒硬试一次（TCP 连接槽被占满是 Web 假死的元凶）
  if (!apMode && cfgValid && cfg.proto == 0 &&
      (int32_t)(millis() - upBackoffUntil) >= 0 &&
      millis() - lastPollMs > 10000UL) {
    lastPollMs = millis();
    pollMessage();
  }

  // ---- 网络在线保障（核心：设备一旦入网就绝不主动离开局域网）----
  // 1) 普通掉线由 SDK 的 autoReconnect 秒级自愈，这里只做“升级重连”，不抢底层重连
  // 2) 长时间连不上 → 开备用配置热点，但 STA 侧继续重连，联网一恢复就自动重新在线
  // 3) 绝不因为“长时间没有测量数据”而断开网络：空载时另有 30 秒保活流量
  if (!apMode) {
    static uint32_t lastReconnect = 0;
    static uint32_t offlineSince = 0;
    static uint8_t  reconnectTries = 0;
    if (WiFi.status() != WL_CONNECTED) {
      if (offlineSince == 0) offlineSince = millis();
      uint32_t down = millis() - offlineSince;
      if (down > 20000UL && millis() - lastReconnect > 30000UL) {
        lastReconnect = millis();
        reconnectTries++;
        Serial.printf("[STA] 掉线 %lu 秒，升级重连（第 %u 次）\n",
                      (unsigned long)(down / 1000), (unsigned)reconnectTries);
        WiFi.reconnect();
      }
      if (down > 300000UL) startBackupAp();   // 5 分钟：开备用热点，但绝不退出 STA
    } else {
      if (offlineSince) {
        Serial.printf("[STA] 已恢复在线（中断约 %lu 秒）\n",
                      (unsigned long)((millis() - offlineSince) / 1000));
      }
      offlineSince = 0; reconnectTries = 0;
    }
  }

  // 热点模式下的后台重连：路由器临时重启等导致开机没连上时，仍会持续尝试入网，
  // 一旦连上立刻切回 STA 模式（避免设备被“困”在热点里长期离线）
  if (apMode && cfgValid && cfg.ssid[0]) {
    static uint32_t lastStaTry = 0;
    if (millis() - lastStaTry > 30000UL) {
      lastStaTry = millis();
      if (WiFi.status() == WL_CONNECTED) {
        dnsServer.stop();
        WiFi.softAPdisconnect(true);
        apMode = false;
        WiFi.mode(WIFI_STA);
        configTime(8 * 3600, 0, "ntp.aliyun.com", "pool.ntp.org");
        if (MDNS.begin(MDNS_NAME)) MDNS.addService("http", "tcp", WEB_PORT);
        String ip = WiFi.localIP().toString();
        Serial.printf("[NET] 后台已连上局域网，切回 STA 模式: http://%s/ 或 http://%s:%u/\n",
                      ip.c_str(), ip.c_str(), (unsigned)WEB_PORT);
      } else {
        Serial.println("[NET] 热点模式后台尝试连接局域网...");
        WiFi.begin(cfg.ssid, cfg.pass[0] ? cfg.pass : NULL);
      }
    }
  }

  // 空闲保活：没有测量数据时也每 30 秒产生一次轻量流量，
  // 避免部分路由器/AP 清理“空闲客户端”导致掉线。
  // 注意必须用 UDP：TCP 连不通时（服务器黑洞丢弃）会占住 lwIP 连接槽，反而把 Web 服务饿死
  if (!apMode && WiFi.status() == WL_CONNECTED) {
    static uint32_t lastKeepAliveMs = 0;
    if (millis() - lastKeepAliveMs > 30000UL) {
      lastKeepAliveMs = millis();
      static WiFiUDP ka;
      ka.beginPacket(WiFi.gatewayIP(), 53);   // 发一个空 UDP 包即可刷新 ARP / 客户端活跃表
      ka.write((uint8_t)0);
      ka.endPacket();
    }
  }

  // OLED 刷新
  oledTick();

  yield();
}
