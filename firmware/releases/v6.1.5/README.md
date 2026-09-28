# EnvMon ESP8266 v6.1.5 — 黑屏修复收尾版 (esp8266+4oled)

## 版本: 6.1.5
**发布日期:** 2026-09-28
**目标芯片:** ESP8266 ESP-12F
**编译环境:** PlatformIO, espressif8266 @ 4.2.0
**烧录文件:** 未随包附带 .bin —— v6.1.5 与 v6.1.4 引脚配置一致（同样已移出 GPIO15，无黑屏问题），
可直接继续烧录 `releases/v6.1.4/firmware_v6.1.4.bin`，或按第 3 节编译命令自行生成。
**别名:** esp8266+4oled

---

## v6.1.5 修复内容（相对 v6.1.4）

### 🔴 修复：源码/文档残留的 D8(GPIO15) 接线误导

v6.1.4 已把 MAX30102 移出 GPIO15（黑屏根因修复），但 **`src_esp8266_4oled/main.cpp`
文件头注释仍写着旧接法 "MAX30102 SCL=D7 / SDA=D8(GPIO15)"**。任何人读源码文件头、
照注释接线，都会把 MAX30102 接回 D8 → 再次黑屏。v6.1.5 清掉全部残留误导：

1. `main.cpp` 文件头：MAX30102 标注改为与 AHT20/BMP280 共用 D1/D2，并加
   "严禁接 D8(GPIO15)" 警示注释；
2. `main.cpp` 内联注释："传感器总线 D7/D8" → "D1/D2"；
3. `sensors.cpp`：MAX30102 探测失败时，串口直接打印正确接法
   `SDA->D2(GPIO4) SCL->D1(GPIO5)`，并提示严禁接 D8；
4. 版本号 FW_VERSION → **6.1.5**（`platformio.ini` / `pins.h`）；
5. `firmware/docs/esp8266-wiring.md` 适用范围更新为 v6.1.5。

> **接线与 v6.1.4 完全相同**：MAX30102 SDA→D2(GPIO4)，SCL→D1(GPIO5)，
> 与 AHT20/BMP280 共用一条硬件 I2C 总线。**MAX30102 严禁接 D8(GPIO15)**
> （启动跳线引脚，上电必须为低电平，接上拉会黑屏）。

---

## 1. 硬件引脚接线 (ESP-12F)

### OLED 0.96" I2C (SSD1306, 128x64, 4 引脚)
| OLED 引脚 | ESP8266 引脚 | 说明 |
|-----------|-------------|------|
| VDD | 3V3 | 3.3V 供电 |
| VSS | GND | 地 |
| SCL | **D5 (GPIO14)** | I2C 时钟 |
| SDA | **D6 (GPIO12)** | I2C 数据 |

> OLED 走 u8g2 **软件 I2C**，不占用 ESP8266 的硬件 Wire 外设。

### 传感器总线：AHT20 + BMP280 + MAX30102 共用（v6.1.4 起）
| 传感器 | 引脚 | ESP8266 引脚 |
|--------|------|-------------|
| AHT20 | SDA | **D2 (GPIO4)** |
| | SCL | **D1 (GPIO5)** |
| BMP280 | SDA | **D2 (GPIO4)** (共用) |
| | SCL | **D1 (GPIO5)** (共用) |
| MAX30102 | SDA (SD) | **D2 (GPIO4)** (共用) |
| | SCL (SK) | **D1 (GPIO5)** (共用) |
| | INT | 悬空（轮询模式） |

**地址分配（不冲突）:**
- AHT20: `0x38`
- BMP280: `0x76` (或 `0x77` 自动识别)
- MAX30102: `0x57`

> ⚠️ **D8/GPIO15 铁律**：MAX30102 以及任何带 I2C 上拉的信号**严禁**接 D8(GPIO15)。
> GPIO15 是启动跳线引脚，上电必须为低电平；接上拉会导致 ESP8266 无法启动（黑屏）。
> 板上 D8 已有 10kΩ 下拉，保持悬空即可。

---

## 2. 功能清单

### OLED 4 界面 (128x64, u8g2 软件 I2C)
1. **环境页** — 温度/湿度/气压，超阈值显示 `!` 警示
2. **体征页** — 血氧 SpO2 / 心率 HR，无 MAX30102 时显示 `no dev`
3. **网络页** — 当前 SSID / IP / MQTT 状态 / 信号条
4. **历史曲线页** — 最近 1 小时温度曲线（每 5 分钟 1 点，12 点窗口）

- 每 5 秒自动翻页
- 串口命令 `page1`-`page4` 手动切换，`page` 下一页，`auto` 恢复自动

### 网络管理 (AP↔STA 状态机)
- **首次上电（无 WiFi 配置）:** 直接进 AP 配网
- **有 WiFi 配置:** 先尝试 STA (15s 超时)，同时开 AP 兜底
- **STA 掉线 60s:** 自动切回 AP 配网

### Web 数据页 (STA 联网后)
- `http://<esp_ip>/` → 配网页
- `http://<esp_ip>/data` → 实时数据页
- `http://<esp_ip>/history` → 历史曲线页
- `http://<esp_ip>/api/data` → JSON 实时数据

### 报警
- 环境报警阈值：温度 / 湿度 / 气压
- 体征报警阈值：血氧下限 / 心率范围
- 三级：正常 / 警告 / 报警

### 串口命令 (115200)
| 命令 | 说明 |
|------|------|
| `page1`-`page4` | 切到对应页 |
| `page` / `n` | 下一页 |
| `auto` | 恢复自动轮播 |
| `config` | 立即进入 AP 配网 |
| `factory` | 清除配置并重启 |
| `status` | 打印状态 |
| `hist` | 打印历史 JSON |

---

## 3. 编译

```bash
cd /path/to/firmware
sudo -E $(which pio) run -e esp8266-4oled
```

## 4. 烧录

```bash
sudo -E $(which pio) run -e esp8266-4oled -t upload --upload-port /dev/ttyUSB0
```

或直接烧录 v6.1.4 发布固件（引脚配置与 v6.1.5 相同）：
```bash
esptool.py --before default-reset --chip esp8266 \
    --port /dev/ttyUSB0 --baud 460800 write_flash 0x0 releases/v6.1.4/firmware_v6.1.4.bin
```

## 5. 串口监控

```bash
sudo -E $(which pio) device monitor -p /dev/ttyUSB0 -b 115200
```

---

## 6. 与上一版本 (v6.1.4) 的差异

| 项目 | v6.1.4 | v6.1.5 |
|------|--------|--------|
| MAX30102 接线 | D2/D1 (GPIO4/5) | D2/D1 (GPIO4/5)（不变）✅ |
| 是否占用 GPIO15 (D8) | 否 ✅ | 否 ✅ |
| main.cpp 文件头注释 | ❌ 仍写 D7/D8 旧接法 | ✅ 已改为 D1/D2 + 严禁 D8 |
| MAX30102 探测失败提示 | 仅 "not found" | ✅ 附带正确接法 + 严禁 D8 提示 |
| FW_VERSION | 6.1.4 | **6.1.5** |

**历史版本保留**：`releases/v6.1.2/`、`releases/v6.1.4/` 与 git tag `v6.1.2`、
`v6.1.3`（旧，仍有黑屏问题）、`v6.1.4` 均完整保留，如需回退可烧录对应 bin
（注意 v6.1.2 / 旧 v6.1.3 有黑屏问题，不建议）。

---

## 7. 参考

- ESP8266 Hardware Design Guidelines（GPIO15 boot strap 说明）
- MAX30102 datasheet: https://www.analog.com/en/products/max30102.html
- u8g2 文档: https://github.com/olikraus/U8g2_Arduino