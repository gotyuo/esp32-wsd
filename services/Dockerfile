# EnvMon beacon 服务端镜像
# 版本: 20261006-v1.1
# 注意：运行时必须用 --network host（见 docker-compose.yml），否则收不到多播
FROM python:3.11-slim

LABEL version="20261006-v1.1" \
      description="EnvMon UDP multicast discovery / handshake server"

WORKDIR /app
COPY beacon_server.py projects.json ./

# -u 关闭输出缓冲，保证日志实时可见
CMD ["python", "-u", "beacon_server.py", "--config", "projects.json"]
