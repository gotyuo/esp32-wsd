# services/ — EnvMon 服务端组件

| 文件 | 版本 | 说明 |
|------|------|------|
| `beacon_server.py` | `20261006-v1.1` | **设备发现/握手服务端**（核心）。UDP 多播应答，按项目路由下发配置 |
| `projects.json` | `20261006-v1.1` | 多项目路由表：哪个 pid 对应哪台服务器 |
| `telemetry_relay.py` | `20261006-v1.0` | HTTP→HTTPS 上报转发器（固件只支持明文 http 时使用） |
| `beacon-project-id.patch.md` | `20261006-v1.0` | 固件侧补丁说明（L1 编译期 pid / L2 运行时 pid） |
| `start-beacon.bat` / `.sh` | `20261006-v1.1` | 一键启动脚本 |
| `Dockerfile` / `docker-compose.yml` | `20261006-v1.1` | 容器部署（**必须 host 网络**） |
| `systemd/envmon-beacon.service` | `20261006-v1.1` | Linux 常驻服务 |

> 完整部署流程见 **`docs/部署说明-beacon服务端.md`**。

---

## 30 秒快速开始

```bash
# 1) 编辑项目路由表（改成你的实际服务器地址）
notepad projects.json          # Windows
vim projects.json              # Linux

# 2) 启动
start-beacon.bat               # Windows
./start-beacon.sh              # Linux / macOS

# 3) 另开一个终端自测（模拟设备探针）
python beacon_server.py --probe pid=clinic-a did=esp32-4d3f54
```

看到 `收到应答 ... {"ip":"192.168.2.220",...}` 就说明服务端工作正常。

---

## beacon_server.py 参数

| 参数 | 说明 |
|---|---|
| `--config <文件>` | 指定路由表，默认 `projects.json` |
| `--interface <IP>` | **多网卡机器必填**，指定多播出口网卡（如 `192.168.2.220`） |
| `--probe [pid=xx] [did=yy]` | 自测模式：模拟设备发一次探针，6 秒内看有无应答 |

---

## 排障速查

| 现象 | 原因 / 处理 |
|---|---|
| 自测 6 秒无应答 | 服务端没运行 / Docker 用了 bridge 网络 / 防火墙挡了 UDP 12091 / 网卡选错 |
| 服务端收到探针但"未登记的项目" | `projects.json` 里没有该 pid；确认固件烧的 `PROJECT_ID` 与表里一致 |
| 一直"裸探针无 pid" | 设备还是 v2.0.0 旧固件；把 `default` 填成对象，或刷 v2.1.0 |
| 设备拿到了 IP 但上报失败 | 应答里的 ip 设备不可达；检查同网段、TCP 18830/12090 是否放行 |
| Docker 里收不到任何探针 | 没用 `network_mode: host` |

---

## 版本与回退

| 版本 | 变化 |
|---|---|
| `20261006-v1.1` | 新增 `--interface`、日志时间戳、配置校验（当前版） |
| `20261006-v1.0` | 首个可用版本，多项目路由已实测 |

回退：直接用 git 取 v1.0 的 `beacon_server.py` 即可，配置格式兼容。
