# pitwatch 镜像：前端在构建阶段编译好，运行阶段只留 Python + 静态产物。
# 单端口 8000：后端顺便把页面发出去（frontend_dist_dir() 找到 frontend/dist 就自动挂载）。

# ---------- ① 构建前端 ----------
FROM node:20-alpine AS frontend

WORKDIR /build
# 先只拷依赖清单：改代码时这一层能命中缓存，不用重装 node_modules
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# 注意脚本是 "tsc && vite build"：类型错误会让构建失败，这是有意的
RUN npm run build

# ---------- ② 运行 ----------
FROM python:3.12-slim

# tzdata 不是可选项：系统全部时间戳都是**本地时间**（naive datetime），
# 容器默认 UTC 会让"今天"整体偏 8 小时 —— 观测日期、日报期次都会错。
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY launcher.py VERSION CHANGELOG.md ./
COPY --from=frontend /build/dist ./frontend/dist

# 运行数据统一放 /data，方便挂持久卷；不挂也能跑（演示模式下重启会自己长回示例数据）
RUN mkdir -p /data/database /data/templates /data/uploads /data/backups /data/logs /data/cache/holidays

ENV MONITOR_DB_URL=sqlite:////data/database/monitor.db \
    MONITOR_TEMPLATES_DIR=/data/templates \
    MONITOR_BACKUP_DIR=/data/backups \
    MONITOR_UPLOAD_DIR=/data/uploads \
    MONITOR_HOLIDAY_CACHE_DIR=/data/cache/holidays \
    MONITOR_LOG_DIR=/data/logs \
    MONITOR_DEMO_SEED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/health" || exit 1

# 平台会注入 PORT（Zeabur / Render / HF 都是），没有就用 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
