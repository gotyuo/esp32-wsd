# 服务端 Linux 部署与更新指南

> 版本：`20261006-v1.1` ｜ 适用：本仓库 `server/`（Mosquitto MQTT + FastAPI 后端 + SQLite + Piper TTS + 设备发现应答器）
> 一句话：**首次**照第一节从零部署；**以后**每次更新只需跑 `server/deploy-update.sh`。

---

## 零、三条决定性事实（先看，能省掉一半困惑）

| 事实 | 影响 |
|---|---|
| `backend` / `discovery` 都是 **`build: .`**，用本目录 Dockerfile 本地构建 | 更新**必须重建镜像**；`docker pull` 无效（没有远程镜像） |
| 仓库是**公开**的（已实测匿名 `git ls-remote` 成功） | 服务器上 `git clone` / `git pull` **不需要 token** |
| `.env` 与 `data/` 已在 `.gitignore`（已实测 `git ls-files` 均未跟踪） | `git pull` **不会覆盖**你的配置与数据库 |

数据库是 bind mount `./data:/data`，**重建容器不丢数据**；但脚本仍会先备份一份。

---

## 一、从零部署（Linux 首次）

### 1. 准备依赖

```bash
docker --version            # 必须有，且要有 compose v2 插件
docker compose version      # 输出 v2.x 即可
git --version               # 没有则装：
# Debian / Ubuntu / fnOS：
sudo apt-get update && sudo apt-get install -y git
```

### 2. 克隆仓库（公开仓库，无需 token）

```bash
sudo mkdir -p /vol1/docker && cd /vol1/docker
git clone --depth 1 --branch main https://gitee.com/hotyuo/esp32-wsd.git
cd esp32-wsd/server
```

`--depth 1` 只取最新快照，比完整克隆小很多，更新照样能用。
**想更省空间**（只要服务端与文档）：

```bash
git clone --depth 1 --filter=blob:none --sparse https://gitee.com/hotyuo/esp32-wsd.git
cd esp32-wsd && git sparse-checkout set server docs
```

> 若 `--filter` 报不支持，去掉它、退回上一条普通克隆即可。

### 3. 写配置 `.env`

```bash
cp .env.example .env
vi .env
```

**至少要确认/修改这几项：**

| 变量 | 建议值 | 说明 |
|---|---|---|
| `ADMIN_PASS` | **自己设强密码** | 首次启动自动建管理员，默认 `admin123` 必须改 |
| `MQTT_PASS` | **自己设** | 设备端配网页要填同一个 |
| `DISC_IP` | `172.22.22.83` | **必须显式写死**：多网卡/容器环境下自动探测常选到 docker0(172.17.x)，设备拿到也连不上 |
| `WEB_PORT` | `12090` | Web 管理界面 / API |
| `MQTT_PORT` | `18830` | 设备 MQTT 接入 |
| `DISC_ALLOW_LEGACY` | 单项目留空(默认1)；多项目设 `0` | 是否应答旧固件的裸探针 |
| `DISC_PROJECTS` | 单项目留空；多项目填 JSON | 按 pid 路由，未登记项目一律不应答 |
| `INTEGRATION_TOKEN` | 可选 | 集成平台 XML 接收接口鉴权 |

### 4. 构建并启动

```bash
docker compose up -d --build
```

> TTS（Piper）**首次启动会下载中文语音模型**（约 100–200MB），健康检查给了 180s 宽限，
> 期间 `backend` 会等它，属正常。想先跳过语音：把 `.env` 里 `TTS_ENABLED=0`。

### 5. 验证

```bash
docker compose ps                 # backend / discovery / mosquitto / piper 都应为 Up
docker compose logs --tail=50 backend discovery
curl -I http://127.0.0.1:12090/   # 期望 HTTP 200/302/401 之类（非 5xx 即正常）
```

- 本机：`http://127.0.0.1:12090`
- 局域网：`http://172.22.22.83:12090`
- `discovery` 日志应出现：`envmon-discovery 20261006-v2.0 | listening 239.255.1.1:12091 (iface=... reply_ip=172.22.22.83)`

### 6. 放行端口（有防火墙时）

```bash
sudo ufw allow 18830/tcp     # MQTT 设备接入
sudo ufw allow 12090/tcp     # Web 管理界面 / API
sudo ufw allow 12091/udp     # 设备发现多播（少了这条，设备发现不到服务器）
```

### 7. 关于开机自启

`docker-compose.yml` 里四个服务都是 `restart: unless-stopped`，**Docker 服务自启后容器会自动拉起**，
无需额外配置。若你的 Docker 未开机自启：`sudo systemctl enable --now docker`。

---

## 二、日常更新（已有部署，一条命令）

```bash
cd /vol1/docker/esp32-wsd/server
./deploy-update.sh
```

脚本依次做：**备份 `.env` 与数据库 → 拉取最新代码 → 检查 .env 缺哪些新变量 → 重建镜像 → 重启 → 自检**，
并在结束时打印容器状态、后端 HTTP 自检结果与发现服务日志。

| 参数 | 用途 |
|---|---|
| （无） | 标准更新 |
| `--force` | 本地有改动时强制与远端一致（覆盖的是仓库文件，不碰 `.env`/`data`） |
| `--no-build` | 只重启不重建（仅改了 `.env` 时用） |
| `--rollback` | 回退到**上次更新前**的提交并重启 |
| `--check-env` | 只检查 `.env` 是否缺新增变量，不做任何改动 |
| `-h` | 帮助 |

首次使用请给执行权限：

```bash
chmod +x deploy-update.sh
```

**不想用脚本的手工等价操作：**

```bash
cd /vol1/docker/esp32-wsd/server
ts=$(date +%Y%m%d-%H%M%S)
cp data/envmon.db data/envmon.db.bak-$ts
cp .env .env.bak-$ts

git fetch --prune --depth 1 origin main
git reset --hard FETCH_HEAD          # 或 git pull --ff-only

docker compose build backend discovery
docker compose up -d
docker compose ps && docker compose logs --tail=50 backend discovery
```

---

## 三、我原先那份部署目录不是 git 仓库怎么办

如果你之前是**手动上传/拷贝** `server/` 目录上去的（没有 `.git`），有两种做法：

### 方案 A（推荐）：重新克隆，把配置文件迁过去

```bash
cd /vol1/docker
git clone --depth 1 --branch main https://gitee.com/hotyuo/esp32-wsd.git esp32-wsd-git

# 迁移配置与数据（关键两步）
cp -a /原路径/server/.env   esp32-wsd-git/server/.env
cp -a /原路径/server/data   esp32-wsd-git/server/data

cd esp32-wsd-git/server
docker compose up -d --build      # 用新的构建产物起容器
docker compose logs --tail=50 backend
# 确认 OK 后，停掉/删除旧目录的容器即可
```

### 方案 B：就地把它变成 git 仓库（保留现有 `.env` / `data`）

```bash
cd /原路径/server
cp -a .env .env.bak-before-git 2>/dev/null || true   # 保险

git init -b main || { git init; git symbolic-ref HEAD refs/heads/main; }
git remote add origin https://gitee.com/hotyuo/esp32-wsd.git
git fetch --depth 1 origin main
git reset --hard FETCH_HEAD

# .env 与 data/ 已被忽略，不会被覆盖；其余文件以远端为准
./deploy-update.sh
```

---

## 四、只更新单个服务（不必全量重建）

```bash
cd /vol1/docker/esp32-wsd/server

# 只更新设备发现服务（最先需要，v2.0 才支持 JSON 探针 + pid 路由）
docker compose up -d --build discovery
docker compose logs -f --tail=30 discovery

# 只更新后端
docker compose up -d --build backend
```

---

## 五、`.env` 新增配置要手动补（最容易漏）

`git pull` 只更新 `.env.example`，**不会动你的 `.env`**。脚本会自动列出缺失项，也可手动比对：

```bash
diff <(grep -v '^#' .env.example | grep -v '^$') \
     <(grep -v '^#' .env        | grep -v '^$')
```

本次（发现服务 v2.0）涉及：

```ini
DISC_IP=172.22.22.83          # 强烈建议显式设
# DISC_PROJECTS={"clinic-a":{"ip":"172.22.22.83","port":18830,"user":"envmon","pass":"envmon"}}
# DISC_ALLOW_LEGACY=0         # 多项目共存时设 0
```

改完执行 `docker compose up -d` 生效（环境变量变化会自动重建容器）。

---

## 六、回退

```bash
# ① 代码回退（自动记住上次更新前的提交）
./deploy-update.sh --rollback

# ② 或按 tag 手动回退
git tag -l | sort | tail -20
git reset --hard <tag>            # 或 git checkout <tag> -- .
docker compose build backend discovery && docker compose up -d
```

数据库回退（**仅当新版改坏数据时用**）：

```bash
docker compose stop backend
cp data/envmon.db.bak-<时间戳> data/envmon.db
docker compose start backend
```

---

## 七、飞牛 fnOS 图形界面的用法

- **推荐**：SSH 里跑 `./deploy-update.sh`，图形界面只用来**观察**容器状态。
- 若坚持用界面：先把项目目录 `git pull` 更新到最新，再到 **Docker → 项目** 点 **`构建`**（或「重新部署」），最后 `启动`。
- ⚠️ 在「容器」页面单独点某个容器的 **重启**，**不会重建镜像**——改了代码必须 `build`。

---

## 八、更新检查清单

- [ ] `docker --version` / `docker compose version` / `git --version` 均可用
- [ ] 已 `cp .env.example .env` 并填好 `ADMIN_PASS` / `MQTT_PASS` / `DISC_IP`
- [ ] 已 `docker compose up -d --build`
- [ ] `docker compose ps` 全部 Up
- [ ] `backend` / `discovery` 日志无 ERROR
- [ ] `http://<服务器IP>:12090` 能打开并登录
- [ ] 防火墙放行 18830/tcp、12090/tcp、12091/udp
- [ ] 更新后已按提示补齐 `.env` 新增变量

---

## 九、常见坑

| 现象 | 原因 / 处理 |
|---|---|
| 更新完行为没变 | 只 `restart` 没 `build`；用 `docker compose up -d --build` |
| `git clone` 报认证失败 | 本仓库是公开的，通常无需 token；若被 Gitee 限流，稍后重试或改用 SSH 方式 |
| 设备发现不到服务器 | `discovery` 必须 `network_mode: host`；放行 **UDP 12091**；多网卡设 `DISC_IFACE` |
| 设备拿到 IP 却连不上 | `DISC_IP` 落到了 docker0 的 172.17.x；改成宿主机内网 IP `172.22.22.83` |
| 端口被占用起不来 | 检查 12090 / 18830 是否被占（`ss -lntp`） |
| 拉基础镜像超时 | `./docker-pull-cn.sh python 3.12-slim`、`./docker-pull-cn.sh eclipse-mosquitto 2` |
| Piper 一直 unhealthy | 首次要下模型（约 3–5 分钟）；不想用语音就设 `TTS_ENABLED=0` |
| 担心误删数据 | **绝不要** `docker compose down -v` |

---

## 十、版本与备份约定

- **改前备份**：打备份点 tag `backup-<YYYYMMDD-HHMMSS>-pre-<事由>` 并推送；
- **交付打版本 tag**：如 `server-20261006-v2.0`；
- **回退优先用 tag**：`git reset --hard <tag>` 或 `git checkout <tag> -- .`。

```bash
git tag -a backup-$(date +%Y%m%d-%H%M%S)-pre-update -m "更新前备份"
git push --tags
```

---

## 附：端口速查

| 端口 | 协议 | 用途 | 归属 |
|---|---|---|---|
| 12090 | TCP | Web 管理界面 + REST API（`.env` 的 `WEB_PORT`） | 服务端 |
| 18830 | TCP | MQTT 设备接入（映射容器内 1883，`.env` 的 `MQTT_PORT`） | 服务端 |
| 12091 | UDP | 设备发现多播（`discovery`，host 网络） | 服务端 |
| 10200 | TCP | Piper TTS（容器内，不对外） | 服务端内部 |
| 80 | TCP | 设备自身配网页（AP 模式 `192.168.4.1`） | **设备侧**，与服务端无关 |
