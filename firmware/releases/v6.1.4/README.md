# EnvMon ESP8266 v6.1.4 — 修复 MAX30102 黑屏版 (esp8266+4oled)

## 版本: 6.1.4
**发布日期:** 2026-09-28
**目标芯片:** ESP8266 ESP-12F
**编译环境:** PlatformIO 6.2.0, espressif8266 @ 4.2.0
**烧录文件:** `firmware_v6.1.4.bin`
**别名:** esp8266+4oled

---

## v6.1.4 修复内容（相对 v6.1.2 / 旧 v6.1.3 tag）

### 🔴 修复：接上 MAX30102 后 ESP8266 重启黑屏

**现象**：一接上 MAX30102，ESP8266 开机/复位就黑屏；拔掉 MAX30102 就能正常启动。

**根因**：MAX30102 的 SDA/SCL 被接在 **D7/D8 (GPIO13/GPIO15)** 上，其中 **D8 =
GPIO15 是 ESP8266 的启动跳线引脚（boot strap）**。芯片上电/复位时必须保证 GPIO15
为**低电平**才能从 Flash 正常启动。MAX30102 模块板上自带 I2C 上拉电阻，SDA/SCL
空闲时被拉高——模块一插上就把 GPIO15 拉高，导致芯片根本无法启动 → 黑屏（此时
sketch 尚未运行，所以不是传感器初始化卡死，是芯片级别的 boot 失败）。

**为什么旧的 v6.1.3 tag 没解决问题**：仓库里已存在的 tag `v6.1.3`（提交 69813c8）
只是把 SDA/SCL 对调（SDA=D7, SCL=D8），**仍然占用 GPIO15**。I2C 的 SCL 同样是被
上拉电阻拉高的空闲高电平，所以黑屏 bug 依旧。必须是**彻底不用 GPIO15**。

**修复**：把 MAX30102 改接到 **D1/D2 (GPIO5/GPIO4)**，与 AHT20/BMP280 **共用同一
硬件 I2C 总线**。三个传感器地址不冲突（AHT20=0x38, BMP280=0x76/0x77, MAX30102=0x57），
I2C 协议本身支持一总多设备。D1/D2 不是启动跳线引脚，绝对安全。

**接线变更（必须按新接法接线）**：

| 信号 | v6.1.2 / 旧 v6.1.3（❌） | v6.1.4（✅） |
|------|--------------------------|-------------|
| MAX30102 SDA (SD) | D8 / GPIO15 或 D7 / GPIO13 | **D2 / GPIO4** |
| MAX30102 SCL (SK) | D7 / GPIO13 或 D8 / GPIO15 | **D1 / GPIO5** |
| AHT20/BMP280 | D1/D2 | D1/D2（不变，与 MAX30102 共用） |

其余接线不变：OLED SCL=D5(GPIO14)、SDA=D6(GPIO12)（u8g2 软件 I2C）。

### 附带修复：v6.1.2 发布版 MAX30102 实际未被寻址

v6.1.2 的 `max30102.cpp` 中 `_ensureBus()` 是空实现，而 MAX30102 物理接在 D7/D8，
硬件 Wire 却一直停留在 D1/D2 → 初始化时 `begin()` 会报 `not found`，MAX30102
**实际上不工作**。v6.1.4 把 MAX30102 移到共享总线后不存在该问题（地址 0x57
在 D1/D2 总线上有效）。

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

### 传感器总线：AHT20 + BMP280 + MAX30102 共用
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

或直接用固件文件：
```bash
esptool.py --before default-reset --chip esp8266 \
    --port /dev/ttyUSB0 --baud 460800 write_flash 0x0 releases/v6.1.4/firmware_v6.1.4.bin
```

## 5. 串口监控

```bash
sudo -E $(which pio) device monitor -p /dev/ttyUSB0 -b 115200
```

---

## 6. 与上一版本 (v6.1.2 / 旧 v6.1.3 tag) 的差异

| 项目 | v6.1.2 / 旧 v6.1.3 | v6.1.4 |
|------|---------------------|--------|
| MAX30102 SDA | D8 或 D7 | **D2 (GPIO4)** ✅ |
| MAX30102 SCL | D7 或 D8 | **D1 (GPIO5)** ✅ |
| 是否占用 GPIO15 (D8) | 是 ❌ | **否** ✅ |
| 接 MAX30102 开机 | **黑屏（boot strap 被拉高）** | 正常启动，传感器可读 |
| MAX30102 是否实际工作 | v6.1.2 驱动寻址失败（_ensureBus 空实现） | ✅ 共享总线正常寻址 |
| 传感器总线 | 两组（D1/D2 + D7/D8） | 一组（D1/D2 共 3 设备） |

**历史版本保留**：`releases/v6.1.2/` 目录与 git tag `v6.1.2`、`v6.1.3`（旧，仍有黑屏问题）
均完整保留，如需回退可烧录对应 bin（注意均有上述黑屏问题，不建议）。

---

## 7. 参考

- ESP8266 Hardware Design Guidelines（GPIO15 boot strap 说明）
- MAX30102 datasheet: https://www.analog.com/en/products/max30102.html
- u8g2 文档: https://github.com/olikraus/U8g2_Arduino