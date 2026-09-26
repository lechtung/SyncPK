#!/bin/bash
# SyncPK Update Checker Script
# This script checks for new versions of SyncPK on GitHub and either applies the update automatically or notifies the backend via .conf.

set -e

APP_DIR="/opt/SyncPK"
CONF_FILE="$APP_DIR/server/.conf"
ENV_FILE="$APP_DIR/server/.env"
LOCAL_VER_FILE="$APP_DIR/.ver"
REMOTE_VER_URL="https://raw.githubusercontent.com/chuchill/SyncPK/main/.ver"
REMOTE_UPDATE_SCRIPT_URL="https://raw.githubusercontent.com/chuchill/SyncPK/main/update.sh"
TEMP_VER_FILE="/tmp/syncpk_remote.ver"
TEMP_UPDATE_SCRIPT="/tmp/update_syncpk.sh"

# Ensure directories exist
mkdir -p "$APP_DIR"
cd "$APP_DIR"

# Download remote version file
curl -s -L -o "$TEMP_VER_FILE" "$REMOTE_VER_URL"

if [ ! -f "$TEMP_VER_FILE" ]; then
    echo "Error: Could not download version file."
    exit 1
fi

REMOTE_VER=$(cat "$TEMP_VER_FILE" | tr -d ' \n\r')
LOCAL_VER=$(cat "$LOCAL_VER_FILE" 2>/dev/null | tr -d ' \n\r') || LOCAL_VER="0.0.0"

# Compare versions
if [ "$REMOTE_VER" != "" ] && [ "$REMOTE_VER" != "$LOCAL_VER" ]; then
    echo "New version available: $REMOTE_VER (Local: $LOCAL_VER)"
    
    # Check if AUTO_UPDATE is enabled
    AUTO_UPDATE="false"
    if [ -f "$ENV_FILE" ]; then
        if grep -q "^AUTO_UPDATE=true" "$ENV_FILE"; then
            AUTO_UPDATE="true"
        fi
    fi

    if [ "$AUTO_UPDATE" = "true" ]; then
        echo "Auto-update is enabled. Starting update process..."
        # Download the latest update.sh from Github
        curl -s -L -o "$TEMP_UPDATE_SCRIPT" "$REMOTE_UPDATE_SCRIPT_URL"
        chmod +x "$TEMP_UPDATE_SCRIPT"
        # Execute the update script
        bash "$TEMP_UPDATE_SCRIPT"
    else
        echo "Auto-update is disabled. Updating .conf to notify the UI."
        # Update or add UPDATE_AVAILABLE to .conf
        if [ -f "$CONF_FILE" ]; then
            if grep -q "^UPDATE_AVAILABLE=" "$CONF_FILE"; then
                sed -i "s/^UPDATE_AVAILABLE=.*/UPDATE_AVAILABLE=$REMOTE_VER/" "$CONF_FILE"
            else
                echo "UPDATE_AVAILABLE=$REMOTE_VER" >> "$CONF_FILE"
            fi
        else
            echo "UPDATE_AVAILABLE=$REMOTE_VER" > "$CONF_FILE"
        fi
    fi
else
    echo "SyncPK is up to date (Version: $LOCAL_VER)."
    # Clear UPDATE_AVAILABLE in .conf if it was set
    if [ -f "$CONF_FILE" ]; then
        sed -i "s/^UPDATE_AVAILABLE=.*/UPDATE_AVAILABLE=/" "$CONF_FILE"
    fi
fi

# Clean up
rm -f "$TEMP_VER_FILE"
