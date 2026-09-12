#pragma once
// ============================================================
// 网络管理 — STA 连接 + AP 配网 (captive portal)
// ============================================================
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <DNSServer.h>
#include "config_store.h"

enum NetMode : uint8_t { MODE_STA, MODE_AP };

class NetManager {
public:
    void begin();
    void loop();
    void startAP();
    bool inAPMode() const { return _mode == MODE_AP; }
    bool wifiConnected() const { return WiFi.status() == WL_CONNECTED; }
    String apSSID() const { return _ap_ssid; }
    void setConfig(DeviceConfig *cfg) { _cfg = cfg; }

private:
    void startSTA();
    void tryReconnect();
    void handleRoot();
    void handleSave();
    void handleScan();
    void startPortal();
    void buildScanCache(int n);

    DeviceConfig *_cfg = nullptr;
    NetMode _mode = MODE_STA;
    String  _ap_ssid;
    uint32_t _lastTry = 0;
    uint32_t _retryDelay = 1000;
    bool     _staStarted = false;
    bool     _portalOn = false;
    String   _scanCache = "[]";
    bool     _scanBusy = false;

    ESP8266WebServer web;
    DNSServer dns;
};

extern NetManager g_net;