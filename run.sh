#!/usr/bin/env sh
# ============================================================
# LOCAL DEV SERVER — RETRO PIXEL STREAM (Web UI)
# ------------------------------------------------------------
# Pakai:
#   ./run.sh                        -> http://localhost:8000/
#   PORT=8080 ./run.sh              -> ganti port
#   HOST=127.0.0.1 ./run.sh         -> hanya bisa diakses mesin ini
# ============================================================

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

cd "$DIR"

echo "[i] Menyajikan Web UI dari : $DIR"
echo "[i] Alamat                 : http://localhost:$PORT/"
echo "[i] Hentikan dengan Ctrl+C"
echo

exec python3 -m http.server "$PORT" --bind "$HOST"
