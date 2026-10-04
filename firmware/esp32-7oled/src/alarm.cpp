#include "alarm.h"
#include "pins.h"

#include <WiFiClient.h>
#include <driver/i2s.h>
#include <esp_err.h>

#define BUZZ_CH 0    // 蜂鸣器 LEDC 通道
#define SPK_CH  1    // 喇叭 LEDC 通道（独立于蜂鸣器）

// 呼吸/闪烁节奏（ms）
#define NORMAL_PERIOD   3000
#define WARNING_PERIOD  900
#define ALARM_PERIOD    300
#define NODATA_PERIOD   1600

// TTS 播放内部状态
static uint8_t _ttsPhase = 0;
static uint8_t *_ttsBuf = nullptr;
static int     _ttsLen = 0;
static int     _ttsPos = 0;
static int     _ttsChannels = 1;
static uint16_t _ttsSampleRate = 16000;
static WiFiClient _ttsNet;
static const int TTS_MAX_SIZE = 128 * 1024;
static const int TTS_HDR_BUF  = 4096;
static uint8_t _ttsHdr[TTS_HDR_BUF];

static void _ttsFree() {
    if (_ttsBuf) { free(_ttsBuf); _ttsBuf = nullptr; _ttsLen = 0; }
}

static int _readExact(WiFiClient &net, uint8_t *buf, int need) {
    int got = 0;
    net.setTimeout(800);
    while (got < need) {
        int n = net.read(buf + got, need - got);
        if (n <= 0) break;
        got += n;
    }
    return got;
}

static bool _getTtsWav(const String &host, int port, const String &text) {
    if (!_ttsNet.connect(host.c_str(), port)) {
        Serial.printf("[TTS] connect fail: %s:%d\n", host.c_str(), port);
        return false;
    }
    String body = "{\"text\":\"";
    body += text;
    body += "\"}";
    String req = "POST /api/tts/speak HTTP/1.1\r\n"
                 "Host: " + host + ":" + String(port) + "\r\n"
                 "Content-Type: application/json\r\n"
                 "Content-Length: " + String(body.length()) + "\r\n"
                 "User-Agent: EnvMon\r\n"
                 "Connection: close\r\n\r\n";
    req += body;
    _ttsNet.write(req.c_str(), req.length());

    uint8_t *hdr = _ttsHdr;
    int hdrLen = 0;
    bool foundEnd = false;
    _ttsNet.setTimeout(5000);
    while (_ttsNet.connected() && hdrLen < TTS_HDR_BUF) {
        int n = _ttsNet.read(hdr + hdrLen, TTS_HDR_BUF - hdrLen);
        if (n <= 0) break;
        hdrLen += n;
        for (int i = 4; i <= hdrLen; i++) {
            if (memcmp(hdr + i - 4, "\r\n\r\n", 4) == 0) {
                foundEnd = true;
                break;
            }
        }
        if (foundEnd) break;
    }
    if (!foundEnd) {
        Serial.println("[TTS] header incomplete");
        _ttsNet.stop();
        return false;
    }

    String h = String((const char *)hdr, hdrLen);
    h.toLowerCase();
    int cl = h.indexOf("content-length:");
    if (cl < 0) {
        Serial.println("[TTS] no content-length");
        _ttsNet.stop();
        return false;
    }
    String clStr = h.substring(cl + 15);
    int idx = clStr.indexOf('\r');
    if (idx >= 0) clStr = clStr.substring(0, idx);
    int size = clStr.toInt();
    if (size <= 0 || size > TTS_MAX_SIZE) {
        Serial.printf("[TTS] bad size %d\n", size);
        _ttsNet.stop();
        return false;
    }

    _ttsBuf = (uint8_t *)malloc(size);
    if (!_ttsBuf) {
        _ttsNet.stop();
        return false;
    }
    int got = 0;
    _ttsNet.setTimeout(3000);
    while (got < size) {
        int n = _ttsNet.read(_ttsBuf + got, size - got);
        if (n <= 0) break;
        got += n;
    }
    if (got != size) {
        Serial.printf("[TTS] read %d/%d\n", got, size);
        _ttsFree();
        _ttsNet.stop();
        return false;
    }
    _ttsNet.stop();
    _ttsLen = size;
    _ttsPos = 0;
    return true;
}

void ttsStart(const String &url, const String &text) {
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
    if (host.length() == 0) return;

    if (text.length() > 0) {
        if (_getTtsWav(host, port, text)) {
            _ttsPhase = 2;
            return;
        }
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

    uint8_t *hdr = _ttsHdr;
    int hdrLen = 0;
    bool foundEnd = false;
    _ttsNet.setTimeout(5000);
    while (_ttsNet.connected() && hdrLen < TTS_HDR_BUF) {
        int n = _ttsNet.read(hdr + hdrLen, TTS_HDR_BUF - hdrLen);
        if (n <= 0) break;
        hdrLen += n;
        for (int i = 4; i <= hdrLen; i++) {
            if (memcmp(hdr + i - 4, "\r\n\r\n", 4) == 0) {
                foundEnd = true;
                break;
            }
        }
        if (foundEnd) break;
    }
    if (!foundEnd) {
        Serial.println("[TTS] header incomplete");
        _ttsNet.stop();
        return;
    }

    String h = String((const char *)hdr, hdrLen);
    h.toLowerCase();
    int cl = h.indexOf("content-length:");
    if (cl < 0) { _ttsNet.stop(); _ttsFree(); return; }
    String clStr = h.substring(cl + 15);
    int idx = clStr.indexOf('\r');
    if (idx >= 0) clStr = clStr.substring(0, idx);
    int size = clStr.toInt();
    if (size <= 0 || size > TTS_MAX_SIZE) { _ttsNet.stop(); _ttsFree(); return; }
    _ttsBuf = (uint8_t *)malloc(size);
    if (!_ttsBuf) { _ttsNet.stop(); return; }
    int got = 0;
    _ttsNet.setTimeout(3000);
    while (got < size) {
        int n = _ttsNet.read(_ttsBuf + got, size - got);
        if (n <= 0) break;
        got += n;
    }
    if (got != size) {
        Serial.printf("[TTS] read %d/%d\n", got, size);
        _ttsFree();
        _ttsNet.stop();
        return;
    }
    _ttsNet.stop();
    _ttsLen = size; _ttsPos = 0; _ttsPhase = 2;
}

void ttsStep() {
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

bool ttsIsPlaying() { return _ttsPhase != 0; }

void AlarmDevice::begin() {
    pinMode(PIN_LED_R, OUTPUT);
    pinMode(PIN_LED_G, OUTPUT);
    pinMode(PIN_LED_B, OUTPUT);
    setRGB(false, false, false);
    // 无源蜂鸣器：LEDC 产生方波
    ledcSetup(BUZZ_CH, 2000, 8);
    ledcAttachPin(PIN_BUZZER, BUZZ_CH);
    buzzerOff();
    // 喇叭：LEDC PWM 产生语音/提示音（独立通道，独立于蜂鸣器）
    ledcSetup(SPK_CH, 2000, 8);
    ledcAttachPin(PIN_SPEAKER, SPK_CH);
    speakerOff();
}

void AlarmDevice::setRGB(bool r, bool g, bool b) {
    digitalWrite(PIN_LED_R, r ? HIGH : LOW);
    digitalWrite(PIN_LED_G, g ? HIGH : LOW);
    digitalWrite(PIN_LED_B, b ? HIGH : LOW);
}

void AlarmDevice::buzzerOn(uint32_t freq) {
    ledcWriteTone(BUZZ_CH, freq);
    ledcWrite(BUZZ_CH, 128);   // 50% 占空比，最响
}

void AlarmDevice::buzzerOff() {
    ledcWrite(BUZZ_CH, 0);
}

void AlarmDevice::speakerOn(uint32_t freq) {
    ledcWriteTone(SPK_CH, freq);
    ledcWrite(SPK_CH, 128);
}

void AlarmDevice::speakerOff() {
    ledcWrite(SPK_CH, 0);
}

// 判定单个值是否超出/接近 [lo, hi]：
// 返回 0=正常 1=接近边界(预警) 2=越界(报警)
static int band(float v, float lo, float hi) {
    if (isnan(v)) return 0;
    if (v < lo || v > hi) return 2;
    float span = hi - lo;
    float margin = span * 0.10f;     // 距边界 10% 内视为预警
    if (v < lo + margin || v > hi - margin) return 1;
    return 0;
}

AlarmLevel AlarmDevice::evaluate(const EnvData &d, const DeviceConfig &cfg) {
    if (!d.valid) { _level = AL_NODATA; return _level; }
    if (!cfg.alarm_enabled) { _level = AL_NORMAL; return _level; }

    int worst = 0;
    worst = max(worst, band(d.temp_c,   cfg.temp_min, cfg.temp_max));
    worst = max(worst, band(d.hum_pct,  cfg.hum_min,  cfg.hum_max));
    worst = max(worst, band(d.pres_hpa, cfg.pres_min, cfg.pres_max));

    // 体征阈值（固定医学正常范围）+ 突变检测
    worst = max(worst, band(d.sp_o2,   95, 100));
    worst = max(worst, band(d.pr_hr,   60, 100));
    worst = max(worst, band(d.ecg_hr,  60, 100));
    worst = max(worst, band(d.rr_bpm,  12, 25));
    worst = max(worst, band(d.glucose, 3.9, 6.1));

    // 心率突变检测：与上一次差值 >30% 视为突变
    static float s_prev_hr = NAN;
    if (!isnan(d.ecg_hr) && !isnan(s_prev_hr)) {
        float delta = fabsf(d.ecg_hr - s_prev_hr) / fmaxf(s_prev_hr, 1.0f);
        if (delta > 0.30f) worst = max(worst, 2);
    }
    if (!isnan(d.ecg_hr)) s_prev_hr = d.ecg_hr;

    _level = (worst == 2) ? AL_ALARM : (worst == 1) ? AL_WARNING : AL_NORMAL;
    return _level;
}

void AlarmDevice::update(AlarmLevel level, bool alarm_sound) {
    uint32_t now = millis();

    switch (level) {
    case AL_NORMAL: {
        // 绿色呼吸（用分段近似）
        uint32_t period = NORMAL_PERIOD;
        uint32_t ph = (now % period);
        bool on = (ph < period / 2);
        if (on) setRGB(false, true, false); else setRGB(false, false, false);
        buzzerOff();
        break;
    }
    case AL_WARNING: {
        if (now - _lastToggle >= WARNING_PERIOD / 2) {
            _lastToggle = now;
            _phase = !_phase;
        }
        setRGB(_phase, _phase, false);   // 红+绿 = 橙
        buzzerOff();
        break;
    }
    case AL_ALARM: {
        if (now - _lastToggle >= ALARM_PERIOD / 2) {
            _lastToggle = now;
            _phase = !_phase;
        }
        setRGB(_phase, false, false);    // 红色快闪
        if (alarm_sound) {
            // 间歇鸣叫：500ms 响 / 500ms 停（蜂鸣器+喇叭同时）
            if ((now % 1000) < 500) {
                buzzerOn(2700);
                speakerOn(880);   // 喇叭低音，语音提示
            } else {
                buzzerOff();
                speakerOff();
            }
        } else {
            buzzerOff();
            speakerOff();
        }
        break;
    }
    case AL_NODATA: {
        if (now - _lastToggle >= NODATA_PERIOD / 2) {
            _lastToggle = now;
            _phase = !_phase;
        }
        setRGB(false, false, _phase);    // 蓝色慢闪：传感器异常
        buzzerOff();
        break;
    }
    case AL_CONFIG: {
        // 青色呼吸：配网模式
        uint32_t period = NODATA_PERIOD;
        bool on = ((now % period) < period / 2);
        setRGB(false, on, on);           // 绿+蓝 = 青
        buzzerOff();
        break;
    }
    }
}

// ---------------- TTS 提示音 ----------------
// 播放短促提示音序列，表示收到 TTS 语音播报消息
// level: 0=信息(两短低音) 1=预警(三短中音) 2=报警(连续高音)
void playTtsAlert(int level) {
    // 直接用 ledc 驱动 PIN_BUZZER，不经过 AlarmDevice（避免干扰报警状态机）
    // 注：BUZZ_CH=0 已在 AlarmDevice::begin() 中 setup
    const uint32_t freqs[] = {880, 1200, 2000};  // 低/中/高
    uint32_t freq = freqs[level > 2 ? 2 : level];

    if (level == 0) {
        // 信息：两短低音
        ledcWriteTone(BUZZ_CH, freq);
        ledcWrite(BUZZ_CH, 128);
        delay(120);
        ledcWrite(BUZZ_CH, 0);
        delay(80);
        ledcWriteTone(BUZZ_CH, freq);
        ledcWrite(BUZZ_CH, 128);
        delay(120);
        ledcWrite(BUZZ_CH, 0);
    } else if (level == 1) {
        // 预警：三短中音
        for (int i = 0; i < 3; i++) {
            ledcWriteTone(BUZZ_CH, freq);
            ledcWrite(BUZZ_CH, 128);
            delay(100);
            ledcWrite(BUZZ_CH, 0);
            delay(60);
        }
    } else {
        // 报警：连续高音 500ms
        ledcWriteTone(BUZZ_CH, freq);
        ledcWrite(BUZZ_CH, 128);
        delay(500);
        ledcWrite(BUZZ_CH, 0);
    }
}
