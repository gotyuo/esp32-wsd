// ============================================================
// 物联网环境监测系统 - ESP32-S3 固件主程序
//
//  数据流: AHT20/BMP280 -> (采样) -> 阈值判定 -> LED/蜂鸣器
//                                  -> ST7735 屏幕显示
//                                  -> MQTT 上报服务器
//
//  串口调试命令(115200):
//    config   立即进入 AP 配网模式
//    factory  清除全部配置并重启
//    status   打印当前状态
// ============================================================
#include <Arduino.h>
#include "pins.h"
#include "config_store.h"
#include "sensors.h"
#include "ui.h"
#include "alarm.h"
#include "net_mgr.h"
#include "mqtt_mgr.h"
#include "ota_mgr.h"

static SensorHub   g_sensors;
static DisplayUI   g_ui;
static AlarmDevice g_alarm;

static EnvData  g_last;
static uint32_t g_lastRead = 0;
static uint32_t g_lastUi   = 0;
static uint32_t g_lastPub  = 0;
static bool     g_mqttReady = false;

// 麦克风声音检测
static int g_micBaseline = 0;     // 环境噪声基线
static bool g_voiceTrig = false;  // 是否检测到语音

static void initMic() {
    // 裸驻极体咪头: 无外部偏置电阻时用内部上拉(~45kΩ)作偏置
    pinMode(PIN_MIC, INPUT_PULLUP);
    analogReadResolution(12);  // 12-bit ADC
    analogSetPinAttenuation(PIN_MIC, ADC_11db);  // 0-3.3V 全量程
    // 采样100次建立基线
    int sum = 0;
    for (int i = 0; i < 100; i++) {
        sum += analogRead(PIN_MIC);
        delay(1);
    }
    g_micBaseline = sum / 100;
    Serial.printf("[MIC] baseline=%d\n", g_micBaseline);
}

static void checkMic() {
    // 连续采样10次，计算平均偏差
    int sum = 0, sumDev = 0;
    for (int i = 0; i < 10; i++) {
        int v = analogRead(PIN_MIC);
        sum += v;
        sumDev += abs(v - g_micBaseline);
    }
    int avgDev = sumDev / 10;
    // 阈值：基线偏差超过80视为有声音
    g_voiceTrig = (avgDev > 80);
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
                      g_net.wifiConnected(), g_mqtt.connected(),
                      ESP.getFreeHeap(), g_last.temp_c, g_last.hum_pct, g_last.pres_hpa);
    } else if (cmd == "mic") {
        // 实时打印麦克风 ADC 值，连续 20 次
        Serial.printf("[MIC] baseline=%d, samples:", g_micBaseline);
        for (int i = 0; i < 20; i++) {
            Serial.printf(" %d", analogRead(PIN_MIC));
        }
        Serial.println();
    } else if (cmd == "adcall") {
        const int pins[] = {1,2,3,4,5,6,7,8,9,10};
        for (int pi = 0; pi < 10; pi++) {
            analogSetPinAttenuation(pins[pi], ADC_11db);
            Serial.printf("  GPIO%d=%d\n", pins[pi], analogRead(pins[pi]));
        }
        Serial.println("[ADC] scan done");
    } else if (cmd == "otacheck") {
        Serial.println("[CMD] OTA 手动检查...");
        ota_check(true);
    }
}

void setup() {
    Serial.begin(115200);
    delay(400);
    Serial.println();
    Serial.println(F("======================================"));
    Serial.println(F(" EnvMon ESP32-S3  firmware " FW_VERSION));
    Serial.println(F("======================================"));

#ifdef DISPLAY_DIAG
    // 屏幕方向诊断模式：只初始化屏幕，循环尝试 8 种配置，不会返回
    Serial.println(F("[DIAG] Display diagnostics started"));
    g_ui.begin();
    g_ui.diagLoop();   // 永不返回
#endif

    g_cfgStore.begin();
    bool saved = g_cfgStore.load(g_cfg);
    Serial.printf("[BOOT] config %s, device_id=%s\n",
                  saved ? "loaded" : "NOT found (first boot)", g_cfg.device_id);

    g_ui.begin();
    g_ui.showBoot(FW_VERSION);

    g_alarm.begin();

    if (!g_sensors.begin()) {
        Serial.println(F("[BOOT] WARNING: sensors unavailable"));
    }

    g_net.setConfig(&g_cfg);
    g_net.begin();

    // 根据保存的 MQTT host 自动推断 OTA 服务器
    ota_set_server((String(g_cfg.mqtt_host) + ":" + String(g_cfg.mqtt_port)).c_str(), nullptr);

    initMic();  // 初始化麦克风

    // 启动时自动检查一次 OTA（首次引导会顺便完成 boot_count 回滚判定）
    delay(500);
    ota_setup();
    ota_check(false);

    delay(1000);   // 显示启动画面
}

void loop() {
    handleSerialCmd();
    g_net.loop();
    uint32_t now = millis();

    // ---------- AP 配网模式 ----------
    if (g_net.inAPMode()) {
        if (now - g_lastRead >= 2000) {
            g_lastRead = now;
            g_sensors.read(g_last);
        }
        checkMic();  // 检测声音
        g_ui.showAP(g_net.apSSID().c_str(), &g_last, g_voiceTrig);
        g_alarm.update(AL_CONFIG, false);
        delay(10);
        return;
    }

    // ---------- MQTT 初始化（WiFi 就绪后） ----------
    if (!g_mqttReady && g_cfg.has_mqtt() && g_net.wifiConnected()) {
        g_mqtt.begin();
        g_mqttReady = true;
    }
    if (g_mqttReady) g_mqtt.loop();

    // ---------- 传感器采样（每 2s） ----------
    if (now - g_lastRead >= 2000) {
        g_lastRead = now;
        g_sensors.read(g_last);
    }

    // ---------- 阈值判定 + LED/蜂鸣器 ----------
    AlarmLevel lvl = g_alarm.evaluate(g_last, g_cfg);
    g_alarm.update(lvl, g_cfg.alarm_sound);

    // ---------- MQTT 周期上报 ----------
    if (g_mqttReady && g_mqtt.connected() &&
        now - g_lastPub >= (uint32_t)g_cfg.report_interval * 1000UL) {
        g_lastPub = now;
        if (g_mqtt.publishTelemetry(g_last, (int)lvl)) {
            Serial.printf("[MAIN] telemetry published (t=%.1f h=%.1f p=%.1f)\n",
                          g_last.temp_c, g_last.hum_pct, g_last.pres_hpa);
        } else {
            Serial.println(F("[MAIN] publish failed"));
        }
    }

    // ---------- 屏幕刷新（每 500ms，内部局部重绘） ----------
    if (now - g_lastUi >= 500) {
        g_lastUi = now;
        NetState ns = NET_OFF;
        if (g_net.wifiConnected())
            ns = (g_mqttReady && g_mqtt.connected()) ? NET_MQTT_OK : NET_WIFI_OK;
        else if (g_cfg.has_wifi())
            ns = NET_CONNECTING;
        g_ui.showMain(g_last, ns, lvl == AL_ALARM);
    }

    delay(5);   // 让出 CPU，喂狗
}
