#!/usr/bin/env sh
# ============================================================
# LAUNCHER RELEASE MANAGER (TUNDA 4)
# ------------------------------------------------------------
# Pakai:
#   ./release_ui.sh                  -> http://127.0.0.1:8765/
#   PORT=9000 ./release_ui.sh        -> ganti port
#   HOST=0.0.0.0 ./release_ui.sh     -> bind ke semua interface
#   ./release_ui.sh --no-browser     -> jangan buka browser otomatis
#   PYTHON_BIN=python3.12 ./release_ui.sh
#
# Token GitHub dibaca dari `gh auth` / GH_TOKEN.
# MAL_CLIENT_ID dibaca dari environment atau .env lokal.
# ============================================================

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "[-] '$PYTHON_BIN' tidak ditemukan. Set PYTHON_BIN atau install Python 3." >&2
    exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
    echo "[!] 'gh' (GitHub CLI) tidak ditemukan. UI tetap jalan, tetapi fitur release akan gagal." >&2
fi

cd "$DIR"

# HOST/PORT dari environment bersifat opsional. Argumen CLI tetap menang
# karena ditempatkan setelah nilai environment.
if [ -n "${HOST:-}" ]; then
    set -- --host "$HOST" "$@"
fi
if [ -n "${PORT:-}" ]; then
    set -- --port "$PORT" "$@"
fi

exec "$PYTHON_BIN" "$DIR/release_ui.py" "$@"
