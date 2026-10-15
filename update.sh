#!/usr/bin/env bash
{
set -euo pipefail

# Acquire exclusive update lock in /run
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

# Ensure secure backup directory exists
install -d -m 700 -o root -g root "$BACKUP_DIR"

# Extract effective listening port from systemd unit and drop-in overrides
get_port() {
    local p
    p=$(systemctl cat syncpk-server 2>/dev/null | grep -oP -- '--port\s+\K[0-9]+' | tail -n1)
    echo "${p:-8000}"
}

# State tracking for safe rollback
UPDATE_PHASE="init"
BACKUP_OK=0
CURRENT_BACKUP_FILE=""
ROLLED_BACK=0

cleanup() {
    local exit_code=$?
    set +e  # Ensure rollback commands continue even if an intermediate step fails
    trap - EXIT  # Prevent nested trigger

    if [ -n "${TMP_DIR:-}" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi

    # Always clean temporary staging copy of code
    rm -rf "$CODE_DIR.prev.tmp"

    if [ $exit_code -ne 0 ] || [ "$UPDATE_PHASE" = "error" ]; then
        if [ "$ROLLED_BACK" -eq 1 ]; then
            exit $exit_code
        fi
        ROLLED_BACK=1

        echo "[Error] Update failed at phase: $UPDATE_PHASE. Initiating rollback..."

        # Only stop/restore service if the update actually progressed past initialization
        if [ "$UPDATE_PHASE" != "init" ]; then
            systemctl stop syncpk-server 2>/dev/null || true

            # Rollback code if previous code snapshot exists
            if [ -d "$CODE_DIR.prev" ]; then
                rm -rf "$CODE_DIR"
                mv "$CODE_DIR.prev" "$CODE_DIR"

                # Restore previous systemd units if available in code dir
                if [ -d "$CODE_DIR/systemd" ]; then
                    install -m 644 -o root -g root "$CODE_DIR/systemd/"* /etc/systemd/system/ 2>/dev/null || true
                    if command -v restorecon >/dev/null 2>&1; then
                        restorecon -R /etc/systemd/system/syncpk-* 2>/dev/null || true
                    fi
                    systemctl daemon-reload 2>/dev/null || true
                fi

                if command -v restorecon >/dev/null 2>&1; then
                    restorecon -R "$CODE_DIR" 2>/dev/null || true
                fi
            fi

            # Rollback data (extract verified tarball backup for this run)
            if [ "$BACKUP_OK" -eq 1 ] && [ -n "$CURRENT_BACKUP_FILE" ] && [ -f "$CURRENT_BACKUP_FILE" ]; then
                echo "[Info] Restoring data from verified backup: $CURRENT_BACKUP_FILE"
                # Use -maxdepth 1 to avoid recursing into and wiping cache contents
                find "$DATA_DIR" -mindepth 1 -maxdepth 1 ! -name 'cache' -exec rm -rf {} +
                tar -xzf "$CURRENT_BACKUP_FILE" -C "$DATA_DIR"
            fi

            systemctl reset-failed syncpk-server 2>/dev/null || true
            if ! systemctl is-active -q syncpk-server; then
                systemctl start syncpk-server || true
            fi
        fi

        echo "[Error] Update failed. Rollback completed."
    elif [ "$UPDATE_PHASE" = "done" ]; then
        # Clean previous code backup on success to save disk space
        rm -rf "$CODE_DIR.prev"

        systemctl reset-failed syncpk-server 2>/dev/null || true
        if ! systemctl is-active -q syncpk-server; then
            systemctl start syncpk-server || true
        fi
        echo "[Info] Update completed successfully."
        rm -f "$DATA_DIR/update_status.json" "$DATA_DIR/.trigger_update"
    fi

    exit $exit_code
}
trap cleanup EXIT

# Check if we are running the newly downloaded script
if [ "${1:-}" != "--exec-new" ]; then
    TMP_DIR=$(mktemp -d)
    echo "[Info] Fetching latest version from GitHub..."
    curl -fsSL --max-time 30 "$API_URL" > "$TMP_DIR/api_resp.json" || { echo "[Error] Could not fetch release info"; exit 1; }

    # Prefer system python3 for release parsing in case venv is damaged
    SYS_PYTHON=$(command -v python3 2>/dev/null || echo "$PYTHON_BIN")
    TAR_URL=$("$SYS_PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("tarball_url", ""))' < "$TMP_DIR/api_resp.json")
    RELEASE_TAG=$("$SYS_PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("tag_name", "unknown"))' < "$TMP_DIR/api_resp.json")

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
"$PIP_BIN" download -q -r "$TMP_DIR/src/server/requirements.txt" -d "$TMP_DIR/wheels" || {
    echo "[Error] Failed to download Python dependencies. If the virtual environment is corrupted, run 'sudo $CODE_DIR/install.sh --force' to recreate it."
    exit 1
}

UPDATE_PHASE="stopping"
echo "[Info] Stopping service..."
systemctl stop syncpk-server

UPDATE_PHASE="backup"
echo "[Info] Creating local backup (code and DB)..."
# Clean any stale previous backups before starting new code snapshot
rm -rf "$CODE_DIR.prev.tmp" "$CODE_DIR.prev"
cp -a "$CODE_DIR" "$CODE_DIR.prev.tmp"
mv "$CODE_DIR.prev.tmp" "$CODE_DIR.prev"

# Full backup with rotation (keep latest 3, delete older)
BACKUP_FILE="$BACKUP_DIR/syncpk_data_backup_$(date +%Y%m%d_%H%M%S).tar.gz"
if ! tar -czf "$BACKUP_FILE" --exclude="./cache" -C "$DATA_DIR" . || ! tar -tzf "$BACKUP_FILE" >/dev/null 2>&1; then
    echo "[Error] Data backup creation or verification failed."
    rm -f "$BACKUP_FILE"
    exit 1
fi
BACKUP_OK=1
CURRENT_BACKUP_FILE="$BACKUP_FILE"
ls -t "$BACKUP_DIR"/syncpk_data_backup_*.tar.gz 2>/dev/null | tail -n +4 | xargs -r rm -f

UPDATE_PHASE="applying"
echo "[Info] Cleaning old code..."
find "$CODE_DIR" -mindepth 1 -maxdepth 1 ! -name 'venv' ! -name '.ver' ! -name 'update.sh' ! -name 'check_update.sh' ! -name 'uninstall.sh' -exec rm -rf {} +

echo "[Info] Applying new code..."
cp -a "$TMP_DIR/src/server/." "$CODE_DIR/"
cp -a "$TMP_DIR/src/update.sh" "$CODE_DIR/update.sh.tmp"
mv "$CODE_DIR/update.sh.tmp" "$CODE_DIR/update.sh"
if [ -f "$TMP_DIR/src/check_update.sh" ]; then
    cp -a "$TMP_DIR/src/check_update.sh" "$CODE_DIR/check_update.sh.tmp"
    mv "$CODE_DIR/check_update.sh.tmp" "$CODE_DIR/check_update.sh"
fi
if [ -f "$TMP_DIR/src/uninstall.sh" ]; then
    cp -a "$TMP_DIR/src/uninstall.sh" "$CODE_DIR/uninstall.sh.tmp"
    mv "$CODE_DIR/uninstall.sh.tmp" "$CODE_DIR/uninstall.sh"
fi

if [ -d "$TMP_DIR/src/server/systemd" ]; then
    install -m 644 -o root -g root "$TMP_DIR/src/server/systemd/"* /etc/systemd/system/
    if command -v restorecon >/dev/null 2>&1; then
        restorecon -R /etc/systemd/system/syncpk-* 2>/dev/null || true
    fi
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
if [ -f "$CODE_DIR/uninstall.sh" ]; then
    chmod +x "$CODE_DIR/uninstall.sh"
fi

if command -v restorecon >/dev/null 2>&1; then
    restorecon -R "$CODE_DIR" 2>/dev/null || true
fi

UPDATE_PHASE="starting"
echo "[Info] Starting service..."
systemctl reset-failed syncpk-server 2>/dev/null || true
systemctl start syncpk-server

echo "[Info] Checking service health..."
PORT=$(get_port)
HEALTH_OK=0
for i in $(seq 1 15); do
    if curl -fsSL --max-time 5 "http://127.0.0.1:${PORT}/api/time" >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    sleep 2
done

if [ "$HEALTH_OK" -eq 0 ]; then
    echo "[Error] Service is unresponsive on port ${PORT} after update."
    exit 1
fi

echo "[Info] Update to ${RELEASE_TAG} completed successfully!"
UPDATE_PHASE="done"
}