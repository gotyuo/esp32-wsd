#include "aht20.h"

bool AHT20::begin(TwoWire *wire) {
    _wire = wire;
    _wire->beginTransmission(AHT20_ADDR);
    if (_wire->endTransmission() != 0) return false;   // 无应答
    delay(40);                                          // 上电稳定

    // 读状态字节，若未校准(bit3=0)则发送校准命令 0xBE
    _wire->requestFrom(AHT20_ADDR, (size_t)1);
    if (_wire->available()) {
        uint8_t st = _wire->read();
        if (!(st & 0x08)) {
            const uint8_t cal[3] = {0xBE, 0x08, 0x00};
            if (!sendCmd(cal, 3)) return false;
            delay(10);
        }
    }
    return true;
}

bool AHT20::softReset() {
    const uint8_t cmd[1] = {0xBA};
    if (!sendCmd(cmd, 1)) return false;
    delay(20);
    return true;
}

bool AHT20::sendCmd(const uint8_t *cmd, size_t len) {
    _wire->beginTransmission(AHT20_ADDR);
    _wire->write(cmd, len);
    return _wire->endTransmission() == 0;
}

bool AHT20::triggerAndRead(uint8_t *buf) {
    const uint8_t trig[3] = {0xAC, 0x33, 0x00};
    if (!sendCmd(trig, 3)) return false;
    delay(80);                                   // 等待测量完成
    size_t n = _wire->requestFrom(AHT20_ADDR, (size_t)7);
    if (n < 6) return false;
    for (size_t i = 0; i < 7 && _wire->available(); i++) buf[i] = _wire->read();
    return true;
}

bool AHT20::read(float &temp_c, float &hum_pct) {
    uint8_t buf[7] = {0};
    if (!triggerAndRead(buf)) {
        // 重试一次，含软复位
        softReset();
        delay(20);
        const uint8_t cal[3] = {0xBE, 0x08, 0x00};
        sendCmd(cal, 3);
        delay(10);
        if (!triggerAndRead(buf)) return false;
    }

    // bit7=1 表示忙
    if (buf[0] & 0x80) return false;

    uint32_t rawH = ((uint32_t)buf[1] << 12) | ((uint32_t)buf[2] << 4) | (buf[3] >> 4);
    uint32_t rawT = ((uint32_t)(buf[3] & 0x0F) << 16) | ((uint32_t)buf[4] << 8) | buf[5];

    hum_pct = (float)rawH * 100.0f / 1048576.0f;
    temp_c  = (float)rawT * 200.0f / 1048576.0f - 50.0f;

    if (hum_pct > 100.0f) hum_pct = 100.0f;
    if (hum_pct < 0.0f)   hum_pct = 0.0f;
    return true;
}
