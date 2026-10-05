#!/bin/bash
# SyncPK Update Checker Script

set -euo pipefail

APP_DIR="/opt/syncpk"
DATA_DIR="${DATA_DIR:-/var/lib/syncpk}"
ENV_FILE="$DATA_DIR/.env"
LOCAL_VER_FILE="$APP_DIR/.ver"
REMOTE_VER_URL="https://raw.githubusercontent.com/lechtung/SyncPK/main/.ver"
TEMP_VER_FILE="/tmp/syncpk_remote.ver"
TRIGGER_FILE="$DATA_DIR/.trigger_update"

# Fallar si hay error HTTP y max time 20s
curl -fsSL --max-time 20 -o "$TEMP_VER_FILE" "$REMOTE_VER_URL" || true

if [ ! -f "$TEMP_VER_FILE" ]; then
    echo "Error: Could not download version file."
    exit 1
fi

REMOTE_VER=$(cat "$TEMP_VER_FILE" | tr -d ' \n\r')
LOCAL_VER=$(cat "$LOCAL_VER_FILE" 2>/dev/null | tr -d ' \n\r') || LOCAL_VER="0.0.0"

# Comprobar que cumple formato de version (ej. 1.0.0)
if [[ ! "$REMOTE_VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: Versión remota inválida ($REMOTE_VER)"
    rm -f "$TEMP_VER_FILE"
    exit 1
fi

if [ "$REMOTE_VER" != "" ] && [ "$REMOTE_VER" != "$LOCAL_VER" ]; then
    echo "New version available: $REMOTE_VER (Local: $LOCAL_VER)"
    
    AUTO_UPDATE="false"
    if [ -f "$ENV_FILE" ]; then
        if grep -q "^AUTO_UPDATE=true" "$ENV_FILE"; then
            AUTO_UPDATE="true"
        fi
    fi

    if [ "$AUTO_UPDATE" = "true" ]; then
        echo "Auto-update enabled. Triggering update..."
        touch "$TRIGGER_FILE"
    else
        echo "Auto-update disabled. Notifying UI."
        if [ -f "$ENV_FILE" ]; then
            if grep -q "^UPDATE_AVAILABLE=" "$ENV_FILE"; then
                sed -i "s/^UPDATE_AVAILABLE=.*/UPDATE_AVAILABLE=$REMOTE_VER/" "$ENV_FILE"
            else
                echo "UPDATE_AVAILABLE=$REMOTE_VER" >> "$ENV_FILE"
            fi
        else
            echo "UPDATE_AVAILABLE=$REMOTE_VER" > "$ENV_FILE"
        fi
    fi
else
    echo "SyncPK is up to date."
    if [ -f "$ENV_FILE" ]; then
        sed -i "s/^UPDATE_AVAILABLE=.*/UPDATE_AVAILABLE=/" "$ENV_FILE"
    fi
fi

rm -f "$TEMP_VER_FILE"