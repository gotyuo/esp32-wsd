// ============================================================
// ST7735S 驱动实现 — 软件 SPI (bit-bang)
// ⚠️ ESP8266 IntWDT 170ms: 写 25600 字节 ≈ 256ms 会 WDT reset
//    每写 100 字节 yield() 喂狗
// ============================================================
#include "st7735.h"

// --- 软件 SPI bit-bang (每 100 字节 yield 喂 IntWDT) ---
static void spiWrite(int8_t mosi, int8_t sck, uint8_t dat) {
    static uint16_t wdt_tick = 0;
    for (int8_t i = 7; i >= 0; i--) {
        digitalWrite(sck, LOW);
        digitalWrite(mosi, (dat & (1 << i)) ? HIGH : LOW);
        digitalWrite(sck, HIGH);
    }
    if (++wdt_tick >= 100) { wdt_tick = 0; yield(); }
}

// CS/DC 宏: 255 = 引脚未连接, 不操作
#define CS_L()  if (_cs < 255) digitalWrite(_cs, LOW)
#define CS_H()  if (_cs < 255) digitalWrite(_cs, HIGH)
#define DC_L()  if (_dc < 255) digitalWrite(_dc, LOW)
#define DC_H()  if (_dc < 255) digitalWrite(_dc, HIGH)
#define RST_L() if (_rst < 255) digitalWrite(_rst, LOW)
#define RST_H() if (_rst < 255) digitalWrite(_rst, HIGH)

void ST7735::begin(int8_t cs, int8_t dc, int8_t rst, int8_t mosi, int8_t sck) {
    _cs = cs; _dc = dc; _rst = rst; _mosi = mosi; _sck = sck;
    if (_cs < 255) pinMode(_cs, OUTPUT);
    if (_dc < 255) pinMode(_dc, OUTPUT);
    if (_rst < 255) pinMode(_rst, OUTPUT);
    pinMode(_mosi, OUTPUT);
    pinMode(_sck, OUTPUT);
    CS_H(); DC_H();
    digitalWrite(_sck, HIGH);
    digitalWrite(_mosi, HIGH);

    // 硬件复位
    RST_H(); delay(10); RST_L(); delay(15); RST_H(); delay(200);

    Serial.println(F("[TFT] Init..."));

    // 发送命令 (CS低 + DC低 = 命令模式)
    #define CMD(c)  { CS_L(); DC_L(); spiWrite(_mosi, _sck, (c)); DC_H(); CS_H(); }
    // 发送命令+数据
    #define CMDD(cmd, d, n) { \
        CS_L(); DC_L(); spiWrite(_mosi, _sck, (cmd)); DC_H(); \
        for (uint8_t _i = 0; _i < (n); _i++) spiWrite(_mosi, _sck, (d)[_i]); \
        CS_H(); }

    CMD(0x01); delay(120);   // SWRESET
    CMD(0x11); delay(120);   // SLPOUT

    { uint8_t d[] = {0x05,0x3C,0x3C}; CMDD(0xB1, d, 3); }
    { uint8_t d[] = {0x05,0x3C,0x3C}; CMDD(0xB2, d, 3); }
    { uint8_t d[] = {0x05,0x3C,0x3C,0x05,0x3C,0x3C}; CMDD(0xB3, d, 6); }
    { uint8_t d[] = {0x03}; CMDD(0xB4, d, 1); }
    { uint8_t d[] = {0xAB,0x0B,0x04}; CMDD(0xC0, d, 3); }
    { uint8_t d[] = {0xC5}; CMDD(0xC1, d, 1); }
    { uint8_t d[] = {0x0D,0x00}; CMDD(0xC2, d, 2); }
    { uint8_t d[] = {0x8D,0x6A}; CMDD(0xC3, d, 2); }
    { uint8_t d[] = {0x8D,0xEE}; CMDD(0xC4, d, 2); }
    { uint8_t d[] = {0x0F}; CMDD(0xC5, d, 1); }
    { uint8_t d[] = {0x07,0x0E,0x08,0x07,0x10,0x07,0x02,0x07,0x09,0x0F,0x25,0x36,0x00,0x08,0x04,0x10};
      CMDD(0xE0, d, 16); }
    { uint8_t d[] = {0x0A,0x0D,0x08,0x07,0x0F,0x07,0x02,0x07,0x09,0x0F,0x25,0x35,0x00,0x09,0x04,0x10};
      CMDD(0xE1, d, 16); }
    { uint8_t d[] = {0x80}; CMDD(0xFC, d, 1); }
    { uint8_t d[] = {0x05}; CMDD(0x3A, d, 1); }  // COLMOD 16bit
    { uint8_t d[] = {0xA8}; CMDD(0x36, d, 1); }  // MADCTL
    CMD(0x21);                    // INVON
    CMD(0x29); delay(10);         // DISPON

    Serial.println(F("[TFT] OK"));
}

// 设置显示窗口 (CASET + RASET + RAMWR)
void ST7735::_setWindow(int16_t x, int16_t y, int16_t w, int16_t h) {
    int16_t xs = x + 1, xe = x + w;
    int16_t ys = y + 26, ye = y + h + 25;
    // CASET
    CS_L(); DC_L(); spiWrite(_mosi, _sck, 0x2A); DC_H();
    spiWrite(_mosi, _sck, 0x00); spiWrite(_mosi, _sck, xs);
    spiWrite(_mosi, _sck, 0x00); spiWrite(_mosi, _sck, xe);
    CS_H();
    // RASET
    CS_L(); DC_L(); spiWrite(_mosi, _sck, 0x2B); DC_H();
    spiWrite(_mosi, _sck, 0x00); spiWrite(_mosi, _sck, ys);
    spiWrite(_mosi, _sck, 0x00); spiWrite(_mosi, _sck, ye);
    CS_H();
    // RAMWR (CS 保持低, 进入写入模式)
    CS_L(); DC_L(); spiWrite(_mosi, _sck, 0x2C); DC_H();
}

void ST7735::fillScreen(uint16_t color) {
    _setWindow(0, 0, 160, 80);
    CS_L(); DC_H();
    for (uint32_t i = 0; i < 160 * 80; i++) {
        spiWrite(_mosi, _sck, color >> 8);
        spiWrite(_mosi, _sck, color & 0xFF);
    }
    CS_H();
}

void ST7735::fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
    if (x >= 160 || y >= 80 || w <= 0 || h <= 0) return;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > 160) w = 160 - x;
    if (y + h > 80) h = 80 - y;
    _setWindow(x, y, w, h);
    CS_L(); DC_H();
    for (uint32_t i = 0; i < (uint32_t)w * h; i++) {
        spiWrite(_mosi, _sck, color >> 8);
        spiWrite(_mosi, _sck, color & 0xFF);
    }
    CS_H();
}

void ST7735::drawPixel(int16_t x, int16_t y, uint16_t color) {
    if (x < 0 || x >= 160 || y < 0 || y >= 80) return;
    _setWindow(x, y, 1, 1);
    CS_L(); DC_H();
    spiWrite(_mosi, _sck, color >> 8);
    spiWrite(_mosi, _sck, color & 0xFF);
    CS_H();
}

void ST7735::setCursor(int16_t x, int16_t y) { _cx = x; _cy = y; }

void ST7735::drawChar(int16_t x, int16_t y, char c, uint16_t color, uint8_t size) {
    if (c < 32 || c > 126) c = '?';
    int16_t cw = 6 * size, ch = 8 * size;
    _setWindow(x, y, cw, ch);
    CS_L(); DC_H();
    for (int8_t row = 0; row < 8; row++) {
        for (uint8_t sy = 0; sy < size; sy++) {
            for (int8_t col = 0; col < 6; col++) {
                bool lit = (col < 5) && ((FONT5X7[(c - 32) * 5 + col] >> row) & 1);
                uint16_t pc = lit ? color : _bg;
                for (uint8_t sx = 0; sx < size; sx++) {
                    spiWrite(_mosi, _sck, pc >> 8);
                    spiWrite(_mosi, _sck, pc & 0xFF);
                }
            }
        }
    }
    CS_H();
}

void ST7735::print(char c) {
    if (c == '\n') { _cy += 8 * _size; _cx = 0; return; }
    if (_cx + 6 * _size > 160) { _cx = 0; _cy += 8 * _size; }
    drawChar(_cx, _cy, c, _fg, _size);
    _cx += 6 * _size;
}
void ST7735::print(const char *s) { while (*s) print(*s++); }
void ST7735::print(int v) { char b[12]; snprintf(b, 12, "%d", v); print(b); }
void ST7735::print(float v, int dec) { char b[16]; snprintf(b, 16, "%.*f", dec, v); print(b); }

// 8x16 大号字符 (行主序: 每字符 16 字节, 每字节 8 像素)
void ST7735::drawChar8x16(int16_t x, int16_t y, char c, uint16_t color) {
    if (c < 32 || c > 126) c = '?';
    const unsigned char *glyph = ascii_1608[c - 32];
    _setWindow(x, y, 8, 16);
    CS_L(); DC_H();
    for (int8_t row = 0; row < 16; row++) {
        uint8_t line = glyph[row];
        for (int8_t col = 0; col < 8; col++) {
            bool lit = (line >> (7 - col)) & 1;
            uint16_t pc = lit ? color : _bg;
            spiWrite(_mosi, _sck, pc >> 8);
            spiWrite(_mosi, _sck, pc & 0xFF);
        }
    }
    CS_H();
}

// 16x16 中文字符 (行主序, 每行 2 字节)
void ST7735::drawChinese(int16_t x, int16_t y, uint8_t index, uint16_t color) {
    if (index > 4) return;
    const unsigned char *glyph = font_cn16x16[index];
    _setWindow(x, y, 16, 16);
    CS_L(); DC_H();
    for (int8_t row = 0; row < 16; row++) {
        uint8_t b0 = glyph[row * 2], b1 = glyph[row * 2 + 1];
        for (int8_t col = 0; col < 8; col++) {
            bool lit = (b0 >> (7 - col)) & 1;
            uint16_t pc = lit ? color : _bg;
            spiWrite(_mosi, _sck, pc >> 8);
            spiWrite(_mosi, _sck, pc & 0xFF);
        }
        for (int8_t col = 0; col < 8; col++) {
            bool lit = (b1 >> (7 - col)) & 1;
            uint16_t pc = lit ? color : _bg;
            spiWrite(_mosi, _sck, pc >> 8);
            spiWrite(_mosi, _sck, pc & 0xFF);
        }
    }
    CS_H();
}