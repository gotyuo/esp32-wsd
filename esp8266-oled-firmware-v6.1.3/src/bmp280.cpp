#include "bmp280.h"

#define BMP280_REG_ID     0xD0
#define BMP280_REG_RESET  0xE0
#define BMP280_REG_STATUS 0xF3
#define BMP280_REG_CTRL   0xF4
#define BMP280_REG_CONFIG 0xF5
#define BMP280_REG_PRESS  0xF7
#define BMP280_REG_TEMP   0xFA
#define BMP280_CHIP_ID    0x58

bool BMP280::begin(TwoWire *wire) {
    _wire = wire;
    // 探测两个可能的 I2C 地址
    for (uint8_t addr : { (uint8_t)0x76, (uint8_t)0x77 }) {
        _addr = addr;
        _wire->beginTransmission(_addr);
        if (_wire->endTransmission() == 0) break;
        _addr = 0;
    }
    if (_addr == 0) return false;

    uint8_t id = 0;
    if (!readReg(BMP280_REG_ID, &id, 1)) return false;
    if (id != BMP280_CHIP_ID && id != 0x56 && id != 0x57) return false;

    // 软复位
    writeReg(BMP280_REG_RESET, 0xB6);
    delay(10);

    readCalibration();
    // config: IIR 滤波 x4
    writeReg(BMP280_REG_CONFIG, 0x08);
    return true;
}

bool BMP280::read(float &temp_c, float &pres_hpa) {
    // 强制模式：温度 x2 过采样、气压 x16 过采样，触发一次测量
    // osrs_t=010 osrs_p=101 mode=01  -> 0b01010101 = 0x55
    if (!writeReg(BMP280_REG_CTRL, 0x55)) return false;
    delay(20);

    // 等待测量完成（status bit3 measuring）
    uint8_t st = 0;
    for (int i = 0; i < 20; i++) {
        readReg(BMP280_REG_STATUS, &st, 1);
        if (!(st & 0x08)) break;
        delay(2);
    }

    int32_t adc_T = read24(BMP280_REG_TEMP);
    int32_t adc_P = read24(BMP280_REG_PRESS);
    if (adc_T == 0 || adc_T == 0x80000) return false;

    int32_t T = compTemp(adc_T);          // 0.01 ℃
    temp_c = T / 100.0f;

    uint32_t P = compPres(adc_P);         // Pa
    pres_hpa = P / 100.0f;
    return true;
}

// ---------------- 补偿公式（数据手册 32 位整数版） ----------------
int32_t BMP280::compTemp(int32_t adc_T) {
    int32_t var1, var2, T;
    var1 = ((((adc_T >> 3) - ((int32_t)_T1 << 1))) * ((int32_t)_T2)) >> 11;
    var2 = (((((adc_T >> 4) - ((int32_t)_T1)) * ((adc_T >> 4) - ((int32_t)_T1))) >> 12) *
            ((int32_t)_T3)) >> 14;
    _t_fine = var1 + var2;
    T = (_t_fine * 5 + 128) >> 8;
    return T;
}

uint32_t BMP280::compPres(int32_t adc_P) {
    int32_t var1, var2;
    uint32_t p;
    var1 = (((int32_t)_t_fine) >> 1) - (int32_t)64000;
    var2 = (((var1 >> 2) * (var1 >> 2)) >> 11) * ((int32_t)_P6);
    var2 = var2 + ((var1 * ((int32_t)_P5)) << 1);
    var2 = (var2 >> 2) + (((int32_t)_P4) << 16);
    var1 = (((_P3 * (((var1 >> 2) * (var1 >> 2)) >> 13)) >> 3) +
            ((((int32_t)_P2) * var1) >> 1)) >> 18;
    var1 = ((((32768 + var1)) * ((int32_t)_P1)) >> 15);
    if (var1 == 0) return 0;   // 防止除零
    p = (((uint32_t)(((int32_t)1048576) - adc_P) - (var2 >> 12))) * 3125;
    if (p < 0x80000000) p = (p << 1) / ((uint32_t)var1);
    else                p = (p / (uint32_t)var1) * 2;
    var1 = (((int32_t)_P9) * ((int32_t)(((p >> 3) * (p >> 3)) >> 13))) >> 12;
    var2 = (((int32_t)(p >> 2)) * ((int32_t)_P8)) >> 13;
    p = (uint32_t)((int32_t)p + ((var1 + var2 + _P7) >> 4));
    return p;
}

// ---------------- 寄存器读写 ----------------
bool BMP280::readReg(uint8_t reg, uint8_t *buf, size_t len) {
    _wire->beginTransmission(_addr);
    _wire->write(reg);
    if (_wire->endTransmission(false) != 0) return false;
    size_t n = _wire->requestFrom(_addr, len);
    if (n != len) return false;
    for (size_t i = 0; i < len; i++) buf[i] = _wire->read();
    return true;
}

bool BMP280::writeReg(uint8_t reg, uint8_t val) {
    _wire->beginTransmission(_addr);
    _wire->write(reg);
    _wire->write(val);
    return _wire->endTransmission() == 0;
}

uint16_t BMP280::readU16(uint8_t reg) {
    uint8_t b[2];
    readReg(reg, b, 2);
    return (uint16_t)b[0] | ((uint16_t)b[1] << 8);
}

int16_t BMP280::readS16(uint8_t reg) {
    return (int16_t)readU16(reg);
}

int32_t BMP280::read24(uint8_t reg) {
    uint8_t b[3];
    if (!readReg(reg, b, 3)) return 0;
    return ((int32_t)b[0] << 12) | ((int32_t)b[1] << 4) | (b[2] >> 4);
}

void BMP280::readCalibration() {
    _T1 = readU16(0x88); _T2 = readS16(0x8A); _T3 = readS16(0x8C);
    _P1 = readU16(0x8E); _P2 = readS16(0x90); _P3 = readS16(0x92);
    _P4 = readS16(0x94); _P5 = readS16(0x96); _P6 = readS16(0x98);
    _P7 = readS16(0x9A); _P8 = readS16(0x9C); _P9 = readS16(0x9E);
}
