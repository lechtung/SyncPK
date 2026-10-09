#!/bin/sh
set -e

# Ensure data directory exists
mkdir -p /app/data

# Fix mounted volume permissions only if they differ (Optimization)
if [ -n "$(find /app/data ! -uid 1000 -print -quit 2>/dev/null)" ]; then
    chown -R syncpkuser:syncpkuser /app/data || true
fi

# Restrict umask so created files are private
umask 077

# Execute the passed command (uvicorn) dropping root privileges
exec gosu syncpkuser "$@"
