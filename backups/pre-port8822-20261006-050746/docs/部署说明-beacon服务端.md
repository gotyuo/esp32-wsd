# EnvMon 部署说明：设备自动发现 + 多项目接入

文档版本：`20261006-v1.0`
适用版本：固件 `20261006-v2.1.0` + 服务端 `20261006-v1.1`

---

## 一、这套东西解决什么

设备开机后**自动找到属于自己项目的服务器**，不需要逐台在配网页填地址；
多个项目共用同一网络时，设备**不会连错服务器**。

```
设备广播探针（带项目标识 pid）
        ↓  UDP 多播 239.255.1.1:12091
服务端查路由表 → 是本项目就应答，不是就静默
        ↓
设备拿到服务器地址/端口/账号 → 存 NVS → 重启 → 开始上报
```

### 端口一览

| 端口 | 协议 | 用途 | 必须放行 |
|---|---|---|---|
| **12091** | UDP | 设备发现（多播） | ✅ 服务端入站 |
| 18830 | TCP | MQTT 上报 | ✅ |
| 12090 | TCP | HTTP 遥测上报 | ✅ |
| 80 | TCP | AP 配网页面 `192.168.4.1` | 仅配网时需要 |

---

## 二、本次交付版本清单

| 版本 | 位置 | 说明 |
|---|---|---|
| **`20261006-v2.1.0`** | `firmware/esp32-7oled-pid-v2.1.0/` | 固件**源码**：探针携带 pid；USB 串口已恢复 |
| **`20261006-v1.1`** | `services/beacon_server.py` | 发现服务端：按 pid 路由、支持指定网卡、带时间戳日志 |
| **`20261006-v1.1`** | `services/projects.json` | 多项目路由表 |
| **`20261006-v1.1`** | `services/start-beacon.{bat,sh}` | 一键启动脚本 |
| **`20261006-v1.1`** | `services/Dockerfile` `docker-compose.yml` `systemd/` | 容器与常驻部署 |
| `20261005-v2.0.0` | `esp32-4spi-st7735s-tft-v2.0.0/` | **现成可烧固件**（回退用，已实机验证） |
| `20261005-v1.0.0` | `esp32-4spi-st7735s-oled/` | 现成可烧固件（更保守的回退点） |

---

## 三、部署前检查

| # | 确认项 | 怎么确认 |
|---|---|---|
| 1 | 服务端机器与 ESP32 在**同一网段** | `ipconfig` / `ip a`，对比 ESP32 拿到的 IP |
| 2 | 服务端机器有**固定 IP**（或 DHCP 保留） | 路由器里绑定 MAC，否则 IP 变了设备就失联 |
| 3 | EnvMon 服务端（MQTT/HTTP）已在运行 | 确认 18830 / 12090 在监听 |
| 4 | 服务器是否**多网卡** | 是则启动时必须加 `--interface <本网段IP>` |
| 5 | 防火墙放行 UDP 12091 | 见第五节 |

---

## 四、步骤 1：部署发现服务端

### 方式 A：Windows 直接跑（最快验证）

```bat
cd services
start-beacon.bat 192.168.2.218        :: 参数是本机在设备网段的 IP
```

### 方式 B：Linux 常驻（systemd）

```bash
sudo mkdir -p /opt/envmon-beacon
sudo cp beacon_server.py projects.json /opt/envmon-beacon/
sudo cp systemd/envmon-beacon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now envmon-beacon
sudo systemctl status envmon-beacon          # 看状态
journalctl -u envmon-beacon -f               # 看日志
```

多网卡机器：编辑 service 文件，在 `ExecStart` 末尾加 `--interface 192.168.2.220`。

### 方式 C：Docker（飞牛/NAS 常用）

```bash
cd services
docker compose up -d
docker compose logs -f
```

> ⚠️ **必须 host 网络**（compose 里已配 `network_mode: host`）。
> bridge 模式下容器收不到多播，设备永远发现不了——这是最常见的坑。

---

## 五、步骤 2：放行防火墙

**Windows（管理员）：**

```bat
netsh advfirewall firewall add rule name="EnvMon Beacon UDP 12091" dir=in action=allow protocol=UDP localport=12091
netsh advfirewall firewall add rule name="EnvMon MQTT 18830" dir=in action=allow protocol=TCP localport=18830
netsh advfirewall firewall add rule name="EnvMon HTTP 12090" dir=in action=allow protocol=TCP localport=12090
```

**Linux：**

```bash
sudo ufw allow 12091/udp
sudo ufw allow 18830/tcp
sudo ufw allow 12090/tcp
```

---

## 六、步骤 3：配置多项目路由表

编辑 `services/projects.json`：

```json
{
  "projects": {
    "clinic-a": {"ip": "192.168.2.220", "port": 18830, "user": "envmon", "pass": "envmon"},
    "ward-*":   {"ip": "192.168.2.222", "port": 18830, "user": "ward",    "pass": "ward"}
  },
  "default": null
}
```

| 字段 | 说明 |
|---|---|
| `projects.<pid>` | pid 必须与固件里的 `PROJECT_ID` 完全一致；支持 `前缀*` 通配 |
| `ip` | **设备能连到的地址**，别填 127.0.0.1 或容器内网 IP |
| `default` | **多项目共存时务必保持 `null`**；单项目才填对象，让任意设备都能接入 |

> `default: null` 意味着：没登记 pid 的设备一律不答。这是防止设备"串项目"的关键开关。

---

## 七、步骤 4：编译并上传固件 v2.1.0

⚠️ 本机无 espressif32 工具链，**这一步需要你本地编译**。

### PlatformIO

```bash
cd firmware/esp32-7oled-pid-v2.1.0
# 改项目标识：
#   platformio.ini -> -DPROJECT_ID=\"clinic-a\"
pio run -t upload
```

### Arduino IDE

1. `src/` 下所有 `.cpp/.h` 复制到草图目录，主文件改名为 `草图名.ino`
2. 板子参数（N16R8 板）：**ESP32S3 Dev Module** / Flash **16MB** / PSRAM **OPI PSRAM** / USB CDC On Boot **Enabled**
3. 改 `src/net_mgr.cpp` 里的 `#define PROJECT_ID "clinic-a"`

> 每个项目编译一份固件（pid 不同）。项目多、不想逐个编译时用进阶方案：
> `services/beacon-project-id.patch.md` 的 L2（pid 存 NVS，配网页可改）。

---

## 八、步骤 5：验证

### 服务端侧（beacon 终端应出现）

```
[14:02:11] [beacon] 192.168.2.50:12091 -> {"probe":"EnvMon","pid":"clinic-a","did":"esp32-4d3f54"}
[14:02:11]     └ did=esp32-4d3f54
[14:02:11]     └ 应答 project=clinic-a -> 192.168.2.220:18830
```

### 设备侧（串口 115200，v2.1.0 起 USB 口直接可看）

```
[DISC] mode=LAN discover, pid=clinic-a, send every 4s, timeout 45s
[DISC] reply len=61: {"ip":"192.168.2.220","port":18830,...}
[DISC] got server 192.168.2.220:18830, saving & rebooting
```

随后屏上出数据、串口打 `[HTTP] telemetry OK`。

### 不用等真机也能自测

```bash
python services/beacon_server.py --probe pid=clinic-a did=esp32-4d3f54
```

收到 `{"ip":"192.168.2.220",...}` 即服务端正常。

---

## 九、排障表

| 现象 | 原因 | 处理 |
|---|---|---|
| 服务端**完全收不到**探针 | Docker 用了 bridge / 防火墙挡 UDP 12091 / 网卡选错 | 改 host 网络；放行 12091；加 `--interface` |
| 收到探针但提示"未登记的项目" | pid 与 `projects.json` 不一致 | 核对固件 `PROJECT_ID` 与表里 key |
| 一直"裸探针无 pid，且不答" | 设备还是 v2.0.0 旧固件 | 刷 v2.1.0，或把 `default` 填成对象 |
| 设备拿到 IP 但上报失败 | 该 ip 设备不可达 / 18830、12090 未放行 | 检查同网段与端口 |
| 设备 45 秒后退回 AP 配网 | 上述任一失败都会导致超时 | 按上表逐项排查 |
| 两个项目互相串 | `default` 不是 null，或未登记 pid 的设备被兜底接入 | `default` 设 null，所有 pid 显式登记 |

---

## 十、回退

| 回退目标 | 操作 | 代价 |
|---|---|---|
| **固件回 v2.0.0（现成可烧）** | `esp32-4spi-st7735s-tft-v2.0.0\flash.bat COM9` | 接线不动；设备改发裸探针，服务端需开 `default` |
| 固件回 v1.0.0 | `esp32-4spi-st7735s-oled\flash.bat COM9` | 同上 |
| 源码回 v2.0.0 | 用 `firmware/esp32-7oled/`（上游基线，原样保留） | 需重新编译 |
| 服务端回 v1.0 | `git` 取旧版 `beacon_server.py` | 配置格式兼容，无需改 `projects.json` |

> 所有版本切换**接线都不用动**。

---

## 十一、扩展：再加一个项目

1. `projects.json` 里加一条 `"新pid": {"ip":..., "port":..., ...}`
2. 用该 pid 编译一份固件（改 `PROJECT_ID`）
3. 重启服务端加载新配置：`docker compose restart` 或 `systemctl restart envmon-beacon`

不用动已有设备，也不用动已有项目。
