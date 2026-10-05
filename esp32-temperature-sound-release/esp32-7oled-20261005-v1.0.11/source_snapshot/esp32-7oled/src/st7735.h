#pragma once
// ============================================================
// ST7735S 0.96" 160x80 IPS TFT 驱动（软件 SPI）
// ============================================================
#include <Arduino.h>

#include "font8x16.h"
#include "font5x7.h"
#include "font_cn.h"

// 常用 RGB565 颜色
#define C_BLACK   0x0000
#define C_WHITE   0xFFFF
#define C_RED     0xF800
#define C_GREEN   0x07E0
#define C_BLUE    0x001F
#define C_YELLOW  0xFFE0
#define C_CYAN    0x07FF
#define C_ORANGE  0xFD20
#define C_GRAY    0x8410

class ST7735 {
public:
    void begin(int8_t cs, int8_t dc, int8_t rst, int8_t mosi, int8_t sck);
    void fillScreen(uint16_t color);
    void fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color);
    void drawPixel(int16_t x, int16_t y, uint16_t color);
    void drawCircle(int16_t x0, int16_t y0, int16_t r, uint16_t color);
    void fillCircle(int16_t x0, int16_t y0, int16_t r, uint16_t color);
    // 文本
    void setCursor(int16_t x, int16_t y);
    void setTextColor(uint16_t c) { _fg = c; }
    void setTextBackground(uint16_t c) { _bg = c; }
    void setTextSize(uint8_t s) { _size = s > 0 ? s : 1; }
    void print(const char *s);
    void print(const String &s) { print(s.c_str()); }
    void print(char c);
    void print(int v);
    void print(float v, int dec = 1);
    // 直接绘制单个字符（不做换行/越界检查），供大字号精确摆放
    void drawChar(int16_t x, int16_t y, char c, uint16_t color, uint8_t size);
    // 绘制 8x16 大号字符
    void drawChar8x16(int16_t x, int16_t y, char c, uint16_t color);
    // 绘制 16x16 中文字符 (index: 0=温 1=湿 2=气)
    void drawChinese(int16_t x, int16_t y, uint8_t index, uint16_t color);

    int16_t width()  const { return _w; }
    int16_t height() const { return _h; }

private:
    void sendCmd(uint8_t cmd, const uint8_t *data, uint8_t len);
    void writeCmd(uint8_t c);
    void writeData(uint8_t d);
    void writeData16(uint16_t d);
    void setAddrWindow(int16_t x, int16_t y, int16_t w, int16_t h);
    void spiWrite(uint8_t b);
    void dcHigh() { digitalWrite(_dc, HIGH); }
    void dcLow()  { digitalWrite(_dc, LOW); }
    void csLow()  { digitalWrite(_cs, LOW); }
    void csHigh() { digitalWrite(_cs, HIGH); }

    int8_t _cs = -1, _dc = -1, _rst = -1;
    int16_t _cx = 0, _cy = 0;
    uint16_t _fg = C_WHITE;
    uint16_t _bg = C_BLACK;
    uint8_t _size = 1;
    int16_t _w = 160, _h = 80;        // 当前方向下的逻辑宽高
    int16_t _offx = 1, _offy = 26;    // 面板在 RAM(132x162) 中的偏移
    uint8_t _madctl = 0;              // 当前 MADCTL 值（用于判断 MV 位）
};
