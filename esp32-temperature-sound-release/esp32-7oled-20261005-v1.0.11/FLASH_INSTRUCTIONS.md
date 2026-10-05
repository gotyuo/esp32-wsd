# 烧录说明 — 20261005-v1.0.11

## 环境

推荐在 Linux/macOS 使用：

```bash
python3 -m pip install esptool
```

## 方法一：使用本目录脚本

```bash
cd esp32-temperature-sound-release/esp32-7oled-20261005-v1.0.11
./flash_esp32_temp_sound.sh /dev/ttyACM0
```

## 方法二：手动 esptool

```bash
python3 -m esptool --chip esp32s3 --port /dev/ttyACM0 --baud 921600 write_flash \
  --flash_mode dio \
  --flash_freq 40m \
  --flash_size detect \
  0x00000000 firmware_bin/bootloader.bin \
  0x00008000 firmware_bin/partitions.bin \
  0x00010000 firmware_bin/firmware.bin
```

## 串口日志

```bash
python3 -m serial.tools.miniterm /dev/ttyACM0 115200
```

成功启动应看到：

```text
EnvMon ESP32-S3 (TFT7735) 20261005-v1.0.11
```

## 常见问题

### not in bootstrap mode

按住 BOOT，轻按 RESET，然后松开 BOOT，再执行烧录。

### 串口无权限

加入 dialout 组：

```bash
sudo -S -p '' usermod -aG dialout $USER
```

重新登录生效。

### 烧录后反复重启

确认 bootloader/partitions/firmware 三个文件分别烧在 0x00000000、0x00008000、0x00010000。不要合并成一个镜像烧录。
