# ESP32 温度声音独立烧录包

当前版本：`20261005-v1.0.12`

本目录只包含当前可用温度/TTS 声音固件及其烧录文件，后期可直接使用本目录烧录。

## 文件

- `firmware_bin/bootloader.bin` → `0x00000000`
- `firmware_bin/partitions.bin` → `0x00008000`
- `firmware_bin/firmware.bin` → `0x00010000`
- `firmware_bin/firmware.elf`：调试符号，不烧录
- `platformio.ini.snapshot`、`partitions_ota.csv`、`SHA256SUMS`
- `WIRING.md`：接线顺序和引脚说明
- `FLASH_INSTRUCTIONS.md`：烧录说明
- `RELEASE_NOTES.md`：版本说明
- `flash_esp32_temp_sound.sh`：本机一键烧录脚本

## 当前验证

- v1.0.12 已修复 TTS I2S 播放状态机。
- 串口已验证：`received` → `init I2S` → `playing` → `playback complete`。
- 不再出现 `I2S port 0 has not installed`。
