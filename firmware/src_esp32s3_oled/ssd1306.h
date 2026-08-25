#pragma once
// ============================================================
// 0.96" I2C OLED (SSD1306 / SSD1315 兼容) 128x64 驱动
// 4 孔: VSS=GND, VDD=3V3, SCL=D5(GPIO14), SDA=D3(GPIO2)
// 地址 0x3C；与 AHT20(0x38)/BMP280(0x76) 并联同一 I2C 总线，地址不冲突
// 纯手写 I2C 命令/数据协议，零第三方库；复用 src/font5x7.h (5x7 ASCII)
// ============================================================
#include <Arduino.h>
#include <Wire.h>
#include <stdint.h>
#include <string.h>

#define OLED_W 128
#define OLED_H 64
#define OLED_ADDR 0x3C

// SSD1306 命令
#define C_SET_CONTRAST_A    0x81
#define C_ENTIRE_DISP       0xA4
#define C_NORMAL_DISP       0xA6
#define C_DISPLAY_OFF       0xAE
#define C_DISPLAY_ON        0xAF
#define C_SET_DISPLAY_CLK   0xD5
#define C_SET_MULTIPLEX     0xA8
#define C_SET_OFFSET        0xD3
#define C_SET_START_LINE    0x40
#define C_CHARGE_PUMP       0x8D
#define C_SEG_REMAP_LO      0xA0
#define C_SEG_REMAP_HI      0xA1
#define C_COM_NORMAL        0xC0
#define C_COM_REMAPPED      0xC8
#define C_SET_COM_PIN       0xDA
#define C_MEMORY_ADDR       0x20
#define C_COLUMN_ADDR       0x21
#define C_PAGE_ADDR         0x22
#define C_SET_PRECHARGE     0xD9
#define C_SET_VCOM_DESELECT 0xDB

// 命令字节=0x00, 数据字节=0x40(Wire 库用 0x00/0x40 前缀切换模式)
#define OLED_MODE_CMD  0x00
#define OLED_MODE_DATA 0x40

class Ssd1306 {
public:
    // 初始化;返回 false 表示 I2C 上找不到屏幕
    bool begin(uint8_t scl = 14, uint8_t sda = 2, uint8_t addr = OLED_ADDR);
    // 指定 I2C 接口（ESP32-S3 OLED 走 I2C1，传感器走 I2C0；ESP8266 默认用 Wire）
    bool begin(uint8_t scl, uint8_t sda, uint8_t addr, TwoWire *wire);

    // 清屏(填充 0)
    void clear();

    // 画点
    void drawPixel(int16_t x, int16_t y, uint8_t c);

    // 画水平/垂直线
    void drawLineH(int16_t x, int16_t y, int16_t w, uint8_t c);
    void drawLineV(int16_t x, int16_t y, int16_t h, uint8_t c);

    // 画矩形边框 / 填充矩形
    void drawRect(int16_t x, int16_t y, int16_t w, int16_t h, uint8_t c);
    void fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint8_t c);

    // 5x7 英文字符(依赖 src/font5x7.h 的 FONT5X7[])
    void drawChar(int16_t x, int16_t y, char ch, uint8_t c);
    void drawString(int16_t x, int16_t y, const char *s, uint8_t c = 1);

    // 数字转字符串输出
    void drawNum(int16_t x, int16_t y, int32_t v, uint8_t c = 1);
    void drawNumFP(int16_t x, int16_t y, float v, uint8_t frac, uint8_t c = 1);

    // 画 WiFi 信号条(0-4 格)
    void drawWifiBars(int16_t x, int16_t y, uint8_t bars);

    // 把帧缓冲整屏推送到屏幕
    void flush();

    // 发送命令 / 数据
    void cmd(uint8_t b);
    void data(const uint8_t *b, uint16_t len);

private:
    void i2c_write(uint8_t mode, const uint8_t *buf, uint16_t len);

    uint8_t _addr;
    bool    _ok;
    TwoWire *_wire = nullptr;
};