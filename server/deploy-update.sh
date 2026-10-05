#!/usr/bin/env bash
# ============================================================
# EnvMon 服务端 一键更新脚本（Linux / fnOS / 群晖 / 树莓派）
# 版本: 20261006-v1.1
#
# 依次做四件事：备份 -> 拉取最新代码 -> 重建镜像 -> 重启并自检
#
# 用法（在本脚本所在目录执行）:
#   ./deploy-update.sh                # 标准更新
#   ./deploy-update.sh --force        # 本地有改动时，强制与远端一致
#   ./deploy-update.sh --no-build     # 只拉代码，不重建镜像
#   ./deploy-update.sh --rollback     # 回退到上次更新前的提交
#   ./deploy-update.sh --check-env    # 只检查 .env 是否缺新增变量，不做任何改动
#
# 说明:
#   - 本仓库为公开仓库，拉取不需要 token。
#   - backend / discovery 都是 build: .（本地构建），故必须重建镜像。
#   - 数据库在 ./data（bind mount），重建容器不丢数据；脚本仍会先备份。
#   - .env 与 data/ 已被 .gitignore 忽略，代码更新不会覆盖它们。
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BRANCH="${DEPLOY_BRANCH:-main}"
SERVICES=(backend discovery)
FORCE=0
NO_BUILD=0
ROLLBACK=0
CHECK_ENV_ONLY=0
STATE_FILE="data/.deploy-last-commit"
BACKUP_KEEP=5

for arg in "$@"; do
  case "$arg" in
    --force)      FORCE=1 ;;
    --no-build)   NO_BUILD=1 ;;
    --rollback)   ROLLBACK=1 ;;
    --check-env)  CHECK_ENV_ONLY=1 ;;
    -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg （用 -h 查看用法）"; exit 2 ;;
  esac
done

log()  { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[警告]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { printf '\033[1;32m[完成]\033[0m %s\n' "$*"; }

# ---------- 0) 环境检查 ----------
command -v git >/dev/null 2>&1 || die "未安装 git。Debian/Ubuntu/fnOS: apt-get update && apt-get install -y git"
command -v docker >/dev/null 2>&1 || die "未安装 docker 或 docker 不在 PATH"
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  die "docker compose 不可用（v2 插件与 v1 命令都找不到）"
fi
log "目录: $SCRIPT_DIR"
log "命令: ${DC[*]}"
mkdir -p data

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
  warn "当前目录不是 git 仓库，无法自动拉取代码。"
  cat <<'EOF'

  【方案 A · 推荐】在同级目录克隆一份，再把配置与数据迁过去：
      cd ../
      git clone --depth 1 --branch main https://gitee.com/hotyuo/esp32-wsd.git esp32-wsd-git
      cp -a esp32-wsd/server/.env          esp32-wsd-git/server/.env
      cp -a esp32-wsd/server/data          esp32-wsd-git/server/data
      cd esp32-wsd-git/server
      ./deploy-update.sh

  【方案 B · 就地转成 git 仓库】先备份，再执行：
      cp -a .env .env.bak-before-git 2>/dev/null || true
      git init -b main || { git init; git symbolic-ref HEAD refs/heads/main; }
      git remote add origin https://gitee.com/hotyuo/esp32-wsd.git
      git fetch --depth 1 origin main
      git reset --hard FETCH_HEAD
      # .env 与 data/ 已被忽略，不会被覆盖；其余文件以远端为准
      ./deploy-update.sh

EOF
  exit 1
fi
log "仓库根: $REPO_ROOT"

git_in_repo() { git -C "$REPO_ROOT" "$@"; }

# ---------- 1) 备份 ----------
backup_once() {
  local ts; ts="$(date +%Y%m%d-%H%M%S)"
  local did=0
  if [ -f data/envmon.db ]; then
    cp -a data/envmon.db "data/envmon.db.bak-$ts"; did=1
  fi
  if [ -f .env ]; then
    cp -a .env ".env.bak-$ts"; did=1
  fi
  if [ "$did" = 1 ]; then
    log "已备份（时间戳 $ts）: data/envmon.db.bak-$ts / .env.bak-$ts"
  else
    warn "未发现 envmon.db 或 .env，跳过备份（首次部署属正常）"
  fi
  # 只保留最近 N 份
  if [ -d data ]; then
    ls -1t data/envmon.db.bak-* 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)) | while read -r f; do rm -f "$f"; done || true
  fi
  ls -1t .env.bak-* 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)) | while read -r f; do rm -f "$f"; done || true
}

# ---------- 2) .env 缺失变量检查 ----------
check_env() {
  [ -f .env.example ] || return 0
  if [ ! -f .env ]; then
    warn ".env 不存在，请先执行: cp .env.example .env  并修改其中密码等项"
    return 0
  fi
  local missing
  missing="$(comm -23 \
      <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env.example | cut -d= -f1 | sort -u) \
      <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env         | cut -d= -f1 | sort -u) || true)"
  if [ -n "$missing" ]; then
    warn ".env 缺少以下变量（.env.example 中已有，按需补上）："
    printf '%s\n' "$missing" | sed 's/^/         /'
    printf '         -> 说明见 .env.example 对应条目；补完执行: %s up -d\n' "${DC[*]}"
  else
    ok ".env 与 .env.example 的变量键一致"
  fi
}

if [ "$CHECK_ENV_ONLY" = 1 ]; then
  check_env
  exit 0
fi

# ---------- 3) 回退模式 ----------
if [ "$ROLLBACK" = 1 ]; then
  [ -f "$STATE_FILE" ] || die "没有记录到上次更新前的提交（$STATE_FILE 不存在）。可手动:
      git -C $REPO_ROOT log --oneline -10
      git -C $REPO_ROOT reset --hard <commit>"
  PREV="$(cat "$STATE_FILE")"
  log "回退到提交: $(git_in_repo rev-parse --short "$PREV")"
  git_in_repo reset --hard "$PREV"
  "${DC[@]}" build "${SERVICES[@]}"
  "${DC[@]}" up -d
  "${DC[@]}" ps || true
  ok "已回退并重启。数据库未回退；如需一并回退，手动覆盖 data/envmon.db（见文档第六节）。"
  exit 0
fi

# ---------- 4) 拉取最新代码 ----------
log "拉取 origin/$BRANCH ..."
git_in_repo fetch --prune --depth 1 origin "$BRANCH"
LOCAL="$(git_in_repo rev-parse HEAD)"
REMOTE="$(git_in_repo rev-parse FETCH_HEAD)"

if [ "$LOCAL" = "$REMOTE" ]; then
  ok "已是最新版本 ($(git_in_repo rev-parse --short HEAD))，跳过代码更新"
else
  log "本地 $(git_in_repo rev-parse --short "$LOCAL")  ->  远端 $(git_in_repo rev-parse --short "$REMOTE")"
  echo "     本次包含的提交："
  git_in_repo log --oneline "$LOCAL..$REMOTE" 2>/dev/null | sed 's/^/         /' || true

  DIRTY="$(git_in_repo status --porcelain | head -20 || true)"
  if [ -n "$DIRTY" ]; then
    warn "本地有未提交改动："
    printf '%s\n' "$DIRTY" | sed 's/^/         /'
    if [ "$FORCE" = 1 ]; then
      warn "--force 已启用：这些改动将被远端覆盖（.env / data 不受影响）"
    else
      die "为避免覆盖你的改动已中止。可先 git stash 后重跑，或加 --force。"
    fi
  fi

  backup_once
  git_in_repo reset --hard "$REMOTE"
  printf '%s\n' "$LOCAL" > "$STATE_FILE"
  ok "代码已更新到 $(git_in_repo rev-parse --short HEAD)；上次提交已记录（可用 --rollback 回退）"
fi

check_env
[ -f "$STATE_FILE" ] || printf '%s\n' "$LOCAL" > "$STATE_FILE"

# ---------- 5) 重建镜像并重启 ----------
if [ "$NO_BUILD" = 1 ]; then
  warn "--no-build：跳过重建，仅重启容器（适用于仅改 .env 的场景）"
  "${DC[@]}" up -d
else
  log "重建镜像：${SERVICES[*]} ..."
  "${DC[@]}" build "${SERVICES[@]}"
  log "启动 / 重建容器 ..."
  "${DC[@]}" up -d
fi

# ---------- 6) 自检 ----------
sleep 3
log "容器状态："
"${DC[@]}" ps || true

WEB_PORT_VAL="$(sed -n 's/^WEB_PORT=//p' .env 2>/dev/null | head -1 | tr -d ' \r"')"
WEB_PORT_VAL="${WEB_PORT_VAL:-12090}"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${WEB_PORT_VAL}/" 2>/dev/null || echo 000)"
if [ "$CODE" != "000" ] && [ "$CODE" -lt 500 ] 2>/dev/null; then
  ok "后端自检通过: http://127.0.0.1:${WEB_PORT_VAL}/ -> HTTP $CODE"
else
  warn "后端自检未通过（HTTP $CODE）。查看日志: ${DC[*]} logs --tail=100 backend"
fi

log "发现服务日志（最近 15 行）："
"${DC[@]}" logs --tail=15 discovery 2>/dev/null | sed 's/^/         /' || true

echo
ok "流程结束。若异常可用: ./deploy-update.sh --rollback"
