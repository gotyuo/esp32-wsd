#include "ad8232.h"

bool AD8232::begin(int pin) {
    _pin = pin;
    if (_pin < 0) return false;
    analogReadResolution(12);
    #if defined(ARDUINO_ARCH_ESP32)
        analogSetPinAttenuation(_pin, ADC_11db);   // 0-3.3V 全量程
    #endif
    // 预采样 200 点建立基线并预热 ADC
    for (int i = 0; i < 200; i++) {
        _buf[i] = analogRead(_pin);
    }
    Serial.printf("[AD8232] OK pin=%d\n", _pin);
    return true;
}

// 把新采样点灌入环形缓冲区(~250Hz)
void AD8232::processWindow(float &ecg_hr_bpm) {
    // 采集一小段(约 10 点 ≈ 40ms @250Hz)
    for (int i = 0; i < 10; i++) {
        _buf[_write] = analogRead(_pin);
        _write = (_write + 1) % BUF;
        if (_write == 0) _full = true;
    }
    int valid = _full ? BUF : _write;
    if (valid < 400) { ecg_hr_bpm = NAN; return; }

    // 找相邻 QRS 波峰间距 => 心率
    // QRS 峰:比前后都高 且高于局部阈值
    int peaks[400]; int np = 0;
    for (int i = 3; i < valid - 3 && np < 399; i++) {
        int cidx = (i + valid - BUF) % BUF;  // 不需要,已线性展开
        int v = _buf[i];
        int prev = _buf[i-2]; int nxt = _buf[i+2];
        if (v > prev && v > nxt && (v - prev) > 25) {
            peaks[np++] = i;
        }
    }
    if (np < 4) { ecg_hr_bpm = NAN; return; }
    // 只取明显等间隔的峰(去噪)
    int goodN = 0; float goodD = 0;
    for (int i = 0; i + 1 < np; i++) {
        int d = peaks[i+1] - peaks[i];
        if (d >= 6 && d <= 60) { goodD += d; goodN++; }
    }
    if (goodN < 2) { ecg_hr_bpm = NAN; return; }
    // 采样约 250Hz：心率 = 250 * 60 / avgGap = 15000 / avgGap
    float avgGap = goodD / goodN;
    ecg_hr_bpm = 15000.0f / avgGap;
    if (ecg_hr_bpm < 30 || ecg_hr_bpm > 220) ecg_hr_bpm = NAN;
}

bool AD8232::read(float &ecg_hr_bpm) {
    if (_pin < 0) return false;
    processWindow(ecg_hr_bpm);
    return true;
}
