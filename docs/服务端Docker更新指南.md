# 服务端 Docker 更新指南

> 版本：`20261006-v1.0` ｜ 适用：本仓库 `server/` 这套 Docker Compose 部署
> （Mosquitto MQTT + FastAPI 后端 + SQLite + Piper TTS + 设备发现应答器）

---

## 零、先记住三件事（决定了更新方式）

| 事实 | 影响 |
|---|---|
| `backend` / `discovery` 两个服务都是 **`build: .`**（用本目录 Dockerfile 本地构建，没有远程镜像） | 更新**必须重建镜像**，`docker pull` 无效 |
| `.env` 和 `data/` 都写在 `.gitignore` 里 | `git pull` **不会覆盖**你的配置和数据库 |
| 数据库是 **bind mount** `./data:/data` | 重建容器**不丢数据**；但升级前仍建议手动备份一次 |

一句话：**更新 = 拉代码 → 重建镜像 → 重启容器**。

---

## 一、标准更新流程（SSH 命令行，推荐）

```bash
# 1) 进到 docker-compose.yml 所在目录（按你的实际路径改）
cd /vol1/docker/esp32-wsd/server

# 2) 备份：数据库 + 配置（务必先做）
ts=$(date +%Y%m%d-%H%M%S)
cp data/envmon.db data/envmon.db.bak-$ts
cp .env .env.bak-$ts
echo "已备份到 *.bak-$ts"

# 3) 拉取新代码
git pull

# 4) 看这次更新了什么（建议）
git log --oneline -5

# 5) 重建镜像
docker compose build backend discovery

# 6) 让新镜像生效（会自动重建容器）
docker compose up -d

# 7) 确认状态
docker compose ps
docker compose logs --tail=50 backend discovery
```

**成功判据（三条都对才算成功）**

1. `docker compose ps` 中 `envmon-backend`、`envmon-discovery` 均为 `Up`；
2. `discovery` 日志出现：
   `envmon-discovery 20261006-v2.0 | listening 239.255.1.1:12091 (iface=... reply_ip=...)`
3. 浏览器能打开 `http://172.22.22.83:12090` 并正常登录。

---

## 二、只更新设备发现服务（最常用）

只想让 v2.0 发现脚本生效，不必动 backend：

```bash
cd /vol1/docker/esp32-wsd/server
git pull
docker compose up -d --build discovery
docker compose logs -f --tail=30 discovery
```

同理，只更新后端：

```bash
docker compose up -d --build backend
```

---

## 三、`.env` 新增配置要手动补（关键）

`git pull` 只更新 `.env.example`，**不会动你的 `.env`**。每次更新后对照差异补新变量：

```bash
diff <(grep -v '^#' .env.example | grep -v '^$') \
     <(grep -v '^#' .env        | grep -v '^$')
```

本次（发现服务 v2.0）新增：

```ini
# 回复给设备的服务器 IP。强烈建议显式写死，否则自动探测可能选到 docker0(172.17.x)
DISC_IP=172.22.22.83

# 多项目路由表（单项目可不设）。未登记的 pid 一律不应答
# DISC_PROJECTS={"clinic-a":{"ip":"172.22.22.83","port":18830,"user":"envmon","pass":"envmon"}}

# 多项目共存时设 0，避免未登记的旧设备被本服务器认领
# DISC_ALLOW_LEGACY=0
```

改完 `.env` 后执行 `docker compose up -d`（环境变量变化会自动重建受影响容器）。

---

## 四、飞牛 fnOS / 图形界面部署的更新

**A. 图形界面操作**

1. fnOS → **Docker → 项目（Compose）**，找到本项目；
2. 点 `停止`；
3. 在该项目目录下执行 `git pull`（用 SSH 或文件管理器里的终端）；
4. 回界面点 **`构建`**（重建镜像）→ **`启动`**。

**B. 终端执行（推荐，最可控）**

直接照第一节的命令走，图形界面只用来观察容器状态。

> ⚠️ 在 fnOS「容器」页面单独点某个容器的 **重启**，**不会重建镜像**——改了代码或脚本，必须 `build` + `up`，或在图形界面点「构建 / 重新部署」。

---

## 五、`git pull` 遇到冲突

`.env`、`data/` 已被忽略，正常不会冲突。若报错来自**你自己改过的仓库文件**：

```bash
git status          # 看是哪些文件被改
git stash           # 暂存本地改动（之后 git stash pop 可找回）
git pull
git stash pop
```

若想让服务端目录**强制**与远端一致（**会丢弃本地对仓库文件的改动，但不影响 `.env` 与 `data/`**）：

```bash
git fetch origin
git reset --hard origin/main
```

---

## 六、回退

```bash
# 查看可用的版本 / 备份点
git tag -l | sort | tail -20

# 回退整个 server/ 到某个 tag
git checkout <tag> -- server/
docker compose build backend discovery
docker compose up -d
```

数据库回退（仅当新版改坏数据时用）：

```bash
docker compose stop backend
cp data/envmon.db.bak-<时间戳> data/envmon.db
docker compose start backend
```

---

## 七、更新检查清单

- [ ] 已备份 `data/envmon.db` 与 `.env`
- [ ] `git pull` 成功、无冲突
- [ ] 已按 `.env.example` 补齐新增变量
- [ ] 已 `docker compose build`
- [ ] 已 `docker compose up -d`
- [ ] `docker compose ps` 全部 Up
- [ ] `backend` / `discovery` 日志无 ERROR
- [ ] 浏览器可登录，设备在列表中可见
- [ ] 若更新了发现服务，设备端已能自动识别到服务器

---

## 八、常见坑

| 现象 | 原因 / 处理 |
|---|---|
| 更新完行为没变 | 只 `restart` 没 `build`；改用 `docker compose up -d --build` |
| 设备发现不到服务器 | `discovery` 必须是 `network_mode: host`；放行 UDP 12091；多网卡设 `DISC_IFACE` |
| 设备拿到 IP 却连不上 | `DISC_IP` 落到了 docker0 的 172.17.x；改成宿主机内网 IP（`172.22.22.83`） |
| 端口被占用起不来 | 检查 12090 / 18830 是否被占（`ss -lntp` 或 `netstat -ano`） |
| 拉基础镜像超时 | `./docker-pull-cn.sh python 3.12-slim`、`./docker-pull-cn.sh eclipse-mosquitto 2` |
| 担心误删数据 | **绝不要** `docker compose down -v`；数据在 `./data`（bind mount），`-v` 会删匿名卷并可能误伤 |

---

## 九、版本与备份约定

- **改前备份**：打备份点 tag `backup-<YYYYMMDD-HHMMSS>-pre-<事由>` 并推送；
- **交付打版本 tag**：如 `server-20261006-v2.0`；
- **回退优先用 tag**：`git checkout <tag> -- server/`。

```bash
# 打备份点示例
git tag -a backup-$(date +%Y%m%d-%H%M%S)-pre-update -m "更新前备份"
git push --tags
```

---

## 附：端口速查

| 端口 | 用途 | 归属 |
|---|---|---|
| 12090 | Web 管理界面 + REST API | 服务端（`.env` 的 `WEB_PORT`） |
| 18830 | MQTT 设备接入（映射容器内 1883） | 服务端（`.env` 的 `MQTT_PORT`） |
| 12091 (UDP) | 设备发现多播 | `discovery` 容器（host 网络） |
| 80 | 设备自身配网页（AP 模式 `192.168.4.1`） | **设备侧**，与服务端无关 |
