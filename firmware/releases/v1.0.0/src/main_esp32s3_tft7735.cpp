// ============================================================
// EnvMon ESP32-S3 固件主程序 — ST7735 TFT LCD 变体
// 屏幕：0.96" SPI TFT (ST7735 IPS) 160x80
// 接线：SCK=GPIO12, MOSI=GPIO11, CS=GPIO10, DC=GPIO7, RST=GPIO6, BLK=GPIO5
// 数据流：AHT20/BMP280(I2C0) -> 采样 -> 阈值判定 -> RGB LED/蜂鸣器
//         -> MQTT 上报 + TFT 显示
// 串口调试命令(115200)：config / factory / status
// ============================================================
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <SPI.h>
#include "pins_esp32s3_tft7735.h"
#include "config_store.h"
#include "sensors.h"
#include "alarm.h"
#include "net_mgr.h"
#include "mqtt_mgr.h"
#include "ota_mgr.h"
#include "st7735.h"

// sensors.cpp 用 extern TwoWire oledWire(1)（MAX30102 共用 I2C1）
TwoWire oledWire(1);  // I2C1 实例（供 MAX30102 传感器共用，TFT 变体无 OLED）

static SensorHub   g_sensors;
static ST7735      g_tft;
static bool        g_tftOk = false;
static AlarmDevice g_alarm;

static EnvData  g_last;
static uint32_t g_lastRead = 0;
static uint32_t g_lastTft  = 0;
static uint32_t g_lastPub  = 0;
static uint32_t g_lastHttp = 0;  // HTTP POST 上次上报时间
static bool     g_mqttReady = false;
static bool     g_displayDirty = false;
static uint8_t  g_pageIdx = 0;      // 轮播页索引 0=WiFi 1=体征 2=血氧
static uint32_t g_lastPageSwitch = 0;
static const uint32_t PAGE_INTERVAL = 4000;  // 每4秒切页
static float    g_micLevel = 0;   // MIC 当前电平 0~1

static void forceRefreshTft() { if (g_tftOk) g_lastTft = 0; }

static String getCurSsid() {
    if (WiFi.status() == WL_CONNECTED) {
        String s = WiFi.SSID(); s.trim();
        if (!s.isEmpty()) return s;
    }
    if (g_net.inAPMode()) return "AP-CONFIG";
    if (g_cfg.has_wifi()) return String(g_cfg.wifi_ssid);
    return "";
}

// HTTP POST 遥测上报到服务器 /api/telemetry（无需鉴权）
static bool httpPostTelemetry(const EnvData &d, int alarmLevel) {
    if (!g_cfg.has_mqtt() || !g_net.wifiConnected()) return false;
    if (g_cfg.http_port == 0) return false;

    WiFiClient client;
    HTTPClient http;
    String url = String("http://") + String(g_cfg.mqtt_host)
               + ":" + String((int)g_cfg.http_port) + "/api/telemetry";
    http.begin(client, url);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(5000);

    // seq: 单调序列号(去重), 与 MQTT 一致用 uptime 秒
    uint32_t seq = (uint32_t)(millis() / 1000);

    // NaN 值发 null (服务器 handle_telemetry 对 None 跳过)
    auto nanOrNull = [](float v, int prec) -> String {
        if (isnan(v)) return "null";
        return String(v, prec);
    };

    // JSON payload: 与 MQTT 遥测格式一致
    String json = String("{\"device_id\":\"") + g_cfg.device_id + "\""
                + ",\"seq\":" + String((unsigned long)seq)
                + ",\"t\":"  + nanOrNull(d.temp_c, 2)
                + ",\"h\":"  + nanOrNull(d.hum_pct, 2)
                + ",\"p\":"  + nanOrNull(d.pres_hpa, 2)
                + ",\"rssi\":" + String((int)WiFi.RSSI())
                + ",\"uptime\":" + String((unsigned long)(millis() / 1000))
                + ",\"alarm\":" + String((int)alarmLevel)
                + ",\"fw\":\"" + String(FW_VERSION) + "\""
                + ",\"heap\":" + String((unsigned long)ESP.getFreeHeap())
                + ",\"ip\":\"" + WiFi.localIP().toString() + "\"";

    // 体征数据（非 NaN 时附带）
    if (!isnan(d.sp_o2))  json += ",\"sp_o2\":"  + nanOrNull(d.sp_o2, 1);
    if (!isnan(d.pr_hr))  json += ",\"pr_hr\":"  + nanOrNull(d.pr_hr, 1);
    if (!isnan(d.ecg_hr)) json += ",\"ecg_hr\":" + nanOrNull(d.ecg_hr, 1);
    if (!isnan(d.rr_bpm)) json += ",\"rr_bpm\":" + nanOrNull(d.rr_bpm, 1);
    if (!isnan(d.glucose)) json += ",\"glucose\":" + nanOrNull(d.glucose, 2);

    json += "}";

    int code = http.POST(json);
    http.end();

    if (code == 200) {
        Serial.printf("[HTTP] telemetry OK seq=%lu (t=%.1f h=%.1f p=%.0f)\n",
                      (unsigned long)seq, d.temp_c, d.hum_pct, d.pres_hpa);
        return true;
    } else {
        Serial.printf("[HTTP] telemetry FAIL code=%d\n", code);
        return false;
    }
}

static void renderTft() {
    if (!g_tftOk) return;
    g_tft.fillScreen(C_BLACK);

    // 顶部分隔线 (WiFi信息已在轮播第1页显示)
    for (int x = 0; x < 160; x += 2) g_tft.drawPixel(x, 8, C_GRAY);

    // ---- AP 配网模式: 显示热点名+密码+管理IP ----
    if (g_net.inAPMode()) {
        g_tft.setTextColor(C_YELLOW);
        g_tft.setTextSize(1);
        g_tft.setCursor(4, 18);
        g_tft.print("AP Config");
        g_tft.setTextColor(C_WHITE);
        g_tft.setCursor(4, 32);
        String apName = g_net.apSSID();
        if (apName.length() > 20) apName = apName.substring(0, 20);
        g_tft.print("SSID:" + apName);
        g_tft.setTextColor(C_GREEN);
        g_tft.setCursor(4, 44);
        g_tft.print("PWD:12345689");
        g_tft.setTextColor(C_CYAN);
        g_tft.setCursor(4, 56);
        g_tft.print("IP:192.168.4.1");
        g_tft.setTextColor(C_GRAY);
        g_tft.setCursor(4, 68);
        g_tft.print("scan wifi -> save");
        g_displayDirty = false;
        return;
    }

    // ---- 连接 WiFi 后: 三页轮播 WiFi/体征/血氧 ----
    char buf[16];

    if (g_pageIdx == 0) {
        // ---- 第1页: WiFi ----
        String ipStr = WiFi.localIP().toString();
        String curSsid = getCurSsid();
        if (curSsid.isEmpty()) curSsid = "---";
        if (curSsid.length() > 20) curSsid = curSsid.substring(0, 20);

        g_tft.setTextSize(1);
        g_tft.setTextColor(C_GREEN);
        g_tft.setCursor(4, 20);
        g_tft.print("WIFI OK");
        g_tft.setTextColor(C_WHITE);
        g_tft.setCursor(4, 38);
        g_tft.print("WiFi:" + curSsid);
        g_tft.setTextColor(C_CYAN);
        g_tft.setCursor(4, 58);
        g_tft.print("IP:" + ipStr);
    }
    else if (g_pageIdx == 1) {
        // ---- 第2页: 体征 (温湿度气压) ----
        g_tft.setTextSize(1);
        g_tft.setTextColor(C_ORANGE);
        g_tft.setCursor(4, 20);
        g_tft.print("T:");
        snprintf(buf, sizeof(buf), "%.1f", g_last.temp_c);
        g_tft.setTextColor(C_WHITE);
        g_tft.setCursor(20, 20);
        g_tft.print(buf);
        g_tft.setTextColor(C_GRAY);
        g_tft.setCursor(48, 20);
        g_tft.print("C");

        g_tft.setTextColor(C_CYAN);
        g_tft.setCursor(4, 40);
        g_tft.print("H:");
        snprintf(buf, sizeof(buf), "%.1f", g_last.hum_pct);
        g_tft.setTextColor(C_WHITE);
        g_tft.setCursor(20, 40);
        g_tft.print(buf);
        g_tft.setTextColor(C_GRAY);
        g_tft.setCursor(48, 40);
        g_tft.print("%");

        g_tft.setTextColor(C_GREEN);
        g_tft.setCursor(4, 60);
        g_tft.print("P:");
        snprintf(buf, sizeof(buf), "%d", (int)g_last.pres_hpa);
        g_tft.setTextColor(C_WHITE);
        g_tft.setCursor(20, 60);
        g_tft.print(buf);
        g_tft.setTextColor(C_GRAY);
        g_tft.setCursor(48, 60);
        g_tft.print("hPa");
    }
    else {
        // ---- 第3页: 血氧心率 ----
        g_tft.setTextSize(1);
        g_tft.setTextColor(C_RED);
        g_tft.setCursor(4, 20);
        g_tft.print("SpO2:");
        if (!isnan(g_last.sp_o2)) {
            snprintf(buf, sizeof(buf), "%.0f%%", g_last.sp_o2);
            g_tft.setTextColor(C_WHITE);
        } else {
            g_tft.setTextColor(C_GRAY);
            buf[0] = '-'; buf[1] = '-'; buf[2] = 0;
        }
        g_tft.setCursor(20, 20);
        g_tft.print(buf);

        g_tft.setTextColor(C_CYAN);
        g_tft.setCursor(4, 40);
        g_tft.print("HR:");
        if (!isnan(g_last.pr_hr)) {
            snprintf(buf, sizeof(buf), "%.0f", g_last.pr_hr);
            g_tft.setTextColor(C_WHITE);
        } else {
            g_tft.setTextColor(C_GRAY);
            buf[0] = '-'; buf[1] = '-'; buf[2] = 0;
        }
        g_tft.setCursor(20, 40);
        g_tft.print(buf);
        g_tft.setTextColor(C_GRAY);
        g_tft.setCursor(48, 40);
        g_tft.print("bpm");

        g_tft.setTextColor(C_GRAY);
        g_tft.setCursor(4, 60);
        g_tft.print("MIC:");
        int micBars = (int)(g_micLevel * 10);
        if (micBars > 10) micBars = 10;
        if (micBars < 0) micBars = 0;
        for (int i = 0; i < 10; i++) {
            uint16_t col = (i < micBars) ? C_GREEN : C_GRAY;
            g_tft.fillRect(20 + i * 12, 60, 10, 8, col);
        }
    }

    // ---- 底部状态栏 (版本+页指示+报警等级) ----
    for (int x = 0; x < 160; x += 2) g_tft.drawPixel(x, 70, C_GRAY);

    g_tft.setTextSize(1);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(4, 72);
    g_tft.print("v" FW_VERSION);

    // 页指示点 (●●○ / ●○● / ○●●)
    const char *dots = (g_pageIdx == 0) ? "●●○" :
                       (g_pageIdx == 1) ? "●○●" : "○●●";
    g_tft.setTextColor(C_CYAN);
    g_tft.setCursor(60, 72);
    g_tft.print(dots);

    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(100, 72);
    g_tft.print("L:");
    g_tft.print(g_alarm.level());

    // 报警红边框
    if (g_alarm.level() >= AL_ALARM) {
        for (int x = 0; x < 160; x += 2) { g_tft.drawPixel(x, 0, C_RED); g_tft.drawPixel(x, 79, C_RED); }
        for (int y = 0; y < 80; y += 2)  { g_tft.drawPixel(0, y, C_RED); g_tft.drawPixel(159, y, C_RED); }
    }
    g_displayDirty = false;
}

static void handleSerialCmd() {
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toLowerCase();
    if (cmd == "config") {
        Serial.println(F("[CMD] Entering AP config mode..."));
        g_net.startAP();
        forceRefreshTft();
    } else if (cmd == "factory") {
        Serial.println(F("[CMD] Factory reset..."));
        if (g_tftOk) {
            g_tft.fillScreen(C_BLACK);
            g_tft.setTextColor(C_RED);
            g_tft.setTextSize(2);
            g_tft.setCursor(20, 28);
            g_tft.print("FACTORY");
            g_tft.setCursor(24, 44);
            g_tft.print("RESET");
            delay(1500);
        }
        g_cfgStore.clear();
        delay(300);
        ESP.restart();
    } else if (cmd == "status") {
        Serial.printf("[CMD] wifi=%d mqtt=%d heap=%u t=%.1f h=%.1f p=%.1f\n",
                      (WiFi.status() == WL_CONNECTED), g_mqtt.connected(),
                      (unsigned)ESP.getFreeHeap(),
                      g_last.temp_c, g_last.hum_pct, g_last.pres_hpa);
    }
}

void setup() {
    Serial.begin(115200);
    delay(400);
    Serial.println();
    Serial.println(F("======================================"));
    Serial.println(F(" EnvMon ESP32-S3 (TFT7735) " FW_VERSION));
    Serial.println(F("======================================"));

    g_cfgStore.begin();
    bool saved = g_cfgStore.load(g_cfg);
    g_cfgStore.applyDefaults(g_cfg);
    Serial.printf("[BOOT] config %s, device_id=%s\n",
                  saved ? "loaded" : "NOT found (first boot)", g_cfg.device_id);

    g_alarm.begin();

    // 传感器
    if (!g_sensors.begin()) {
        Serial.println(F("[BOOT] WARNING: sensors unavailable"));
    }

    // TFT: 软件 SPI 初始化
    g_tft.begin(PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_RST, PIN_TFT_MOSI, PIN_TFT_SCK);
    g_tft.fillScreen(C_BLACK);
    g_tft.setTextColor(C_WHITE);
    g_tft.setTextSize(2);
    g_tft.setCursor(30, 26);
    g_tft.print("ENVMON");
    g_tft.setTextSize(1);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(40, 50);
    g_tft.print("ESP32-S3 v");
    g_tft.print(FW_VERSION);
    g_tftOk = true;

    // 背光
    pinMode(PIN_TFT_BLK, OUTPUT);
    digitalWrite(PIN_TFT_BLK, HIGH);

    Serial.println(F("[BOOT] TFT 0.96\" OK (SPI CS=10 DC=7 RST=6 MOSI=11 SCK=12)"));

    g_net.setConfig(&g_cfg);
    // 注册数据回调：Web 数据页通过 /json 获取实时传感器数据
    g_net.setDataCallback([]() -> SensorSnapshot {
        SensorSnapshot s;
        s.temp_c   = g_last.temp_c;
        s.hum_pct  = g_last.hum_pct;
        s.pres_hpa = g_last.pres_hpa;
        s.sp_o2    = g_last.sp_o2;
        s.pr_hr    = g_last.pr_hr;
        s.mic      = g_micLevel;
        s.valid    = g_last.valid;
        s.mqtt     = g_mqttReady && g_mqtt.connected();
        return s;
    });
    g_net.begin();

    String _otaHost = String(g_cfg.mqtt_host) + ":" + String(g_cfg.mqtt_port);
    ota_set_server(_otaHost.length() > 1 ? _otaHost.c_str() : nullptr, nullptr);
    delay(500);
    ota_setup();
    ota_check(false);
    delay(1000);
}

void loop() {
    handleSerialCmd();
    g_net.loop();
    uint32_t now = millis();

    // AP 配网模式: 内容不变,只在首次或脏数据时刷新(避免反复整屏清黑闪烁)
    if (g_net.inAPMode()) {
        if (g_tftOk && (g_displayDirty || now - g_lastTft >= 5000)) {
            g_lastTft = now;
            renderTft();
            g_displayDirty = false;
        }
        if (now - g_lastRead >= 2000) {
            g_lastRead = now;
            g_sensors.read(g_last);
        }
        g_alarm.update(AL_CONFIG, false);
        delay(10);
        return;
    }

    // MQTT
    if (!g_mqttReady && g_cfg.has_mqtt() && g_net.wifiConnected()) {
        g_mqtt.begin();
        g_mqttReady = true;
    }
    if (g_mqttReady) g_mqtt.loop();

    // LAN 自动发现
    if (!g_mqttReady && g_net.wifiConnected()
            && g_cfg.server_mode == 0 && !g_cfg.has_mqtt()) {
        static bool discoveryFailed = false;
        if (!discoveryFailed && !g_net.inDiscovery()) g_net.startDiscover();
        int disc = g_net.discoverLoop(now);
        if (disc == 1) {
            Serial.println(F("[MAIN] server discovered -> restarting to apply"));
            delay(500);
            ESP.restart();
        } else if (disc == -1) {
            // WiFi 已连上，发现失败不回落到 AP，也不再重试（保持 STA 模式）
            // 下次重启或手动 config 命令时可重新触发发现
            discoveryFailed = true;
            Serial.println(F("[MAIN] discovery failed, staying in STA mode (AP off)"));
        }
    }

    // 传感器
    if (now - g_lastRead >= 2000) {
        g_lastRead = now;
        g_sensors.read(g_last);
        g_sensors.readVitals(g_last);
        g_micLevel = g_sensors.readMic();
    }
    AlarmLevel lvl = g_alarm.evaluate(g_last, g_cfg);
    g_alarm.update(lvl, g_cfg.alarm_sound);

    // TFT 刷新: 非AP模式下每4秒切页轮播(WiFi/体征/血氧),切页时才重绘
    if (g_tftOk && !g_net.inAPMode()) {
        if (now - g_lastPageSwitch >= PAGE_INTERVAL) {
            g_lastPageSwitch = now;
            g_pageIdx = (g_pageIdx + 1) % 3;
            g_lastTft = 0;  // 触发下次刷新
            Serial.printf("[TFT] page -> %d\n", g_pageIdx);
        }
    }
    // 刷新间隔 5s,切页时立即刷新(g_lastTft=0 让 now-0>=5000 为真)
    if (g_tftOk && (now - g_lastTft >= 5000 || g_displayDirty)) {
        g_lastTft = now;
        renderTft();
    }

    // MQTT 上报
    if (g_mqttReady && g_mqtt.connected() &&
        now - g_lastPub >= (uint32_t)g_cfg.report_interval * 1000UL) {
        g_lastPub = now;
        if (g_mqtt.publishTelemetry(g_last, (int)lvl)) {
            Serial.printf("[MAIN] telemetry published (t=%.1f h=%.1f p=%.1f)\n",
                          g_last.temp_c, g_last.hum_pct, g_last.pres_hpa);
        }
    }

    // HTTP POST 上报（与 MQTT 并行，独立触发）
    if (g_cfg.has_mqtt() && g_net.wifiConnected() &&
        now - g_lastHttp >= (uint32_t)g_cfg.report_interval * 1000UL) {
        g_lastHttp = now;
        httpPostTelemetry(g_last, (int)lvl);
    }
    delay(5);
}