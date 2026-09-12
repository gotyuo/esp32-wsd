// ============================================================
// I2C 引脚扫描诊断固件
// 扫描所有可用 GPIO 组合，找到 MAX30102 (0x57)
// 同时扫描 OLED (0x3C/0x3D)、AHT20 (0x38)、BMP280 (0x77)
// ============================================================
#include <Arduino.h>
#include <Wire.h>

// ESP8266 可用 GPIO (排除 Flash 占用的 6-11)
static const uint8_t GPIOS[] = {0, 2, 4, 5, 12, 13, 14, 15, 16};
static const int N = sizeof(GPIOS) / sizeof(GPIOS[0]);

// 目标 I2C 地址
static const uint8_t TARGETS[] = {0x3C, 0x3D, 0x38, 0x57, 0x77};
static const int NT = sizeof(TARGETS) / sizeof(TARGETS[0]);
static const char *TARGET_NAMES[] = {"OLED", "OLED2", "AHT20", "MAX30102", "BMP280"};

void setup() {
    Serial.begin(115200);
    delay(400);
    Serial.println();
    Serial.println("======================================");
    Serial.println(" ESP8266 I2C Pin Scan Diagnostic");
    Serial.println("======================================");
    Serial.println("Scanning all GPIO combinations...");
    Serial.println();

    bool found = false;

    // 扫描所有 SDA/SCL 组合 (SDA != SCL)
    for (int i = 0; i < N; i++) {
        for (int j = 0; j < N; j++) {
            if (GPIOS[i] == GPIOS[j]) continue;

            uint8_t sda = GPIOS[i];
            uint8_t scl = GPIOS[j];

            // 尝试初始化 I2C 总线
            Wire.begin(sda, scl);
            Wire.setClock(100000); // 低速扫描
            delay(50);

            for (int t = 0; t < NT; t++) {
                Wire.beginTransmission(TARGETS[t]);
                uint8_t err = Wire.endTransmission();
                if (err == 0) {
                    Serial.printf("FOUND: %s (0x%02X) at SDA=GPIO%d SCL=GPIO%d\n",
                                  TARGET_NAMES[t], TARGETS[t], sda, scl);
                    found = true;
                }
            }

            Wire.end();
            delay(20);
        }
    }

    if (!found) {
        Serial.println("No I2C devices found!");
        Serial.println();
        Serial.println("Trying all GPIO pairs with full scan...");
        for (int i = 0; i < N; i++) {
            for (int j = 0; j < N; j++) {
                if (GPIOS[i] == GPIOS[j]) continue;

                uint8_t sda = GPIOS[i];
                uint8_t scl = GPIOS[j];

                Wire.begin(sda, scl);
                Wire.setClock(100000);
                delay(50);

                for (uint8_t addr = 0x01; addr <= 0x7F; addr++) {
                    if (addr == 0x3C || addr == 0x3D || addr == 0x38 ||
                        addr == 0x57 || addr == 0x77) continue; // already checked

                    Wire.beginTransmission(addr);
                    uint8_t err = Wire.endTransmission();
                    if (err == 0) {
                        Serial.printf("UNKNOWN device 0x%02X at SDA=GPIO%d SCL=GPIO%d\n",
                                      addr, sda, scl);
                    }
                }

                Wire.end();
                delay(10);
            }
        }
    }

    Serial.println();
    Serial.println("======================================");
    Serial.println(" Scan complete");
    Serial.println("======================================");
}

void loop() {
    delay(1000);
}