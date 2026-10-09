#!/usr/bin/env bash
# Development Update Script for SyncPK
# Fetches the absolute latest code directly from the 'main' branch on GitHub, bypassing releases.

set -euo pipefail

echo "====================================="
echo "   UPDATING TO LATEST MAIN BRANCH    "
echo "====================================="

CODE_DIR="/opt/syncpk"
DATA_DIR="/var/lib/syncpk"
TMP_DIR=$(mktemp -d)
TAR_URL="https://github.com/lechtung/SyncPK/archive/refs/heads/main.tar.gz"

cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT ERR

echo "[1/4] Downloading latest code from 'main' branch..."
mkdir -p "$TMP_DIR/src"
curl -fsSL --max-time 120 "$TAR_URL" | tar -xz -C "$TMP_DIR/src" --strip-components=1 || {
    echo "[Error] Failed to download or extract the source code."
    exit 1
}

echo "[2/4] Stopping syncpk-server service..."
systemctl stop syncpk-server || true

echo "[3/4] Applying new code..."
# Clean old code (ignoring venv, .git, and keep scripts)
find "$CODE_DIR" -mindepth 1 -maxdepth 1 ! -name 'venv' ! -name '.ver' ! -name '.git' ! -name 'update.sh' ! -name 'check_update.sh' -exec rm -rf {} +

# Copy fresh code
cp -a "$TMP_DIR/src/server/." "$CODE_DIR/"
cp -a "$TMP_DIR/src/update.sh" "$CODE_DIR/update.sh.tmp"
mv "$CODE_DIR/update.sh.tmp" "$CODE_DIR/update.sh"

if [ -f "$TMP_DIR/src/check_update.sh" ]; then
    cp -a "$TMP_DIR/src/check_update.sh" "$CODE_DIR/check_update.sh.tmp"
    mv "$CODE_DIR/check_update.sh.tmp" "$CODE_DIR/check_update.sh"
fi

# Permissions
chown -R root:root "$CODE_DIR"
find "$CODE_DIR" -type d ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 755 {} +
find "$CODE_DIR" -type f ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 644 {} +
chmod +x "$CODE_DIR/update.sh"
[ -f "$CODE_DIR/check_update.sh" ] && chmod +x "$CODE_DIR/check_update.sh"

echo "[4/4] Starting service..."
systemctl start syncpk-server

echo "====================================="
echo " Update to 'main' branch completed!  "
echo "====================================="
