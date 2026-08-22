#!/usr/bin/env sh
# ============================================================
# SMART PUSH LAUNCHER KHUSUS WEB UI, SKRIP & METADATA
# ============================================================

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/git_smart_push.py" "$@"
