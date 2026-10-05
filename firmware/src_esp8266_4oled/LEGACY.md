# ⚠️ 已废弃（LEGACY）— 已被 `esp8266oled/` 取代

**本目录已停止维护**，请勿在此修改 ESP8266 OLED 血氧版固件。

## 迁移指引

| 项 | 本目录（已废弃） | 新位置（维护中）|
|---|---|---|
| 路径 | `firmware/src_esp8266_4oled/` | **`esp8266oled/`**（仓库根目录）|
| 版本 | `6.1.5` | `20261005-6.1.3`（对应 commit `63658ef`）|
| 构建方式 | 依赖 `firmware/platformio.ini` 的 `[env:esp8266-4oled]` | **自带 `platformio.ini`，独立编译** |
| MAX30102 | 无 FIFO wr/rd 稳定化 | 含 DC/AC 血氧算法 + FIFO 消费稳定化 |

## 为什么保留而不是删除

1. 它**没有自己的 `platformio.ini`**，是 `firmware/` **多设备共享构建系统**的一部分 ——
   `[env:esp32-4oled]`、`[env:esp32-6oled]`、`[env:esp8266-6oled]` 与它共用同一套 `firmware/` 目录布局。
2. ESP32 系列固件线仍依赖该目录结构，物理删除会破坏 `firmware/platformio.ini` 里的 `src_dir` 引用。
3. 作为历史归档，可追溯 `6.1.5` 版本的实现差异。

## 统一烧录入口

**ESP8266 + MAX30102 + 0.96" OLED 血氧监护版，一律从根目录 `esp8266oled/` 编译烧录：**

```bash
cd esp8266oled
pio run -e esp8266oled -t upload
```

已验证的发布归档见 `esp8266-oled-firmware-v6.1.3/`（含 bin/elf 及说明）。
