#!/bin/sh
set -e

# Restrict umask so created files are private
umask 077

# Ensure data and cache directories exist
mkdir -p /app/data/cache/posters /app/data/cache/fanarts

# If running as root (container default), support custom PUID/PGID and step down with gosu
if [ "$(id -u)" = "0" ]; then
    PUID="${PUID:-1000}"
    PGID="${PGID:-1000}"

    # Fix mounted volume permissions only if they differ (Optimization)
    if [ -n "$(find /app/data ! -uid "$PUID" -print -quit 2>/dev/null)" ]; then
        chown -R "$PUID:$PGID" /app/data || true
    fi

    exec gosu "$PUID:$PGID" "$@"
fi

# If container was started directly as non-root (e.g. docker run --user or compose user:)
exec "$@"
