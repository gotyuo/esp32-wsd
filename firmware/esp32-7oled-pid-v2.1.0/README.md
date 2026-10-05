# esp32-7oled-pid-v2.1.0 — 带项目标识的 EnvMon 固件源码

版本：**`20261006-v2.1.1`**（基于 `TFT7735 2.0.0`）

| 项 | 值 |
|---|---|
| 版本标识 | `20261006-v2.1.0` |
| 固件自述 `FW_VERSION` | `2.1.1`（上报表 `"fw"` 字段、屏显可见） |
| 上游基线 | `firmware/esp32-7oled`（TFT7735 **2.0.0**，已实机验证） |
| 日期 | 2026-10-06 |
| 用途 | 让设备探针携带**项目标识 pid**，实现多项目共存不串扰 |
| 验证状态 | ⚠️ **源码交付，未实机编译烧录**（本机无 espressif32 工具链） |

> ⚠️ 本目录是**源码**，没有预编译 `.bin`。需用 PlatformIO 或 Arduino IDE 自行编译上传。
> 想要"开箱即烧"的现成固件，用 `esp32-4spi-st7735s-tft-v2.0.0/`（v2.0.0，已验证）。

---

## 一、相对 v2.0.0 改了什么（4 处）

| # | 位置 | 改动 | 目的 |
|---|---|---|---|
| 1 | `src/net_mgr.cpp` 常量区 | 新增 `PROJECT_ID` 宏 + `DISC_REQ_FMT` 格式串，旧裸探针常量改名 `DISC_REQ_LEGACY` 保留 | 探针可携带项目标识 |
| 2 | `src/net_mgr.cpp` 发送处 | `_udp.print(DISC_REQ)` → `snprintf` 组装 JSON 后发送 | 实际发出带身份的探针 |
| 3 | `src/net_mgr.cpp` 启动日志 | `[DISC] mode=LAN discover, pid=%s, ...` | 串口一眼看出这块设备属于哪个项目 |
| 4 | `platformio.ini` | `FW_VERSION` → `2.1.1`；`ARDUINO_USB_CDC_ON_BOOT` → **1** | 版本号可辨认；**恢复 USB 串口日志**（v2.0.0 为 0，导致应用模式下 COM 口无输出，此前诊断受阻） |
| 5 | `src/net_mgr.cpp` + `platformio.ini` | 新增 `WEB_PORT`（**8822**），`web.begin(WEB_PORT)`；AP 日志与重定向 URL 均带端口 | 避开 80 端口冲突；可用 `-D WEB_PORT=xxxx` 覆盖 |

### 探针格式变化

```diff
- "ENVMON?"                                              （v2.0.0，无身份）
+ {"probe":"EnvMon","pid":"clinic-a","did":"esp32-4d3f54"}   （v2.1.0）
```

**服务端应答格式完全不变**，固件的解析代码一行没动：

```json
{"ip":"192.168.2.220","port":18830,"user":"envmon","pass":"envmon"}
```

---

## 二、为什么必须做这个改动

v2.0.0 的探针是裸字符串，**不含任何身份信息**。后果：

> 同一局域网里，**所有** EnvMon 服务器都会应答，设备拿到谁的全看谁先到 →
> 两个项目共用网络时，设备会连到隔壁项目的服务器。

改造后，服务端只应答"登记了该 pid"的项目，其余**静默**——设备天然连自己的服务。

配套服务端：`services/beacon_server.py`（已实测 5 个场景通过）。

---

## 三、编译与上传

### 方式 A：PlatformIO（推荐）

```bash
cd firmware/esp32-7oled-pid-v2.1.0
pio run -t upload
```

改项目标识：编辑 `platformio.ini` 的这一行

```ini
-DPROJECT_ID=\"clinic-a\"
```

### 方式 B：Arduino IDE

1. 把 `src/` 下所有 `.cpp/.h` 复制到草图目录，主文件改名为 `草图名.ino`
   （或保持 `main_esp32s3_tft7735.cpp` 并在同目录建同名 `.ino`）
2. 板子参数（N16R8 板）：

| 项 | 选择 |
|---|---|
| Board | **ESP32S3 Dev Module** |
| USB CDC On Boot | **Enabled** |
| Flash Size | **16MB (128Mb)** |
| PSRAM | **OPI PSRAM** |
| Flash Mode / Freq | DIO / 80MHz |

3. Arduino IDE 不支持 UI 传宏，直接改源码里的 `#define PROJECT_ID "clinic-a"`（`src/net_mgr.cpp`）

---

## 四、设置你的项目标识

| 场景 | pid 建议 |
|---|---|
| 只有一个项目 | 保持 `"default"`，服务端 `projects.json` 里填 `default` 或开 `default` 兜底 |
| 多项目共存 | 每个项目一个 pid（如 `clinic-a` / `ward-1`），**每个项目编译一份固件** |
| 项目很多、不想逐个编译 | 看进阶方案 `services/beacon-project-id.patch.md` 的 L2：把 pid 存 NVS、配网页可改 |

---

## 五、验证是否生效

串口 115200 应看到（**v2.1.0 起 USB CDC 已打开，用 USB 口即可**）：

```
[DISC] mode=LAN discover, pid=clinic-a, send every 4s, timeout 45s
[DISC] reply len=61: {"ip":"192.168.2.220","port":18830,...}
[DISC] got server 192.168.2.220:18830, saving & rebooting
```

服务端侧（`beacon_server.py` 终端）：

```
[beacon] 192.168.2.50:12091 -> {"probe":"EnvMon","pid":"clinic-a","did":"esp32-4d3f54"}
        └ did=esp32-4d3f54
        └ 应答 project=clinic-a -> 192.168.2.220:18830
```

---

## 六、回退

| 回退到 | 做法 |
|---|---|
| **v2.0.0（现成可烧，最简单）** | `esp32-4spi-st7735s-tft-v2.0.0\flash.bat COM9` |
| v1.0.0 | `esp32-4spi-st7735s-oled\flash.bat COM9` |
| v2.0.0 源码 | 用 `firmware/esp32-7oled/`（本目录的上游基线，原样保留） |

接线**全部不用动**。回退后设备会发裸探针，此时服务端需开 `default` 才能自动发现。

---

## 七、兼容性（可灰度推进）

| 组合 | 行为 |
|---|---|
| v2.1.0 设备 + `beacon_server.py` | ✅ 按 pid 精确路由 |
| v2.1.0 设备 + 旧服务端 | ✅ 旧服务端忽略请求内容照旧回 IP（退化为单项目） |
| v2.0.0 设备 + `beacon_server.py` | ✅ 开 `default` 即可接入 |
| v2.0.0 设备 + v2.1.0 设备混跑 | ✅ 两者共存，各自按规则处理 |


---

## 八、v2.1.1 端口变更须知（8822）

设备 Web 服务由默认 80 改为 **8822**，访问方式随之变化：

| 场景 | 地址 |
|---|---|
| AP 配网页面 | `http://192.168.4.1:8822/` |
| STA 数据页 | `http://<设备IP>:8822/data` |

⚠️ **副作用**：改端口后手机的强制门户（连热点自动弹配网页）可能失效，**需手动在浏览器输入地址**。
若更看重自动弹窗，在 `platformio.ini` 加 `-D WEB_PORT=80` 重新编译即可恢复。
