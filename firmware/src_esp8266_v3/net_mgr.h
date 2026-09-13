#pragma once
// ============================================================
// 网络管理模块 (ESP8266) v3.0
// STA 连接 + AP 配网 + UDP 自动发现
// ============================================================
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <DNSServer.h>
#include <WiFiUdp.h>
#include "config.h"

enum NetMode : uint8_t { MODE_STA, MODE_AP };

class NetManager {
public:
    void begin();
    void loop();
    void startAP();
    bool inAPMode() const { return _mode == MODE_AP; }
    bool wifiConnected() const { return WiFi.status() == WL_CONNECTED; }
    bool staHasConfig() const { return _cfg->has_wifi(); }
    String apSSID() const { return _ap_ssid; }
    void setConfig(DeviceConfig *cfg) { _cfg = cfg; }

    // UDP 局域网自动发现
    void startDiscover();
    void stopDiscover();
    int  discoverLoop(uint32_t now);

private:
    void startSTA();
    void tryReconnect();
    void handleRoot();
    void handleSave();
    void handleScan();
    void startPortalServer();
    void buildScanCache(int n);

    DeviceConfig *_cfg = nullptr;
    NetMode _mode = MODE_STA;
    String  _ap_ssid;
    uint32_t _lastTry = 0;
    uint32_t _retryDelay = 1000;
    bool     _staStarted = false;
    bool     _portalRunning = false;
    String  _scanCache;
    bool    _scanBusy = false;

    // UDP 发现
    WiFiUDP  _udp;
    bool     _udpBound = false;
    bool     _discActive = false;
    uint32_t _discLastSent = 0;
    uint32_t _discStartAt = 0;

    ESP8266WebServer web;
    DNSServer dns;
};

extern NetManager g_net;
