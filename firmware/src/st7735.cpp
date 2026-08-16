#include "st7735.h"
#include <SPI.h>
#include "font5x7.h"

// ST77xx 命令
#define SWRESET 0x01
#define SLPOUT  0x11
#define NORON   0x13
#define INVON   0x21
#define DISPON  0x29
#define CASET   0x2A
#define RASET   0x2B
#define RAMWR   0x2C
#define COLMOD  0x3A
#define MADCTL  0x36
#define FRMCTR1 0xB1
#define FRMCTR2 0xB2
#define FRMCTR3 0xB3
#define INVCTR  0xB4
#define PWCTR1  0xC0
#define PWCTR2  0xC1
#define PWCTR3  0xC2
#define PWCTR4  0xC3
#define PWCTR5  0xC4
#define VMCTR1  0xC5
#define GMCTRP1 0xE0
#define GMCTRN1 0xE1

// 软件模拟 SPI (bit-bang)
static int8_t PIN_SCK, PIN_MOSI, PIN_CS, PIN_DC, PIN_RST;

#define SCK_LOW()  digitalWrite(PIN_SCK, LOW)
#define SCK_HIGH() digitalWrite(PIN_SCK, HIGH)
#define MOSI_LOW() digitalWrite(PIN_MOSI, LOW)
#define MOSI_HIGH() digitalWrite(PIN_MOSI, HIGH)
#define CS_LOW()   digitalWrite(PIN_CS, LOW)
#define CS_HIGH()  digitalWrite(PIN_CS, HIGH)
#define DC_LOW()   digitalWrite(PIN_DC, LOW)
#define DC_HIGH()  digitalWrite(PIN_DC, HIGH)
#define RST_LOW()  digitalWrite(PIN_RST, LOW)
#define RST_HIGH() digitalWrite(PIN_RST, HIGH)

// 软件 SPI 发送一字节 (MSB first, SPI Mode 3)
static void sw_spi_write(uint8_t dat) {
    for (int8_t i = 7; i >= 0; i--) {
        SCK_LOW();
        if (dat & (1 << i)) MOSI_HIGH(); else MOSI_LOW();
        delayMicroseconds(1);
        SCK_HIGH();
        delayMicroseconds(1);
    }
    SCK_LOW();
}

static void sw_write_cmd(uint8_t cmd) {
    CS_LOW(); DC_LOW(); sw_spi_write(cmd); DC_HIGH(); CS_HIGH();
}

static void sw_write_data(uint8_t dat) {
    CS_LOW(); DC_HIGH(); sw_spi_write(dat); CS_HIGH();
}

static void sw_cmd_with_data(uint8_t cmd, const uint8_t *data, uint8_t len) {
    CS_LOW(); DC_LOW(); sw_spi_write(cmd); DC_HIGH();
    for (uint8_t i = 0; i < len; i++) sw_spi_write(data[i]);
    CS_HIGH();
}

// 设置地址窗口 + RAMWR (CS 保持低，写入模式不中断)
static void sw_set_addr(int16_t x, int16_t y, int16_t w, int16_t h) {
    int16_t x_start = x + 1;
    int16_t x_end   = x + w;
    int16_t y_start = y + 26;
    int16_t y_end   = y + h + 25;
    uint8_t ca[] = {0x00,(uint8_t)x_start,0x00,(uint8_t)x_end};
    uint8_t ra[] = {0x00,(uint8_t)y_start,0x00,(uint8_t)y_end};
    // CASET
    CS_LOW(); DC_LOW(); sw_spi_write(0x2A); DC_HIGH();
    for (uint8_t i = 0; i < 4; i++) sw_spi_write(ca[i]);
    CS_HIGH();
    // RASET
    CS_LOW(); DC_LOW(); sw_spi_write(0x2B); DC_HIGH();
    for (uint8_t i = 0; i < 4; i++) sw_spi_write(ra[i]);
    CS_HIGH();
    // RAMWR - CS 保持低，由调用者写入像素
    CS_LOW(); DC_LOW(); sw_spi_write(0x2C); DC_HIGH();
    // CS 不拉高，保持写入模式
}

// 填充矩形
static void sw_fill_rect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
    sw_set_addr(x, y, w, h);
    uint32_t n = (uint32_t)w * h;
    CS_LOW(); DC_HIGH();
    while (n--) {
        sw_spi_write(color >> 8);
        sw_spi_write(color & 0xFF);
    }
    CS_HIGH();
}

void ST7735::begin(int8_t cs, int8_t dc, int8_t rst, int8_t mosi, int8_t sck) {
    PIN_CS = cs; PIN_DC = dc; PIN_RST = rst; PIN_MOSI = mosi; PIN_SCK = sck;
    pinMode(PIN_CS, OUTPUT); pinMode(PIN_DC, OUTPUT); pinMode(PIN_RST, OUTPUT);
    pinMode(PIN_MOSI, OUTPUT); pinMode(PIN_SCK, OUTPUT);
    CS_HIGH(); DC_HIGH(); SCK_HIGH(); MOSI_HIGH();

    // 硬件复位
    RST_HIGH(); delay(10); RST_LOW(); delay(15); RST_HIGH(); delay(200);

    Serial.println(F("[TFT] Starting init..."));
    sw_write_cmd(0x01); delay(120);
    sw_write_cmd(0x11); delay(120);

    { uint8_t d[]={0x05,0x3C,0x3C}; sw_cmd_with_data(0xB1, d, 3); }
    { uint8_t d[]={0x05,0x3C,0x3C}; sw_cmd_with_data(0xB2, d, 3); }
    { uint8_t d[]={0x05,0x3C,0x3C,0x05,0x3C,0x3C}; sw_cmd_with_data(0xB3, d, 6); }
    { uint8_t d[]={0x03}; sw_cmd_with_data(0xB4, d, 1); }
    { uint8_t d[]={0xAB,0x0B,0x04}; sw_cmd_with_data(0xC0, d, 3); }
    { uint8_t d[]={0xC5}; sw_cmd_with_data(0xC1, d, 1); }
    { uint8_t d[]={0x0D,0x00}; sw_cmd_with_data(0xC2, d, 2); }
    { uint8_t d[]={0x8D,0x6A}; sw_cmd_with_data(0xC3, d, 2); }
    { uint8_t d[]={0x8D,0xEE}; sw_cmd_with_data(0xC4, d, 2); }
    { uint8_t d[]={0x0F}; sw_cmd_with_data(0xC5, d, 1); }
    { uint8_t d[]={0x07,0x0E,0x08,0x07,0x10,0x07,0x02,0x07,0x09,0x0F,0x25,0x36,0x00,0x08,0x04,0x10};
      sw_cmd_with_data(0xE0, d, 16); }
    { uint8_t d[]={0x0A,0x0D,0x08,0x07,0x0F,0x07,0x02,0x07,0x09,0x0F,0x25,0x35,0x00,0x09,0x04,0x10};
      sw_cmd_with_data(0xE1, d, 16); }
    { uint8_t d[]={0x80}; sw_cmd_with_data(0xFC, d, 1); }
    { uint8_t d[]={0x05}; sw_cmd_with_data(0x3A, d, 1); }
    { uint8_t d[]={0xA8}; sw_cmd_with_data(0x36, d, 1); }  // MADCTL=0xA8
    sw_write_cmd(0x21);  // INVON
    sw_write_cmd(0x29); delay(10);  // DISPON

    _w = 160; _h = 80; _madctl = 0xA8;
    Serial.println(F("[TFT] Init OK"));
}

void ST7735::spiWrite(uint8_t b) { sw_spi_write(b); }
void ST7735::sendCmd(uint8_t cmd, const uint8_t *data, uint8_t len) {
    if (data && len) sw_cmd_with_data(cmd, data, len);
    else sw_write_cmd(cmd);
}
void ST7735::writeCmd(uint8_t c) { sw_write_cmd(c); }
void ST7735::writeData(uint8_t d) { sw_write_data(d); }
void ST7735::writeData16(uint16_t d) { sw_write_data(d>>8); sw_write_data(d&0xFF); }
void ST7735::setAddrWindow(int16_t x, int16_t y, int16_t w, int16_t h) { sw_set_addr(x, y, w, h); }
void ST7735::fillScreen(uint16_t color) { sw_fill_rect(0, 0, _w, _h, color); }

void ST7735::fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color) {
    if (x >= _w || y >= _h || w <= 0 || h <= 0) return;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > _w) w = _w - x;
    if (y + h > _h) h = _h - y;
    sw_fill_rect(x, y, w, h, color);
}

void ST7735::drawPixel(int16_t x, int16_t y, uint16_t color) {
    if (x < 0 || x >= _w || y < 0 || y >= _h) return;
    sw_set_addr(x, y, 1, 1);
    CS_LOW(); DC_HIGH();
    sw_spi_write(color >> 8);
    sw_spi_write(color & 0xFF);
    CS_HIGH();
}

void ST7735::drawCircle(int16_t x0, int16_t y0, int16_t r, uint16_t color) {
    int16_t f = 1 - r, ddF_x = 1, ddF_y = -2 * r, x = 0, y = r;
    drawPixel(x0, y0 + r, color); drawPixel(x0, y0 - r, color);
    drawPixel(x0 + r, y0, color); drawPixel(x0 - r, y0, color);
    while (x < y) {
        if (f >= 0) { y--; ddF_y += 2; f += ddF_y; }
        x++; ddF_x += 2; f += ddF_x;
        drawPixel(x0 + x, y0 + y, color); drawPixel(x0 - x, y0 + y, color);
        drawPixel(x0 + x, y0 - y, color); drawPixel(x0 - x, y0 - y, color);
        drawPixel(x0 + y, y0 + x, color); drawPixel(x0 - y, y0 + x, color);
        drawPixel(x0 + y, y0 - x, color); drawPixel(x0 - y, y0 - x, color);
    }
}

void ST7735::fillCircle(int16_t x0, int16_t y0, int16_t r, uint16_t color) {
    fillRect(x0 - r, y0 - r, 2 * r + 1, 2 * r + 1, C_BLACK);
    int16_t f = 1 - r, ddF_x = 1, ddF_y = -2 * r, x = 0, y = r;
    fillRect(x0, y0 - r, 1, 2 * r + 1, color);
    while (x < y) {
        if (f >= 0) { y--; ddF_y += 2; f += ddF_y; }
        x++; ddF_x += 2; f += ddF_x;
        fillRect(x0 - x, y0 - y, 2 * x + 1, 1, color);
        fillRect(x0 - x, y0 + y, 2 * x + 1, 1, color);
        fillRect(x0 - y, y0 - x, 2 * y + 1, 1, color);
        fillRect(x0 - y, y0 + x, 2 * y + 1, 1, color);
    }
}

void ST7735::setCursor(int16_t x, int16_t y) { _cx = x; _cy = y; }

void ST7735::drawChar(int16_t x, int16_t y, char c, uint16_t color, uint8_t size) {
    if (c < 32 || c > 126) c = '?';
    int16_t charW = 6 * size;
    int16_t charH = 8 * size;
    sw_set_addr(x, y, charW, charH);
    // 行主序: 外循环行(Y), 内循环列(X)
    CS_LOW(); DC_HIGH();
    for (int8_t j = 0; j < 8; j++) {
        for (uint8_t sy = 0; sy < size; sy++) {
            for (int8_t px = 0; px < 6; px++) {
                bool lit = false;
                if (px < 5) {
                    uint8_t line = FONT5X7[(c - 32) * 5 + px];
                    lit = (line >> j) & 0x01;
                }
                uint16_t pc = lit ? color : _bg;
                for (uint8_t sx = 0; sx < size; sx++) {
                    sw_spi_write(pc >> 8);
                    sw_spi_write(pc & 0xFF);
                }
            }
        }
    }
    CS_HIGH();
}

void ST7735::print(char c) {
    if (c == '\n') { _cy += 8 * _size; _cx = 0; return; }
    if (_cx + 6 * _size > _w) { _cx = 0; _cy += 8 * _size; }
    drawChar(_cx, _cy, c, _fg, _size);
    _cx += 6 * _size;
}

// 8x16 大号字符 (行主序: 每字符16字节, 每字节8像素)
void ST7735::drawChar8x16(int16_t x, int16_t y, char c, uint16_t color) {
    if (c < 32 || c > 126) c = '?';
    const unsigned char *glyph = ascii_1608[c - 32];
    sw_set_addr(x, y, 8, 16);
    CS_LOW(); DC_HIGH();
    for (int8_t j = 0; j < 16; j++) {           // 行
        uint8_t line = glyph[j];
        for (int8_t px = 0; px < 8; px++) {      // 列
            bool lit = (line >> (7 - px)) & 0x01;
            uint16_t pc = lit ? color : _bg;
            sw_spi_write(pc >> 8);
            sw_spi_write(pc & 0xFF);
        }
    }
    CS_HIGH();
}

// 16x16 中文字符 (行主序, 每行2字节)
void ST7735::drawChinese(int16_t x, int16_t y, uint8_t index, uint16_t color) {
    if (index > 4) return;
    const unsigned char *glyph = font_cn16x16[index];
    sw_set_addr(x, y, 16, 16);
    CS_LOW(); DC_HIGH();
    for (int8_t j = 0; j < 16; j++) {           // 行
        uint8_t b0 = glyph[j*2];
        uint8_t b1 = glyph[j*2+1];
        for (int8_t px = 0; px < 8; px++) {
            bool lit = (b0 >> (7 - px)) & 0x01;
            uint16_t pc = lit ? color : _bg;
            sw_spi_write(pc >> 8);
            sw_spi_write(pc & 0xFF);
        }
        for (int8_t px = 0; px < 8; px++) {
            bool lit = (b1 >> (7 - px)) & 0x01;
            uint16_t pc = lit ? color : _bg;
            sw_spi_write(pc >> 8);
            sw_spi_write(pc & 0xFF);
        }
    }
    CS_HIGH();
}

void ST7735::print(const char *s) { while (*s) print(*s++); }
void ST7735::print(int v) { char buf[12]; snprintf(buf,12,"%d",v); print(buf); }
void ST7735::print(float v, int dec) { char buf[16]; snprintf(buf,16,"%.*f",dec,v); print(buf); }