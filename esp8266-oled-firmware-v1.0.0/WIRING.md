# esp8266oled Wiring

## ESP8266 Dev Board
- VCC -> 5V
- GND -> GND

## OLED (SPI 0.96inch)
- SCK -> D6 (GPIO12)
- MOSI / SDA -> D5 (GPIO14)
- CS -> D2 (GPIO4)
- DC -> D1 (GPIO5)
- RST -> D0 (GPIO16)
- BL -> 3V3

## MAX30102
- VCC -> 5V
- GND -> GND
- SCL -> D4 (GPIO2)
- SDA -> D3 (GPIO0)

## AHT20
- VCC -> 3V3
- GND -> GND
- SCL -> D4 (GPIO2)
- SDA -> D3 (GPIO0)

## BMP280
- VCC -> 3V3
- GND -> GND
- SCL -> D4 (GPIO2)
- SDA -> D3 (GPIO0)

## Notes
- MAX30102 uses the same I2C bus as AHT20 and BMP280 in this build.
- OLED is on the SPI side and should not share I2C pins.
- Keep the OLED backlight on 3V3.
