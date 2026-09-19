// ============================================================
// EnvMon ESP8266 v4.0 固件主程序
//
// 硬件:
//   OLED 0.96" I2C (SSD1306)  SCL=D5(GPIO14)  SDA=D6(GPIO12)
//   AHT20 + BMP280 + MAX30102 共用总线  SCL=D8(GPIO15)  SDA=D7(GPIO13)
//
// OLED 界面 (自动 5s 切换; 串口命令可手动切换/固定):
//   1 环境  : 温度 湿度 气压
//   2 体征  : 血氧 SpO2  心率 HR  (无 MAX30102 时显示 --)
//   3 网络  : 当前 SSID / IP / AP 配网地址
//   4 历史  : 最近 1h 曲线 (每 5min 1 点, 12 点窗口)
//
// 串口命令 (115200):
//   page1..page4  切到对应页
//   page          下一页
//   auto          恢复自动轮播
//   config        立即进入 AP 配网模式
//   factory       清除全部配置并重启
//   status        打印当前状态
//   hist          打印历史 JSON
// ============================================================
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <U8g2lib.h>
#include "pins.h"
#include "config.h"
#include "sensors.h"
#include "alarm.h"
#include "net_mgr.h"
#include "mqtt_mgr.h"
#include "history.h"

// u8g2 SSD1306 128x64, **软件 I2C** (D5/D6 与传感器总线 D7/D8 不同)
// 硬件 Wire 给传感器用, 屏幕走 u8g2 自带的软件 I2C
U8G2_SSD1306_128X64_NONAME_F_SW_I2C g_oled(U8G2_R0,
    /* clock pin */ PIN_OLED_SCL,
    /* data pin  */ PIN_OLED_SDA,
    /* reset pin */ U8X8_PIN_NONE);

static bool        g_oledOk   = false;
SensorHub          g_sensors;
AlarmDevice        g_alarm;
EnvData            g_last;
uint32_t           g_lastRead = 0;
uint32_t           g_lastPub  = 0;
bool               g_mqttReady = false;
bool               g_discActive = false;
uint32_t           g_lastOled = 0;
uint32_t           g_lastHist = 0;
char               g_lastSsid[33] = "";
bool               g_displayDirty = false;

HistoryStore       g_hist;

// OLED 页面状态
static uint8_t     g_page      = 1;
static bool        g_autoPage  = true;
static uint32_t    g_lastPageSwitch = 0;

static void renderPageEnv();
static void renderPageVitals();
static void renderPageWifi();
static void renderPageHistory();
static void renderOled();
static void forceRefreshOled();
static void nextPage();

// ---------- 当前 SSID (WiFi 已连 -> SDK; AP -> AP-CONFIG; 已保存配置兜底) ----------
static String makeDefaultApSsid() {
    uint8_t mac[6];
    WiFi.macAddress(mac);
    return "ESP8266OLED-" + String(mac[4], HEX) + String(mac[5], HEX);
}

static String getCurSsid() {
    wl_status_t st = WiFi.status();
    if (st == WL_CONNECTED) {
        String s = WiFi.SSID();
        s.trim();
        if (!s.isEmpty()) return s;
    }
    if (g_net.inAPMode()) return makeDefaultApSsid();
    if (g_cfg.has_wifi()) return String(g_cfg.wifi_ssid);
    return "";
}

static void checkSsidChanged() {
    String cur = getCurSsid();
    const char *cp = cur.c_str();
    if (strcmp(g_lastSsid, cp) != 0) {
        strncpy(g_lastSsid, cp, sizeof(g_lastSsid) - 1);
        g_lastSsid[sizeof(g_lastSsid) - 1] = '\0';
        g_displayDirty = true;
    }
}

// ---------- 页面 1: 环境 (温度/湿度/气压) ----------
static void renderPageEnv() {
    g_oled.clearBuffer();
    g_oled.setFont(u8g2_font_7x13B_tr);
    g_oled.drawStr(2, 12, "Environment");
    g_oled.drawHLine(2, 16, 124);

    g_oled.setFont(u8g2_font_6x10_tr);
    g_oled.drawStr(2, 28, "T:");
    if (isnan(g_last.temp_c)) g_oled.drawStr(14, 28, "--");
    else {
        char buf[16];
        snprintf(buf, sizeof(buf), "%.1f", g_last.temp_c);
        g_oled.drawStr(14, 28, buf);
    }
    g_oled.drawStr(56, 28, "C");
    if (g_alarm.level() >= AL_WARNING && (isnan(g_last.temp_c) == false) &&
        (g_last.temp_c < g_cfg.temp_min || g_last.temp_c > g_cfg.temp_max))
        g_oled.drawStr(72, 28, "!");

    g_oled.drawStr(2, 42, "H:");
    if (isnan(g_last.hum_pct)) g_oled.drawStr(14, 42, "--");
    else {
        char buf[16];
        snprintf(buf, sizeof(buf), "%.1f", g_last.hum_pct);
        g_oled.drawStr(14, 42, buf);
    }
    g_oled.drawStr(56, 42, "%");

    g_oled.drawStr(2, 56, "P:");
    if (isnan(g_last.pres_hpa)) g_oled.drawStr(14, 56, "--");
    else {
        char buf[16];
        snprintf(buf, sizeof(buf), "%d", (int)g_last.pres_hpa);
        g_oled.drawStr(14, 56, buf);
    }
    g_oled.drawStr(56, 56, "hPa");
}

// ---------- 页面 2: 体征 (血氧/心率) ----------
static void renderPageVitals() {
    g_oled.clearBuffer();
    g_oled.setFont(u8g2_font_7x13B_tr);
    g_oled.drawStr(2, 12, "Vitals");
    g_oled.drawHLine(2, 16, 124);

    g_oled.setFont(u8g2_font_6x10_tr);
    g_oled.drawStr(2, 28, "SpO2:");
    if (!g_sensors.max_ok())      g_oled.drawStr(32, 28, "no dev");
    else if (isnan(g_last.sp_o2)) g_oled.drawStr(32, 28, "--");
    else {
        char buf[16];
        snprintf(buf, sizeof(buf), "%.0f", g_last.sp_o2);
        g_oled.drawStr(32, 28, buf);
    }
    g_oled.drawStr(58, 28, "%");
    if (!isnan(g_last.sp_o2) && g_last.sp_o2 < g_cfg.spo2_min) g_oled.drawStr(72, 28, "!");

    g_oled.drawStr(2, 42, "HR:");
    if (!g_sensors.max_ok())      g_oled.drawStr(28, 42, "no dev");
    else if (isnan(g_last.pr_hr)) g_oled.drawStr(28, 42, "--");
    else {
        char buf[16];
        snprintf(buf, sizeof(buf), "%d", (int)g_last.pr_hr);
        g_oled.drawStr(28, 42, buf);
    }
    g_oled.drawStr(60, 42, "bpm");
    if (!isnan(g_last.pr_hr) &&
        (g_last.pr_hr < g_cfg.hr_min || g_last.pr_hr > g_cfg.hr_max))
        g_oled.drawStr(76, 42, "!");

    g_oled.setFont(u8g2_font_5x7_tr);
    g_oled.drawStr(2, 58, g_sensors.max_ok() ? "OK" : "no MAX30102");
}

// ---------- 页面 3: 网络 (SSID/IP/AP) ----------
static void renderPageWifi() {
    g_oled.clearBuffer();
    g_oled.setFont(u8g2_font_7x13B_tr);
    g_oled.drawStr(2, 12, "Network");
    g_oled.drawHLine(2, 16, 124);

    g_oled.setFont(u8g2_font_6x10_tr);
    g_oled.drawStr(2, 28, "SSID:");
    String ssid = getCurSsid();
    if (ssid.isEmpty()) ssid = "---";
    if (ssid.length() > 13) ssid = ssid.substring(0, 13);
    g_oled.drawStr(28, 28, ssid.c_str());

    // 信号条
    int8_t rssi = g_net.wifiConnected() ? WiFi.RSSI() : 127;
    uint8_t bars = 0;
    if (rssi >= -50) bars = 4;
    else if (rssi >= -65) bars = 3;
    else if (rssi >= -78) bars = 2;
    else if (rssi >= -90) bars = 1;
    for (int i = 0; i < 4; i++) {
        uint8_t h = 2 + i * 2;
        if (i < bars) g_oled.drawBox(102 + i * 6, 24 - h, 4, h);
        else          g_oled.drawFrame(102 + i * 6, 24 - h, 4, h);
    }

    g_oled.drawStr(2, 42, "IP:");
    if (g_net.wifiConnected()) {
        String ip = WiFi.localIP().toString();
        if (ip.length() > 14) ip = ip.substring(0, 14);
        g_oled.drawStr(20, 42, ip.c_str());
    } else if (g_net.inAPMode()) {
        g_oled.drawStr(20, 42, "192.168.4.1 (AP)");
    } else {
        g_oled.drawStr(20, 42, "offline");
    }

    g_oled.drawStr(2, 56, "MQTT:");
    g_oled.drawStr(28, 56, g_mqtt.connected() ? "up" : "off");
}

// ---------- 页面 4: 历史曲线 (最近 12 点, 每 5 分钟 1 点) ----------
static void renderPageHistory() {
    g_oled.clearBuffer();
    g_oled.setFont(u8g2_font_6x10_tr);
    g_oled.drawStr(2, 12, "History (1h, 5min)");

    uint8_t n = g_hist.count();
    char sub[24];
    snprintf(sub, sizeof(sub), "points: %d", n);
    g_oled.drawStr(2, 24, sub);

    if (n == 0) {
        g_oled.drawStr(28, 40, "no data yet");
        g_oled.drawStr(28, 52, "wait 5 min");
        return;
    }

    // 画图区: x=[2,124], y=[30,58]  H=28px, W=122px
    uint8_t GX0 = 2, GX1 = 124, GY0 = 32, GY1 = 58;
    uint8_t GW  = GX1 - GX0, GH = GY1 - GY0;

    // 边框
    g_oled.drawFrame(GX0, GY0, GW, GH);

    // 取温度 min/max
    float tMin = 1e9, tMax = -1e9;
    for (uint8_t i = 0; i < n; i++) {
        float t = g_hist.at(i).temp_c;
        if (isnan(t)) continue;
        if (t < tMin) tMin = t;
        if (t > tMax) tMax = t;
    }
    if (tMax - tMin < 1.0f) { tMax += 0.5f; tMin -= 0.5f; }

    // 画温度线
    int8_t prevX = -1, prevY = -1;
    for (uint8_t i = 0; i < n; i++) {
        float t = g_hist.at(i).temp_c;
        if (isnan(t)) { prevX = -1; continue; }
        uint8_t x = GX0 + (n == 1 ? 0 : (uint8_t)((int)(i * (GW - 1)) / (n - 1)));
        uint8_t y = GY1 - (uint8_t)((t - tMin) * GH / (tMax - tMin));
        if (y > GY1) y = GY1;
        if (y < GY0) y = GY0;
        if (prevX >= 0) g_oled.drawLine(prevX, prevY, x, y);
        g_oled.drawPixel(x, y);
        prevX = x; prevY = y;
    }

    // 显示温度范围
    char buf[24];
    snprintf(buf, sizeof(buf), "T %.1f~%.1fC", tMin, tMax);
    g_oled.drawStr(2, 62, buf);
}

static void renderOled() {
    if (!g_oledOk) return;
    switch (g_page) {
        case 1: renderPageEnv();     break;
        case 2: renderPageVitals();  break;
        case 3: renderPageWifi();    break;
        case 4: renderPageHistory(); break;
        default: g_page = 1; renderPageEnv(); break;
    }
    g_oled.sendBuffer();
    g_displayDirty = false;
}

static void forceRefreshOled() {
    if (!g_oledOk) return;
    g_lastOled = 0;
}

static void nextPage() {
    g_page = (g_page % OLED_PAGE_COUNT) + 1;
    g_lastPageSwitch = millis();
    forceRefreshOled();
    Serial.printf("[PAGE] -> %d\n", g_page);
}

static void handleSerialCmd() {
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toLowerCase();
    if (cmd == "page" || cmd == "n") {
        nextPage();
    } else if (cmd == "page1") {
        g_page = 1; g_lastPageSwitch = millis(); forceRefreshOled();
    } else if (cmd == "page2") {
        g_page = 2; g_lastPageSwitch = millis(); forceRefreshOled();
    } else if (cmd == "page3") {
        g_page = 3; g_lastPageSwitch = millis(); forceRefreshOled();
    } else if (cmd == "page4") {
        g_page = 4; g_lastPageSwitch = millis(); forceRefreshOled();
    } else if (cmd == "auto") {
        g_autoPage = true;
        Serial.println(F("[CMD] auto page on"));
    } else if (cmd == "config") {
        Serial.println(F("[CMD] Entering AP config mode..."));
        g_net.startAP();
    } else if (cmd == "factory") {
        Serial.println(F("[CMD] Factory reset..."));
        g_cfgStore.clear();
        g_hist.clear();
        delay(300);
        ESP.restart();
    } else if (cmd == "status") {
        Serial.printf("[CMD] wifi=%d mqtt=%d heap=%u t=%.1f h=%.1f p=%.1f spo2=%.1f hr=%.1f page=%d hist=%d\n",
                      (WiFi.status() == WL_CONNECTED), g_mqtt.connected(),
                      (unsigned)ESP.getFreeHeap(),
                      g_last.temp_c, g_last.hum_pct, g_last.pres_hpa,
                      g_last.sp_o2, g_last.pr_hr, g_page, g_hist.count());
    } else if (cmd == "hist") {
        Serial.println(F("[HIST] "));
        Serial.println(g_hist.toJson());
    }
}

void setup() {
    Serial.begin(115200);
    delay(400);
    Serial.println();
    Serial.println(F("======================================"));
    Serial.println(F(" EnvMon ESP8266 firmware " FW_VERSION));
    Serial.println(F("======================================"));

    g_cfgStore.begin();
    bool saved = g_cfgStore.load(g_cfg);
    g_cfgStore.applyDefaults(g_cfg);
    Serial.printf("[BOOT] config %s, device_id=%s\n",
                  saved ? "loaded" : "NOT found (first boot)", g_cfg.device_id);

    g_alarm.begin();

    if (!g_sensors.begin()) {
        Serial.println(F("[BOOT] WARNING: no sensors available"));
    }

    // 历史: 纯内存环形缓冲, 无需 begin 失败检查
    g_hist.begin();

    // OLED: u8g2 软件 I2C (SCL=GPIO14, SDA=GPIO12) — 与传感器硬件 Wire 独立
    pinMode(PIN_OLED_SDA, INPUT);
    pinMode(PIN_OLED_SCL, INPUT);
    g_oled.setBusClock(400000);
    g_oled.begin();
    g_oled.clearBuffer();
    g_oled.setFont(u8g2_font_6x10_tr);
    g_oled.drawStr(2, 12, "EnvMon starting...");
    g_oled.drawStr(2, 26, "ESP8266 v" FW_VERSION);
    g_oled.sendBuffer();
    g_oledOk = true;
    Serial.printf("[BOOT] OLED OK (SW I2C: SDA=GPIO%d SCL=GPIO%d)\n",
                  PIN_OLED_SDA, PIN_OLED_SCL);

    // 网络: v4.0 默认上电进 AP 配网 (若无 WiFi 配置), 否则先 STA 后 AP 兜底
    g_net.setConfig(&g_cfg);
    g_net.begin();

    // 启动页: 显示当前要连的 SSID / AP 名, 2s 停留
    delay(200);
    if (g_oledOk) {
        String bootSsid = getCurSsid();
        g_oled.clearBuffer();
        g_oled.setFont(u8g2_font_6x10_tr);
        g_oled.drawStr(2, 18, g_net.inAPMode() ? "AP mode" : "Connecting...");
        if (bootSsid.isEmpty()) bootSsid = "---";
        if (bootSsid.length() > 18) bootSsid = bootSsid.substring(0, 18);
        String bootLine = "SSID:" + bootSsid;
        g_oled.setCursor(2, 38);
        g_oled.print(bootLine.c_str());
        g_oled.drawStr(2, 54, "EnvMon v" FW_VERSION);
        g_oled.sendBuffer();
    }
    Serial.printf("[BOOT] target SSID: %s, AP mode: %d\n",
                  getCurSsid().c_str(), g_net.inAPMode());
    delay(2000);
    forceRefreshOled();
}

void loop() {
    handleSerialCmd();
    g_net.loop();
    checkSsidChanged();
    uint32_t now = millis();

    // ---------- 传感器采样 (2s) ----------
    if (now - g_lastRead >= 2000) {
        g_lastRead = now;
        g_sensors.read(g_last);
        g_sensors.readVitals(g_last);
    }

    // ---------- 历史采样 (5 min) ----------
    if (now - g_lastHist >= HISTORY_INTERVAL_MS) {
        g_lastHist = now;
        if (g_last.valid) g_hist.record(g_last);
    }

    // ---------- AP 配网模式 ----------
    if (g_net.inAPMode()) {
        // 报警
        g_alarm.update(AL_CONFIG, false);
        // OLED 自动轮播（即使在 AP 模式也翻页）
        if (g_autoPage && (now - g_lastPageSwitch >= OLED_PAGE_INTERVAL)) {
            nextPage();
        }
        if (g_oledOk && (now - g_lastOled >= 500 || g_displayDirty)) {
            g_lastOled = now;
            renderOled();
        }
        delay(10);
        return;
    }

    // ---------- UDP 自动发现 (server_mode=0 且无 mqtt_host) ----------
    if (g_cfg.server_mode == 0 && !g_cfg.has_mqtt() && g_net.wifiConnected()) {
        if (!g_discActive) {
            g_net.startDiscover();
        } else {
            int dr = g_net.discoverLoop(now);
            if (dr == 1) {
                Serial.println(F("[MAIN] discovery success, rebooting"));
                delay(500);
                ESP.restart();
            } else if (dr == -1) {
                Serial.println(F("[MAIN] discovery failed -> entering AP portal"));
                g_net.startAP();
            }
        }
    }

    // ---------- MQTT ----------
    if (!g_mqttReady && g_cfg.has_mqtt() && g_net.wifiConnected()) {
        g_mqtt.begin();
        g_mqttReady = true;
    }
    if (g_mqttReady) g_mqtt.loop();

    // ---------- 报警 ----------
    AlarmLevel lvl = g_alarm.evaluate(g_last, g_cfg);
    g_alarm.update(lvl, g_cfg.alarm_sound);

    // ---------- OLED 自动轮播 ----------
    if (g_autoPage && (now - g_lastPageSwitch >= OLED_PAGE_INTERVAL)) {
        nextPage();
    }
    if (g_oledOk && (now - g_lastOled >= 500 || g_displayDirty)) {
        g_lastOled = now;
        renderOled();
    }

    // ---------- MQTT 周期上报 ----------
    if (g_mqttReady && g_mqtt.connected() &&
        now - g_lastPub >= (uint32_t)g_cfg.report_interval * 1000UL) {
        g_lastPub = now;
        if (g_mqtt.publishTelemetry(g_last, (int)lvl)) {
            Serial.printf("[MAIN] telemetry (t=%.1f h=%.1f p=%.1f spo2=%.1f hr=%.1f)\n",
                          g_last.temp_c, g_last.hum_pct, g_last.pres_hpa,
                          g_last.sp_o2, g_last.pr_hr);
        }
    }
    delay(5);
}
