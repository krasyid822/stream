#!/usr/bin/env sh
# ============================================================
# SMART PULL LAUNCHER KHUSUS REPOSITORI WEB STREAM
# ============================================================

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/git_smart_pull.py" "$@"
