// ============================================================
// 物联网环境监测系统 - ESP32-S3 固件主程序
//
//  数据流: AHT20/BMP280 -> (采样) -> 阈值判定 -> LED/蜂鸣器
//                                  -> ST7735 屏幕显示
//                                  -> MQTT 上报服务器
//
//  串口调试命令(115200):
//    config   立即进入 AP 配网模式
//    factory  清除全部配置并重启
//    status   打印当前状态
// ============================================================
#include <Arduino.h>
#include <driver/i2s.h>
#include <esp_err.h>
#include <WiFi.h>
#include <HTTPClient.h>

// ---------- TTS 语音 WAV 下载 + 喇叭播放 ----------
// 使用 I2S 模式(ESP32 I2S0)：PIN_SPEAKER=GPIO21 为 BCLK,
// 数据走 GPIO18(I2S0_SDOUT), 16kHz 16bit mono。
// 备选: 无 I2S 硬件时仅发提示音（兼容）
static const int TTS_I2S_SDOUT_PIN = 18;   // I2S0 数据输出
static const int TTS_I2S_BCLK_PIN  = 21;   // = PIN_SPEAKER, 复用为 BCLK
static const int TTS_I2S_LRCLK_PIN = -1;   // mono 不需要 LRCLK

// 状态机: 0=idle, 1=downloading body, 2=playing
static uint8_t _ttsPhase = 0;
static uint8_t *_ttsBuf = nullptr;
static int     _ttsLen = 0;
static int     _ttsPos = 0;
static int     _ttsChannels = 1;
static uint16_t _ttsSampleRate = 16000;
static WiFiClient _ttsNet;
static const int TTS_MAX_SIZE = 128 * 1024;
static const int TTS_HDR_BUF  = 4096;   // header 临时缓冲

static void _ttsFree() {
    if (_ttsBuf) { free(_ttsBuf); _ttsBuf = nullptr; _ttsLen = 0; }
}

static void ttsStart(const String &url) {
    if (_ttsPhase != 0) return;
    _ttsFree();
    _ttsNet.stop();
    _ttsPos = 0; _ttsLen = 0; _ttsChannels = 1; _ttsSampleRate = 16000;

    int scheme = url.indexOf("://");
    String host; int port = 80;
    if (scheme >= 0) {
        String rest = url.substring(scheme + 3);
        int slash = rest.indexOf('/');
        String hostPort = (slash >= 0) ? rest.substring(0, slash) : rest;
        int cp = hostPort.indexOf(':');
        host = (cp >= 0) ? hostPort.substring(0, cp) : hostPort;
        port = (cp >= 0) ? hostPort.substring(cp + 1).toInt() : 80;
    }
    String path = url.substring(url.lastIndexOf('/'));
    if (path.length() == 0) path = "/";

    if (!_ttsNet.connect(host.c_str(), port)) {
        Serial.printf("[TTS] connect fail: %s:%d\n", host.c_str(), port);
        return;
    }
    String req = "GET " + path + " HTTP/1.1\r\n"
                 "Host: " + host + ":" + String(port) + "\r\n"
                 "User-Agent: EnvMon\r\n"
                 "Connection: close\r\n\r\n";
    _ttsNet.write(req.c_str(), req.length());

    // 收集 header 到第一个空行, 解析 Content-Length
    uint8_t hdr[TTS_HDR_BUF];
    int hdrLen = 0;
    _ttsNet.setTimeout(3000);
    while (_ttsNet.connected() && hdrLen < TTS_HDR_BUF) {
        int n = _ttsNet.read(hdr + hdrLen, TTS_HDR_BUF - hdrLen);
        if (n <= 0) break;
        hdrLen += n;
        for (int i = 4; i <= hdrLen; i++) {
            if (memcmp(hdr + i - 4, "\r\n\r\n", 4) == 0) {
                String h = (const char *)hdr;
                int cl = h.indexOf("Content-Length:");
                if (cl < 0) { _ttsNet.stop(); _ttsFree(); return; }
                String clStr = h.substring(cl + 15);
                int idx = clStr.indexOf('\r');
                if (idx >= 0) clStr = clStr.substring(0, idx);
                int size = clStr.toInt();
                if (size <= 0 || size > TTS_MAX_SIZE) { _ttsNet.stop(); _ttsFree(); return; }
                _ttsBuf = (uint8_t *)malloc(size);
                if (!_ttsBuf) { _ttsNet.stop(); return; }
                _ttsLen = size; _ttsPos = 0;
                _ttsPhase = 1;
                return;
            }
        }
    }
    Serial.println("[TTS] header incomplete");
    _ttsNet.stop();
}

static void ttsStep() {
    if (_ttsPhase == 1) {
        if (_ttsPos < _ttsLen) {
            int n = _ttsNet.read(_ttsBuf + _ttsPos, _ttsLen - _ttsPos);
            if (n > 0) { _ttsPos += n; return; }
        }
        _ttsNet.stop();
        if (_ttsPos < 44 || _ttsBuf[0] != 'R' || _ttsBuf[1] != 'A' ||
            _ttsBuf[2] != 'T' || _ttsBuf[3] != 'E') {
            Serial.println("[TTS] bad WAV");
            _ttsFree(); _ttsPhase = 0; return;
        }
        _ttsChannels   = (_ttsBuf[22]) | (_ttsBuf[23] << 8);
        _ttsSampleRate = (_ttsBuf[24]) | (_ttsBuf[25] << 8);
        if (_ttsPos < _ttsLen) {
            _ttsFree(); _ttsPhase = 0; return;
        }
        _ttsPos = 44;
        _ttsPhase = 2;
        #if CONFIG_IDF_TARGET_ESP32S3
        i2s_driver_uninstall(I2S_NUM_0);
        i2s_config_t i2s_cfg = {
            .mode              = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
            .sample_rate       = _ttsSampleRate,
            .bits_per_sample   = I2S_BITS_PER_SAMPLE_16BIT,
            .channel_format    = _ttsChannels == 1 ? I2S_CHANNEL_FMT_ONLY_LEFT : I2S_CHANNEL_FMT_RIGHT_LEFT,
            .communication_format = (i2s_comm_format_t)(I2S_COMM_FORMAT_STAND_I2S),
            .intr_alloc_flags  = 0,
            .dma_buf_count     = 3,
            .dma_buf_len       = 256,
            .use_apll          = false,
            .tx_desc_auto_clear = true,
            .fixed_mclk        = 0,
            .mclk_multiple     = I2S_MCLK_MULTIPLE_DEFAULT,
            .bits_per_chan     = I2S_BITS_PER_CHAN_DEFAULT,
        };
        i2s_pin_config_t pin_cfg = {
            .mck_io_num   = I2S_PIN_NO_CHANGE,
            .bck_io_num   = TTS_I2S_BCLK_PIN,
            .ws_io_num    = I2S_PIN_NO_CHANGE,
            .data_out_num = TTS_I2S_SDOUT_PIN,
            .data_in_num  = I2S_PIN_NO_CHANGE,
        };
        if (i2s_driver_install(I2S_NUM_0, &i2s_cfg, 0, NULL) == ESP_OK) {
            i2s_set_pin(I2S_NUM_0, &pin_cfg);
        }
        #endif
        return;
    }
    if (_ttsPhase == 2) {
        #if CONFIG_IDF_TARGET_ESP32S3
        if (_ttsPos < _ttsLen) {
            size_t batch = 256;
            if (_ttsPos + batch > _ttsLen) batch = _ttsLen - _ttsPos;
            size_t sent = 0;
            i2s_write(I2S_NUM_0, _ttsBuf + _ttsPos, batch, &sent, 100);
            _ttsPos += sent;
        }
        #endif
        if (_ttsPos >= _ttsLen) {
            #if CONFIG_IDF_TARGET_ESP32S3
            i2s_driver_uninstall(I2S_NUM_0);
            #endif
            _ttsFree();
            _ttsPhase = 0;
        }
    }
}

static bool ttsIsPlaying() { return _ttsPhase != 0; }

#include "pins.h"
#include "config_store.h"
#include "sensors.h"
#include "ui.h"
#include "alarm.h"
#include "net_mgr.h"
#include "mqtt_mgr.h"
#include "ota_mgr.h"

static SensorHub   g_sensors;
static DisplayUI   g_ui;
static AlarmDevice g_alarm;

static EnvData  g_last;
static uint32_t g_lastRead = 0;
static uint32_t g_lastUi   = 0;
static uint32_t g_lastPub  = 0;
static bool     g_mqttReady = false;

// 麦克风声音检测
static int g_micBaseline = 0;     // 环境噪声基线
static bool g_voiceTrig = false;  // 是否检测到语音

static void initMic() {
    // 裸驻极体咪头: 无外部偏置电阻时用内部上拉(~45kΩ)作偏置
    pinMode(PIN_MIC, INPUT_PULLUP);
    analogReadResolution(12);  // 12-bit ADC
    analogSetPinAttenuation(PIN_MIC, ADC_11db);  // 0-3.3V 全量程
    // 采样100次建立基线
    int sum = 0;
    for (int i = 0; i < 100; i++) {
        sum += analogRead(PIN_MIC);
        delay(1);
    }
    g_micBaseline = sum / 100;
    Serial.printf("[MIC] baseline=%d\n", g_micBaseline);
}

static void checkMic() {
    // 连续采样10次，计算平均偏差
    int sum = 0, sumDev = 0;
    for (int i = 0; i < 10; i++) {
        int v = analogRead(PIN_MIC);
        sum += v;
        sumDev += abs(v - g_micBaseline);
    }
    int avgDev = sumDev / 10;
    // 阈值：基线偏差超过80视为有声音
    g_voiceTrig = (avgDev > 80);
}

static void handleSerialCmd() {
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toLowerCase();
    if (cmd == "config") {
        Serial.println(F("[CMD] Entering AP config mode..."));
        g_net.startAP();
    } else if (cmd == "factory") {
        Serial.println(F("[CMD] Factory reset..."));
        g_cfgStore.clear();
        delay(300);
        ESP.restart();
    } else if (cmd == "status") {
        Serial.printf("[CMD] wifi=%d mqtt=%d heap=%u t=%.1f h=%.1f p=%.1f\n",
                      g_net.wifiConnected(), g_mqtt.connected(),
                      ESP.getFreeHeap(), g_last.temp_c, g_last.hum_pct, g_last.pres_hpa);
    } else if (cmd == "mic") {
        // 实时打印麦克风 ADC 值，连续 20 次
        Serial.printf("[MIC] baseline=%d, samples:", g_micBaseline);
        for (int i = 0; i < 20; i++) {
            Serial.printf(" %d", analogRead(PIN_MIC));
        }
        Serial.println();
    } else if (cmd == "adcall") {
        const int pins[] = {1,2,3,4,5,6,7,8,9,10};
        for (int pi = 0; pi < 10; pi++) {
            analogSetPinAttenuation(pins[pi], ADC_11db);
            Serial.printf("  GPIO%d=%d\n", pins[pi], analogRead(pins[pi]));
        }
        Serial.println("[ADC] scan done");
    } else if (cmd == "scan") {
        // 串口诊断：直接跑一次扫描并打印结果，验证 AP 模式下扫描是否可用
        WiFi.disconnect(false);
        delay(100);
        int n = WiFi.scanNetworks(false, false, false, 300);
        Serial.printf("[CMD] scan found %d networks (mode=%d)\n", n, (int)WiFi.getMode());
        if (n < 0) {
            delay(500);
            n = WiFi.scanNetworks(false, false, false, 300);
            Serial.printf("[CMD] scan retry: %d\n", n);
        }
        for (int i = 0; i < n && i < 20; i++) {
            Serial.printf("  #%d %-32s %d dBm %s\n", i, WiFi.SSID(i).c_str(),
                          WiFi.RSSI(i), (WiFi.encryptionType(i)==WIFI_AUTH_OPEN)?"open":"wpa");
        }
        WiFi.scanDelete();
    } else if (cmd == "otacheck") {
        Serial.println("[CMD] OTA 手动检查...");
        ota_check(true);
    }
}

void setup() {
    Serial.begin(115200);
    delay(400);
    Serial.println();
    Serial.println(F("======================================"));
    Serial.println(F(" EnvMon ESP32-S3  firmware " FW_VERSION));
    Serial.println(F("======================================"));

#ifdef DISPLAY_DIAG
    // 屏幕方向诊断模式：只初始化屏幕，循环尝试 8 种配置，不会返回
    Serial.println(F("[DIAG] Display diagnostics started"));
    g_ui.begin();
    g_ui.diagLoop();   // 永不返回
#endif

    g_cfgStore.begin();
    bool saved = g_cfgStore.load(g_cfg);
    g_cfgStore.applyDefaults(g_cfg);  // 凭据兜底：只配 WiFi 未配 MQTT 账号也能连
    Serial.printf("[BOOT] config %s, device_id=%s\n",
                  saved ? "loaded" : "NOT found (first boot)", g_cfg.device_id);

    g_ui.begin();
    g_ui.showBoot(FW_VERSION);

    g_alarm.begin();

    if (!g_sensors.begin()) {
        Serial.println(F("[BOOT] WARNING: sensors unavailable"));
    }

    g_net.setConfig(&g_cfg);
    g_net.begin();

    // 根据保存的 MQTT host 自动推断 OTA 服务器（空 host 时 OTA 暂不检查）
    // OTA 服务器：用 web_port（Web=12090），不能用 mqtt_port（MQTT=18830）。
    // web_port=0 时默认 12090。
    uint16_t _otaPort = g_cfg.web_port ? g_cfg.web_port : 12090;
    String _otaHost = String(g_cfg.mqtt_host) + ":" + String(_otaPort);
    ota_set_server(_otaHost.length() > 1 ? _otaHost.c_str() : nullptr, nullptr);

    initMic();  // 初始化麦克风

    // 启动时自动检查一次 OTA（首次引导会顺便完成 boot_count 回滚判定）
    delay(500);
    ota_setup();
    ota_check(false);

    delay(1000);   // 显示启动画面
}

void loop() {
    handleSerialCmd();
    g_net.loop();
    uint32_t now = millis();

    // ---------- AP 配网模式 ----------
    if (g_net.inAPMode()) {
        if (now - g_lastRead >= 2000) {
            g_lastRead = now;
            g_sensors.read(g_last);
        }
        checkMic();  // 检测声音
        g_ui.showAP(g_net.apSSID().c_str(), &g_last, g_voiceTrig);
        g_alarm.update(AL_CONFIG, false);
        delay(10);
        return;
    }

    // ---------- MQTT 初始化（WiFi 就绪后） ----------
    if (!g_mqttReady && g_cfg.has_mqtt() && g_net.wifiConnected()) {
        g_mqtt.begin();
        g_mqttReady = true;
    }
    if (g_mqttReady) g_mqtt.loop();

    // ---------- 局域网自动发现（LAN 模式且尚未连上 MQTT 时） ----------
    if (!g_mqttReady && g_net.wifiConnected()
            && g_cfg.server_mode == 0 && !g_cfg.has_mqtt()) {
        if (!g_net.inDiscovery()) {
            g_net.startDiscover();
        }
        int disc = g_net.discoverLoop(now);
        if (disc == 1) {
            Serial.println(F("[MAIN] server discovered -> restarting to apply"));
            delay(500);
            ESP.restart();
        } else if (disc == -1) {
            // 超时未收到应答：退回到 AP 配网让用户手动指定
            Serial.println(F("[MAIN] discovery failed -> entering AP portal"));
            g_net.startAP();
        }
    }

    // ---------- TTS 语音播报处理 ----------
    {
        int ttsLevel = 0;
        String ttsText = g_mqtt.takeTtsText(&ttsLevel);
        if (ttsText.length() > 0) {
            Serial.printf("[TTS] %s (level=%d)\n", ttsText.c_str(), ttsLevel);
            g_ui.showTtsMessage(ttsText);
            // 尝试通过 HTTP 从服务器拉 WAV 语音
            // 注意：必须用 web_port（Web 服务端口），不能用 mqtt_port（MQTT 端口），
            // 两者不同（Web=12090, MQTT=18830），且 web_port 非 80 时必须显式拼上。
            if (!ttsIsPlaying() && g_cfg.has_mqtt() && g_net.wifiConnected() &&
                g_cfg.web_port != 0) {
                String url = String("http://") + g_cfg.mqtt_host + ":" + String(g_cfg.web_port) + "/api/tts/speak?text=";
                // UTF-8 字节级 URL 编码（中文等多字节字符每字节 %XX）
                for (int i = 0; i < ttsText.length(); i++) {
                    unsigned char c = (unsigned char)ttsText[i];
                    if (isalnum(c))       url += (char)c;
                    else if (c == ' ')    url += '+';
                    else if (c == '_' || c == '-' || c == '.') url += (char)c;
                    else { char h[6]; sprintf(h, "%%%02X", c); url += h; }
                }
                ttsStart(url);
            }
            playTtsAlert(ttsLevel);
        }
        if (ttsIsPlaying()) {
            ttsStep();
        }
    }

    // ---------- 传感器采样（每 2s） ----------
    if (now - g_lastRead >= 2000) {
        g_lastRead = now;
        g_sensors.read(g_last);
        g_sensors.readVitals(g_last);
    }
    AlarmLevel lvl = g_alarm.evaluate(g_last, g_cfg);
    g_alarm.update(lvl, g_cfg.alarm_sound);

    // ---------- MQTT 周期上报 ----------
    if (g_mqttReady && g_mqtt.connected() &&
        now - g_lastPub >= (uint32_t)g_cfg.report_interval * 1000UL) {
        g_lastPub = now;
        if (g_mqtt.publishVitals(g_last)) {
            Serial.printf("[MAIN] vitals published (t=%.1f pr=%.1f)\n",
                          g_last.temp_c, isnan(g_last.pr_hr) ? 0 : g_last.pr_hr);
        }
        if (g_mqtt.publishTelemetry(g_last, (int)lvl)) {
            Serial.printf("[MAIN] telemetry published (t=%.1f h=%.1f p=%.1f)\n",
                          g_last.temp_c, g_last.hum_pct, g_last.pres_hpa);
        }
    }

    // ---------- 屏幕刷新（每 500ms，内部局部重绘） ----------
    if (now - g_lastUi >= 500) {
        g_lastUi = now;
        NetState ns = NET_OFF;
        if (g_net.wifiConnected())
            ns = (g_mqttReady && g_mqtt.connected()) ? NET_MQTT_OK : NET_WIFI_OK;
        else if (g_cfg.has_wifi())
            ns = NET_CONNECTING;
        g_ui.showMain(g_last, ns, lvl == AL_ALARM);
    }

    delay(5);   // 让出 CPU，喂狗
}
