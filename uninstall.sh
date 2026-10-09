#!/usr/bin/env bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo -e "\e[31m[ERROR] This script must be run as root (use sudo).\e[0m"
    exit 1
fi

PURGE=0
for arg in "$@"; do
    if [ "$arg" == "--purge" ]; then
        PURGE=1
    fi
done

echo -e "\e[31m======================================================\e[0m"
echo -e "\e[31m          SyncPK Uninstaller (System Cleanup)         \e[0m"
echo -e "\e[31m======================================================\e[0m"

echo "This will permanently delete:"
echo " - The application code (/opt/syncpk)"
echo " - Systemd services and timers"

if [ "$PURGE" -eq 1 ]; then
    echo -e "\e[31m - The syncpk user\e[0m"
    echo -e "\e[31m - ALL DATA AND BACKUPS (/var/lib/syncpk, /var/backups/syncpk)\e[0m"
else
    echo "Data directory (/var/lib/syncpk) and backups will be kept by default for safety."
    echo "The syncpk user will also be kept so files aren't orphaned."
fi

# Ensure read works even if piped (by reading from /dev/tty)
if [ -t 0 ]; then
    read -p "Are you sure you want to proceed? (y/N) " -r REPLY < /dev/tty
else
    # If not interactive, require a force flag to proceed. Here we just abort.
    echo "[Error] Script must be run interactively to confirm, or implement a force flag."
    exit 1
fi

echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

echo "[Info] Stopping and disabling systemd services..."
systemctl stop syncpk-server syncpk-updater.path syncpk-checker.timer 2>/dev/null || true
# Stop oneshots if running
systemctl stop syncpk-updater.service syncpk-checker.service 2>/dev/null || true

systemctl disable syncpk-server syncpk-updater.path syncpk-checker.timer 2>/dev/null || true

echo "[Info] Removing systemd files..."
rm -f /etc/systemd/system/syncpk-server.service
rm -f /etc/systemd/system/syncpk-updater.path
rm -f /etc/systemd/system/syncpk-updater.service
rm -f /etc/systemd/system/syncpk-checker.timer
rm -f /etc/systemd/system/syncpk-checker.service
systemctl daemon-reload

echo "[Info] Deleting application code..."
rm -rf /opt/syncpk
rm -rf /opt/syncpk.prev
rm -f /var/log/syncpk-install.log

if [ "$PURGE" -eq 1 ]; then
    echo "[Info] Purging all data and backups..."
    rm -rf /var/lib/syncpk
    rm -rf /var/backups/syncpk
    
    echo "[Info] Removing syncpk user..."
    if id "syncpk" &>/dev/null; then
        userdel syncpk || true
    fi
fi

# Clean MOTD if we added it (LXC specific but safe to check)
if [ -f "/etc/motd" ]; then
    sed -i '/SyncPK/d' /etc/motd || true
    sed -i '/http:/d' /etc/motd || true
fi

echo "======================================================"
echo -e "\e[32mSyncPK has been uninstalled successfully.\e[0m"
if [ "$PURGE" -eq 0 ]; then
    echo "If you also want to delete all your data and database, run:"
    echo "sudo rm -rf /var/lib/syncpk /var/backups/syncpk"
    echo "sudo userdel syncpk"
fi
echo "======================================================"
