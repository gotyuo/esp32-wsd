#!/usr/bin/env bash
# ============================================================
# SQLite 数据库在线备份（不停机，利用 SQLite WAL 快照）
# 用法: bash scripts/backup_db.sh [输出目录]
# 输出: envmon_backup_YYYYmmdd_HHMMSS.db
# 建议加入 crontab: 0 3 * * * bash /path/scripts/backup_db.sh /backup
# ============================================================
set -e
cd "$(dirname "$0")/.."

OUT_DIR="${1:-$(pwd)/backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$OUT_DIR/envmon_backup_${STAMP}.db"

if docker ps --format '{{.Names}}' | grep -q '^envmon-backend$'; then
    echo ">> 通过容器内 SQLite 备份（一致性快照）..."
    docker exec envmon-backend python -c "
import sqlite3, os, shutil
src = os.environ.get('DB_PATH','/data/envmon.db')
dst = '/data/_backup_tmp.db'
con = sqlite3.connect(src)
with con:
    con.execute('PRAGMA wal_checkpoint(FULL)')
    bak = sqlite3.connect(dst)
    con.backup(bak)
    bak.close()
con.close()
print(dst)
" >/dev/null
    docker cp envmon-backend:/data/_backup_tmp.db "$OUT"
    docker exec envmon-backend rm -f /data/_backup_tmp.db
else
    echo ">> 未检测到运行中的容器，直接复制数据文件..."
    cp "$(pwd)/data/envmon.db" "$OUT"
fi

echo ">> 备份完成: $OUT"
# 仅保留最近 30 份
ls -1t "$OUT_DIR"/envmon_backup_*.db 2>/dev/null | tail -n +31 | xargs -r rm -f
