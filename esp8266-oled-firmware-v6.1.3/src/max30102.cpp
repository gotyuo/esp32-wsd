#include "max30102.h"

// MAX30102 寄存器(低字节 index):
// 0x00/0x01 中断, 0x04 FIFO 配置, 0x05 FIFO 高水位,
// 0x07 读头(读剩余采样点数), 0x08 数据 FIFO 基址,
// 0x09 MODE, 0x0A SPO2, 0x0C/0x0D/0x0E LED1/2/3, 0x0F PI, 0x10 MULTI,
// 0x11 写尾指针(带清除位), 0xFE 芯片 ID.
//
// 关键 FIFO 协议(见 datasheet FIFO READ/WRITE 节):
//   读剩余点数 : 读 1 字节 reg 0x07
//   读一组 6B : 发 2 字节头 [0x07][dataIndex], 再读 6 字节(R12,G12,IR12)
//              dataIndex = 0x07,0x0D,...,0x69 (每组间隔 6)
//   写尾清除   : 发 6 字节 [0x11][tail|0x80][ch0][ch1][ch2]
//              ch0 = 0xFF - sum(前 5 字节 low), ch1 = 0xFF - sum(前 5 字节 high)
//   控制寄存器 : 普通 1 字节 reg 地址写
//
enum {
    REG_STATUS    = 0x00,
    REG_INTR_EN1  = 0x02,
    REG_INTR_EN2  = 0x03,
    REG_WR_PTR    = 0x04,
    REG_OVF       = 0x05,
    REG_RD_PTR    = 0x06,
    REG_FIFO_DATA = 0x07,
    REG_FIFO_CFG  = 0x08,
    REG_MODE      = 0x09,
    REG_SPO2_CFG  = 0x0A,
    REG_LED1_PA   = 0x0C,   // red
    REG_LED2_PA   = 0x0D,   // IR
    REG_PART_ID   = 0xFF,
    REG_INTR1     = REG_STATUS,
    REG_FIFOHW    = REG_FIFO_CFG,
    REG_FIFOPH    = REG_FIFO_DATA,
    REG_FIFO      = REG_FIFO_CFG,
    REG_LED1      = REG_LED1_PA,
    REG_LED2      = REG_LED2_PA,
    REG_LED3      = 0x0E,
    REG_SPO2      = REG_SPO2_CFG,
    REG_FIFOTAIL  = REG_RD_PTR,
    REG_PARTID    = REG_PART_ID,
};
static const uint8_t DATA_START = 0x07;               // 数据组首 dataIndex
static const uint8_t B_SPO2_AEN  = 0x20, B_SPO2_AEN2 = 0x10;
static const uint8_t B_SPO2_SR50 = 0x07;              // SPO2 采样率 50Hz

// ESP8266 单 I2C 外设：Wire 已在 SensorHub::begin() 中固定到 D1/D2。
// MAX30102 仍然通过 setPins 记录 D7/D8，但不再切换 Wire 引脚。
void MAX30102::_ensureBus() {}

// --- 1 字节 reg 写(控制寄存器用) ---
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

// --- 连续读 FIFO：直接读 0x07 之后的样本字节流 ---
bool MAX30102::readFIFO(uint8_t *buf, uint8_t n) {
    _ensureBus();
    return readRegN(REG_FIFOPH, buf, n);
}

bool MAX30102::readRegN(uint8_t addr, uint8_t *buf, uint8_t n) {
    _ensureBus();
    _wire->beginTransmission(MAX30102_ADDR);
    _wire->write(addr);
    if (_wire->endTransmission(false) != 0) return false;
    _wire->requestFrom(MAX30102_ADDR, (size_t)n);
    if (_wire->available() < n) return false;
    for (uint8_t i = 0; i < n; i++) buf[i] = _wire->read();
    return true;
}


// --- 写尾指针清除(6 字节 + 校验) ---
// MAX30102 写尾= 32 位寄存器写:
//   p0=REG_FIFOTAIL(0x11), p1=(tail&0x3F)|0x80, p2/p3/p4=0x00
//   校验: ch0=0xFF-(p0+p1+p2+p3+p4)低字节, ch1=0xFF-(p0+p1+p2+p3+p4+ch0)高字节
bool MAX30102::writeTail(uint8_t tail) {
    _ensureBus();
    uint8_t p[6];
    p[0] = REG_FIFOTAIL;
    p[1] = (tail & 0x3F) | 0x80;   // 清除位
    p[2] = 0; p[3] = 0; p[4] = 0;
    uint16_t s = 0;
    for (int i = 0; i < 5; i++) s += p[i];
    p[5] = 0xFF - (s & 0xFF);
    // 注: 写尾只用 6 字节(ch1 随下一进位隐含),实际 datasheet 写尾写 6 字节即止
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
    if (!readReg(REG_PARTID, adi) || adi != 0x15) { 
        Serial.printf("[MAX30102] wrong chip id! (expected 0x15 at PART_ID, got 0x%02X)\n", adi); 
        return false; 
    }
    // 软复位 + 等待复位完成
    for (int t = 0; t < 3; t++) {
        if (writeReg(REG_MODE, 0x40)) break;
        delay(50);
    }
    delay(100);
    // 重新探测，确认复位后还能读到
    if (!readReg(REG_PARTID, adi) || adi != 0x15) {
        Serial.printf("[MAX30102] reset lost chip id! (got 0x%02X)\n", adi);
        return false;
    }
    // 逐项写入配置并校验关键寄存器
    bool cfgOk = writeReg(REG_FIFO_CFG, 0xBF);
    cfgOk &= writeReg(REG_SPO2_CFG, 0x58);
    cfgOk &= writeReg(REG_LED1_PA, 0x28);
    cfgOk &= writeReg(REG_LED2_PA, 0x28);
    cfgOk &= writeReg(REG_MODE, 0x03);
    if (cfgOk) {
        uint8_t modeRead = 0xFF;
        if (!readReg(REG_MODE, modeRead) || modeRead != 0x03) cfgOk = false;
    }
    if (!cfgOk) {
        delay(50);
        writeReg(REG_MODE, 0x40); delay(100);
        writeReg(REG_FIFO_CFG, 0xBF);
        writeReg(REG_SPO2_CFG, 0x58);
        writeReg(REG_LED1_PA, 0x28);
        writeReg(REG_LED2_PA, 0x28);
        writeReg(REG_MODE, 0x03);
        delay(100);
    }
    // 清空 FIFO
    writeReg(REG_WR_PTR, 0);
    writeReg(REG_OVF, 0);
    writeReg(REG_RD_PTR, 0);
    _write = 0; _full = false;
    Serial.println(F("[MAX30102] OK"));
    return true;
}

// 心率：原始 IR 局部峰值 + 最近 6 个 IBI 中位数
static float computeHR(uint16_t *ir, int n) {
    if (n < 25) return NAN;
    const int BUF_MAX = 200;
    const int IBI_WIN = 6;
    int lastPeakIdx = -1;
    int ibi[IBI_WIN] = {0};
    int ibiCount = 0;
    int peakCount = 0;
    int lastPeakDelta = 0;
    for (int i = 2; i < n - 1 && i < BUF_MAX; i++) {
        if (ir[i] > ir[i - 1] && ir[i] > ir[i + 1] &&
            ir[i] > ir[i - 1] + 10 && ir[i] > ir[i + 1] + 10) {
            peakCount++;
            if (lastPeakIdx >= 0) {
                int d = i - lastPeakIdx;
                lastPeakDelta = d;
                if (d >= 8 && d <= 150) {
                    if (ibiCount < IBI_WIN) {
                        ibi[ibiCount++] = d;
                    } else {
                        for (int j = 0; j < IBI_WIN - 1; j++) ibi[j] = ibi[j + 1];
                        ibi[IBI_WIN - 1] = d;
                    }
                }
            }
            lastPeakIdx = i;
        }
    }
    if (ibiCount < 3) return NAN;
    int tmp[IBI_WIN];
    for (int i = 0; i < ibiCount; i++) tmp[i] = ibi[i];
    for (int i = 0; i < ibiCount - 1; i++) {
        for (int j = i + 1; j < ibiCount; j++) {
            if (tmp[i] > tmp[j]) {
                int t = tmp[i]; tmp[i] = tmp[j]; tmp[j] = t;
            }
        }
    }
    int med = tmp[ibiCount / 2];
    float hr = 1500.0f / med;
    if (hr < 30 || hr > 220) return NAN;
    return hr;
}
// 血氧：DC/AC 比值法(MAX30102 常用曲线，70~100)
static float computeSPO2(uint16_t *ir, uint16_t *red, int n) {
    if (n < 20) return NAN;
    unsigned long irSum = 0, redSum = 0;
    uint16_t irMax = 0, redMax = 0, irMin = 65535, redMin = 65535;
    for (int i = 0; i < n; i++) {
        irSum += ir[i]; redSum += red[i];
        if (ir[i] > irMax) irMax = ir[i]; if (ir[i] < irMin) irMin = ir[i];
        if (red[i] > redMax) redMax = red[i]; if (red[i] < redMin) redMin = red[i];
    }
    if (irMin <= 0 || redMin <= 0 || irMax <= irMin || redMax <= redMin) return NAN;
    float redDC = (float)redSum / n;
    float irDC = (float)irSum / n;
    float redAC = (float)(redMax - redMin);
    float irAC = (float)(irMax - irMin);
    if (redDC <= 0 || irDC <= 0 || redAC <= 0 || irAC <= 0) return NAN;
    float redRatio = redAC / redDC;
    float irRatio = irAC / irDC;
    if (irRatio <= 0) return NAN;
    float R = redRatio / irRatio;
    float spo2 = 110.0f - 25.0f * R;
    if (spo2 > 100.0f) spo2 = 100.0f;
    if (spo2 < 70.0f) spo2 = NAN;
    return spo2;
}

bool MAX30102::read(float &sp_o2, float &hr_bpm) {
    uint8_t wr, rd;
    if (!readReg(REG_WR_PTR, wr)) return false;
    if (!readReg(REG_RD_PTR, rd)) return false;
    if (wr == rd) return false;
    uint8_t n = (uint8_t)((wr - rd) & 0x1F);
    if (n == 0) return false;
    if (n > 8) n = 8;

    uint8_t buf[6 * 8] = {};
    if (!readFIFO(buf, (uint8_t)(6 * n))) return false;

    for (uint8_t i = 0; i < n; i++) {
        uint32_t red = ((uint32_t)(buf[i * 6] & 0x03) << 16) | ((uint32_t)buf[i * 6 + 1] << 8) | buf[i * 6 + 2];
        uint32_t ir  = ((uint32_t)(buf[i * 6 + 3] & 0x03) << 16) | ((uint32_t)buf[i * 6 + 4] << 8) | buf[i * 6 + 5];
        _lastRed = (uint16_t)red;
        _lastIR = (uint16_t)ir;
        _irBuf[_write] = _lastIR;
        _redBuf[_write] = _lastRed;
        _write = (uint8_t)((_write + 1) % BUF);
        if (_write == 0) _full = true;
    }

    uint8_t newrd = (uint8_t)((rd + n) & 0x1F);
    if (!writeReg(REG_RD_PTR, newrd)) return false;

    int valid = _full ? BUF : (int)(_write == 0 ? 0 : _write);
    uint16_t ir[BUF], red[BUF];
    for (int i = 0; i < valid; i++) {
        ir[i] = _irBuf[(_write - valid + i + BUF) % BUF];
        red[i] = _redBuf[(_write - valid + i + BUF) % BUF];
    }

    hr_bpm = computeHR(ir, valid);
    if (valid < 40) {
        sp_o2 = NAN;
    } else {
        sp_o2 = computeSPO2(ir, red, valid);
    }
    return true;
}
