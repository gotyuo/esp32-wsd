// ============================================================
// 网络管理 ESP8266 — STA + AP 配网
// ============================================================
#include "net_mgr.h"
#include "pins.h"

NetManager g_net;

void NetManager::begin() {
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.setSleepMode(WIFI_NONE_SLEEP);
    if (_cfg->has_wifi()) {
        startSTA();
    } else {
        Serial.println(F("[NET] No WiFi config, entering AP"));
        startAP();
    }
}

void NetManager::startSTA() {
    _mode = MODE_STA;
    WiFi.begin(_cfg->wifi_ssid, _cfg->wifi_pass);
    _staStarted = true;
    _lastTry = millis();
    Serial.printf("[NET] Connecting to %s ...\n", _cfg->wifi_ssid);
}

void NetManager::tryReconnect() {
    uint32_t now = millis();
    if (now - _lastTry < _retryDelay) return;
    _lastTry = now;
    _retryDelay = min((uint32_t)30000, _retryDelay * 2);
    Serial.println(F("[NET] WiFi lost, reconnecting..."));
    WiFi.disconnect(true, false);
    delay(300);
    WiFi.begin(_cfg->wifi_ssid, _cfg->wifi_pass);
}

void NetManager::loop() {
    if (_mode == MODE_STA) {
        if (_staStarted && WiFi.status() != WL_CONNECTED) {
            tryReconnect();
        } else if (WiFi.status() == WL_CONNECTED) {
            _retryDelay = 1000;
        }
    } else {
        dns.processNextRequest();
        web.handleClient();
        if (_scanBusy) {
            int n = WiFi.scanComplete();
            if (n >= 0) {
                buildScanCache(n);
                WiFi.scanDelete();
                _scanBusy = false;
                Serial.printf("[NET] scan: %d\n", n);
            }
        }
    }
}

void NetManager::startAP() {
    _mode = MODE_AP;
    if (_cfg->ap_ssid[0] != '\0') {
        _ap_ssid = String(_cfg->ap_ssid);
    } else {
        uint8_t mac[6];
        WiFi.macAddress(mac);
        _ap_ssid = "ENVMON-" + String(mac[4], HEX) + String(mac[5], HEX);
        _ap_ssid.toUpperCase();
    }
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(_ap_ssid, "", 6, 0);
    delay(300);
    dns.start(53, "*", WiFi.softAPIP());
    startPortal();
    Serial.printf("[NET] AP: %s (http://192.168.4.1)\n", _ap_ssid.c_str());
    _scanCache = "[]";
    delay(800);
    WiFi.scanNetworks(true, false, 0, NULL);
}

// 配网页 HTML
static const char PORTAL_HTML[] PROGMEM = R"rawliteral(<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EnvMon 配网</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}
h2{color:#38bdf8;margin:8px 0 16px}
.card{background:#1e293b;border-radius:12px;padding:16px;margin-bottom:14px}
label{display:block;font-size:13px;color:#94a3b8;margin:10px 0 4px}
input{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:15px}
button{width:100%;padding:13px;border:0;border-radius:10px;background:#0ea5e9;color:#fff;font-size:16px;font-weight:600;margin-top:16px}
.hint{font-size:12px;color:#64748b;margin-top:6px}
.overlay{position:fixed;inset:0;background:rgba(7,11,23,.82);z-index:99;display:flex;align-items:center;justify-content:center}
.panel{background:#1e293b;border:1px solid #334155;border-radius:14px;padding:18px;width:92%;max-height:72%;display:flex;flex-direction:column}
.panel h3{color:#38bdf8;margin:0 0 8px}
.panel #scanStatus{color:#94a3b8;font-size:13px;min-height:18px}
.netlist{margin-top:10px;overflow:auto;flex:1;min-height:0}
.netrow{display:flex;justify-content:space-between;align-items:center;padding:11px 12px;border-bottom:1px solid #0f172a;font-size:14px;cursor:pointer;border-radius:6px}
.netrow:hover{background:#0f172a}
.netrow .ss{font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.netrow .meta{color:#64748b;font-size:12px;flex-shrink:0;margin-left:10px}
.btn-close{margin-top:14px;background:#334155}
</style></head><body>
<h2>EnvMon 配网</h2>
<form method="POST" action="/save">
<div class="card"><b>WiFi</b>
<link id="link"><i></i></link>
<label>名称</label>
<input name="ssid" id="ssid" placeholder="输入或扫描">
<label>密码</label>
<input name="pass" type="password">
<button type="button" id="refBtn" style="margin:8px 0;font-size:14px;padding:10px" onclick="_doScan()">扫描</button>
<div class="hint">点按钮扫描WiFi，点击结果自动填入</div>
</div>
<div class="card"><b>服务器</b>
<label>地址</label>
<input name="host" placeholder="如 192.168.1.100">
<label>MQTT 端口</label>
<input name="port" type="number" value="18830">
<label>MQTT 用户名</label>
<input name="user" value="envmon">
<label>MQTT 密码</label>
<input name="mpass" type="password" value="envmon">
<label>设备编号</label>
<input name="devid" placeholder="留空自动生成">
<label>上报间隔(秒)</label>
<input name="interval" type="number" value="10" min="3">
</div>
<button type="submit">保存并连接</button>
<div class="hint">保存后设备重启</div>
</form>
<script>
var _ssid=document.getElementById('ssid');
var _refBtn=document.getElementById('refBtn');
function _popup(){
 if(document.getElementById('scanPopup'))return;
 var p=document.createElement('div');p.id='scanPopup';p.className='overlay';
 p.innerHTML='<div class="panel"><h3>扫描WiFi</h3><p id="scanStatus">扫描中...</p><div id="scanList" class="netlist"></div><button type="button" class="btn-close" id="scanClose">关闭</button></div>';
 document.body.appendChild(p);
 document.getElementById('scanClose').onclick=function(){p.remove()};
}
function _render(arr){
 var box=document.getElementById('scanList');
 var st=document.getElementById('scanStatus');
 if(!box)return;
 if(!arr||!arr.length){st.textContent='未找到,手动输入';box.innerHTML='';return}
 st.textContent='共'+arr.length+'个,点击选择:';
 var h='';
 for(var i=0;i<arr.length;i++){
  var it=arr[i];
  var bar=it.rssi>=-70?4:it.rssi>=-80?3:it.rssi>=-90?2:1;
  var bars='';for(var b=0;b<4;b++)bars+=(b<bar?'*':'_');
  h+='<div class="netrow" data-s="'+it.ssid.replace(/"/g,'&quot;')+'"><span class="ss">'+it.ssid+'</span><span class="meta">'+bars+' '+it.rssi+'</span></div>'
 }
 box.innerHTML=h;
 var rows=box.querySelectorAll('.netrow');
 for(var i=0;i<rows.length;i++){rows[i].onclick=(function(e){return function(){_ssid.value=e.getAttribute('data-s');document.getElementById('scanPopup').remove()}})(rows[i])}
}
function _poll(){
 fetch('/scan',{signal:AbortSignal.timeout(6000)}).then(function(r){return r.json()}).then(function(d){
  if(d.scanning===true){document.getElementById('scanStatus').textContent='扫描中...';setTimeout(_poll,1000);return}
  _render(d.networks);_refBtn.disabled=false;_refBtn.textContent='扫描'
 }).catch(function(){document.getElementById('scanStatus').textContent='失败';_refBtn.disabled=false;_refBtn.textContent='扫描'})
}
function _doScan(){
 _popup();
 fetch('/scan?refresh=1',{signal:AbortSignal.timeout(6000)}).catch(function(){});
 _refBtn.disabled=true;_refBtn.textContent='扫描中...';
 setTimeout(_poll,1200);
}
_refBtn.onclick=_doScan;
setTimeout(_doScan,800);
</script></body></html>)rawliteral";

void NetManager::startPortal() {
    if (_portalOn) return;
    web.on("/", HTTP_GET, [this]() { handleRoot(); });
    web.on("/scan", HTTP_GET, [this]() {
        if (web.arg("refresh") == "1") { _scanBusy = true; WiFi.scanNetworks(true, false, 0, NULL); }
        handleScan();
    });
    web.on("/save", HTTP_POST, [this]() { handleSave(); });
    web.onNotFound([this]() { web.sendHeader("Location", "http://192.168.4.1/"); web.send(302, "text/plain", ""); });
    web.begin();
    _portalOn = true;
}

void NetManager::handleRoot() {
    web.send(200, "text/html", FPSTR(PORTAL_HTML));
}

void NetManager::handleScan() {
    String json;
    json = String("{\"scanning\":") + (_scanBusy ? "true" : "false")
           + ",\"networks\":" + _scanCache + "}";
    web.send(200, "application/json", json);
}

void NetManager::buildScanCache(int n) {
    _scanCache = "[";
    for (int i = 0; i < n && i < 30; i++) {
        if (i) _scanCache += ",";
        String ssid = WiFi.SSID(i);
        ssid.replace("\\", "\\\\");
        ssid.replace("\"", "\\\"");
        _scanCache += "{\"ssid\":\"" + ssid + "\",\"rssi\":" + WiFi.RSSI(i)
                    + ",\"open\":" + (WiFi.encryptionType(i) == ENC_TYPE_NONE ? "true" : "false") + "}";
    }
    _scanCache += "]";
}

void NetManager::handleSave() {
    String ssid = web.arg("ssid");
    ssid.trim();
    DeviceConfig c = *_cfg;
    memset(c.wifi_ssid, 0, sizeof(c.wifi_ssid));
    memset(c.wifi_pass, 0, sizeof(c.wifi_pass));
    ssid.toCharArray(c.wifi_ssid, sizeof(c.wifi_ssid));
    web.arg("pass").toCharArray(c.wifi_pass, sizeof(c.wifi_pass));
    String h = web.arg("host");
    if (h.length() > 0) h.toCharArray(c.mqtt_host, sizeof(c.mqtt_host));
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
        "<h2>已保存!设备重启中...</h2></body>");
    Serial.println(F("[NET] Saved, reboot in 1.5s"));
    delay(1500);
    ESP.restart();
}