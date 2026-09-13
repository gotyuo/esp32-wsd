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
.overlay{position:fixed;inset:0;background:rgba(7,11,23,.82);z-index:99;display:flex;align-items:center;justify-content:center}
.panel{background:#1e293b;border:1px solid #334155;border-radius:14px;padding:18px;width:92%;max-height:72%;display:flex;flex-direction:column;box-shadow:0 10px 30px rgba(0,0,0,.5)}
.panel h3{color:#38bdf8;margin:0 0 8px;font-size:17px}
.panel #scanStatus{color:#94a3b8;font-size:13px;min-height:18px}
.netlist{margin-top:10px;overflow:auto;flex:1;min-height:0}
.netrow{display:flex;justify-content:space-between;align-items:center;padding:11px 12px;border-bottom:1px solid #0f172a;font-size:14px;cursor:pointer;border-radius:6px}
.netrow:hover{background:#0f172a}
.netrow .ss{font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.netrow .meta{color:#64748b;font-size:12px;flex-shrink:0;margin-left:10px}
.btn-close{margin-top:14px;background:#334155}
.mopt{display:inline-block;padding:7px 12px;border-radius:20px;border:1px solid #334155;background:#0f172a;color:#94a3b8;font-size:14px;cursor:pointer;user-select:none}
.mopt.on{background:#0ea5e9;color:#0f172a;border-color:#0ea5e9;font-weight:600}
.mopt .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#94a3b8;margin-right:6px;vertical-align:middle}
.mopt.on .dot{background:#0f172a}
</style></head><body>
<h2>环境监测站 配网</h2>
<form method="POST" action="/save">
<div class="card"><b>热点设置</b>
<label>热点名称（配网时用手机连接的 WiFi，留空用默认）</label>
<input name="apssid" id="apssid" placeholder="如 EnvMon-Pro">
</div>
<div class="card"><b>无线网络</b>
<label>WiFi 名称（可点下方按钮扫描选择，或手动输入）</label>
<input name="ssid2" id="ssid2" placeholder="手动输入 WiFi 名称">
<label>WiFi 密码</label>
<input name="pass" type="password">
<button type="button" id="refBtn" style="margin:8px 0;font-size:14px;padding:10px" onclick="_doScan()">刷新 WiFi 列表</button>
<div class="hint">点按钮弹出扫描结果，点击一个网络自动填入上方名称。</div>
</div>
<div class="card"><b>服务器（MQTT）</b>
<div class="card-mode" style="display:flex;gap:8px;margin-bottom:10px"><label class="mopt on" id="m_auto" onclick="switchMode('auto')"><span class="dot"></span>局域网自动发现</label><label class="mopt" id="m_manual" onclick="switchMode('manual')"><span class="dot"></span>手动指定</label></div>
<div class="hint" id="m_hint">局域网自动发现：设备上电后在同一无线网下自动找到服务器并上报，无需手动填地址。</div>
<div id="m_manual_box">
<label>服务器地址（IP 或域名）</label>
<input name="host" id="m_host" placeholder="例如 192.168.1.100"></div>
<label>MQTT 端口</label>
<input name="port" type="number" value="18830">
<label>MQTT 用户名</label>
<input name="user" value="envmon">
<label>MQTT 密码</label>
<input name="mpass" type="password">
<label>设备编号</label>
<input name="devid" placeholder="留空自动生成"><input type="hidden" name="smode" id="m_smode" value="0">
<label>上报间隔（秒）</label>
<input name="interval" type="number" value="10" min="3">
</div>
<button type="submit">保存并连接</button>
<div class="hint">保存后设备将自动重启并连接网络。</div>
</form>
<script>function switchMode(m){
  var auto=document.getElementById("m_auto");
  var man=document.getElementById("m_manual");
  var box=document.getElementById("m_manual_box");
  var host=document.getElementById("m_host");
  var hint=document.getElementById("m_hint");
  if(m==="auto"){auto.className="mopt on";man.className="mopt";
    box.style.display="none";host.removeAttribute("required");
    hint.textContent="局域网自动发现：设备上电后在同一无线网下自动找到服务器并上报，无需手动填地址。";
    host.value="";}
  else{man.className="mopt on";auto.className="mopt";
    box.style.display="block";host.setAttribute("required","required");
    hint.textContent="手动指定：填写服务器公网 IP/域名，外网可直达。";}
  document.getElementById("m_smode").value = m==="manual" ? "1" : "0";
}
document.getElementById("m_smode").value = "0";
var _ssid = document.getElementById('ssid2');
var _refBtn = document.getElementById('refBtn');
function _lock(){ return ' 🔒'; }
function _popup(){
  if (document.getElementById('scanPopup')) return;
  var p = document.createElement('div'); p.id = 'scanPopup'; p.className = 'overlay';
  p.innerHTML = '<div class="panel"><h3>扫描无线网络</h3>'
    + "<p id=\"scanStatus\">正在扫描…</p>"
    + "<div id=\"scanList\" class=\"netlist\"></div>"
    + "<div class=\"hint\" style=\"margin:10px 0\">选一个网络后自动填入「WiFi 名称」。</div>"
    + "<button type=\"button\" class=\"btn-close\" id=\"scanClose\">关闭</button></div>";
  document.body.appendChild(p);
  document.getElementById('scanClose').onclick = function(){ p.remove(); };
  p.onclick = function(e){ if(e.target===p) p.remove(); };
}
function _renderList(arr){
  var box = document.getElementById('scanList');
  var st = document.getElementById('scanStatus');
  if(!box) return;
  if(!arr || !arr.length){ st.textContent='未扫描到网络，请重试或手动输入'; box.innerHTML=''; return; }
  st.textContent = '共 ' + arr.length + ' 个网络，点击选择：';
  var h = '';
  for(var i=0;i<arr.length;i++){
    var it = arr[i];
    var bar = (it.rssi>=-70?4:it.rssi>=-80?3:it.rssi>=-90?2:1);
    var bars=''; for(var b=0;b<4;b++) bars += (b<bar?'█':'░');
    h += "<div class=\"netrow\" data-ssid=\""+it.ssid.replace(/"/g,'&quot;')+"\">"
       + "<span class=\"ss\">"+it.ssid+"</span>"
       + "<span class=\"meta\">"+bars+" "+it.rssi+"dBm"+(it.open?"":"  "+_lock())+"</span></div>";
  }
  box.innerHTML = h;
  var rows = box.querySelectorAll('.netrow');
  for(var i=0;i<rows.length;i++){
    rows[i].onclick = (function(el){return function(){
      var v = el.getAttribute('data-ssid');
      _ssid.value = v;
      document.getElementById('scanPopup').remove();
    };})(rows[i]);
  }
}
function _doScan(){
  _popup();
  fetch('/scan?refresh=1',{signal:AbortSignal.timeout(6000)}).catch(function(){});
  _refBtn.disabled = true; _refBtn.textContent = '扫描中…';
  setTimeout(_poll, 1200);
}
function _poll(){
  fetch('/scan',{signal:AbortSignal.timeout(6000)})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.scanning === true){ document.getElementById('scanStatus').textContent='扫描中，稍等…'; setTimeout(_poll,1000); return; }
      _renderList(d.networks);
      _refBtn.disabled = false; _refBtn.textContent = '刷新 WiFi 列表';
    })
    .catch(function(){ document.getElementById('scanStatus').textContent='扫描失败，请手动输入'; _refBtn.disabled=false; _refBtn.textContent='刷新 WiFi 列表'; });
}
_refBtn.onclick = _doScan;
setTimeout(_doScan, 800);
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
            // 不再回落到 AP：已有 WiFi 配置则无限重连 STA，避免断一次就永久离线。
            // AP 仅在首次启动无 WiFi 配置时出现(begin() 的 has_wifi 判断)。
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

    // 用 AP_STA: STA 射频常开, 扫描无需切模式, 手机不掉线。
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(_ap_ssid.c_str(), nullptr, 6, 0);  // 固定信道 6
    delay(300);
    dns.start(53, "*", WiFi.softAPIP());
    startPortalServer();
    Serial.printf("[NET] AP started: %s (http://192.168.4.1)\n", _ap_ssid.c_str());

    // AP 刚起、尚无客户端时扫一次填充缓存 (射频稳定后重试)
    _scanCache = "[]";
    for (int tt = 0; tt < 3 && (_scanCache == "[]"); tt++) {
        delay(800);
        WiFi.scanNetworks(true, false, false, 200);
        uint32_t t0 = millis();
        while (millis() - t0 < 3500) {
            int n = WiFi.scanComplete();
            if (n >= 0) { buildScanCache(n); WiFi.scanDelete(); break; }
            dns.processNextRequest(); web.handleClient();
        }
    }
    Serial.printf("[NET] initial scan cache: %s\n", (_scanCache == "[]" ? "empty" : _scanCache.substring(0, 40).c_str()));
}

void NetManager::startPortalServer() {
    if (_portalRunning) return;
    _portalRunning = true;

    web.on("/", HTTP_GET, [this]() { handleRoot(); });
    web.on("/scan", HTTP_GET, [this]() {
        if (web.arg("refresh") == "1") requestScan();
        handleScan();
    });
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
    // 返回 {scanning:bool, networks:[...]}, HTTP 瞬间响应, 不阻塞 AP。
    String json;
    json = String("{\"scanning\":") + (_scanBusy ? "true" : "false")
           + ",\"networks\":" + _scanCache + "}";
    web.send(200, "application/json", json);
}

void NetManager::buildScanCache(int n) {
    _scanCache = "[";
    for (int i = 0; i < n && i < 30; i++) {
        if (i) _scanCache += ",";
        _scanCache += "{\"ssid\":\"" + WiFi.SSID(i) + "\",\"rssi\":" + WiFi.RSSI(i) + ",\"open\":" + (WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "true" : "false") + "}";
    }
    _scanCache += "]";
}

void NetManager::requestScan() {
    if (_scanBusy || WiFi.getMode() == WIFI_OFF) return;
    _scanBusy = true;
    WiFi.scanNetworks(true, false, false, 250);
    Serial.println("[NET] on-demand scan started");
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
    c.server_mode = (uint8_t)web.arg("smode").toInt();
    String host = web.arg("host");
    if (host.length() > 0) host.toCharArray(c.mqtt_host, sizeof(c.mqtt_host));
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
// =================== 局域网自动发现（UDP 多播 beacon） ===================
// 服务器端 UDP 12091 监听；ESP 在 LAN 发现模式下周期广播 "ENVMON?"，
// 收到服务端 JSON 应答后立即保存配置并重启，进入正常 MQTT 上报。
// 应答 JSON: {"ip":"192.168.1.100","port":18830,"user":"envmon","pass":"envmon"}
// 无第三方依赖，极简 JSON 解析
static const int DISC_PORT = 12091;
static const char DISC_MCAST_IP[] = "239.255.1.1";
static const char DISC_REQ[] = "ENVMON?";
static const uint32_t DISC_SEND_INTERVAL = 4000;   // 每 4s 发一次
static const uint32_t DISC_TIMEOUT = 45000;       // 45s 超时回 AP

void NetManager::startDiscover() {
    if (_udpBound) _udp.stop();
    if (_udp.beginMulticast(IPAddress(239, 255, 1, 1), DISC_PORT) == 0) {
        Serial.println(F("[DISC] UDP multicast begin failed"));
        _udpBound = false;
        return;
    }
    _udpBound = true;
    _discLastSent = 0;
    _discStartAt  = millis();
    _discActive   = true;
    Serial.printf("[DISC] mode=LAN discover, send every %us, timeout %us\n",
                  DISC_SEND_INTERVAL / 1000, DISC_TIMEOUT / 1000);
}

void NetManager::stopDiscover() {
    if (_udpBound) { _udp.stop(); _udpBound = false; }
    _discActive = false;
}

// 抠出 "key":"value" 或 "key":number；只匹配 JSON 键（前缀 { 或 ,）
static bool jsonPop(const String &j, const char *key, String &val) {
    String tgt = "\"" + String(key) + "\"";
    int p = 0;
    while (true) {
        int i = j.indexOf(tgt, p);
        if (i < 0) return false;
        if (i > 0) {
            char b = j.charAt(i - 1);
            if (b != '{' && b != ',') { p = i + 1; continue; }
        }
        int c = j.indexOf(':', i + 1);
        if (c < 0) return false;
        int start = j.indexOf('"', c);
        if (start >= 0 && start < (int)j.length()) {
            int end = j.indexOf('"', start + 1);
            if (end < 0) return false;
            val = j.substring(start + 1, end);
            return true;
        }
        int s2 = c + 1;
        while (s2 < (int)j.length() && (j[s2] == ' ' || j[s2] == '\t')) s2++;
        int e2 = s2;
        while (e2 < (int)j.length() && j[e2] != ',' && j[e2] != '}') e2++;
        val = j.substring(s2, e2);
        return true;
    }
}

int NetManager::discoverLoop(uint32_t now) {
    if (!_discActive) return 0;
    // 超时 -> 回到 AP 配网
    if (now - _discStartAt > DISC_TIMEOUT) {
        Serial.println(F("[DISC] timeout -> back to AP portal"));
        stopDiscover();
        return -1;
    }
    // 周期 beacon
    if ((now - _discLastSent) > DISC_SEND_INTERVAL) {
        _discLastSent = now;
        _udp.beginPacket(IPAddress(239, 255, 1, 1), DISC_PORT);
        _udp.print(DISC_REQ);
        _udp.endPacket();
    }
    int n = _udp.parsePacket();
    if (n <= 0) return 0;
    String buf; buf.reserve(n + 1);
    while (_udp.available()) buf += (char)_udp.read();
    Serial.printf("[DISC] reply len=%d: %s\n", buf.length(), buf.c_str());

    String ip, port, user, psw;
    if (!jsonPop(buf, "ip", ip) || ip.length() == 0) return 0;
    jsonPop(buf, "port", port);
    if (port.isEmpty()) port = "18830";
    jsonPop(buf, "user", user);
    jsonPop(buf, "pass", psw);

    strcpy(_cfg->mqtt_host, ip.c_str());
    _cfg->mqtt_port = (uint16_t)port.toInt();
    if (_cfg->mqtt_port == 0) _cfg->mqtt_port = 18830;
    if (user.length() > 0) strcpy(_cfg->mqtt_user, user.c_str());
    if (psw.length() > 0)  strcpy(_cfg->mqtt_pass, psw.c_str());
    _cfg->server_mode = 1;   // 发现成功后标记为手动，避免重启再扫
    stopDiscover();
    Serial.printf("[DISC] got server %s:%d, saving & rebooting\n",
                  _cfg->mqtt_host, _cfg->mqtt_port);
    g_cfgStore.save(*_cfg);
    return 1;
}
