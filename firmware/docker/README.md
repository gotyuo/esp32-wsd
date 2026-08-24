# 固件 Docker 构建/烧录环境

一键、免在本机装 PlatformIO：Docker 镜像内置 PlatformIO + espressif32 / espressif8266 工具链，
可直接 `docker build` 编译、`docker run` 挂载串口烧录 ESP32-S3 或 ESP8266。

## 1. 构建镜像（首次较慢，会下载 ~400MB 工具链）

```bash
cd firmware/docker
sudo docker build -t envmon-firmware .
```

## 2. 编译（不烧录）

```bash
# 输出: firmware/.pio/build/<env>/firmware.bin
sudo docker run --rm \
  -v "$(pwd)/..:/work/firmware" -w /work/firmware \
  envmon-firmware build esp32-s3
sudo docker run --rm \
  -v "$(pwd)/..:/work/firmware" -w /work/firmware \
  envmon-firmware build esp8266
```

## 3. 编译 + 烧录

```bash
# ESP8266 (CP210x/CP2102, 通常 /dev/ttyUSB0)
sudo docker run --rm --device=/dev/ttyUSB0 \
  -v "$(pwd)/..:/work/firmware" -w /work/firmware \
  envmon-firmware esp8266 /dev/ttyUSB0

# ESP32-S3 (CH340, 通常 /dev/ttyACM0)
sudo docker run --rm --device=/dev/ttyACM0 \
  -v "$(pwd)/..:/work/firmware" -w /work/firmware \
  envmon-firmware esp32-s3 /dev/ttyACM0
```

> 端口权限若报 Permission denied，加 `--group-add dialout`，或确保宿主 `$USER` 已加入 `dialout` 组。
> 烧录前设备须进入下载模式：ESP32-S3 按住 BOOT 复位 / ESP8266 拉低 GPIO0（按 FLASH 键）。
> 脚本内置 5 次重试（CH340/CP210x 开串口会复位设备，首烧常失败）。

## 4. 在线 monitor（串口抓日志）

```bash
# 容器内没有长时间运行的 monitor；用宿主机 pyserial / minicom 抓即可
python3 -c "import serial,time;s=serial.Serial('/dev/ttyUSB0',115200);time.sleep(1);print(s.read(s.in_waiting))"
```

## 与本机烧录对比

| 方式 | 需装 | 命令 |
|------|------|------|
| 本机 PIO | python3 + platformio | `sudo -E $(which pio) run -e esp8266 -t upload --upload-port /dev/ttyUSB0` |
| ## 镜像构建
## 构建前提（镜像拉取）

Dockerfile 默认基于 `registry.xuanyuan.run/kuuyee/base-python:3.11-slim`。如果你的网络能直连 docker.io，
可改为官方源并构建：

```bash
sudo docker build -t envmon-firmware \
  --build-arg BASE_IMG=python:3.11-slim-bookworm .
```

若默认源也可用，直接：

```bash
sudo docker build -t envmon-firmware .
```

构建时会预热 espressif32 / espressif8266 工具链（~400MB），首次较慢。容器运行时通过
`-v <项目>/firmware:/work/firmware` 绑定源码、`--device=/dev/ttyUSB*` 直通串口完成烧录。
