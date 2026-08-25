#include "ssd1306.h"
#include "font5x7.h"
#include <Wire.h>

static uint8_t g_framebuf[OLED_W][OLED_H / 8]; // 1024 bytes, 128x64 in pages

// SSD1306 / SSD1315 标准启动序列
bool Ssd1306::begin(uint8_t scl, uint8_t sda, uint8_t addr) {
    return begin(scl, sda, addr, &Wire);
}
bool Ssd1306::begin(uint8_t scl, uint8_t sda, uint8_t addr, TwoWire *wire) {
    if (!wire) return false;
    _wire = wire;
    _addr = addr;
    _wire->begin(sda, scl);
    _wire->setClock(400000);
    delay(60);

    // 关显示,避免初始化闪烁
    cmd(C_DISPLAY_OFF);
    cmd(C_SET_DISPLAY_CLK);   cmd(0x80);      // 分频 100Hz
    cmd(C_SET_MULTIPLEX);     cmd(63);        // 1/64 duty
    cmd(C_SET_OFFSET);        cmd(0);         // 无偏移
    cmd(C_SET_START_LINE);                     // start line 0
    cmd(C_SET_CONTRAST_A);    cmd(0xCF);      // 对比度
    cmd(C_ENTIRE_DISP);                       // 按 RAM 显示
    cmd(C_NORMAL_DISP);                       // 正常(非反色)
    cmd(C_SEG_REMAP_HI);                      // 0xA1 段重映射
    cmd(C_COM_REMAPPED);                      // 0xC8 COM 反向
    cmd(C_SET_COM_PIN);     cmd(0x12);        // 段引脚配置
    cmd(C_SET_PRECHARGE);   cmd(0xF1);
    cmd(C_SET_VCOM_DESELECT); cmd(0x40);
    cmd(C_MEMORY_ADDR);     cmd(0x00);        // 水平寻址模式
    cmd(C_CHARGE_PUMP);     cmd(0x14);        // 升压使能
    cmd(C_DISPLAY_ON);

    clear();
    _ok = true;
    return _ok;
}

void Ssd1306::i2c_write(uint8_t mode, const uint8_t *buf, uint16_t len) {
    _wire->beginTransmission(_addr);
    _wire->write(mode);
    if (buf && len) _wire->write(buf, len);
    _wire->endTransmission(true);
}

void Ssd1306::cmd(uint8_t b) {
    _wire->beginTransmission(_addr);
    _wire->write(OLED_MODE_CMD);
    _wire->write(b);
    _wire->endTransmission(true);
}

void Ssd1306::data(const uint8_t *b, uint16_t len) {
    i2c_write(OLED_MODE_DATA, b, len);
}

// 把整屏帧缓冲一次推送到屏幕(最高效的刷新方式)
void Ssd1306::flush() {
    cmd(C_COLUMN_ADDR); cmd(0); cmd(OLED_W - 1);
    cmd(C_PAGE_ADDR);   cmd(0); cmd((OLED_H / 8) - 1);
    _wire->beginTransmission(_addr);
    _wire->write(OLED_MODE_DATA);
    for (int p = 0; p < (OLED_H / 8); p++) {
        _wire->write((uint8_t *)g_framebuf[p], OLED_W);
    }
    _wire->endTransmission(true);
}

void Ssd1306::clear() {
    for (int p = 0; p < (OLED_H / 8); p++)
        for (int x = 0; x < OLED_W; x++)
            g_framebuf[p][x] = 0;
}

void Ssd1306::drawPixel(int16_t x, int16_t y, uint8_t c) {
    if (x < 0 || x >= OLED_W || y < 0 || y >= OLED_H) return;
    uint8_t p = y / 8;
    uint8_t mask = 1 << (y & 0x07);
    if (c & 1) g_framebuf[p][x] |= mask;
    else       g_framebuf[p][x] &= ~mask;
}

void Ssd1306::drawLineH(int16_t x, int16_t y, int16_t w, uint8_t c) {
    if (y < 0 || y >= OLED_H) return;
    int16_t xe = x + w - 1;
    if (xe >= OLED_W) xe = OLED_W - 1;
    if (x < 0) { w += x; x = 0; xe = x + w - 1; }
    if (w <= 0) return;
    uint8_t p = y / 8;
    uint8_t mask = 1 << (y & 0x07);
    for (int xi = x; xi <= xe; xi++) {
        if (c & 1) g_framebuf[p][xi] |= mask;
        else       g_framebuf[p][xi] &= ~mask;
    }
}

void Ssd1306::drawLineV(int16_t x, int16_t y, int16_t h, uint8_t c) {
    for (int yy = 0; yy < h; yy++) drawPixel(x, y + yy, c);
}

void Ssd1306::drawRect(int16_t x, int16_t y, int16_t w, int16_t h, uint8_t c) {
    drawLineH(x, y, w, c);
    drawLineH(x, y + h - 1, w, c);
    drawLineV(x, y, h, c);
    drawLineV(x + w - 1, y, h, c);
}

void Ssd1306::fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint8_t c) {
    for (int yy = 0; yy < h; yy++) drawLineH(x, y + yy, w, c);
}

void Ssd1306::drawChar(int16_t x, int16_t y, char ch, uint8_t c) {
    if (x + 6 > OLED_W || y + 8 > OLED_H) return;
    if (ch < 32 || ch > 126) ch = ' ';
    uint16_t idx = (uint16_t)(ch - 32) * 5;
    for (int col = 0; col < 5; col++) {
        uint8_t glyph = FONT5X7[idx + col];
        for (int row = 0; row < 8; row++) {
            uint8_t on = (glyph & (1 << row)) ? (c & 1) : 0;
            if (on) drawPixel(x + col, y + row, 1);
        }
    }
}

void Ssd1306::drawString(int16_t x, int16_t y, const char *s, uint8_t c) {
    int16_t cx = x;
    while (*s && cx + 6 <= OLED_W) {
        drawChar(cx, y, *s++, c);
        cx += 6;
    }
}

void Ssd1306::drawNum(int16_t x, int16_t y, int32_t v, uint8_t c) {
    char buf[14];
    sprintf(buf, "%ld", (long)v);
    drawString(x, y, buf, c);
}

void Ssd1306::drawNumFP(int16_t x, int16_t y, float v, uint8_t frac, uint8_t c) {
    char buf[16], fmt[8];
    sprintf(fmt, "%%.%df", frac);
    sprintf(buf, fmt, v);
    drawString(x, y, buf, c);
}

// 信号强度条(0-4 格)
void Ssd1306::drawWifiBars(int16_t x, int16_t y, uint8_t bars) {
    if (bars > 4) bars = 4;
    for (int i = 0; i < 4; i++) {
        uint8_t h = 2 + i * 2;          // 2/4/6/8
        int16_t bx = x + i * 4;
        fillRect(bx, y + (8 - h), 3, h, (i < bars));
        drawLineH(bx, y + 8 - 1, 3, (i < bars));
        drawLineV(bx, y + (8 - h), h, (i < bars));
        drawLineV(bx + 3, y + (8 - h), h, (i < bars));
    }
}
