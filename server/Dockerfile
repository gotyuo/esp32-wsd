FROM python:3.12-slim

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY app ./app
COPY schema.sql .

# SQLite 数据目录
RUN mkdir -p /data
VOLUME ["/data"]

ENV DB_PATH=/data/envmon.db \
    SCHEMA_FILE=/app/schema.sql \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
