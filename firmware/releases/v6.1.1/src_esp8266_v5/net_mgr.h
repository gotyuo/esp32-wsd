#pragma once
// ============================================================
// 网络管理模块 (ESP8266) v4.0
// STA 连接 + AP 配网 + Web 数据页 (实时数据 + 历史曲线)
//
// 默认行为:
//   - 无 WiFi 配置: 直接进 AP 配网
//   - 有 WiFi 配置: 先试 STA (同时开 AP 兜底), STA 成功后关 AP 跑数据页
//     STA 15s 超时未连 -> 切回纯 AP 重新配网
// ============================================================
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <DNSServer.h>
#include "config.h"
#include "sensors.h"
#include "history.h"
#include "alarm.h"

// main.cpp 暴露的全局对象
// (config.h 已声明 ConfigStore g_cfgStore / DeviceConfig g_cfg / SensorHub 类)
extern EnvData      g_last;
extern HistoryStore g_hist;
extern AlarmDevice  g_alarm;

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

private:
    void startSTA();
    void tryReconnect();
    String apSsidOrDefault() const;

    void startPortalServer();
    void startDataService();

    void handleRoot();
    void handleSave();
    void handleScan();
    void handleDataJson();
    void handleHistJson();
    void handleDataHtml();
    void handleHistHtml();

    void buildScanCache(int n);

    DeviceConfig *_cfg = nullptr;
    NetMode _mode = MODE_STA;
    String  _ap_ssid;
    uint32_t _lastTry = 0;
    uint32_t _retryDelay = 1000;
    uint32_t _staAttemptStart = 0;
    uint32_t _staAttemptTimeout = 15000;
    bool     _staStarted = false;
    bool     _staConnectedLocked = false;
    bool     _portalRunning = false;
    bool     _dataServerRunning = false;
    String  _scanCache;
    bool    _scanBusy = false;

    ESP8266WebServer web;
    DNSServer dns;
};

extern NetManager g_net;
