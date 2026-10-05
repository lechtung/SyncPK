#!/bin/bash
# Development Reset Script for SyncPK
# This script resets the environment, deletes the DB, and clears settings.

set -euo pipefail

echo "====================================="
echo "   RESETTING SYNCPK ENVIRONMENT (DEV)"
echo "====================================="

read -p "This will delete all your data. Type RESET to confirm: " CONFIRM
if [ "$CONFIRM" != "RESET" ]; then
    echo "Cancelled."
    exit 0
fi

CODE_DIR="/opt/syncpk"
DATA_DIR="/var/lib/syncpk"

# 1. Stop the service
echo "[1/5] Stopping syncpk-server service..."
systemctl stop syncpk-server || true

# 2. Delete database and settings
echo "[2/5] Deleting database, settings, and cache..."
rm -rf "$DATA_DIR/sync.db"* "$DATA_DIR/plex_settings.json" "$DATA_DIR/update_status.json" "$DATA_DIR/.env" "$DATA_DIR/static/cache"

# 3. Ask for sync limit
echo ""
echo "[3/5] Do you want to set an item limit for scanning?"
echo "      (Example: type 100 for a quick test, or press ENTER for a full scan)"
read -p "      Limit: " LIMIT_INPUT

if [ -z "$LIMIT_INPUT" ]; then
    echo "      -> No limit. Removing sync_limit.txt if it exists."
    rm -f "$DATA_DIR/sync_limit.txt"
else
    if [[ "$LIMIT_INPUT" =~ ^[0-9]+$ ]]; then
        echo "      -> Limit set to $LIMIT_INPUT items."
        echo "$LIMIT_INPUT" > "$DATA_DIR/sync_limit.txt"
        chown syncpk:syncpk "$DATA_DIR/sync_limit.txt"
    else
        echo "      -> [Error] Limit must be an integer. It will be ignored."
        rm -f "$DATA_DIR/sync_limit.txt"
    fi
fi
echo ""

# 4. Vacuum old logs
echo "[4/5] Cleaning up system journal logs for the service..."
journalctl -u syncpk-server --vacuum-time=1s > /dev/null 2>&1 || true

# 5. Start the service
echo "[5/5] Starting syncpk-server..."
systemctl start syncpk-server

echo "Done! Showing live logs (Press Ctrl+C to exit)..."
echo "=================================================================="
sleep 2
journalctl -u syncpk-server -f -n 0
