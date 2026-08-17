#include "net_mgr.h"
#include "pins.h"
#include <WiFi.h>
#include <DNSServer.h>
#include <WebServer.h>
#include <esp_wifi.h>

static WebServer web(80);
static DNSServer dns;

NetManager g_net;

// ---------------- 配网页面（手机自适应） ----------------
static const char PORTAL_HTML[] PROGMEM = R"rawliteral(<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EnvMon 配网</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}
h2{color:#38bdf8;margin:8px 0 16px}
.card{background:#1e293b;border-radius:12px;padding:16px;margin-bottom:14px}
label{display:block;font-size:13px;color:#94a3b8;margin:10px 0 4px}
input,select{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:15px}
button{width:100%;padding:13px;border:0;border-radius:10px;background:#0ea5e9;color:#fff;font-size:16px;font-weight:600;margin-top:16px}
.hint{font-size:12px;color:#64748b;margin-top:6px}
</style></head><body>
<h2>环境监测站 配网</h2>
<form method="POST" action="/save">
<div class="card"><b>热点设置</b>
<label>热点名称（配网时用手机连接的 WiFi，留空用默认）</label>
<input name="apssid" id="apssid" placeholder="如 EnvMon-Pro">
</div>
<div class="card"><b>无线网络</b>
<label>WiFi 名称</label>
<select name="ssid" id="ssid"><option>正在扫描...</option></select>
<label>或手动输入 WiFi 名称</label>
<input name="ssid2" placeholder="手动输入（优先于上方选择）">
<label>WiFi 密码</label>
<input name="pass" type="password">
</div>
<div class="card"><b>服务器（MQTT）</b>
<label>服务器地址（IP 或域名）</label>
<input name="host" placeholder="例如 192.168.1.100" required>
<label>MQTT 端口</label>
<input name="port" type="number" value="18830">
<label>MQTT 用户名</label>
<input name="user" value="envmon">
<label>MQTT 密码</label>
<input name="mpass" type="password">
<label>设备编号</label>
<input name="devid" placeholder="留空自动生成">
<label>上报间隔（秒）</label>
<input name="interval" type="number" value="10" min="3">
</div>
<button type="submit">保存并连接</button>
<div class="hint">保存后设备将自动重启并连接网络。</div>
</form>
<script>
fetch('/scan').then(r=>r.json()).then(l=>{
 var s=document.getElementById('ssid');s.innerHTML='';
 if(!l.length){s.innerHTML='<option>未扫描到网络</option>';return;}
 l.forEach(n=>{var o=document.createElement('option');o.value=n.ssid;
  o.textContent=n.ssid+' ('+n.rssi+' dBm'+(n.open?'':' 🔒')+')';s.appendChild(o);});
});
</script></body></html>)rawliteral";

// ---------------- STA ----------------
void NetManager::begin() {
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(false);   // 自行实现带退避的重连
    if (_cfg->has_wifi()) {
        startSTA();
    } else {
        Serial.println(F("[NET] No WiFi config, entering AP portal"));
        startAP();
    }
}

void NetManager::startSTA() {
    _mode = MODE_STA;
    WiFi.begin(_cfg->wifi_ssid, _cfg->wifi_pass);
    _staStarted = true;
    _staStartedAt = millis();
    _lastTry = millis();
    Serial.printf("[NET] Connecting to %s ...\n", _cfg->wifi_ssid);
}

bool NetManager::wifiConnected() const {
    return WiFi.status() == WL_CONNECTED;
}

void NetManager::tryReconnect() {
    uint32_t now = millis();
    if (now - _lastTry < _retryDelay) return;
    _lastTry = now;
    _retryDelay = min((uint32_t)30000, _retryDelay * 2);   // 指数退避，上限 30s
    Serial.println(F("[NET] WiFi lost, reconnecting..."));
    WiFi.disconnect();
    WiFi.begin(_cfg->wifi_ssid, _cfg->wifi_pass);
}

void NetManager::loop() {
    if (_mode == MODE_STA) {
        if (_staStarted && !wifiConnected()) {
            tryReconnect();
            // 连接超时（>60s）回落到 AP 配网
            if (!wifiConnected() && (millis() - _staStartedAt) > 60000) {
                Serial.println("[NET] STA 连接超时，回落到 AP 配网模式");
                WiFi.disconnect();
                startAP();
            }
        } else if (wifiConnected()) {
            _retryDelay = 1000;
        }
    } else {
        dns.processNextRequest();
        web.handleClient();
    }
}

// ---------------- AP 配网 ----------------
void NetManager::startAP() {
    _mode = MODE_AP;
    // 使用已保存的热点名，未设置时用默认值
    if (_cfg->ap_ssid[0] != '\0') {
        _ap_ssid = String(_cfg->ap_ssid);
    } else {
        uint8_t mac[6];
        esp_read_mac(mac, ESP_MAC_WIFI_STA);
        _ap_ssid = String("EnvMon-") + String(mac[4], HEX) + String(mac[5], HEX);
        _ap_ssid.toUpperCase();
    }

    WiFi.mode(WIFI_AP);
    WiFi.softAP(_ap_ssid.c_str(), nullptr);   // 开放热点
    delay(300);
    dns.start(53, "*", WiFi.softAPIP());      // DNS 劫持 -> captive portal
    startPortalServer();
    Serial.printf("[NET] AP started: %s (http://192.168.4.1)\n", _ap_ssid.c_str());
}

void NetManager::startPortalServer() {
    if (_portalRunning) return;
    _portalRunning = true;

    web.on("/", HTTP_GET, [this]() { handleRoot(); });
    web.on("/scan", HTTP_GET, [this]() { handleScan(); });
    web.on("/save", HTTP_POST, [this]() { handleSave(); });
    web.on("/generate_204", HTTP_GET, [this]() { handleRoot(); });
    web.on("/hotspot-detect.html", HTTP_GET, [this]() { handleRoot(); });
    web.onNotFound([this]() {
        web.sendHeader("Location", "http://192.168.4.1/", true);
        web.send(302, "text/plain", "");
    });
    web.begin();
}

void NetManager::handleRoot() {
    web.send_P(200, "text/html", PORTAL_HTML);
}

void NetManager::handleScan() {
    int n = WiFi.scanNetworks(false, false, false, 1500);  // 异步禁用，仅 2.4G，1.5s 超时
    String json = "[";
    for (int i = 0; i < n; ++i) {
        if (i) json += ",";
        json += "{\"ssid\":\"" + WiFi.SSID(i) + "\",\"rssi\":" + WiFi.RSSI(i) +
                ",\"open\":" + (WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "true" : "false") + "}";
    }
    WiFi.scanDelete();
    json += "]";
    web.send(200, "application/json", json);
}

void NetManager::handleSave() {
    String ssid = web.arg("ssid2");
    if (ssid.length() == 0) ssid = web.arg("ssid");
    ssid.trim();

    DeviceConfig c = *_cfg;
    memset(c.wifi_ssid, 0, sizeof(c.wifi_ssid));
    memset(c.wifi_pass, 0, sizeof(c.wifi_pass));
    ssid.toCharArray(c.wifi_ssid, sizeof(c.wifi_ssid));
    web.arg("pass").toCharArray(c.wifi_pass, sizeof(c.wifi_pass));
    // 热点名称（留空保留现有）
    String apssid = web.arg("apssid");
    apssid.trim();
    if (apssid.length() > 0) {
        memset(c.ap_ssid, 0, sizeof(c.ap_ssid));
        apssid.toCharArray(c.ap_ssid, sizeof(c.ap_ssid));
    }
    web.arg("host").toCharArray(c.mqtt_host, sizeof(c.mqtt_host));
    c.mqtt_port = (uint16_t)web.arg("port").toInt();
    if (c.mqtt_port == 0) c.mqtt_port = 18830;
    web.arg("user").toCharArray(c.mqtt_user, sizeof(c.mqtt_user));
    web.arg("mpass").toCharArray(c.mqtt_pass, sizeof(c.mqtt_pass));
    String devid = web.arg("devid");
    devid.trim();
    if (devid.length() > 0) devid.toCharArray(c.device_id, sizeof(c.device_id));
    int iv = web.arg("interval").toInt();
    if (iv >= 3) c.report_interval = (uint16_t)iv;

    g_cfgStore.save(c);

    web.send(200, "text/html",
        "<meta charset='utf-8'><body style='font-family:sans-serif'>"
        "<h2>已保存！设备正在重启并连接...</h2>"
        "<p>请重新连回家庭 WiFi，稍后在服务器上查看数据。</p></body>");
    Serial.println(F("[NET] Config saved, rebooting in 1.5s"));
    delay(1500);
    ESP.restart();
}
