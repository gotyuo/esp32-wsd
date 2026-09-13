// ============================================================
// EnvMon ESP8266 v5.0
// 最小 OLED 固件：仅保留 ESP8266 + SSD1306 OLED
// OLED 接线: VDD=3V3, VSS=GND, SCL=D5(GPIO14), SDA=D6(GPIO12)
// ============================================================
#include <Arduino.h>
#include <U8g2lib.h>
#include <Wire.h>
#include "pins.h"

U8G2_SSD1306_128X64_NONAME_F_HW_I2C g_oled(U8G2_R0, PIN_OLED_SDA, PIN_OLED_SCL, U8X8_PIN_NONE);

void setup() {
    Serial.begin(115200);
    delay(300);
    Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
    Wire.setClock(400000);

    Serial.println();
    Serial.println(F("======================================"));
    Serial.println(F(" EnvMon ESP8266 firmware 5.0.0"));
    Serial.println(F("======================================"));
    Serial.printf("[BOOT] OLED SDA=GPIO%d SCL=GPIO%d\n", PIN_OLED_SDA, PIN_OLED_SCL);

    g_oled.begin();
    g_oled.setContrast(255);
    g_oled.setFont(u8g2_font_6x10_tr);
    g_oled.clearBuffer();
    g_oled.drawStr(2, 14, "EnvMon v5.0.0");
    g_oled.drawStr(2, 30, "OLED OK");
    g_oled.drawStr(2, 46, "D5/D6");
    g_oled.sendBuffer();
    Serial.println("[BOOT] OLED init done");
}

void loop() {
    static uint32_t last = 0;
    if (millis() - last < 2000) return;
    last = millis();

    g_oled.firstPage();
    do {
        g_oled.clearBuffer();
        g_oled.drawStr(2, 14, "EnvMon v5.0.0");
        g_oled.drawStr(2, 30, "OLED OK");
        g_oled.drawStr(2, 46, "D5/D6");
    } while (g_oled.nextPage());

    delay(500);

    g_oled.firstPage();
    do {
        g_oled.clearBuffer();
        g_oled.drawFrame(4, 4, 120, 56);
        g_oled.drawStr(10, 34, "TEST2");
    } while (g_oled.nextPage());

    Serial.println("frame loop ok");
}
