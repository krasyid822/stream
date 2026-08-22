#!/usr/bin/env sh
exec python3 "$(dirname "$0")/generate_hls.py" "$@"
