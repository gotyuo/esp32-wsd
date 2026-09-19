#include "max30102.h"
#include <math.h>

enum {
    REG_STATUS   = 0x00,
    REG_INTR1    = 0x02,
    REG_WR_PTR   = 0x04,
    REG_OVF      = 0x05,
    REG_RD_PTR   = 0x06,
    REG_FIFO_DATA= 0x07,
    REG_FIFO_CFG = 0x08,
    REG_MODE     = 0x09,
    REG_SPO2     = 0x0A,
    REG_LED1     = 0x0C,
    REG_LED2     = 0x0D,
    REG_TEMP_INT = 0x1F,
    REG_TEMP_FRAC= 0x20,
    REG_TEMP_CFG = 0x21,
    REG_PART_ID  = 0xFF,
};

static const uint8_t B_SPO2_AEN  = 0x20;
static const uint8_t B_SPO2_AEN2 = 0x10;
static const uint8_t B_SPO2_SR25 = 0x06; // 25Hz, matches hrcalc.py SAMPLE_FREQ=25

void MAX30102::_ensureBus() {
    if (_sda >= 0 && _scl >= 0 && _wire) {
        _wire->begin(_sda, _scl);
    }
}

bool MAX30102::writeReg(uint8_t addr, uint8_t val) {
    _ensureBus();
    for (int t = 0; t < 3; t++) {
        _wire->beginTransmission(MAX30102_ADDR);
        _wire->write(addr);
        _wire->write(val);
        if (_wire->endTransmission() == 0) return true;
        delay(2);
    }
    return false;
}

bool MAX30102::readReg(uint8_t addr, uint8_t &val) {
    _ensureBus();
    for (int t = 0; t < 3; t++) {
        _wire->beginTransmission(MAX30102_ADDR);
        _wire->write(addr);
        if (_wire->endTransmission() != 0) continue;
        uint8_t got = _wire->requestFrom(MAX30102_ADDR, (size_t)1);
        if (got != 1) continue;
        val = _wire->read();
        return true;
    }
    return false;
}

bool MAX30102::readFifo(uint8_t *buf, uint8_t n) {
    _ensureBus();
    for (int t = 0; t < 3; t++) {
        _wire->beginTransmission(MAX30102_ADDR);
        _wire->write(REG_FIFO_DATA);
        if (_wire->endTransmission() != 0) continue;
        uint8_t got = _wire->requestFrom(MAX30102_ADDR, (size_t)n);
        if (got != n) continue;
        for (int i = 0; i < n; i++) buf[i] = _wire->read();
        return true;
    }
    return false;
}

bool MAX30102::begin(TwoWire *wire) {
    _wire = wire;
    _ensureBus();
    uint8_t id = 0;
    if (!readReg(REG_PART_ID, id) || id != 0x15) {
        Serial.printf("[MAX30102] part id=0x%02X, not 0x15\n", id);
        return false;
    }
    if (!writeReg(REG_MODE, 0x40)) return false; // soft reset
    delay(50);
    bool cfgOk = true;
    cfgOk &= writeReg(REG_INTR1, 0xC0);          // clear interrupts / enable
    cfgOk &= writeReg(REG_FIFO_CFG, 0x0F);       // FIFO high water 15
    cfgOk &= writeReg(REG_SPO2, B_SPO2_AEN | B_SPO2_AEN2 | B_SPO2_SR25); // 25Hz Red+IR
    cfgOk &= writeReg(REG_LED1, 0x24);           // IR, moderate current
    cfgOk &= writeReg(REG_LED2, 0x24);           // Red
    cfgOk &= writeReg(REG_MODE, 0x03);           // Red+IR active mode
    if (!cfgOk) {
        delay(50);
        writeReg(REG_INTR1, 0xC0);
        writeReg(REG_FIFO_CFG, 0x0F);
        writeReg(REG_SPO2, B_SPO2_AEN | B_SPO2_AEN2 | B_SPO2_SR25);
        writeReg(REG_LED1, 0x24);
        writeReg(REG_LED2, 0x24);
        writeReg(REG_MODE, 0x03);
    }
    writeReg(REG_WR_PTR, 0);
    writeReg(REG_OVF, 0);
    writeReg(REG_RD_PTR, 0);
    _write = 0; _fill = 0; _full = false;
    _hr = -999; _hrValid = false; _hrAvg = 0; _hrAvgValid = false;
    _spo2Raw = -999; _spo2Valid = false; _spo2Avg = 0; _spo2AvgValid = false;
    _tempC = NAN; _lastTempMs = 0; _tempBad = 0;
    readTempC(_tempC);
    Serial.println(F("[MAX30102] OK hrcalc25Hz dieTemp"));
    return true;
}

void MAX30102::findPeaks(const int16_t *x, uint8_t *locs, uint8_t &count) const {
    count = 0;
    uint8_t i = 0;
    while (i < BUF - 1) {
        if (i > 0 && x[i] > x[i - 1]) {
            uint8_t w = 1;
            while (i + w < BUF - 1 && x[i] == x[i + w]) w++;
            if (x[i] > x[i + w] && count < 15) {
                locs[count++] = i;
                i += w + 1;
                continue;
            }
            i += w;
            continue;
        }
        i++;
    }
}

void MAX30102::removeClosePeaks(uint8_t count, uint8_t *locs, const int16_t *x) const {
    if (count < 2) return;
    uint8_t order[15];
    for (uint8_t i = 0; i < count; i++) order[i] = locs[i];
    // sort descending by height, like maxim_sort_indices_descend
    for (uint8_t i = 0; i < count; i++) {
        for (uint8_t j = i + 1; j < count; j++) {
            if (x[order[j]] > x[order[i]]) { uint8_t t = order[i]; order[i] = order[j]; order[j] = t; }
        }
    }
    uint8_t kept = 0;
    int8_t prev = -1;
    for (uint8_t i = 0; i < count; i++) {
        int8_t cur = (int8_t)order[i];
        int16_t dist = (prev >= 0) ? (cur - prev) : (cur + 1);
        if (prev < 0 || dist > 4 || dist < -4) {
            locs[kept++] = order[i];
            prev = cur;
        }
    }
    if (kept > 15) kept = 15;
    // sort positions ascending
    for (uint8_t i = 0; i < kept; i++) {
        for (uint8_t j = i + 1; j < kept; j++) {
            if (locs[j] < locs[i]) { uint8_t t = locs[i]; locs[i] = locs[j]; locs[j] = t; }
        }
    }
    // caller expects original count field updated by this function's caller; here only locs compacted.
}

bool MAX30102::readTempC(float &temp_c) {
    if (!writeReg(REG_TEMP_CFG, 0x01)) return false;
    delay(10); // datasheet: TEMP_EN is self-clearing after conversion
    uint8_t ti = 0, tf = 0;
    if (!readReg(REG_TEMP_INT, ti) || !readReg(REG_TEMP_FRAC, tf)) return false;
    int8_t whole = (int8_t)ti; // 2's complement, 1°C per bit
    uint8_t frac = tf & 0x0F;  // 0.0625°C per bit
    if (whole < 0 && frac != 0) {
        float positiveFrac = frac * 0.0625f;
        temp_c = (float)whole + positiveFrac;
    } else {
        temp_c = (float)whole + (frac * 0.0625f);
    }
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

    uint8_t wr = 0, rd = 0;
    if (!readReg(REG_WR_PTR, wr) || !readReg(REG_RD_PTR, rd)) return false;
    uint8_t n = (uint8_t)((wr - rd) & 0x1F);
    if (n == 0) return false;
    if (n > 8) n = 8;
    uint8_t raw[6 * 8];
    if (!readFifo(raw, (uint8_t)(n * 6))) return false;
    for (uint8_t k = 0; k < n; k++) {
        uint32_t r = ((uint32_t)(raw[k * 6] & 0x03) << 16) | ((uint32_t)raw[k * 6 + 1] << 8) | raw[k * 6 + 2];
        uint32_t i = ((uint32_t)(raw[k * 6 + 3] & 0x03) << 16) | ((uint32_t)raw[k * 6 + 4] << 8) | raw[k * 6 + 5];
        _irBuf[_write]  = (float)i;
        _redBuf[_write] = (float)r;
        _write = (_write + 1) % BUF;
        if (_fill < BUF) _fill++;
        _full = (_fill >= BUF);
    }

    if (_full) {
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
        // Find above min height
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
        // Remove close peaks
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
        if (nPeaks >= 2) {
            long peakSum = 0;
            for (uint8_t p = 1; p < nPeaks; p++) peakSum += (locs[p] - locs[p - 1]);
            long avgInt = peakSum / (nPeaks - 1);
            if (avgInt > 0) {
                long h = (long)MAX30102_SAMPLE_FREQ * 60L / avgInt;
                if (h >= 30 && h <= 220) {
                    _hr = (int)h;
                    _hrValid = true;
                    _hrAvg = _hrAvgValid ? (float)(_hrAvg * 0.7 + _hr * 0.3) : (float)_hr;
                    _hrAvgValid = true;
                }
            }
        }

        int8_t ratioVals[5] = {};
        uint8_t ratioN = 0;
        bool outOfRange = false;
        for (uint8_t p = 0; p < nPeaks; p++) {
            if (locs[p] >= BUF) { outOfRange = true; break; }
        }
        if (!outOfRange) {
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
        if (!_spo2AvgValid && _spo2Avg == 0) {} // keep stale-free only when new valid values arrive
    }

    hr_bpm = _hrAvgValid ? _hrAvg : NAN;
    sp_o2 = _spo2AvgValid ? _spo2Avg : NAN;
    if (nowMs - _lastTempMs > 1000) {
        if (!readTempC(_tempC)) {
            _tempBad++;
            if (_tempBad > 10) _tempC = NAN;
        }
    }
    return _hrAvgValid || _spo2AvgValid || !isnan(_tempC);
}
