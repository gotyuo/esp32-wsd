# esp8266oled

ESP8266 + 0.96 OLED 工程包。

## Wiring
- GUN -> GND
- VCC -> 3V3
- SCK -> D6 (GPIO12)
- SDA -> D5 (GPIO14)
- RST -> RST
- DC -> D1
- CS -> D2

## Features
- 默认 AP 模式配置 SSID/密码
- OLED 显示患者信息
- Web 设置页
- Webhook 推送文本到 OLED

## Build
PlatformIO project:

```bash
cd esp8266oled
pio run -e esp8266oled
```

Current baseline:
- `v3.5.0`
- OLED wiring: 7-pin SPI OLED
- Driver: `U8G2_SSD1306_128X64_NONAME_F_4W_SW_SPI`
- Pins: `SCK=GPIO12`, `SDA/MOSI=GPIO14`, `CS=GPIO4`, `DC=GPIO5`, `RST=GPIO0`
- Web: AP 配置页支持 WiFi 扫描与保存

## Notes
- 当前目录已创建，适合继续补完整 Web 服务、数据存留、webhook 逻辑。
- Gitee 仓库创建和 push 需要网络权限。

## Gitee Intro
#### 介绍
esp8266oled 纯7角的spi的方式接入，床头卡的功能。

#### 软件架构
软件架构说明

#### 安装教程
1.  xxxx
2.  xxxx
3.  xxxx

#### 使用说明
1.  xxxx
2.  xxxx
3.  xxxx

#### 参与贡献
1.  Fork 本仓库
2.  新建 Feat_xxx 分支
3.  提交代码
4.  新建 Pull Request

#### 特技
1.  使用 Readme\_-XXX.md 来支持不同的语言，例如 Readme\_-en.md, Readme\_-zh.md
2.  Gitee 官方博客 [blog.gitee.com](https://blog.gitee.com)
3.  你可以 [https://gitee.com/explore](https://gitee.com/explore) 这个地址来了解 Gitee 上的优秀开源项目
4.  [GVP](https://gitee.com/gvp) 全称是 Gitee 最有价值开源项目，是综合评定出的优秀开源项目
5.  Gitee 官方提供的使用手册 [https://gitee.com/help](https://gitee.com/help)
6.  Gitee 封面人物是一档用来展示 Gitee 会员风采的栏目 [https://gitee.com/gitee-stars/](https://gitee.com/gitee-stars/)


---

## 版本与近期变更（20261006-v1.0）

| 项 | 值 |
|---|---|
| 版本标识 | `20261006-v1.0` |
| 编译宏 | `FW_VERSION`、`PROJECT_ID`、`WEB_PORT`（见 `platformio.ini`） |

本版相对此前源码的变更（与 ESP32 v2.1.1 保持同协议）：

1. **发现探针携带项目标识**：`ENVMON?` → `{"probe":"EnvMon","pid":"<PROJECT_ID>","did":"<MAC>"}`
   服务端只应答登记了该 pid 的项目，多项目共存不再串。应答格式未变，可灰度。
2. **Web 服务端口 80 → 8822**：`web.begin(WEB_PORT)`，AP 日志与重定向地址同步带端口。
   访问配网页用 `http://192.168.4.1:8822/`（改端口后手机自动弹窗可能失效，需手动输入地址）。
3. **发现日志输出 pid**：串口可见 `[DISC] mode=LAN discover, pid=xxx, ...`，便于辨认设备归属。

> 本项目与 ESP32 使用**完全相同的发现协议**（多播 `239.255.1.1:12091`），
> 因此可共用同一个 `services/beacon_server.py` 服务端，无需分别部署。
>
> 注意：`esp8266-oled-firmware-v6.1.3/` 是**已发布快照**（含编译好的 bin），为保证源码与固件一致，未做改动。
