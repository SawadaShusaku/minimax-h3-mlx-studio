#!/usr/bin/env bash
# MiniMax-H3 のサーバをこのプロジェクトのモデルで起動する。
# 重みは外付けSSD上のプロジェクト配下（models/）から読む。
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
# FL2VA と REF2VA を両方見せ、リクエストの "model" で選ばせる。
# 片方しか無くても起動でき、無いパックを要求したときだけエラーになる。
MODEL_DIR="$PROJECT/models/ddalcu"
MODEL="$MODEL_DIR/MiniMax-H3-FL2VA-MLX-Serve-8bit"
PORT="${PORT:-11434}"
LOG="$PROJECT/logs/server.log"

[ -f "$MODEL/transformer.safetensors" ] || { echo "モデルが見つかりません: $MODEL" >&2; exit 1; }
mkdir -p "$PROJECT/logs"

if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "すでに起動しています (port $PORT)"
  exit 0
fi

nohup mlx-serve --model-dir "$MODEL_DIR" --serve --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
echo "起動中 (pid $!) — log: $LOG"

for _ in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "起動しました: http://127.0.0.1:$PORT"
    exit 0
  fi
  sleep 1
done

echo "起動に失敗しました。ログを確認してください: $LOG" >&2
exit 1
