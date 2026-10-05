#!/bin/bash
# SyncPK Update Checker Script

set -euo pipefail

APP_DIR="/opt/syncpk"
DATA_DIR="${DATA_DIR:-/var/lib/syncpk}"
ENV_FILE="$DATA_DIR/.env"
LOCAL_VER_FILE="$APP_DIR/.ver"
STATUS_FILE="$DATA_DIR/update_status.json"
TRIGGER_FILE="$DATA_DIR/.trigger_update"

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

# API de Github (Punto 3 y 8)
curl -fsSL --max-time 20 -o "$TMP" "https://api.github.com/repos/lechtung/SyncPK/releases/latest" || {
    echo "Error: Could not check for updates (Network error)."
    exit 1
}

REMOTE_VER=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"].lstrip("v"))' "$TMP" 2>/dev/null || true)

if [ -z "$REMOTE_VER" ] || [[ ! "$REMOTE_VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: Invalid remote version ($REMOTE_VER)"
    exit 1
fi

LOCAL_VER=$(cat "$LOCAL_VER_FILE" 2>/dev/null | tr -d ' \n\r') || LOCAL_VER="0.0.0"

# Comparamos semánticamente (solo actualiza si remota es mayor)
if [ "$REMOTE_VER" != "$LOCAL_VER" ] && [ "$(printf '%s\n%s\n' "$LOCAL_VER" "$REMOTE_VER" | sort -V | tail -n1)" = "$REMOTE_VER" ]; then
    echo "New version available: $REMOTE_VER (Local: $LOCAL_VER)"
    
    # Leer AUTO_UPDATE de .env (Punto 7) - Soporta comillas simples o dobles o sin comillas
    AUTO_UPDATE="false"
    if [ -f "$ENV_FILE" ]; then
        if grep -q -i "^AUTO_UPDATE=[\"']\?true[\"']\?" "$ENV_FILE"; then
            AUTO_UPDATE="true"
        fi
    fi

    if [ "$AUTO_UPDATE" = "true" ]; then
        echo "Auto-update enabled. Triggering update..."
        touch "$TRIGGER_FILE"
    else
        echo "Auto-update disabled. Notifying UI."
        echo "{\"update_available\": \"$REMOTE_VER\"}" > "$STATUS_FILE"
        chmod 644 "$STATUS_FILE"
    fi
else
    echo "SyncPK is up to date."
    echo "{\"update_available\": false}" > "$STATUS_FILE"
    chmod 644 "$STATUS_FILE"
fi