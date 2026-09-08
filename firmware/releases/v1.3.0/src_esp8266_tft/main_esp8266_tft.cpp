// ============================================================
// EnvMon ESP8266 固件主程序 — 6 线 SPI TFT 变体
//
// 屏幕: 0.96" SPI TFT (ST7735S) 160x80, 走软件 SPI
// 数据流: AHT20/BMP280(I2C) -> 采样 -> 阈值判定 -> LED(让位) -> MQTT
//         -> TFT 显示
//
// 与 ESP8266 OLED 变体的区别: 屏从 SSD1306(I2C,u8g2) 换成 ST7735(SPI),
// 引脚从 D0/D1 软件 I2C 换成 D0/D1/D2/D4/D5 软件 SPI; 传感器 I2C 走 D6/D7 硬件 Wire。
//
// 串口调试命令(115200): config / factory / status
// ============================================================
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <SPI.h>
#include "pins_esp8266_tft.h"
#include "config_store_esp8266.h"
#include "sensors_esp8266.h"
#include "alarm_esp8266.h"
#include "net_mgr_esp8266.h"
#include "mqtt_mgr_esp8266.h"
#include "st7735.h"

static SensorHub g_sensors;
static ST7735    g_tft;
static bool      g_tftOk = false;
static AlarmDevice g_alarm;

static EnvData  g_last;
static uint32_t g_lastRead = 0;
static uint32_t g_lastTft  = 0;
static uint32_t g_lastPub  = 0;
static bool     g_mqttReady = false;
static char     g_lastSsid[33] = "";
static bool     g_displayDirty = false;

static void renderTft();
static void forceRefreshTft();

// 当前 SSID
static String getCurSsid() {
    wl_status_t st = WiFi.status();
    if (st == WL_CONNECTED) {
        String s = WiFi.SSID(); s.trim();
        if (!s.isEmpty()) return s;
    }
    if (g_net.inAPMode()) return "AP-CONFIG";
    if (g_cfg.has_wifi()) return String(g_cfg.wifi_ssid);
    return "";
}

static void checkSsidChanged() {
    String cur = getCurSsid();
    if (cur != String(g_lastSsid)) {
        strncpy(g_lastSsid, cur.c_str(), sizeof(g_lastSsid) - 1);
        g_lastSsid[sizeof(g_lastSsid) - 1] = '\0';
        g_displayDirty = true;
    }
}

// 简化版显示 —— 复用 ESP8266 OLED 变体布局思路, 换成 TFT 160x80 坐标
static void renderTft() {
    if (!g_tftOk) return;

    g_tft.fillScreen(C_BLACK);

    // ---- 顶部状态栏 (y=0-12) ----
    g_tft.setTextSize(1);
    String ssid = getCurSsid();
    if (ssid.isEmpty()) ssid = "---";
    if (ssid.length() > 14) ssid = ssid.substring(0, 14);
    String topLine = "SSID:" + ssid;

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

    // ---- AP 配网模式 ----
    if (g_net.inAPMode()) {
        g_tft.setTextColor(C_YELLOW);
        g_tft.setTextSize(1);
        g_tft.setCursor(4, 18);
        g_tft.print("AP config mode");
        g_tft.setTextColor(C_WHITE);
        g_tft.setCursor(4, 32);
        g_tft.print(g_net.apSSID().c_str());
        g_tft.setCursor(4, 44);
        g_tft.print("192.168.4.1");
        g_tft.setCursor(4, 56);
        g_tft.print("set WiFi+MQTT");
        return;
    }

    // ---- 主数据区: T/H/P ----
    char buf[16];

    // 温度
    g_tft.setTextColor(C_ORANGE);
    g_tft.setTextSize(1);
    g_tft.setCursor(4, 20);
    g_tft.print("T:");
    snprintf(buf, sizeof(buf), "%.1f", g_last.temp_c);
    g_tft.setTextColor(C_WHITE);
    g_tft.setCursor(20, 20);
    g_tft.print(buf);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(48, 20);
    g_tft.print("C");

    // 湿度
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

    // 气压
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

    // ---- 底部状态栏 (y=72) ----
    for (int x = 0; x < 160; x += 2) g_tft.drawPixel(x, 70, C_GRAY);

    g_tft.setTextSize(1);
    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(4, 72);
    const char *net = g_mqttReady ? "MQTT" : (g_net.wifiConnected() ? "WIFI" : "OFF");
    g_tft.print(net);
    g_tft.print(" v");
    g_tft.print(FW_VER);

    g_tft.setTextColor(C_GRAY);
    g_tft.setCursor(100, 72);
    g_tft.print("L:");
    g_tft.print(g_alarm.level());

    // 报警闪灯(红色边框)
    if (g_alarm.level() >= AL_ALARM) {
        // 红色边框（左右上下）
        for (int x = 0; x < 160; x += 2) { g_tft.drawPixel(x, 0, C_RED); g_tft.drawPixel(x, 79, C_RED); }
        for (int y = 0; y < 80; y += 2)  { g_tft.drawPixel(0, y, C_RED); g_tft.drawPixel(159, y, C_RED); }
    }

    g_displayDirty = false;
}

static void forceRefreshTft() {
    if (!g_tftOk) return;
    g_lastTft = 0;
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
    Serial.println(F("===================================="));
    Serial.println(F(" EnvMon ESP8266 TFT firmware " FW_VERSION));
    Serial.println(F("===================================="));

    g_cfgStore.begin();
    bool saved = g_cfgStore.load(g_cfg);
    g_cfgStore.applyDefaults(g_cfg);
    Serial.printf("[BOOT] config %s, device_id=%s\n",
                  saved ? "loaded" : "NOT found (first boot)", g_cfg.device_id);

    g_alarm.begin();

    // 传感器: 硬件 I2C (SDA=D6, SCL=D7)
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
    g_tft.print("ESP8266 v" FW_VERSION);
    g_tftOk = true;

    // BL 初始化
    if (PIN_TFT_BL < 255) {
        pinMode(PIN_TFT_BL, OUTPUT);
        digitalWrite(PIN_TFT_BL, HIGH);
    }

    Serial.println(F("[BOOT] TFT 0.96\" OK (SPI CS=D0 DC=D1 RST=None)"));

    g_net.setConfig(&g_cfg);
    g_net.begin();

    // 启动页: 显示目标 SSID
    delay(200);
    if (g_tftOk) {
        g_tft.fillScreen(C_BLACK);
        g_tft.setTextColor(C_WHITE);
        g_tft.setTextSize(1);
        g_tft.setCursor(20, 20);
        g_tft.print("Connecting...");
        String bootSsid = getCurSsid();
        if (bootSsid.isEmpty()) bootSsid = "---";
        if (bootSsid.length() > 20) bootSsid = bootSsid.substring(0, 20);
        String bootLine = "SSID:" + bootSsid;
        g_tft.setCursor(10, 40);
        g_tft.print(bootLine.c_str());
        g_tft.setTextColor(C_GRAY);
        g_tft.setCursor(20, 60);
        g_tft.print("EnvMon " FW_VERSION);
    }
    Serial.printf("[BOOT] target SSID: %s\n", getCurSsid().c_str());
    delay(2000);
    forceRefreshTft();
}

void loop() {
    handleSerialCmd();
    g_net.loop();
    checkSsidChanged();
    uint32_t now = millis();

    // AP 配网模式
    if (g_net.inAPMode()) {
        if (g_tftOk && (now - g_lastTft >= 500 || g_displayDirty)) {
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

    // 传感器
    if (now - g_lastRead >= 2000) {
        g_lastRead = now;
        g_sensors.read(g_last);
    }
    AlarmLevel lvl = g_alarm.evaluate(g_last, g_cfg);
    g_alarm.update(lvl, g_cfg.alarm_sound);
    if (g_tftOk && (now - g_lastTft >= 500 || g_displayDirty)) {
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