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
static bool     g_mqttReady = false;
static bool     g_displayDirty = false;
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

static void renderTft() {
    if (!g_tftOk) return;
    g_tft.fillScreen(C_BLACK);

    // ---- 顶部状态栏 ----
    String ssid = getCurSsid();
    if (ssid.isEmpty()) ssid = "---";
    if (ssid.length() > 14) ssid = ssid.substring(0, 14);
    String topLine = "SSID:" + ssid;

    g_tft.setTextSize(1);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(2, 3);
    g_tft.print(topLine.c_str());

    // WiFi 信号条（右侧）
    int8_t rssi = g_net.wifiConnected() ? WiFi.RSSI() : 127;
    uint8_t bars = 0;
    if (rssi >= -50) bars = 4;
    else if (rssi >= -65) bars = 3;
    else if (rssi >= -78) bars = 2;
    else if (rssi >= -90) bars = 1;
    g_tft.fillRect(138, 3, 2, 8, C_GRAY);
    g_tft.fillRect(136, 5, 2, 6, C_GRAY);
    g_tft.fillRect(134, 7, 2, 4, C_GRAY);
    g_tft.fillRect(132, 9, 2, 2, C_GRAY);
    if (bars >= 1) g_tft.fillRect(138, 3, 2, 8, C_GREEN);
    if (bars >= 2) g_tft.fillRect(136, 5, 2, 6, C_GREEN);
    if (bars >= 3) g_tft.fillRect(134, 7, 2, 4, C_GREEN);
    if (bars >= 4) g_tft.fillRect(132, 9, 2, 2, C_GREEN);

    // 分隔线
    for (int x = 0; x < 160; x += 2) g_tft.drawPixel(x, 12, C_GRAY);

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

    // ---- 连接 WiFi 后: 仅显示 WiFi 名称和 IP 地址 ----
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

    // ---- 底部状态栏 ----
    for (int x = 0; x < 160; x += 2) g_tft.drawPixel(x, 70, C_GRAY);

    g_tft.setTextSize(1);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(4, 72);
    const char *net = g_mqttReady ? "MQTT" : (g_net.wifiConnected() ? "WIFI" : "OFF");
    g_tft.print(net);
    g_tft.print(" v");
    g_tft.print(FW_VERSION);

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

    // AP 配网模式
    if (g_net.inAPMode()) {
        if (g_tftOk && (now - g_lastTft >= 1500 || g_displayDirty)) {
            g_lastTft = now;
            renderTft();
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

    // TFT 刷新
    if (g_tftOk && (now - g_lastTft >= 1500 || g_displayDirty)) {
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
    delay(5);
}