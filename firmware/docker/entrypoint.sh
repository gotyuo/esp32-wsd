#!/bin/sh
set -e

usage() {
  echo "用法:"
  echo "  docker run ... envmon-firmware build <env>"
  echo "  docker run ... envmon-firmware <env> /dev/ttyUSB0"
  echo "  env = esp32-s3 | esp8266"
  echo
  echo "示例:"
  echo "  # 仅编译不烧录"
  echo "  sudo docker run --rm -v <项目>/firmware:/work/firmware -w /work/firmware envmon-firmware build esp32-s3"
  echo "  # 编译 + 烧录 ESP8266"
  echo "  sudo docker run --rm --device=/dev/ttyUSB0 -v <项目>/firmware:/work/firmware -w /work/firmware envmon-firmware esp8266 /dev/ttyUSB0"
  echo "  # 编译 + 烧录 ESP32-S3"
  echo "  sudo docker run --rm --device=/dev/ttyACM0 -v <项目>/firmware:/work/firmware -w /work/firmware envmon-firmware esp32-s3 /dev/ttyACM0"
  echo
  echo "注意: 容器直连串口无需 sudo；如端口权限不通请加 --group-add dialout。"
  echo "      烧录完成后设备自动复位，再次上电即运行新固件。"
}

MODE=${1:-}; shift || true
ENV=${1:-}; shift || true
PORT=${1:-}

if [ -z "$MODE" ] || [ "$MODE" = "--help" ] || [ "$MODE" = "-h" ]; then
  usage; exit 0
fi

case "$MODE" in
  build)
    if [ -z "$ENV" ]; then usage; exit 1; fi
    echo ">>> 编译环境: $ENV"
    pio run -e "$ENV"
    echo ">>> 产物: .pio/build/$ENV/firmware.bin"
    ls -l .pio/build/$ENV/firmware.bin
    ;;
  upload)
    if [ -z "$ENV" ]; then usage; exit 1; fi
    if [ -z "$PORT" ]; then usage; exit 1; fi
    # 先编译（确保是最新）
    echo ">>> 编译环境: $ENV"
    pio run -e "$ENV"
    # 等待设备进入下载模式（CH340/CP210x 开串口会复位设备，首次常失败，重试）
    echo ">>> 等待串口 $PORT 就绪..."
    for i in 1 2 3 4 5; do
      echo ">>> 尝试烧录 (第 $i 次)..."
      if pio run -e "$ENV" -t upload --upload-port "$PORT" 2>&1 | tee /tmp/upload.log; then
        echo ">>> 烧录成功 ✅"
        exit 0
      fi
      echo ">>> 烧录失败，稍后重试..."
      sleep 3
    done
    echo ">>> 烧录失败，请确认设备已进入下载模式(ESP32 按住 BOOT 复位 / ESP8266 拉低 GPIO0)";
    exit 1
    ;;
  *)
    # 兼容: 直接传 env + port 视为 upload
    ENV=$MODE
    if [ -z "$PORT" ]; then usage; exit 1; fi
    echo ">>> 编译 + 烧录环境: $ENV  端口: $PORT"
    pio run -e "$ENV"
    for i in 1 2 3 4 5; do
      echo ">>> 尝试烧录 (第 $i 次)..."
      if pio run -e "$ENV" -t upload --upload-port "$PORT" 2>&1 | tee /tmp/upload.log; then
        echo ">>> 烧录成功 ✅"
        exit 0
      fi
      echo ">>> 烧录失败，稍后重试..."
      sleep 3
    done
    echo ">>> 烧录失败，请确认设备已进入下载模式";
    exit 1
    ;;
esac
