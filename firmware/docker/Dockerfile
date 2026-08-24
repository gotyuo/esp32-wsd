ARG BASE_IMG=registry.xuanyuan.run/kuuyee/base-python:3.11-slim
FROM ${BASE_IMG} AS base

# 允许容器内直连 /dev/ttyUSB* / /dev/ttyACM* 烧录（无需 sudo）
RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential wget curl ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir platformio && \
    # 预热 PlatformIO 的 espressif32 / espressif8266 平台，首次运行时不阻塞下载
    pio platform install espressif32@6.5.0 && \
    pio platform install espressif8266@4.2.0

WORKDIR /work/firmware

ENTRYPOINT ["/work/entrypoint.sh"]
