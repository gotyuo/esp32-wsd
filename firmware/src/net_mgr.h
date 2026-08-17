#pragma once
// ============================================================
// 网络管理模块
//  - STA 模式：连接已保存 WiFi，断线自动重连（指数退避）
//  - AP 配网模式：无配置或长按复位时，开启热点 + 网页配置
//    （DNS 劫持 captive portal，手机连热点后访问任意网址即弹出）
// ============================================================
#include <Arduino.h>
#include <functional>
#include "config_store.h"

enum NetMode : uint8_t {
    MODE_STA,
    MODE_AP
};

// 配网完成后由主程序重启使用
typedef std::function<void(const DeviceConfig &)> ConfigSavedCb;

class NetManager {
public:
    void begin();
    void loop();

    // 进入 AP 配网模式（长按复位或无配置时调用）
    void startAP();
    bool inAPMode() const { return _mode == MODE_AP; }

    // STA 状态
    bool wifiConnected() const;
    bool staHasConfig() const { return _cfg->has_wifi(); }

    String apSSID() const { return _ap_ssid; }
    void onConfigSaved(ConfigSavedCb cb) { _onSaved = cb; }

    void setConfig(DeviceConfig *cfg) { _cfg = cfg; }

private:
    void startSTA();
    void tryReconnect();
    // AP 内部
    void handleRoot();
    void handleSave();
    void handleScan();
    void startPortalServer();

    DeviceConfig *_cfg = nullptr;
    NetMode _mode = MODE_STA;
    String  _ap_ssid;

    uint32_t _lastTry = 0;
    uint32_t _retryDelay = 1000;
    bool     _staStarted = false;
    uint32_t _staStartedAt = 0;

    ConfigSavedCb _onSaved;
    bool     _portalRunning = false;
};

extern NetManager g_net;
