#!/bin/sh
set -e

# Restrict umask so created files are private
umask 077

# If running as root (container default), support custom PUID/PGID and step down with gosu
if [ "$(id -u)" = "0" ]; then
    # Infer owner from mounted /app/data if PUID/PGID not explicitly specified
    DETECTED_UID=$(stat -c '%u' /app/data 2>/dev/null || echo 1000)
    DETECTED_GID=$(stat -c '%g' /app/data 2>/dev/null || echo 1000)
    
    # Avoid running service as root (UID 0 or GID 0) if no custom IDs were set
    if [ "$DETECTED_UID" = "0" ]; then
        DETECTED_UID=1000
    fi
    if [ "$DETECTED_GID" = "0" ]; then
        DETECTED_GID=1000
    fi

    TARGET_UID="${PUID:-$DETECTED_UID}"
    TARGET_GID="${PGID:-$DETECTED_GID}"
    if [ -z "$PUID" ] && [ "$TARGET_UID" = "0" ]; then TARGET_UID=1000; fi
    if [ -z "$PGID" ] && [ "$TARGET_GID" = "0" ]; then TARGET_GID=1000; fi

    # Ensure cache directories exist before adjusting permissions
    mkdir -p /app/data/cache/posters /app/data/cache/fanarts 2>/dev/null || true

    # Fix mounted volume permissions only if UID or GID differ
    if [ -n "$(find /app/data \( ! -uid "$TARGET_UID" -o ! -gid "$TARGET_GID" \) -print -quit 2>/dev/null)" ]; then
        chown -R "$TARGET_UID:$TARGET_GID" /app/data || {
            echo "[Warning] Unable to set ownership on /app/data. If using network storage (NFS with root_squash), ensure /app/data is writable by UID $TARGET_UID."
        }
    fi

    exec gosu "$TARGET_UID:$TARGET_GID" "$@"
fi

# If container was started directly as non-root (e.g. docker run --user or compose user:)
if ! mkdir -p /app/data/cache/posters /app/data/cache/fanarts 2>/dev/null; then
    echo "[ERROR] Cannot create cache directories in /app/data."
    echo "[ERROR] Current user $(id -u):$(id -g) lacks write permissions on the mounted volume."
    exit 1
fi

exec "$@"
