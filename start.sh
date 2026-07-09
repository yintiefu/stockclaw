#!/usr/bin/env bash
# Vibe-Research 一键启停脚本
#
# 用法:
#   ./start.sh start     # 启动后端(:8900) → 前端(:55890)
#   ./start.sh restart   # 先停后启(后端 → 前端)
#   ./start.sh stop      # 停止两端
#   ./start.sh status    # 查看运行状态
#
# 顺序遵循约定:先切后端,再前端。日志见 backend/.run/backend.log、frontend/.run/frontend.log。

set -u

# ── 路径与配置 ────────────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

BACKEND_PORT=8900
FRONTEND_PORT=55890
BACKEND_HOST="127.0.0.1"

BACKEND_PID="$BACKEND/.run/backend.pid"
BACKEND_LOG="$BACKEND/.run/backend.log"
FRONTEND_PID="$FRONTEND/.run/frontend.pid"
FRONTEND_LOG="$FRONTEND/.run/frontend.log"

# 后端用 uv 管理:在 backend/ 下 `uv sync` 装依赖,`uv run python -m uvicorn` 启动。
# (不直接 `uv run uvicorn` —— uvicorn 是 entry-point 脚本,`uv run <脚本名>` 找不到它,
#  必须走 `uv run python -m uvicorn` 才稳定。)
UV="${UV:-uv}"

# ── 工具函数 ──────────────────────────────────────────────────────────────
log()  { printf '\033[32m[启动脚本]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[启动脚本]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[31m[启动脚本]\033[0m %s\n' "$*" >&2; }

mkdir -p "$BACKEND/.run" "$FRONTEND/.run"

# 读取 pid 文件并返回「仍在运行」的 PID,清理失效的 pid 文件。
read_pid() {
  local pidfile="$1" pid
  [[ -f "$pidfile" ]] || return 1
  pid="$(cat "$pidfile" 2>/dev/null)"
  [[ -n "${pid:-}" ]] || return 1
  if kill -0 "$pid" 2>/dev/null; then
    echo "$pid"
    return 0
  fi
  rm -f "$pidfile"   # 进程已退出,清理失效 pid 文件
  return 1
}

# 按端口反查监听进程 PID(兼容 lsof / ss / netstat)。仅作 pid 文件失效时的兜底。
pid_on_port() {
  local port="$1"
  command -v lsof >/dev/null 2>&1 && { lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1; return; }
  command -v ss    >/dev/null 2>&1 && { ss -ltnp 2>/dev/null | grep -oP "pid=\K[0-9]+" | head -1; return; }
  command -v netstat >/dev/null 2>&1 && { netstat -ltnp 2>/dev/null | awk -v p=":$port" '$4 ~ p {split($NF,a,"/"); print a[1]; exit}'; return; }
  return 1
}

# ── 后端 ──────────────────────────────────────────────────────────────────
backend_running() { read_pid "$BACKEND_PID" >/dev/null 2>&1; }

backend_start() {
  if backend_running; then
    log "后端已在运行(pid $(cat "$BACKEND_PID")),跳过"; return 0
  fi
  # uv 接管:首次或依赖变更时 uv sync 创建 .venv 并装好依赖;已就位则秒过
  if ! "$UV" --version >/dev/null 2>&1; then
    err "未找到 uv 命令。请先安装:curl -LsSf https://astral.sh/uv/install.sh | sh"
    return 1
  fi
  log "同步后端依赖: uv sync"
  (cd "$BACKEND" && "$UV" sync --quiet) \
    || { err "后端 uv sync 失败"; return 1; }
  log "启动后端: uv run python -m uvicorn app:app → ${BACKEND_HOST}:${BACKEND_PORT}"
  # nohup 包住整个 uv run(uv 会派生子进程跑 uvicorn,pid 记的是 uv 的,
  # stop 时 kill uv → uv 转发信号给 uvicorn,子进程随之退出)
  (cd "$BACKEND" && nohup "$UV" run python -m uvicorn app:app \
      --host "$BACKEND_HOST" --port "$BACKEND_PORT" \
      > "$BACKEND_LOG" 2>&1 & echo $! > "$BACKEND_PID")
  local i
  for i in $(seq 1 20); do
    if curl -sf "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
      log "后端就绪(pid $(cat "$BACKEND_PID"))  日志: $BACKEND_LOG"
      return 0
    fi
    # 进程可能已崩溃
    if ! kill -0 "$(cat "$BACKEND_PID")" 2>/dev/null; then
      err "后端启动失败,见日志: $BACKEND_LOG"; tail -n 20 "$BACKEND_LOG" >&2 || true; return 1
    fi
    sleep 0.5
  done
  warn "后端进程已起,但 /api/health 未在预期时间内响应(pid $(cat "$BACKEND_PID"))"
  warn "日志: $BACKEND_LOG"
  return 0
}

backend_stop() {
  local pid
  if pid="$(read_pid "$BACKEND_PID" 2>/dev/null)"; then
    log "停止后端 pid $pid"
    kill "$pid" 2>/dev/null || true
    for i in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$BACKEND_PID"
  fi
  # uv run 的进程模型:uv 主进程派生 python -m uvicorn,kill uv 后 uvicorn 可能成孤儿
  # 继续占着 8900 端口(上次启动失败「address already in use」的根因)。故无论 pid 文件
  # 是否命中,都按端口再扫一遍,确保残留的 uvicorn/python 进程被清掉。
  local orphan
  if orphan="$(pid_on_port "$BACKEND_PORT" 2>/dev/null)"; then
    [[ -n "$orphan" ]] && { log "按端口清理后端残留进程 pid $orphan"; kill "$orphan" 2>/dev/null || true; sleep 0.5; }
    # 还在就强杀
    if kill -0 "$orphan" 2>/dev/null; then kill -9 "$orphan" 2>/dev/null || true; sleep 0.3; fi
  fi
  # 不在 stop 阶段死等端口释放:kill 完进程即可,TCP LISTEN socket 回收有延迟,
  # 立刻查会误报「仍被占用」。真正的端口冲突由后续 backend_start 的 bind 健康检查兜底
  # (bind 失败 → /api/health 不响应 → 脚本报错退出)。
  [[ -z "${pid:-}" ]] && [[ -z "${orphan:-}" ]] && log "后端未在运行"
}

# ── 前端 ──────────────────────────────────────────────────────────────────
frontend_running() { read_pid "$FRONTEND_PID" >/dev/null 2>&1; }

frontend_start() {
  if frontend_running; then
    log "前端已在运行(pid $(cat "$FRONTEND_PID")),跳过"; return 0
  fi
  if [[ ! -d "$FRONTEND/node_modules" ]]; then
    log "未发现 node_modules,自动 npm install..."
    (cd "$FRONTEND" && npm install) || { err "前端依赖安装失败"; return 1; }
  fi
  log "启动前端: npm run dev → :${FRONTEND_PORT}"
  (cd "$FRONTEND" && nohup npm run dev > "$FRONTEND_LOG" 2>&1 & echo $! > "$FRONTEND_PID")
  # 等待端口就绪(最多 ~20s;Vite 首启稍慢)
  local i
  for i in $(seq 1 40); do
    if pid_on_port "$FRONTEND_PORT" >/dev/null 2>&1; then
      log "前端就绪(pid $(cat "$FRONTEND_PID"))  → http://localhost:${FRONTEND_PORT}"
      log "日志: $FRONTEND_LOG"
      return 0
    fi
    if ! kill -0 "$(cat "$FRONTEND_PID")" 2>/dev/null; then
      err "前端启动失败,见日志: $FRONTEND_LOG"; tail -n 20 "$FRONTEND_LOG" >&2 || true; return 1
    fi
    sleep 0.5
  done
  warn "前端进程已起,但端口未在预期时间内监听(pid $(cat "$FRONTEND_PID"))"
  warn "日志: $FRONTEND_LOG"
  return 0
}

frontend_stop() {
  local pid
  if pid="$(read_pid "$FRONTEND_PID" 2>/dev/null)"; then
    log "停止前端 pid $pid"
    kill "$pid" 2>/dev/null || true
    for i in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$FRONTEND_PID"
  fi
  # npm run dev 的进程模型:kill npm 后 vite 子进程可能成孤儿继续占 :55890
  # (与后端 uv run 同理)。按端口兜底清掉。
  local orphan
  if orphan="$(pid_on_port "$FRONTEND_PORT" 2>/dev/null)"; then
    [[ -n "$orphan" ]] && { log "按端口清理前端残留进程 pid $orphan"; kill "$orphan" 2>/dev/null || true; sleep 0.5; }
    if kill -0 "$orphan" 2>/dev/null; then kill -9 "$orphan" 2>/dev/null || true; sleep 0.3; fi
  fi
  [[ -z "${pid:-}" ]] && [[ -z "${orphan:-}" ]] && log "前端未在运行"
}

# ── 命令分发 ──────────────────────────────────────────────────────────────
cmd_start() {
  log "=== START(先后端,再前端)==="
  backend_start  || exit 1
  frontend_start || exit 1
  log "全部启动完成"
  cmd_status
}

cmd_restart() {
  log "=== RESTART(先停后启;先切后端,再前端)==="
  backend_stop
  frontend_stop
  backend_start  || exit 1
  frontend_start || exit 1
  log "全部重启完成"
  cmd_status
}

cmd_stop() {
  log "=== STOP ==="
  backend_stop
  frontend_stop
  cmd_status
}

cmd_status() {
  local bpid fpid
  if bpid="$(read_pid "$BACKEND_PID" 2>/dev/null)"; then
    printf '  后端  \033[32m● running\033[0m  pid %-8s :%s\n' "$bpid" "$BACKEND_PORT"
  else
    printf '  后端  \033[31m○ stopped\033[0m\n'
  fi
  if fpid="$(read_pid "$FRONTEND_PID" 2>/dev/null)"; then
    printf '  前端  \033[32m● running\033[0m  pid %-8s :%s  http://localhost:%s\n' "$fpid" "$FRONTEND_PORT" "$FRONTEND_PORT"
  else
    printf '  前端  \033[31m○ stopped\033[0m\n'
  fi
}

usage() {
  cat <<EOF
Vibe-Research 启停脚本

用法:
  ./start.sh start      启动后端 → 前端
  ./start.sh restart    重启(先停后启;先切后端,再前端)
  ./start.sh stop       停止两端
  ./start.sh status     查看运行状态

端口:  后端 ${BACKEND_HOST}:${BACKEND_PORT}   前端 :${FRONTEND_PORT}
日志:  ${BACKEND_LOG}
       ${FRONTEND_LOG}
EOF
}

# ── 入口 ──────────────────────────────────────────────────────────────────
case "${1:-}" in
  start)   cmd_start ;;
  restart) cmd_restart ;;
  stop)    cmd_stop ;;
  status)  cmd_status ;;
  ""|-h|--help|help) usage ;;
  *) err "未知命令: $1"; usage; exit 1 ;;
esac
