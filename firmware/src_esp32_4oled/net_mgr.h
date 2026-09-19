#pragma once
// ============================================================
// 网络管理模块
//  - STA 模式：连接已保存 WiFi，断线自动重连（指数退避）
//  - AP 配网模式：无配置或长按复位时，开启热点 + 网页配置
//    （DNS 劫持 captive portal，手机连热点后访问任意网址即弹出）
//  - LAN 自动发现：server_mode==0 且未保存 MQTT host 时，
//    在多播组 239.255.1.1:12091 上广播发现服务器
// ============================================================
#include <Arduino.h>
#include <functional>
#include <WiFiUdp.h>
#include "config_store.h"

enum NetMode : uint8_t {
    MODE_STA,
    MODE_AP
};

// 传感器数据快照：main loop 每轮更新，Web 数据页读取
struct SensorSnapshot {
    float temp_c = NAN, hum_pct = NAN, pres_hpa = NAN;
    float sp_o2  = NAN, pr_hr    = NAN;
    float mic    = 0;
    bool  wifi   = false, mqtt = false;
    uint32_t uptime = 0;
    bool  valid  = false;
};

typedef std::function<void(const DeviceConfig &)> ConfigSavedCb;
typedef std::function<SensorSnapshot()> DataCb;

class NetManager {
public:
    void begin();
    void loop();

    void startAP();
    bool inAPMode() const { return _mode == MODE_AP; }

    bool wifiConnected() const;
    bool staHasConfig() const { return _cfg->has_wifi(); }

    String apSSID() const { return _ap_ssid; }
    void onConfigSaved(ConfigSavedCb cb) { _onSaved = cb; }
    void setConfig(DeviceConfig *cfg) { _cfg = cfg; }
    void setDataCallback(DataCb cb) { _onData = cb; }

    // LAN 自动发现（public：main loop 需要调用）
    void startDiscover();
    int  discoverLoop(uint32_t now);   // 0=继续 1=已发现(重启) -1=超时(回AP)
    void stopDiscover();
    bool inDiscovery() const { return _discActive; }

private:
    void startSTA();
    void tryReconnect();
    void handleRoot();
    void handleSave();
    void handleScan();
    void handleJson();
    void handleData();
    void startPortalServer();
    void startDataServer();

    DeviceConfig *_cfg = nullptr;
    NetMode _mode = MODE_STA;
    String  _ap_ssid;

    uint32_t _lastTry = 0;
    uint32_t _retryDelay = 1000;
    bool     _staStarted = false;
    uint32_t _staStartedAt = 0;

    ConfigSavedCb _onSaved;
    DataCb        _onData;
    bool     _portalRunning = false;
    bool     _dataRunning = false;

    String  _scanCache;
    uint32_t _scanStartedAt = 0;
    bool    _scanBusy = false;
    void buildScanCache(int n);
    void requestScan();

    // LAN 自动发现状态
    WiFiUDP _udp;
    bool    _udpBound = false;
    bool    _discActive = false;
    uint32_t _discLastSent = 0;
    uint32_t _discStartAt  = 0;
};

extern NetManager g_net;
