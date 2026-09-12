#pragma once
// ============================================================
// ST7735S 0.96" 160x80 TFT 驱动 (软件 SPI, bit-bang)
// ESP8266 硬件 SPI 被 Flash 占用, 只能软件模拟
// ============================================================
#include <Arduino.h>
#include "font5x7.h"
#include "font8x16.h"
#include "font_cn.h"

// 常用 RGB565 颜色
#define C_BLACK  0x0000
#define C_WHITE  0xFFFF
#define C_RED    0xF800
#define C_GREEN  0x07E0
#define C_BLUE   0x001F
#define C_YELLOW 0xFFE0
#define C_CYAN   0x07FF
#define C_ORANGE 0xFD20
#define C_GRAY   0x8410

class ST7735 {
public:
    void begin(int8_t cs, int8_t dc, int8_t rst, int8_t mosi, int8_t sck);
    void fillScreen(uint16_t color);
    void fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color);
    void drawPixel(int16_t x, int16_t y, uint16_t color);
    void setCursor(int16_t x, int16_t y);
    void setTextColor(uint16_t c) { _fg = c; }
    void setTextBackground(uint16_t c) { _bg = c; }
    void setTextSize(uint8_t s) { _size = s > 0 ? s : 1; }
    void print(const char *s);
    void print(const String &s) { print(s.c_str()); }
    void print(char c);
    void print(int v);
    void print(float v, int dec = 1);
    void drawChar(int16_t x, int16_t y, char c, uint16_t color, uint8_t size);
    void drawChar8x16(int16_t x, int16_t y, char c, uint16_t color);
    void drawChinese(int16_t x, int16_t y, uint8_t index, uint16_t color);

    int16_t width()  const { return 160; }
    int16_t height() const { return 80; }

private:
    int8_t  _cs, _dc, _rst, _mosi, _sck;
    int16_t _cx = 0, _cy = 0;
    uint16_t _fg = C_WHITE;
    uint16_t _bg = C_BLACK;
    uint8_t _size = 1;
    void _setWindow(int16_t x, int16_t y, int16_t w, int16_t h);
};