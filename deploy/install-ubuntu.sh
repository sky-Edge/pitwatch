#!/usr/bin/env bash
# ============================================================
# pitwatch 一键部署（Ubuntu 20.04 / 22.04 / 24.04）
#
# 用法（在服务器上执行，需要 sudo）：
#   curl -fsSL https://raw.githubusercontent.com/sky-Edge/pitwatch/master/deploy/install-ubuntu.sh | sudo bash
#
# 想先看看效果（自动生成一套演示数据）：
#   curl -fsSL ... | sudo bash -s -- --demo
#
# 重复执行是安全的：会重新拉代码、重建镜像、重启容器，数据目录不动。
# ============================================================
set -euo pipefail

REPO="https://github.com/sky-Edge/pitwatch.git"
APP_DIR="/opt/pitwatch"
NAME="pitwatch"
PORT="${PORT:-8000}"
DEMO=0

for arg in "$@"; do
  case "$arg" in
    --demo) DEMO=1 ;;
    --port=*) PORT="${arg#*=}" ;;
    *) ;;
  esac
done

step() { printf '\n\033[1;36m[%s]\033[0m %s\n' "$1" "$2"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# ── 0. 前置检查 ──
[ "$(id -u)" -eq 0 ] || fail "请用 sudo 运行：curl ... | sudo bash"
command -v curl >/dev/null || fail "没有 curl，先 apt install curl"
command -v git >/dev/null || { step 0 "安装 git"; apt-get update -qq && apt-get install -y -qq git; }

step 1/6 "检查 Docker"
if ! command -v docker >/dev/null 2>&1; then
  echo "  没装，正在安装（约 1~2 分钟）…"
  curl -fsSL https://get.docker.com | sh >/dev/null || fail "Docker 安装失败（检查网络）"
else
  echo "  已安装：$(docker --version)"
fi
systemctl enable --now docker >/dev/null 2>&1 || true

# ── 端口占用检查：端口被别的程序占着，起多少个容器都没用 ──
if ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE ":${PORT}$"; then
  if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    fail "端口 ${PORT} 已被其他程序占用，请换端口：--port=8001"
  fi
fi

step 2/6 "获取代码"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin master >/dev/null 2>&1 || fail "拉取代码失败（检查网络）"
  git -C "$APP_DIR" reset --hard origin/master >/dev/null
  echo "  已更新到最新：$(git -C "$APP_DIR" log --oneline -1)"
else
  rm -rf "$APP_DIR"
  git clone --depth 1 "$REPO" "$APP_DIR" >/dev/null 2>&1 || fail "克隆代码失败（检查网络）"
  echo "  已克隆：$(git -C "$APP_DIR" log --oneline -1)"
fi

step 3/6 "准备数据目录"
mkdir -p "$APP_DIR/data"/{database,templates,uploads,backups,logs,cache/holidays}
echo "  $APP_DIR/data （数据库、母版、上传、备份、日志都在这里）"

step 4/6 "构建镜像（首次约 3~8 分钟）"
cd "$APP_DIR"
docker build -t "${NAME}:local" . || fail "镜像构建失败"
echo "  镜像就绪：$(docker images -q ${NAME}:local | head -1)"

step 5/6 "启动服务"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
  --restart unless-stopped \
  -p "${PORT}:8000" \
  -v "$APP_DIR/data:/data" \
  -e TZ=Asia/Shanghai \
  -e "MONITOR_DEMO_SEED=${DEMO}" \
  "${NAME}:local" >/dev/null || fail "容器启动失败"

echo "  容器已启动，等待就绪…"
READY=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then READY=1; break; fi
  sleep 2
done

step 6/6 "完成"
if [ "$READY" -eq 1 ]; then
  echo "  健康检查通过 ✅"
else
  echo "  ⚠ 40 秒内没等到健康响应，看看日志：docker logs ${NAME} --tail 50"
fi

IP=$(curl -s --max-time 5 https://ifconfig.me 2>/dev/null || echo "<服务器公网IP>")
cat <<EOF

  打开：http://${IP}:${PORT}

  常用命令：
    看日志      docker logs -f ${NAME}
    重启        docker restart ${NAME}
    停止        docker stop ${NAME}
    升级        sudo bash ${APP_DIR}/deploy/install-ubuntu.sh

  数据在哪里：
    ${APP_DIR}/data/       ← 容器里的 /data，删容器不会丢
    想搬走整套数据：把这个目录打包拷走即可。

  注意：
    1) 云厂商「防火墙 / 安全组」要放行 ${PORT} 端口，否则外面打不开；
    2) 现在没有登录认证，别直接挂公网；给同事看时打开主界面的「只读锁定」。
EOF

if [ "$DEMO" -eq 1 ]; then
  echo "  （本次带了演示数据；要清空：主界面 → 数据工具 → 清理自动生成的数据）"
fi
