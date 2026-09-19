#include "max30102.h"
#include <math.h>

enum {
    REG_INTR1    = 0x00, REG_INTR2    = 0x01,
    REG_FIFOG    = 0x04, REG_FIFOHW   = 0x05,
    REG_FIFOPH   = 0x07,
    REG_FIFO     = 0x08,
    REG_MODE     = 0x09, REG_SPO2     = 0x0A,
    REG_LED1     = 0x0C, REG_LED2     = 0x0D, REG_LED3 = 0x0E,
    REG_PI       = 0x0F, REG_MULT     = 0x10,
    REG_FIFOTAIL = 0x11,
    REG_TEMP_INT = 0x1F,
    REG_TEMP_FRAC= 0x20,
    REG_TEMP_CFG = 0x21,
    REG_ADI      = 0xFE,
};
static const uint8_t DATA_START = 0x07;
static const uint8_t B_SPO2_AEN  = 0x20, B_SPO2_AEN2 = 0x10;
static const uint8_t B_SPO2_SR25 = 0x06; // 25Hz, matches hrcalc.py

void MAX30102::_ensureBus() {
    if (_sda >= 0 && _scl >= 0 && _wire) {
        _wire->begin(_sda, _scl);
    }
}

bool MAX30102::writeReg(uint8_t addr, uint8_t val) {
    _ensureBus();
    uint8_t p[2] = {addr, val};
    _wire->beginTransmission(MAX30102_ADDR);
    _wire->write(p, 2);
    return _wire->endTransmission() == 0;
}
bool MAX30102::readReg(uint8_t addr, uint8_t &val) {
    _ensureBus();
    _wire->beginTransmission(MAX30102_ADDR);
    _wire->write(addr);
    if (_wire->endTransmission(false) != 0) return false;
    _wire->requestFrom(MAX30102_ADDR, (size_t)1);
    if (!_wire->available()) return false;
    val = _wire->read(); return true;
}

uint32_t MAX30102::read32(uint8_t dataIndex) {
    _ensureBus();
    uint8_t head[2] = {REG_FIFOPH, dataIndex};
    uint8_t p[6];
    _wire->beginTransmission(MAX30102_ADDR);
    _wire->write(head, 2);
    if (_wire->endTransmission(false) != 0) return 0;
    _wire->requestFrom(MAX30102_ADDR, (size_t)6);
    if (!_wire->available()) return 0;
    for (int i = 0; i < 6; i++) p[i] = _wire->read();
    return ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | p[2];
}

bool MAX30102::writeTail(uint8_t tail) {
    _ensureBus();
    uint8_t p[6];
    p[0] = REG_FIFOTAIL;
    p[1] = (tail & 0x3F) | 0x80;
    p[2] = 0; p[3] = 0; p[4] = 0;
    uint16_t s = 0;
    for (int i = 0; i < 5; i++) s += p[i];
    p[5] = 0xFF - (s & 0xFF);
    _wire->beginTransmission(MAX30102_ADDR);
    _wire->write(p, 6);
    return _wire->endTransmission() == 0;
}

bool MAX30102::begin(TwoWire *wire) {
    _wire = wire;
    _ensureBus();
    _wire->beginTransmission(MAX30102_ADDR);
    if (_wire->endTransmission() != 0) { Serial.println(F("[MAX30102] not found!")); return false; }
    delay(20);
    uint8_t adi;
    if (!readReg(REG_ADI, adi) || adi != 0x15) {
        Serial.printf("[MAX30102] chip id=0x%02X, expected 0x15, continue\n", adi);
    }
    writeReg(REG_MODE, 0x80); delay(5);
    writeReg(REG_INTR1, 0xC0); writeReg(REG_INTR2, 0x80);
    writeReg(REG_FIFOHW, 0x0F);
    uint8_t tail; readReg(REG_FIFOTAIL, tail);
    writeTail(tail & 0x3F); delay(10);
    uint8_t head; readReg(REG_FIFOPH, head);
    writeTail(head & 0x3F); delay(10);
    writeReg(REG_SPO2, B_SPO2_SR25 | B_SPO2_AEN | B_SPO2_AEN2); // 25Hz Red+IR
    writeReg(REG_LED1, 0x24);
    writeReg(REG_LED2, 0x24);
    writeReg(REG_LED3, 0x00);
    writeReg(REG_MODE, 0x03);
    delay(200);
    _write = 0; _fill = 0; _full = false;
    _hr = -999; _hrValid = false; _hrAvg = 0; _hrAvgValid = false;
    _spo2Raw = -999; _spo2Valid = false; _spo2Avg = 0; _spo2AvgValid = false;
    _tempC = NAN; _lastTempMs = 0; _tempBad = 0;
    readTempC(_tempC);
    Serial.println(F("[MAX30102] OK hrcalc25Hz dieTemp"));
    return true;
}

bool MAX30102::readTempC(float &temp_c) {
    if (!writeReg(REG_TEMP_CFG, 0x01)) return false;
    delay(10);
    uint8_t ti = 0, tf = 0;
    if (!readReg(REG_TEMP_INT, ti) || !readReg(REG_TEMP_FRAC, tf)) return false;
    int8_t whole = (int8_t)ti;
    uint8_t frac = tf & 0x0F;
    if (whole < 0 && frac != 0) temp_c = (float)whole + (frac * 0.0625f);
    else temp_c = (float)whole + (frac * 0.0625f);
    _tempC = temp_c;
    _lastTempMs = millis();
    _tempBad = 0;
    return true;
}

bool MAX30102::read(float &sp_o2, float &hr_bpm) {
    static uint32_t lastDbg = 0;
    uint32_t nowMs = millis();
    if (nowMs - lastDbg > 1500) {
        lastDbg = nowMs;
        Serial.printf("[MAXDBG] fill=%d hr=%d valid=%d spo2=%.2f valid=%d temp=%.2f\n",
                      _fill, _hr, _hrValid, _hrAvgValid ? _hrAvg : NAN, _spo2AvgValid, _tempC);
    }
    uint8_t ph;
    if (!readReg(REG_FIFOPH, ph)) return false;
    int toRead = (ph & 0x3F);
    if (toRead == 0) return false;
    if (toRead > 32) toRead = 32;
    for (int k = 0; k < toRead; k++) {
        uint8_t di = DATA_START + k * 6;
        uint32_t raw = read32(di);
        if (raw == 0) continue;
        _irBuf[_write]  = (float)((raw >> 8) & 0x03FF);
        _redBuf[_write] = (float)(raw & 0x03FF);
        _write = (_write + 1) % BUF;
        if (_fill < BUF) _fill++;
        _full = (_fill >= BUF);
    }
    int valid = _full ? BUF : (_write == 0 ? 0 : _write);
    if (valid < BUF) {
        if (nowMs - _lastTempMs > 1000 && !readTempC(_tempC)) {
            _tempBad++; if (_tempBad > 10) _tempC = NAN;
        }
        hr_bpm = _hrAvgValid ? _hrAvg : NAN;
        sp_o2 = _spo2AvgValid ? _spo2Avg : NAN;
        return _hrAvgValid || _spo2AvgValid || !isnan(_tempC);
    }
    if (!_full) {
        hr_bpm = _hrAvgValid ? _hrAvg : NAN;
        sp_o2 = _spo2AvgValid ? _spo2Avg : NAN;
        if (nowMs - _lastTempMs > 1000 && !readTempC(_tempC)) {
            _tempBad++; if (_tempBad > 10) _tempC = NAN;
        }
        return _hrAvgValid || _spo2AvgValid || !isnan(_tempC);
    }

    float irMean = 0;
    for (int b = 0; b < BUF; b++) irMean += _irBuf[b];
    irMean /= BUF;

    int16_t x[BUF];
    for (int b = 0; b < BUF; b++) x[b] = (int16_t)(-(int)(_irBuf[b]) + (int)irMean);
    for (int b = 0; b + MAX30102_MA_SIZE <= BUF; b++) {
        int16_t s = 0;
        for (int m = 0; m < MAX30102_MA_SIZE; m++) s += x[b + m];
        x[b] = s / MAX30102_MA_SIZE;
    }

    int thr = 0;
    for (int b = 0; b < BUF; b++) thr += x[b];
    thr /= BUF;
    if (thr < 30) thr = 30;
    if (thr > 60) thr = 60;

    uint8_t locs[15] = {};
    uint8_t nPeaks = 0;
    uint8_t i = 0;
    while (i < BUF - 1) {
        if (x[i] > thr && x[i] > x[i - 1]) {
            uint8_t width = 1;
            while (i + width < BUF - 1 && x[i] == x[i + width]) width++;
            if (x[i] > x[i + width] && nPeaks < 15) {
                locs[nPeaks++] = i;
                i += width + 1;
                continue;
            }
            i += width;
            continue;
        }
        i++;
    }
    if (nPeaks >= 2) {
        uint8_t order[15];
        for (uint8_t p = 0; p < nPeaks; p++) order[p] = locs[p];
        for (uint8_t p = 0; p < nPeaks; p++) {
            for (uint8_t q = p + 1; q < nPeaks; q++) {
                if (x[order[q]] > x[order[p]]) { uint8_t t = order[p]; order[p] = order[q]; order[q] = t; }
            }
        }
        uint8_t compact[15] = {};
        uint8_t compactN = 0;
        int8_t prev = -1;
        for (uint8_t p = 0; p < nPeaks; p++) {
            int8_t cur = (int8_t)order[p];
            int16_t dist = (prev >= 0) ? (int16_t)(cur - prev) : (int16_t)(cur + 1);
            if (prev < 0 || dist > 4 || dist < -4) {
                compact[compactN++] = order[p];
                prev = cur;
            }
        }
        for (uint8_t p = 0; p < compactN; p++) {
            for (uint8_t q = p + 1; q < compactN; q++) {
                if (compact[q] < compact[p]) { uint8_t t = compact[p]; compact[p] = compact[q]; compact[q] = t; }
            }
        }
        if (compactN > 15) compactN = 15;
        nPeaks = compactN;
        for (uint8_t p = 0; p < nPeaks; p++) locs[p] = compact[p];
    }

    _hr = -999;
    _hrValid = false;
    if (nPeaks >= 3) {
        long intervals[15] = {};
        uint8_t intervalN = 0;
        for (uint8_t p = 1; p < nPeaks && intervalN < 15; p++) {
            long iv = (long)locs[p] - (long)locs[p - 1];
            if (iv < 7) continue; // reject too-fast/artifact intervals
            if (iv > 60) continue; // reject too-slow/artifact intervals
            intervals[intervalN++] = iv;
        }
        if (intervalN >= 2) {
            for (uint8_t a = 0; a < intervalN; a++) {
                for (uint8_t b = a + 1; b < intervalN; b++) {
                    if (intervals[b] < intervals[a]) { long t = intervals[a]; intervals[a] = intervals[b]; intervals[b] = t; }
                }
            }
            long med = intervals[intervalN / 2];
            long h = (long)MAX30102_SAMPLE_FREQ * 60L / med;
            if (h >= 40 && h <= 150) {
                _hr = (int)h;
                _hrValid = true;
                _hrAvg = _hrAvgValid ? (float)(_hrAvg * 0.8f + _hr * 0.2f) : (float)_hr;
                _hrAvgValid = true;
            }
        }
    }

    int8_t ratioVals[5] = {};
    uint8_t ratioN = 0;
    for (uint8_t k = 0; k + 1 < nPeaks; k++) {
        long redDcMax = -16777216L;
        long irDcMax = -16777216L;
        int redDcIdx = locs[k];
        int irDcIdx = locs[k];
        if ((int)locs[k + 1] - (int)locs[k] > 3) {
            for (int b = locs[k]; b < locs[k + 1]; b++) {
                if ((long)_irBuf[b] > irDcMax) { irDcMax = _irBuf[b]; irDcIdx = b; }
                if ((long)_redBuf[b] > redDcMax) { redDcMax = _redBuf[b]; redDcIdx = b; }
            }
        }
        long span = (long)locs[k + 1] - locs[k];
        long redAc = ((long)_redBuf[locs[k + 1]] - (long)_redBuf[locs[k]]) * ((long)redDcIdx - locs[k]);
        redAc = _redBuf[locs[k]] + redAc / span;
        redAc = (long)_redBuf[redDcIdx] - redAc;
        long irAc = ((long)_irBuf[locs[k + 1]] - (long)_irBuf[locs[k]]) * ((long)irDcIdx - locs[k]);
        irAc = _irBuf[locs[k]] + irAc / span;
        irAc = (long)_irBuf[irDcIdx] - irAc;
        long nume = redAc * irDcMax;
        long denom = irAc * redDcMax;
        if (denom > 0 && ratioN < 5 && nume != 0) {
            long r = (nume * 100L) / denom;
            if (r < 0 || r > 255) continue;
            ratioVals[ratioN++] = (int8_t)r;
        }
    }
    for (uint8_t a = 0; a < ratioN; a++) {
        for (uint8_t b = a + 1; b < ratioN; b++) {
            if (ratioVals[b] < ratioVals[a]) { int8_t t = ratioVals[a]; ratioVals[a] = ratioVals[b]; ratioVals[b] = t; }
        }
    }
    int ratioAve = 0;
    int mid = (int)(ratioN / 2);
    if (mid > 1) ratioAve = (ratioVals[mid - 1] + ratioVals[mid]) / 2;
    else if (ratioN > 0) ratioAve = ratioVals[mid];

    if (ratioAve > 2 && ratioAve < 184) {
        float r = (float)ratioAve / 100.0f;
        float s = -45.060f * r * r + 30.054f * r + 94.845f;
        if (s >= 70.0f && s <= 100.0f) {
            _spo2Avg = _spo2AvgValid ? (float)(_spo2Avg * 0.7 + s * 0.3) : s;
            _spo2AvgValid = true;
            _spo2Raw = (int)(s * 100.0f + 0.5f);
            _spo2Valid = true;
        }
    }

    hr_bpm = _hrAvgValid ? _hrAvg : NAN;
    sp_o2 = _spo2AvgValid ? _spo2Avg : NAN;
    if (nowMs - _lastTempMs > 1000 && !readTempC(_tempC)) {
        _tempBad++; if (_tempBad > 10) _tempC = NAN;
    }
    return _hrAvgValid || _spo2AvgValid || !isnan(_tempC);
}
