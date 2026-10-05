# esp32-7oled 20261005-v1.0.8

## Summary
- Keep TTS header/body polling tolerant of brief server stalls.
- Increase TTS WAV body receive deadline to 20 seconds while waiting for content.
- Preserve local fallback for legacy MQTT/TTS target 172.22.22.83 -> 172.22.22.75.

## Build
- Environment: esp32-7oled
- PlatformIO build: success
- Flash size: 8MB ESP32-S3
- Built firmware: firmware.bin
