#!/bin/bash
# ============================================================
# 国内网络环境 Docker 镜像拉取辅助脚本
# 用法: ./docker-pull-cn.sh <镜像名> [tag]
# 示例: ./docker-pull-cn.sh eclipse-mosquitto 2
#       ./docker-pull-cn.sh python 3.12-slim
#
# 原理: 通过可达的中文镜像源拉取，随后重打标准标签，
#       使 docker-compose.yml 无需改动。
# ============================================================
set -e

IMG="$1"
TAG="${2:-latest}"
MIRRORS=("docker.1ms.run" "docker.m.daocloud.io" "hub.rat.dev")

if [ -z "$IMG" ]; then
  echo "用法: $0 <镜像名> [tag]"
  exit 1
fi

echo ">>> 目标: $IMG:$TAG"

for M in "${MIRRORS[@]}"; do
  echo ">>> 尝试镜像源: $M ..."
  if docker pull "$M/library/$IMG:$TAG" 2>/dev/null || docker pull "$M/$IMG:$TAG" 2>/dev/null; then
    echo ">>> 拉取成功，重打标签: $IMG:$TAG"
    docker tag "$M/library/$IMG:$TAG" "$IMG:$TAG" 2>/dev/null || docker tag "$M/$IMG:$TAG" "$IMG:$TAG" 2>/dev/null
    echo ">>> 完成 ✓"
    exit 0
  fi
  echo ">>> $M 失败，切换下一个"
done

echo "!!! 所有镜像源均失败，请检查网络或稍后重试"
exit 1