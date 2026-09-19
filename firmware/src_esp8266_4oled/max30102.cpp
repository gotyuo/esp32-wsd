#include "max30102.h"
#include <math.h>

enum {
    REG_STATUS   = 0x00,
    REG_INTR1    = 0x02,
    REG_INTR2    = 0x03,
    REG_WR_PTR   = 0x04,
    REG_OVF      = 0x05,
    REG_RD_PTR   = 0x06,
    REG_FIFO_DATA= 0x07,
    REG_FIFO_CFG = 0x08,
    REG_MODE     = 0x09,
    REG_SPO2     = 0x0A,
    REG_LED1     = 0x0C,
    REG_LED2     = 0x0D,
    REG_PART_ID  = 0xFF,
};

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
    if (!writeReg(REG_MODE, 0x40)) return false;
    delay(100);
    bool cfgOk = true;
    cfgOk &= writeReg(REG_FIFO_CFG, 0xBF);
    cfgOk &= writeReg(REG_SPO2, 0x58);
    cfgOk &= writeReg(REG_LED1, 0x28);
    cfgOk &= writeReg(REG_LED2, 0x28);
    cfgOk &= writeReg(REG_MODE, 0x03);
    if (!cfgOk) {
        delay(50);
        writeReg(REG_FIFO_CFG, 0xBF);
        writeReg(REG_SPO2, 0x58);
        writeReg(REG_LED1, 0x28);
        writeReg(REG_LED2, 0x28);
        writeReg(REG_MODE, 0x03);
    }
    writeReg(REG_WR_PTR, 0);
    writeReg(REG_OVF, 0);
    writeReg(REG_RD_PTR, 0);
    _write = 0; _full = false;
    _dcEstIR = 0; _lp1 = 0; _lp2 = 0; _peakEnv = 0;
    _aboveThr = false; _lastBeatMs = 0; _ibiCount = 0;
    _hr = 0; _hrValid = false;
    _irDcSlow = 0; _finger = false; _fingerOffMs = 0;
    _spo2 = 0; _spo2Valid = false; _spo2Avg = 0;
    _badRatio = 0; _spo2Counter = 0; _spo2Fill = 0; _spo2Idx = 0;
    Serial.println(F("[MAX30102] OK"));
    return true;
}

void MAX30102::processSample(float red, float ir) {
    uint32_t ms = millis();
    _irDcSlow += 0.05f * (ir - _irDcSlow);
    if (_irDcSlow > 5000.0f && _irDcSlow < 240000.0f) {
        _finger = true;
        _fingerOffMs = 0;
    } else {
        if (_finger && _fingerOffMs == 0) _fingerOffMs = ms;
        if (_fingerOffMs && ms - _fingerOffMs > 1500) {
            _finger = false;
            _hrValid = false;
            _spo2Valid = false;
            _ibiCount = 0;
            _spo2Fill = 0;
            _spo2Avg = 0;
            _badRatio = 0;
            _peakEnv = 0;
            _aboveThr = false;
        }
    }
    if (_finger) {
        float ac = ir - _dcEstIR;
        _dcEstIR += 0.90f * ac;
        _lp1 += 0.30f * (ac - _lp1);
        _lp2 += 0.30f * (_lp1 - _lp2);
        float v = _lp1;
        float av = v < 0 ? -v : v;
        if (av > _peakEnv) _peakEnv = av; else _peakEnv *= 0.985f;
        float thr = _peakEnv * 0.30f;
        if (!_aboveThr && v > thr && thr > 2.0f && (_lastBeatMs == 0 || ms - _lastBeatMs > 300)) {
            _aboveThr = true;
            if (_lastBeatMs != 0) {
                uint32_t ibi = ms - _lastBeatMs;
                if (ibi > 300 && ibi < 2200) {
                    if (_ibiCount >= 3) {
                        uint32_t s[7];
                        memcpy(s, _ibis, _ibiCount * sizeof(uint32_t));
                        for (int i = 0; i < _ibiCount; i++)
                            for (int j = i + 1; j < _ibiCount; j++)
                                if (s[j] < s[i]) { uint32_t t = s[i]; s[i] = s[j]; s[j] = t; }
                        uint32_t med = s[_ibiCount / 2];
                        uint32_t diff = ibi > med ? ibi - med : med - ibi;
                        if (diff * 100 > med * 35) { _lastBeatMs = ms; }
                    } else {
                        if (_ibiCount < 7) _ibis[_ibiCount++] = ibi;
                        else { memmove(_ibis, _ibis + 1, 6 * sizeof(uint32_t)); _ibis[6] = ibi; }
                    }
                    if (_ibiCount >= 2) {
                        uint32_t s[7];
                        memcpy(s, _ibis, _ibiCount * sizeof(uint32_t));
                        for (int i = 0; i < _ibiCount; i++)
                            for (int j = i + 1; j < _ibiCount; j++)
                                if (s[j] < s[i]) { uint32_t t = s[i]; s[i] = s[j]; s[j] = t; }
                        uint32_t med = (_ibiCount == 2) ? (s[0] + s[1]) / 2 : s[_ibiCount / 2];
                        int newHr = (int)(60000UL / med);
                        if (newHr >= 40 && newHr <= 150) {
                            _hr = _hrValid ? (int)(_hr * 0.6f + newHr * 0.4f) : newHr;
                            _hrValid = true;
                        }
                    }
                }
            }
            _lastBeatMs = ms;
        } else if (_aboveThr && v < thr * 0.4f) {
            _aboveThr = false;
        }
        if (_hrValid && _lastBeatMs && ms - _lastBeatMs > 5000) {
            _hrValid = false;
            _ibiCount = 0;
        }
        _redBuf[_spo2Idx] = red;
        _irBuf[_spo2Idx] = ir;
        _spo2Idx = (_spo2Idx + 1) % BUF;
        if (_spo2Fill < BUF) _spo2Fill++;
        _spo2Counter++;
        if (_spo2Fill >= BUF && _spo2Counter >= 25) {
            _spo2Counter = 0;
            float mr = 0, mi = 0;
            for (int i = 0; i < BUF; i++) { mr += _redBuf[i]; mi += _irBuf[i]; }
            mr /= BUF; mi /= BUF;
            float ar = 0, ai = 0;
            for (int i = 0; i < BUF; i++) {
                float dr = _redBuf[i] - mr;
                float di = _irBuf[i] - mi;
                ar += dr * dr;
                ai += di * di;
            }
            ar = sqrtf(ar / BUF);
            ai = sqrtf(ai / BUF);
            if (mr > 1000.0f && mi > 1000.0f && ar > 15.0f && ai > 15.0f) {
                float R = (ar / mr) / (ai / mi);
                if (R > 0.3f && R < 3.5f) {
                    float s = -45.060f * R * R + 30.354f * R + 94.845f;
                    if (s > 60.0f && s <= 100.0f) {
                        _spo2Avg = _spo2Valid ? (_spo2Avg * 0.6f + s * 0.4f) : s;
                        _spo2 = (int)(_spo2Avg + 0.5f);
                        _spo2Valid = true;
                        _badRatio = 0;
                    }
                } else if (++_badRatio > 3) {
                    _spo2Valid = false;
                    _spo2Avg = 0;
                }
            } else if (++_badRatio > 3) {
                _spo2Valid = false;
                _spo2Avg = 0;
            }
        }
    }
}

bool MAX30102::read(float &sp_o2, float &hr_bpm) {
    static uint32_t lastDbg = 0;
    uint32_t nowMs = millis();
    if (nowMs - lastDbg > 1500) {
        lastDbg = nowMs;
        Serial.printf("[MAXDBG] finger=%d irDc=%0.0f peak=%0.0f hr=%d valid=%d ibi=%d\n",
                      _finger, _irDcSlow, _peakEnv, _hr, _hrValid, _ibiCount);
    }
    uint8_t wr = 0, rd = 0;
    if (!readReg(REG_WR_PTR, wr) || !readReg(REG_RD_PTR, rd)) return false;
    uint8_t n = (uint8_t)((wr - rd) & 0x1F);
    if (n == 0) return false;
    if (n > 8) n = 8;
    uint8_t buf[6 * 8];
    if (!readFifo(buf, (uint8_t)(n * 6))) return false;
    for (uint8_t i = 0; i < n; i++) {
        uint32_t red = ((uint32_t)(buf[i * 6] & 0x03) << 16) | ((uint32_t)buf[i * 6 + 1] << 8) | (uint32_t)buf[i * 6 + 2];
        uint32_t ir = ((uint32_t)(buf[i * 6 + 3] & 0x03) << 16) | ((uint32_t)buf[i * 6 + 4] << 8) | (uint32_t)buf[i * 6 + 5];
        processSample((float)red, (float)ir);
    }
    sp_o2 = _spo2Valid ? (float)_spo2 : NAN;
    hr_bpm = _hrValid ? (float)_hr : NAN;
    return _spo2Valid || _hrValid;
}
