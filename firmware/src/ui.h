#pragma once
// ============================================================
// 显示模块：ST7735 0.96" 160x80 IPS TFT
// 显示内容：温度/湿度/气压、系统状态、WiFi/MQTT 连接状态
// ============================================================
#include <Arduino.h>
#include "sensors.h"

enum NetState : uint8_t {
    NET_OFF = 0,     // 未配置/未连接
    NET_CONNECTING,  // 正在连接
    NET_WIFI_OK,     // WiFi 已连接
    NET_MQTT_OK      // WiFi + MQTT 都已连接
};

class DisplayUI {
public:
    bool begin();
    // 主界面刷新（每秒调用一次即可，内部按需局部重绘）
    void showMain(const EnvData &d, NetState net, bool alarm);
    // 配网 AP 界面
    void showAP(const char *ap_ssid, const EnvData *d = nullptr, bool voiceTrig = false);
    // 启动画面
    void showBoot(const char *fw_ver);
    void backlight(bool on);
    // TTS 语音播报：在屏幕上显示文本 3 秒
    void showTtsMessage(const String &text);
#ifdef DISPLAY_DIAG
    // 屏幕方向诊断：循环尝试 8 种 MADCTL/偏移组合，不会返回
    void diagLoop();
#endif

private:
    void drawStatusBar(NetState net, bool alarm);
    uint16_t colorForNet(NetState net);
    const char *netLabel(NetState net);

    NetState _lastNet = NET_OFF;
    bool     _lastAlarm = false;
    char     _lastTemp[16] = {0};
    char     _lastHum[16]  = {0};
    char     _lastPres[16] = {0};
    bool     _apMode = false;
    uint8_t  _apPage = 0;
    uint32_t _apLastSwitch = 0;
    // TTS 消息显示时间戳（0=无消息）
    uint32_t _ttsShowUntil = 0;
};
