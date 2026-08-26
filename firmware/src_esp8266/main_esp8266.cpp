// ============================================================
// EnvMon ESP8266 固件主程序
//
// 数据流: AHT20/BMP280 -> 采样 -> 阈值判定 -> LED -> MQTT
// OLED: 0.96" SSD1306 (u8g2 库, 软件 I2C, SDA=GPIO2/SCL=GPIO14)
//
// 串口调试命令(115200):
//   config   立即进入 AP 配网模式
//   factory  清除全部配置并重启
//   status   打印当前状态
// ============================================================
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <U8g2lib.h>
#include "pins_esp8266.h"
#include "config_store_esp8266.h"
#include "sensors_esp8266.h"
#include "alarm_esp8266.h"
#include "net_mgr_esp8266.h"
#include "mqtt_mgr_esp8266.h"

// u8g2 SSD1306 128x64, 软件 I2C: (rotation, clock=SCL, data=SDA, reset)
U8G2_SSD1306_128X64_NONAME_F_SW_I2C g_oled(U8G2_R0, /*clock=*/ PIN_OLED_SCL, /*data=*/ PIN_OLED_SDA, /*reset=*/ U8X8_PIN_NONE);
static bool        g_oledOk = false;
static SensorHub   g_sensors;
static AlarmDevice g_alarm;
static EnvData     g_last;
static uint32_t    g_lastRead = 0;
static uint32_t    g_lastPub  = 0;
static bool        g_mqttReady = false;
static uint32_t    g_lastOled = 0;
static char        g_lastSsid[33] = "";
static bool        g_displayDirty = false;

static void renderOled();
static void forceRefreshOled();

// 当前 SSID (WiFi 已连 -> SDK 值; AP 配网模式 -> "AP-CONFIG"; 已保存配置兜底)
static String getCurSsid() {
    wl_status_t st = WiFi.status();
    if (st == WL_CONNECTED) {
        String s = WiFi.SSID();
        s.trim();
        if (!s.isEmpty()) return s;
    }
    if (g_net.inAPMode()) return "AP-CONFIG";
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

static void renderOled() {
    if (!g_oledOk) return;
    g_oled.clearBuffer();
    g_oled.setFont(u8g2_font_6x10_tr);

    // ---- 第一行: SSID + 信号条 ----
    bool isApMode = g_net.inAPMode();
    String ssid = getCurSsid();
    if (ssid.isEmpty()) ssid = "---";
    if (ssid.length() > 12) ssid = ssid.substring(0, 12);
    String ssidLine = "SSID:" + ssid;
    g_oled.setCursor(2, 14);
    g_oled.print(ssidLine.c_str());

    int8_t rssi = g_net.wifiConnected() ? WiFi.RSSI() : 127;
    uint8_t bars = 0;
    if (rssi >= -50) bars = 4;
    else if (rssi >= -65) bars = 3;
    else if (rssi >= -78) bars = 2;
    else if (rssi >= -90) bars = 1;
    for (int i = 0; i < 4; i++) {
        uint8_t h = 2 + i * 2;
        if (i < bars) g_oled.drawBox(102 + i * 6, 11 - h, 4, h);
        else          g_oled.drawFrame(102 + i * 6, 11 - h, 4, h);
    }

    // 分隔线
    g_oled.drawHLine(2, 22, 124);

    // AP 模式下显示配网地址 + 提示; 联网模式下显示温湿度气压
    if (isApMode) {
        g_oled.drawStr(2, 34, "AP config");
        g_oled.drawStr(2, 46, "192.168.4.1");
        g_oled.drawStr(2, 56, "set WiFi/MQTT");
    } else {
        char line[24];
        g_oled.drawStr(2, 34, "T:");
        snprintf(line, sizeof(line), "%.1f", g_last.temp_c);
        g_oled.drawStr(14, 34, line);
        g_oled.drawStr(46, 34, "C");

        g_oled.drawStr(2, 46, "H:");
        snprintf(line, sizeof(line), "%.1f", g_last.hum_pct);
        g_oled.drawStr(14, 46, line);
        g_oled.drawStr(46, 46, "%");

        g_oled.drawStr(2, 58, "P:");
        snprintf(line, sizeof(line), "%d", (int)g_last.pres_hpa);
        g_oled.drawStr(14, 58, line);
        g_oled.drawStr(48, 58, "hPa");
    }

    g_oled.sendBuffer();
    g_displayDirty = false;
}

// 强制刷新(OLED 显示内容变化时调用,不受 500ms 节流限制)
static void forceRefreshOled() {
    if (!g_oledOk) return;
    g_lastOled = 0;  // 让下一次正常轮询命中
}

// BOOT 键长按检测: 上电按住 D3(GPIO0) 超过 3 秒 -> factory reset
static void checkBootKey() {
    pinMode(PIN_BOOT_KEY, INPUT_PULLUP);
    uint32_t t0 = millis();
    while (digitalRead(PIN_BOOT_KEY) == LOW) {
        if (millis() - t0 > 3000) {
            Serial.println(F("[BOOT] BOOT key held 3s -> FACTORY RESET"));
            if (g_oledOk) {
                g_oled.clearBuffer();
                g_oled.setFont(u8g2_font_6x10_tr);
                g_oled.drawStr(2, 18, "FACTORY RESET");
                g_oled.drawStr(2, 32, "Clearing config...");
                g_oled.sendBuffer();
            }
            g_cfgStore.clear();
            delay(500);
            ESP.restart();
        }
        delay(50);
    }
}

static void handleSerialCmd() {
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toLowerCase();
    if (cmd == "config") {
        Serial.println(F("[CMD] Entering AP config mode..."));
        g_net.startAP();
    } else if (cmd == "factory") {
        Serial.println(F("[CMD] Factory reset..."));
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
    Serial.println(F(" EnvMon ESP8266 firmware " FW_VERSION));
    Serial.println(F("======================================"));

    g_cfgStore.begin();
    // BOOT 键长按 3s → factory reset（在 load 之前，清掉配置包括 device_id）
    checkBootKey();
    bool saved = g_cfgStore.load(g_cfg);
    g_cfgStore.applyDefaults(g_cfg);
    Serial.printf("[BOOT] config %s, device_id=%s\n",
                  saved ? "loaded" : "NOT found (first boot)", g_cfg.device_id);

    g_alarm.begin();
    if (!g_sensors.begin()) {
        Serial.println(F("[BOOT] WARNING: sensors unavailable"));
    }

    // OLED: u8g2 初始化 (软件 I2C SCL=GPIO14, SDA=GPIO2)
    g_oled.begin();
    g_oled.clearBuffer();
    g_oled.setFont(u8g2_font_6x10_tr);
    g_oled.drawStr(2, 12, "EnvMon starting...");
    g_oled.drawStr(2, 26, "ESP8266 v" FW_VERSION);
    g_oled.sendBuffer();
    g_oledOk = true;
    Serial.println(F("[BOOT] OLED 0.96\" u8g2 OK (SDA=GPIO2 SCL=GPIO14)"));

    g_net.setConfig(&g_cfg);
    g_net.begin();

    // 启动页: 显示当前要连的 SSID / AP 名, 2s 停留
    delay(200);
    if (g_oledOk) {
        String bootSsid = getCurSsid();
        g_oled.clearBuffer();
        g_oled.setFont(u8g2_font_6x10_tr);
        g_oled.drawStr(2, 18, "Connecting...");
        if (bootSsid.isEmpty()) bootSsid = "---";
        if (bootSsid.length() > 18) bootSsid = bootSsid.substring(0, 18);
        String bootLine = "SSID:" + bootSsid;
        g_oled.setCursor(2, 38);
        g_oled.print(bootLine.c_str());
        g_oled.drawStr(2, 54, "EnvMon " FW_VERSION);
        g_oled.sendBuffer();
    }
    Serial.printf("[BOOT] target SSID: %s\n", getCurSsid().c_str());
    delay(2000);
    forceRefreshOled();
}

void loop() {
    handleSerialCmd();
    g_net.loop();
    checkSsidChanged();   // 每次循环检测 SSID 是否变化, 变化则 OLED 立即刷新
    uint32_t now = millis();

    // ---------- AP 配网模式 ----------
    if (g_net.inAPMode()) {
        if (g_oledOk && (now - g_lastOled >= 500 || g_displayDirty)) {
            g_lastOled = now;
            renderOled();
        }
        if (now - g_lastRead >= 2000) {
            g_lastRead = now;
            g_sensors.read(g_last);
        }
        g_alarm.update(AL_CONFIG, false);
        delay(10);
        return;
    }

    // ---------- MQTT 初始化 ----------
    if (!g_mqttReady && g_cfg.has_mqtt() && g_net.wifiConnected()) {
        g_mqtt.begin();
        g_mqttReady = true;
    }
    if (g_mqttReady) g_mqtt.loop();

    // ---------- 传感器采样（每 2s）----------
    if (now - g_lastRead >= 2000) {
        g_lastRead = now;
        g_sensors.read(g_last);
    }
    AlarmLevel lvl = g_alarm.evaluate(g_last, g_cfg);
    g_alarm.update(lvl, g_cfg.alarm_sound);
    if (g_oledOk && (now - g_lastOled >= 500 || g_displayDirty)) {
        g_lastOled = now;
        renderOled();
    }

    // ---------- MQTT 周期上报 ----------
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