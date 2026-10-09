#!/usr/bin/env bash
{
set -euo pipefail

# Priority High 1: Secure lock file in /run
exec 9> /run/syncpk_update.lock
if ! flock -n 9; then
    echo "[Error] Another update process is running. Exiting."
    exit 1
fi

CODE_DIR="/opt/syncpk"
DATA_DIR="/var/lib/syncpk"
BACKUP_DIR="/var/backups/syncpk"
API_URL="https://api.github.com/repos/lechtung/SyncPK/releases/latest"

PYTHON_BIN="$CODE_DIR/venv/bin/python3"
PIP_BIN="$CODE_DIR/venv/bin/pip"

# Priority High 1: Secure backup directory
install -d -m 700 -o root -g root "$BACKUP_DIR"

# Cleanup function for safe rollback
UPDATE_PHASE="init"
cleanup() {
    local exit_code=$?
    
    if [ -n "${TMP_DIR:-}" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
    
    if [ $exit_code -ne 0 ] || [ "$UPDATE_PHASE" = "error" ]; then
        echo "[Error] Update failed at phase: $UPDATE_PHASE. Initiating rollback..."
        
        systemctl stop syncpk-server || true
        
        # Rollback code
        if [ "$UPDATE_PHASE" != "init" ] && [ -d "$CODE_DIR.prev" ]; then
            rm -rf "$CODE_DIR"
            mv "$CODE_DIR.prev" "$CODE_DIR"
        fi
        
        # Rollback data (extract full tarball backup)
        if [ "$UPDATE_PHASE" != "init" ] && ls "$BACKUP_DIR"/syncpk_data_backup_*.tar.gz 1> /dev/null 2>&1; then
            LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/syncpk_data_backup_*.tar.gz | head -1)
            if [ -n "$LATEST_BACKUP" ]; then
                echo "[Info] Restoring data from $LATEST_BACKUP"
                rm -rf "$DATA_DIR"/*
                tar -xzf "$LATEST_BACKUP" -C "$DATA_DIR"
            fi
        fi
        
        if ! systemctl is-active -q syncpk-server; then
            systemctl start syncpk-server || true
        fi
        echo "[Error] Update failed. Rollback completed."
    elif [ "$UPDATE_PHASE" = "done" ]; then
        if ! systemctl is-active -q syncpk-server; then
            systemctl start syncpk-server || true
        fi
        echo "[Info] Update completed successfully."
        rm -f "$DATA_DIR/update_status.json"
    fi
    
    exit $exit_code
}
trap cleanup EXIT ERR

# Check if we are running the newly downloaded script
if [ "${1:-}" != "--exec-new" ]; then
    TMP_DIR=$(mktemp -d)
    echo "[Info] Fetching latest version from GitHub..."
    curl -fsSL --max-time 30 "$API_URL" > "$TMP_DIR/api_resp.json" || { echo "[Error] Could not fetch release info"; exit 1; }
    TAR_URL=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("tarball_url", ""))' < "$TMP_DIR/api_resp.json")
    RELEASE_TAG=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("tag_name", "unknown"))' < "$TMP_DIR/api_resp.json")
    
    if [ -z "$TAR_URL" ]; then
        echo "[Error] Download URL is empty."
        exit 1
    fi
    
    echo "[Info] Found release: $RELEASE_TAG"
    echo "[Info] Downloading and extracting source code..."
    mkdir -p "$TMP_DIR/src"
    curl -fsSL --max-time 300 "$TAR_URL" | tar -xz -C "$TMP_DIR/src" --strip-components=1 || { echo "[Error] Failed to download/extract"; exit 1; }
    
    echo "[Info] Executing new update script to apply the update..."
    # exec replaces the current shell process. The new script will have its own trap and variables.
    exec /bin/bash "$TMP_DIR/src/update.sh" --exec-new "$TMP_DIR" "$RELEASE_TAG"
fi

# --- WE ARE NOW IN THE NEW SCRIPT ---
TMP_DIR="$2"
RELEASE_TAG="$3"

echo "[Info] Downloading dependencies..."
mkdir -p "$TMP_DIR/wheels"
"$PIP_BIN" download -q -r "$TMP_DIR/src/server/requirements.txt" -d "$TMP_DIR/wheels" || { echo "[Error] Failed to download Python dependencies"; exit 1; }

UPDATE_PHASE="stopping"
echo "[Info] Stopping service..."
systemctl stop syncpk-server

UPDATE_PHASE="backup"
echo "[Info] Creating local backup (code and DB)..."
rm -rf "$CODE_DIR.prev"
cp -a "$CODE_DIR" "$CODE_DIR.prev"

# Priority High 2: Full Backup with rotation (we keep latest 3 to save space, delete others)
BACKUP_FILE="$BACKUP_DIR/syncpk_data_backup_$(date +%Y%m%d_%H%M%S).tar.gz"
tar -czf "$BACKUP_FILE" -C "$DATA_DIR" .
ls -t "$BACKUP_DIR"/syncpk_data_backup_*.tar.gz | tail -n +4 | xargs -r rm -f

UPDATE_PHASE="applying"
echo "[Info] Cleaning old code..."
find "$CODE_DIR" -mindepth 1 -maxdepth 1 ! -name 'venv' ! -name '.ver' ! -name 'update.sh' ! -name 'check_update.sh' -exec rm -rf {} +

echo "[Info] Applying new code..."
cp -a "$TMP_DIR/src/server/." "$CODE_DIR/"
cp -a "$TMP_DIR/src/update.sh" "$CODE_DIR/update.sh.tmp"
mv "$CODE_DIR/update.sh.tmp" "$CODE_DIR/update.sh"
if [ -f "$TMP_DIR/src/check_update.sh" ]; then
    cp -a "$TMP_DIR/src/check_update.sh" "$CODE_DIR/check_update.sh.tmp"
    mv "$CODE_DIR/check_update.sh.tmp" "$CODE_DIR/check_update.sh"
fi

if [ -d "$TMP_DIR/src/server/systemd" ]; then
    cp -a "$TMP_DIR/src/server/systemd/"* /etc/systemd/system/
    systemctl daemon-reload
fi

echo "[Info] Installing new dependencies offline..."
"$PIP_BIN" install -q --no-index --find-links "$TMP_DIR/wheels" -r "$CODE_DIR/requirements.txt" || {
    echo "[Error] Failed to install dependencies"
    exit 1
}

echo "[Info] Updating local version..."
VERSION_NUMBER="${RELEASE_TAG#[vV]}"
echo "$VERSION_NUMBER" > "$CODE_DIR/.ver"

echo "[Info] Configuring permissions..."
chown -R root:root "$CODE_DIR"
find "$CODE_DIR" -type d ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 755 {} +
find "$CODE_DIR" -type f ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 644 {} +
chmod +x "$CODE_DIR/update.sh"
if [ -f "$CODE_DIR/check_update.sh" ]; then
    chmod +x "$CODE_DIR/check_update.sh"
fi

UPDATE_PHASE="starting"
echo "[Info] Starting service..."
systemctl start syncpk-server

echo "[Info] Checking service health..."
HEALTH_OK=0
for i in $(seq 1 15); do
    if curl -fsSL --max-time 5 "http://127.0.0.1:8000/api/time" >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    sleep 2
done

if [ "$HEALTH_OK" -eq 0 ]; then
    echo "[Error] Service is unresponsive after update."
    exit 1
fi

UPDATE_PHASE="done"
}