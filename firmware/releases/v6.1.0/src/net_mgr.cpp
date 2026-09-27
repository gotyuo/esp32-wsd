// ============================================================
// 网络管理 ESP8266 v4.0 - STA + AP 配网 + Web 数据页
//
// 状态机:
//   无 WiFi 配置  -> AP_CONFIG (配网, 用户保存 -> 重启)
//   有 WiFi 配置  -> STA_TRYING (开 AP+STA, 等 15s)
//     STA 连接成功 -> STA_CONNECTED (关 AP, 跑数据页)
//     STA 超时     -> AP_CONFIG (重新配网)
//   STA_CONNECTED 掉线 60s -> AP_CONFIG
// ============================================================
#include "net_mgr.h"
#include "pins.h"
#include <cstring>

NetManager g_net;

// ---------- 配网 HTML ----------
static const char PORTAL_HTML[] PROGMEM = R"rawliteral(<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EnvMon8266 v4.0 配网</title>
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
<h2>EnvMon ESP8266 v4.0 配网</h2>
<form method="POST" action="/save">
<div class="card"><b>无线网络</b>
<label>WiFi 名称</label>
<input name="ssid2" id="ssid2" placeholder="手动输入或点扫描">
<label>WiFi 密码</label>
<input name="pass" type="password">
<button type="button" id="refBtn" style="margin:8px 0;font-size:14px;padding:10px" onclick="_doScan()">刷新 WiFi 列表</button>
<div class="hint">点击按钮弹出扫描结果，点击一个网络自动填入。</div>
</div>
<div class="card"><b>服务器（MQTT）</b>
<div style="display:flex;gap:8px;margin-bottom:10px">
<label class="mopt on" id="m_auto" onclick="switchMode('auto')"><span class="dot"></span>局域网自动发现</label>
<label class="mopt" id="m_manual" onclick="switchMode('manual')"><span class="dot"></span>手动指定 IP</label>
</div>
<div class="hint" id="m_hint">局域网自动发现：同一无线网下自动找到服务器并上报。</div>
<div id="m_manual_box"><label>服务器 IP / 域名</label>
<input name="host" id="m_host" placeholder="例如 192.168.1.100"></div>
<label>MQTT 端口</label>
<input name="port" type="number" value="18830">
<label>MQTT 用户名</label>
<input name="user" value="envmon">
<label>MQTT 密码</label>
<input name="mpass" type="password" value="envmon">
<label>设备编号</label>
<input name="devid" placeholder="留空自动生成">
<input type="hidden" name="smode" id="m_smode" value="0">
<label>上报间隔（秒）</label>
<input name="interval" type="number" value="10" min="3">
</div>
<button type="submit">保存并连接</button>
<div class="hint">保存后设备将自动重启并连接网络。</div>
</form>
<script>
function switchMode(m){
var auto=document.getElementById("m_auto");
var man=document.getElementById("m_manual");
var box=document.getElementById("m_manual_box");
var host=document.getElementById("m_host");
var hint=document.getElementById("m_hint");
if(m==="auto"){auto.className="mopt on";man.className="mopt";
box.style.display="none";host.removeAttribute("required");
hint.textContent="局域网自动发现：同一无线网下自动找到服务器并上报。";host.value="";}
else{man.className="mopt on";auto.className="mopt";
box.style.display="block";host.setAttribute("required","required");
hint.textContent="手动指定：填写服务器公网 IP/域名，外网可直达。";}
document.getElementById("m_smode").value=m==="manual"?"1":"0";
}
document.getElementById("m_smode").value="0";
var _ssid=document.getElementById('ssid2');
var _refBtn=document.getElementById('refBtn');
function _popup(){
 if(document.getElementById('scanPopup'))return;
 var p=document.createElement('div');p.id='scanPopup';p.className='overlay';
 p.innerHTML='<div class="panel"><h3>扫描无线网络</h3><p id="scanStatus">正在扫描…</p><div id="scanList" class="netlist"></div><button type="button" class="btn-close" id="scanClose">关闭</button></div>';
 document.body.appendChild(p);
 document.getElementById('scanClose').onclick=function(){p.remove();};
 p.onclick=function(e){if(e.target===p)p.remove();};
}
function _renderList(arr){
 var box=document.getElementById('scanList');
 var st=document.getElementById('scanStatus');
 if(!box)return;
 if(!arr||!arr.length){st.textContent='未扫描到网络，请重试或手动输入';box.innerHTML='';return;}
 st.textContent='共 '+arr.length+' 个网络，点击选择：';
 var h='';
 for(var i=0;i<arr.length;i++){
  var it=arr[i];
  var bar=(it.rssi>=-70?4:it.rssi>=-80?3:it.rssi>=-90?2:1);
  var bars='';for(var b=0;b<4;b++)bars+=(b<bar?'█':'░');
  h+='<div class="netrow" data-ssid="'+it.ssid.replace(/"/g,'&quot;')+'"><span class="ss">'+it.ssid+'</span><span class="meta">'+bars+' '+it.rssi+'dBm</span></div>';
 }
 box.innerHTML=h;
 var rows=box.querySelectorAll('.netrow');
 for(var i=0;i<rows.length;i++){rows[i].onclick=(function(el){return function(){_ssid.value=el.getAttribute('data-ssid');document.getElementById('scanPopup').remove();};})(rows[i]);}
}
function _doScan(){
 _popup();
 fetch('/scan?refresh=1',{signal:AbortSignal.timeout(6000)}).catch(function(){});
 _refBtn.disabled=true;_refBtn.textContent='扫描中…';
 setTimeout(_poll,1200);
}
function _poll(){
 fetch('/scan',{signal:AbortSignal.timeout(6000)}).then(function(r){return r.json();}).then(function(d){
  if(d.scanning===true){document.getElementById('scanStatus').textContent='扫描中，稍等…';setTimeout(_poll,1000);return;}
  _renderList(d.networks);_refBtn.disabled=false;_refBtn.textContent='刷新 WiFi 列表';
 }).catch(function(){document.getElementById('scanStatus').textContent='扫描失败，请手动输入';_refBtn.disabled=false;_refBtn.textContent='刷新 WiFi 列表';});
}
_refBtn.onclick=_doScan;
setTimeout(_doScan,800);
</script></body></html>)rawliteral";

// ---------- 实时数据 HTML ----------
static const char DATA_HTML[] PROGMEM = R"rawliteral(<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>EnvMon8266 v4.0 实时数据</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}
h2{color:#38bdf8;margin:8px 0 16px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.card{background:#1e293b;border-radius:12px;padding:18px;border:1px solid #334155}
.lbl{font-size:12px;color:#94a3b8}
.val{font-size:34px;font-weight:700;color:#e2e8f0;margin-top:4px}
.unit{font-size:14px;color:#64748b;margin-left:6px}
.warn{border-color:#f59e0b}
.alarm{border-color:#ef4444;background:linear-gradient(135deg,#1e293b,#3b1414)}
.ok{border-color:#10b981}
.status{background:#020617;padding:10px 16px;border-radius:10px;font-size:13px;color:#64748b;margin-bottom:12px}
.status b{color:#38bdf8}
a.btn{display:inline-block;padding:8px 14px;background:#334155;color:#e2e8f0;text-decoration:none;border-radius:8px;font-size:13px;margin-right:8px}
</style></head><body>
<h2>EnvMon ESP8266 v4.0 — 实时数据</h2>
<div class="status" id="status">加载中…</div>
<div class="grid" id="grid"></div>
<div style="margin-top:16px">
<a class="btn" href="/history">历史曲线</a>
<a class="btn" href="/config">重新配网</a>
</div>
<script>
var ALARM_TXT=['正常','警告','报警','无数据','配置'];
function card(lbl,val,unit,level){
  var lv=level||'ok';
  return '<div class="card '+lv+'"><div class="lbl">'+lbl+'</div><div class="val">'+val+'<span class="unit">'+unit+'</span></div></div>';
}
fetch('/api/data').then(function(r){return r.json();}).then(function(d){
  var s=document.getElementById('status');
  s.innerHTML='SSID: <b>'+d.ssid+'</b> · IP: <b>'+d.ip+'</b> · RSSI: <b>'+d.rssi+'dBm</b> · 报警: <b>'+ALARM_TXT[d.alarm||0]+'</b> · 设备: <b>'+d.dev+'</b>';
  var g=document.getElementById('grid');
  g.innerHTML=card('温度',d.temp,d.temp?'°C':'',(d.temp!=null&&(d.temp<d.temp_min||d.temp>d.temp_max))?'warn':'ok')
    +card('湿度',d.hum,d.hum?'%':'',(d.hum!=null&&(d.hum<d.hum_min||d.hum>d.hum_max))?'warn':'ok')
    +card('气压',d.pres,d.pres?'hPa':'','ok')
    +card('血氧 SpO2',d.spo2,d.spo2?'%':'',(d.spo2!=null&&d.spo2<d.spo2_min)?'warn':'ok')
    +card('心率 HR',d.hr,d.hr?'bpm':'',(d.hr!=null&&(d.hr<d.hr_min||d.hr>d.hr_max))?'warn':'ok');
}).catch(function(){document.getElementById('status').textContent='数据获取失败，请刷新';});
</script></body></html>)rawliteral";

// ---------- 历史曲线 HTML ----------
static const char HIST_HTML[] PROGMEM = R"rawliteral(<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EnvMon8266 v4.0 历史曲线</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}
h2{color:#38bdf8;margin:8px 0 16px}
.chart{background:#020617;border:1px solid #334155;border-radius:12px;padding:16px;margin-bottom:16px}
.chart h3{margin:0 0 10px;font-size:14px;color:#94a3b8}
canvas{width:100%;height:160px;background:#0f172a;border-radius:8px}
.meta{background:#1e293b;border-radius:10px;padding:12px 16px;font-size:13px;color:#94a3b8;margin-bottom:12px}
a.btn{display:inline-block;padding:8px 14px;background:#334155;color:#e2e8f0;text-decoration:none;border-radius:8px;font-size:13px;margin-right:8px}
</style></head><body>
<h2>EnvMon ESP8266 v4.0 — 历史曲线</h2>
<div class="meta" id="meta">加载中…</div>
<div class="chart"><h3>温度 (°C)</h3><canvas id="c1"></canvas></div>
<div class="chart"><h3>湿度 (%)</h3><canvas id="c2"></canvas></div>
<div class="chart"><h3>气压 (hPa)</h3><canvas id="c3"></canvas></div>
<div class="chart"><h3>血氧 SpO2 (%)</h3><canvas id="c4"></canvas></div>
<div class="chart"><h3>心率 (bpm)</h3><canvas id="c5"></canvas></div>
<div style="margin-top:8px">
<a class="btn" href="/data">返回实时数据</a>
<a class="btn" href="/config">重新配网</a>
</div>
<script>
var COLORS={t:'#38bdf8',h:'#22d3ee',p:'#a78bfa',s:'#f472b6',r:'#34d399'};
var DATA=[];
function fmtTS(ms){
  if(!ms)return '?';
  var s=Math.floor(ms/1000);
  var m=Math.floor(s/60);
  var h=Math.floor(m/60);
  return (h>0?(h+'h '):'')+(m%60)+'m';
}
function drawChart(id,key,color,decimals){
  var cv=document.getElementById(id);
  var dpr=window.devicePixelRatio||1;
  cv.width=cv.clientWidth*dpr;cv.height=160*dpr;
  var ctx=cv.getContext('2d');
  ctx.scale(dpr,dpr);
  var W=cv.clientWidth,H=160;
  ctx.clearRect(0,0,W,H);
  var pad={l:36,r:10,t:8,b:22};
  var gw=W-pad.l-pad.r, gh=H-pad.t-pad.b;
  ctx.strokeStyle='#1e293b';ctx.lineWidth=1;
  for(var i=0;i<=4;i++){var y=pad.t+gh*i/4;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(W-pad.r,y);ctx.stroke();}
  var vals=[];
  for(var i=0;i<DATA.length;i++){var v=DATA[i][key];if(!isNaN(v))vals.push(v);}
  if(vals.length===0){ctx.fillStyle='#64748b';ctx.font='12px system-ui';ctx.fillText('无数据',W/2-20,H/2);return;}
  var mn=Math.min.apply(null,vals),mx=Math.max.apply(null,vals);
  if(mx-mn<0.1){mn-=0.5;mx+=0.5;}
  var pad2=(mx-mn)*0.1;mn-=pad2;mx+=pad2;
  ctx.fillStyle='#64748b';ctx.font='10px system-ui';
  for(var i=0;i<=4;i++){var v=mx-(mx-mn)*i/4;ctx.fillText(v.toFixed(decimals),2,pad.t+gh*i/4+3);}
  ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();
  var pts=[];
  for(var i=0;i<DATA.length;i++){
    var v=DATA[i][key];if(isNaN(v))continue;
    var x=pad.l+(DATA.length===1?gw/2:gw*i/(DATA.length-1));
    var y=pad.t+gh*(mx-v)/(mx-mn);
    pts.push([x,y]);
  }
  for(var i=0;i<pts.length;i++){
    if(i===0)ctx.moveTo(pts[i][0],pts[i][1]);else ctx.lineTo(pts[i][0],pts[i][1]);
  }
  ctx.stroke();
  ctx.fillStyle=color;
  for(var i=0;i<pts.length;i++){ctx.beginPath();ctx.arc(pts[i][0],pts[i][1],3,0,Math.PI*2);ctx.fill();}
  if(DATA.length>0){
    ctx.fillStyle='#64748b';
    ctx.fillText(fmtTS(DATA[0].ts),pad.l,H-6);
    if(DATA.length>1)ctx.fillText(fmtTS(DATA[DATA.length-1].ts),W-pad.r-30,H-6);
  }
}
fetch('/api/history').then(function(r){return r.json();}).then(function(d){
  DATA=d.points||[];
  var m=document.getElementById('meta');
  m.innerHTML='采样间隔: <b>5 分钟</b> · 当前点数: <b>'+DATA.length+'</b> · 时间窗口: <b>1 小时</b><br>设备: <b>'+d.dev+'</b> · 固件: <b>'+d.fw+'</b>';
  drawChart('c1','temp',COLORS.t,1);
  drawChart('c2','hum',COLORS.h,1);
  drawChart('c3','pres',COLORS.p,0);
  drawChart('c4','spo2',COLORS.s,0);
  drawChart('c5','hr',COLORS.r,0);
}).catch(function(){document.getElementById('meta').textContent='数据获取失败';});
</script></body></html>)rawliteral";

// ---------- 工具函数 ----------
static String getCurSsidForJson() {
    wl_status_t st = WiFi.status();
    if (st == WL_CONNECTED) {
        String s = WiFi.SSID();
        s.trim();
        if (!s.isEmpty()) return s;
    }
    return g_cfg.wifi_ssid;
}

static String floatOrNull(float v, int dec) {
    if (isnan(v)) return String("null");
    return String(v, dec);
}

String NetManager::apSsidOrDefault() const {
    if (_cfg->ap_ssid[0] != '\0') return String(_cfg->ap_ssid);
    uint8_t mac[6];
    WiFi.macAddress(mac);
    String s = "ENVMON8266-" + String(mac[4], HEX) + String(mac[5], HEX);
    s.toUpperCase();
    return s;
}

// ---------- /api/data JSON ----------
void NetManager::handleDataJson() {
    String j = "{";
    j += "\"dev\":\"" + String(g_cfg.device_id) + "\",";
    j += "\"fw\":\"" FW_VERSION "\",";
    j += "\"ssid\":\"" + String(getCurSsidForJson()) + "\",";
    j += "\"ip\":\"" + String(WiFi.localIP().toString()) + "\",";
    j += "\"rssi\":" + String(WiFi.RSSI()) + ",";
    j += "\"alarm\":" + String((int)g_alarm.level()) + ",";
    j += "\"temp_min\":" + String(g_cfg.temp_min, 1) + ",";
    j += "\"temp_max\":" + String(g_cfg.temp_max, 1) + ",";
    j += "\"hum_min\":" + String(g_cfg.hum_min, 1) + ",";
    j += "\"hum_max\":" + String(g_cfg.hum_max, 1) + ",";
    j += "\"spo2_min\":" + String(g_cfg.spo2_min, 1) + ",";
    j += "\"hr_min\":" + String(g_cfg.hr_min, 1) + ",";
    j += "\"hr_max\":" + String(g_cfg.hr_max, 1) + ",";
    j += "\"temp\":"   + floatOrNull(g_last.temp_c, 1) + ",";
    j += "\"hum\":"    + floatOrNull(g_last.hum_pct, 1) + ",";
    j += "\"pres\":"   + (isnan(g_last.pres_hpa) ? "null" : String((int)g_last.pres_hpa)) + ",";
    j += "\"spo2\":"   + floatOrNull(g_last.sp_o2, 1) + ",";
    j += "\"hr\":"     + floatOrNull(g_last.pr_hr, 1);
    j += "}";
    web.send(200, "application/json", j);
}

void NetManager::handleHistJson() {
    String j = "{";
    j += "\"dev\":\"" + String(g_cfg.device_id) + "\",";
    j += "\"fw\":\"" FW_VERSION "\",";
    j += "\"interval_ms\":300000,";
    j += "\"points\":" + g_hist.toJson() + "}";
    web.send(200, "application/json", j);
}

void NetManager::handleDataHtml() { web.send(200, "text/html", FPSTR(DATA_HTML)); }
void NetManager::handleHistHtml() { web.send(200, "text/html", FPSTR(HIST_HTML)); }

// ---------- 状态机入口 ----------
void NetManager::begin() {
    WiFi.setAutoReconnect(true);
    WiFi.setSleepMode(WIFI_MODEM_SLEEP);
    _ap_ssid = apSsidOrDefault();

    if (!_cfg->has_wifi()) {
        Serial.println(F("[NET] No WiFi config, entering AP config mode"));
        startAP();
    } else {
        Serial.printf("[NET] Has WiFi config (%s), trying STA first (AP fallback)\n",
                      _cfg->wifi_ssid);
        _mode = MODE_STA;
        _staStarted = true;
        _staAttemptStart = millis();
        _staAttemptTimeout = 15000;
        _retryDelay = 1000;
        WiFi.mode(WIFI_AP_STA);
        WiFi.softAP(_ap_ssid.c_str(), "", 6, 0);
        WiFi.begin(_cfg->wifi_ssid, _cfg->wifi_pass);
        startPortalServer();
        startDataService();
    }
}

void NetManager::tryReconnect() {
    uint32_t now = millis();
    if (now - _lastTry < _retryDelay) return;
    _lastTry = now;
    _retryDelay = min((uint32_t)10000, _retryDelay * 2);
    Serial.println(F("[NET] WiFi lost, reconnecting..."));
    WiFi.disconnect(true, false);
    delay(300);
    WiFi.begin(_cfg->wifi_ssid, _cfg->wifi_pass);
}

void NetManager::loop() {
    dns.processNextRequest();
    web.handleClient();

    if (_scanBusy) {
        int n = WiFi.scanComplete();
        if (n >= 0) {
            buildScanCache(n);
            WiFi.scanDelete();
            _scanBusy = false;
            Serial.printf("[NET] on-demand scan: %d\n", n);
        }
    }

    if (_mode == MODE_STA) {
        if (WiFi.status() == WL_CONNECTED) {
            if (!_staConnectedLocked) {
                WiFi.mode(WIFI_STA);
                _staConnectedLocked = true;
                _retryDelay = 1000;
                Serial.printf("[NET] STA connected: SSID=%s IP=%s (AP closed)\n",
                              WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());
                startDataService();
            }
        } else {
            if (_staConnectedLocked) {
                // 之前连过, 现在断了 -> 走重连, 60s 没恢复则切 AP
                if (millis() - _lastTry > 60000) {
                    Serial.println(F("[NET] STA down >60s, dropping to AP config"));
                    _staConnectedLocked = false;
                    startAP();
                } else {
                    tryReconnect();
                }
            } else if (millis() - _staAttemptStart >= _staAttemptTimeout) {
                // 首次连接超时, 切 AP
                Serial.printf("[NET] STA connect timeout (%ds), dropping to AP\n",
                              _staAttemptTimeout / 1000);
                startAP();
            }
        }
    }
}

void NetManager::startAP() {
    _mode = MODE_AP;
    _ap_ssid = apSsidOrDefault();
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(_ap_ssid.c_str(), "", 6, 0);
    delay(300);
    dns.start(53, "*", WiFi.softAPIP());
    startPortalServer();
    startDataService();
    Serial.printf("[NET] AP started: %s (http://192.168.4.1)\n", _ap_ssid.c_str());
    _scanCache = "[]";
    delay(800);
    WiFi.scanNetworks(true, false, 0, NULL);
}

void NetManager::startPortalServer() {
    if (_portalRunning) return;
    web.on("/", HTTP_GET, [this]() { handleRoot(); });
    web.on("/scan", HTTP_GET, [this]() {
        if (web.arg("refresh") == "1") { _scanBusy = true; WiFi.scanNetworks(true, false, 0, NULL); }
        handleScan();
    });
    web.on("/save", HTTP_POST, [this]() { handleSave(); });
    web.onNotFound([this]() {
        web.sendHeader("Location", "http://192.168.4.1/");
        web.send(302, "text/plain", "");
    });
    web.begin();
    _portalRunning = true;
}

void NetManager::startDataService() {
    if (_dataServerRunning) return;
    web.on("/data", HTTP_GET, [this]() { handleDataHtml(); });
    web.on("/history", HTTP_GET, [this]() { handleHistHtml(); });
    web.on("/api/data", HTTP_GET, [this]() { handleDataJson(); });
    web.on("/api/history", HTTP_GET, [this]() { handleHistJson(); });
    web.on("/config", HTTP_GET, [this]() { handleRoot(); });
    _dataServerRunning = true;
    Serial.println(F("[NET] Data routes: /data /history /api/data /api/history"));
}

void NetManager::handleRoot() { web.send(200, "text/html", FPSTR(PORTAL_HTML)); }

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
        _scanCache += "{\"ssid\":\"" + ssid + "\",\"rssi\":" + WiFi.RSSI(i)
                    + ",\"open\":" + (WiFi.encryptionType(i) == ENC_TYPE_NONE ? "true" : "false") + "}";
    }
    _scanCache += "]";
}

void NetManager::handleSave() {
    String ssid = web.arg("ssid2");
    ssid.trim();
    DeviceConfig c = *_cfg;
    memset(c.wifi_ssid, 0, sizeof(c.wifi_ssid));
    memset(c.wifi_pass, 0, sizeof(c.wifi_pass));
    ssid.toCharArray(c.wifi_ssid, sizeof(c.wifi_ssid));
    web.arg("pass").toCharArray(c.wifi_pass, sizeof(c.wifi_pass));
    c.server_mode = (uint8_t)web.arg("smode").toInt();
    String _h = web.arg("host");
    if (_h.length() > 0) _h.toCharArray(c.mqtt_host, sizeof(c.mqtt_host));
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
        "<h2>已保存！设备正在重启并连接...</h2></body>");
    Serial.println(F("[NET] Config saved, rebooting in 1.5s"));
    delay(1500);
    ESP.restart();
}
