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
    REG_INTR1    = 0x00, REG_INTR2    = 0x01,
    REG_FIFOG    = 0x04, REG_FIFOHW   = 0x05,
    REG_FIFOPH   = 0x07,        // 读头 / 数据头
    REG_FIFO     = 0x08,
    REG_MODE     = 0x09, REG_SPO2     = 0x0A,
    REG_LED1     = 0x0C, REG_LED2     = 0x0D, REG_LED3 = 0x0E,
    REG_PI       = 0x0F, REG_MULT     = 0x10,
    REG_FIFOTAIL = 0x11,
    REG_ADI      = 0xFE,
};
static const uint8_t DATA_START = 0x07;               // 数据组首 dataIndex
static const uint8_t B_SPO2_AEN  = 0x20, B_SPO2_AEN2 = 0x10;
static const uint8_t B_SPO2_SR50 = 0x07;              // SPO2 采样率 50Hz

// --- ESP8266 单总线时分复用：切到目标引脚 ---
void MAX30102::_ensureBus() {
    if (_sda >= 0 && _scl >= 0 && _wire) {
        _wire->begin(_sda, _scl);
    }
}

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

// --- 读一组 6 字节(1 个采样点)：2 字节头 [0x07][dataIndex] ---
uint32_t MAX30102::read32(uint8_t dataIndex) {
    _ensureBus();
    uint8_t head[2] = {REG_FIFOPH, dataIndex};
    uint8_t p[6];
    _wire->beginTransmission(MAX30102_ADDR);
    _wire->write(head, 2);
    if (_wire->endTransmission(false) != 0) return 0;
    _wire->requestFrom(MAX30102_ADDR, (size_t)6);
    if (_wire->available() < 6) return 0;
    for (int i = 0; i < 6; i++) p[i] = _wire->read();
    // R(12b) | G(12b) | IR(12b)，这里只拆 IR/Red
    return ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | p[2];
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
    if (!readReg(REG_ADI, adi) || adi != 0x11) { Serial.println(F("[MAX30102] wrong chip id!")); return false; }
    // 软复位
    writeReg(REG_MODE, 0x80); delay(5);
    // 清空 FIFO
    writeReg(REG_INTR1, 0xC0); writeReg(REG_INTR2, 0x80);
    writeReg(REG_FIFOHW, 0x0F);
    uint8_t tail; readReg(REG_FIFOTAIL, tail);
    writeTail(tail & 0x3F); delay(10);
    uint8_t head; readReg(REG_FIFOPH, head);
    writeTail(head & 0x3F); delay(10);
    // 50Hz 采样，红光+红外，ADCR 1250µA
    writeReg(REG_SPO2, B_SPO2_SR50 | B_SPO2_AEN | B_SPO2_AEN2);
    writeReg(REG_LED1, 0x1F);     // 红外 1250µA
    writeReg(REG_LED2, 0x1F);     // 红光 1250µA
    writeReg(REG_LED3, 0x00);     // 绿光关
    writeReg(REG_MODE, 0x03);     // 红光+红外 连续
    delay(300);
    _write = 0; _full = false;
    Serial.println(F("[MAX30102] OK"));
    return true;
}

// 心率：相邻峰值间距(50Hz 采样)
static float computeHR(uint16_t *ir, int n) {
    if (n < 80) return NAN;
    int bestN = 0; float bestD = 0;
    for (int i = 2; i < n - 20 && i < 4000; i++) {
        if (ir[i] > ir[i-1] && ir[i] > ir[i+1] && ir[i] > ir[i-1] + 40) {
            int next = -1;
            for (int j = i + 15; j < i + 120 && j < n; j++) {
                if (ir[j] > ir[j-1] && ir[j] > ir[j+1] && ir[j] > ir[j-1] + 40) { next = j; break; }
            }
            if (next > 0) {
                int d = next - i;
                if (d >= 10 && d <= 90) { bestD += d; bestN++; }
            }
        }
    }
    if (bestN < 3 || bestD <= 0) return NAN;
    float hr = 3000.0f / bestD / bestN;
    if (hr < 30 || hr > 220) return NAN;
    return hr;
}
// 血氧：DC 比值法(经验曲线, 70~100)
static float computeSPO2(uint16_t *ir, uint16_t *red, int n) {
    float irMax = 0, redMax = 0, irMin = 1<<30, redMin = 1<<30;
    for (int i = 0; i < n; i++) {
        if (ir[i] > irMax) irMax = ir[i]; if (ir[i] < irMin) irMin = ir[i];
        if (red[i] > redMax) redMax = red[i]; if (red[i] < redMin) redMin = red[i];
    }
    if (irMin <= 0 || redMin <= 0) return NAN;
    float ratioA = (redMax - redMin) / (redMax + redMin + 1);
    float ratioB = (irMax - irMin) / (irMax + irMin + 1);
    if (ratioB <= 0 || ratioA <= 0) return NAN;
    float R = ratioA / ratioB;
    float spo2 = 104.0f - 17.0f * R;
    if (spo2 > 100) spo2 = 100;
    if (spo2 < 70) spo2 = NAN;
    return spo2;
}

bool MAX30102::read(float &sp_o2, float &hr_bpm) {
    // 读剩余采样点数
    uint8_t ph;
    if (!readReg(REG_FIFOPH, ph)) return false;
    int toRead = (ph & 0x3F);
    if (toRead == 0) return false;
    if (toRead > 32) toRead = 32;
    // 连续读 toRead 组,每 6 字节 1 点
    for (int k = 0; k < toRead; k++) {
        uint8_t di = DATA_START + k * 6;
        uint32_t raw = read32(di);
        if (raw == 0) continue;
        _irBuf[_write]  = (uint16_t)((raw >> 8) & 0x03FF);
        _redBuf[_write] = (uint16_t)(raw & 0x03FF);
        _write = (_write + 1) % BUF;
        if (_write == 0) _full = true;
    }
    int valid = _full ? BUF : (_write == 0 ? 0 : _write);
    if (valid < 40) return false;
    uint16_t ir[BUF], red[BUF];
    for (int i = 0; i < valid; i++) {
        ir[i]  = _irBuf[(_write - valid + i + BUF) % BUF];
        red[i] = _redBuf[(_write - valid + i + BUF) % BUF];
    }
    hr_bpm = computeHR(ir, valid);
    sp_o2  = computeSPO2(ir, red, valid);
    // 回写尾指针清除(6 字节组的整数倍)
    int clearN = (valid / 6) * 6;
    if (clearN > 0) {
        uint8_t tailCur;
        if (readReg(REG_FIFOTAIL, tailCur)) {
            uint8_t newTail = (tailCur & 0x3F) + clearN;
            newTail &= 0x3F;
            writeTail(newTail);
        }
    }
    return true;
}
