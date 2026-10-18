#!/usr/bin/env bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo -e "\e[31m[ERROR] This script must be run as root (use sudo).\e[0m"
    exit 1
fi

PURGE=0
ASSUME_YES=0

for arg in "$@"; do
    case "$arg" in
        --purge)
            PURGE=1
            ;;
        -y|--yes)
            ASSUME_YES=1
            ;;
    esac
done

# Prevent running while an update is in progress
exec 9> /run/syncpk_update.lock
if ! flock -n 9; then
    echo -e "\e[31m[ERROR] An update is currently in progress. Please wait for it to finish.\e[0m"
    exit 1
fi

echo -e "\e[31m======================================================\e[0m"
echo -e "\e[31m          SyncPK Uninstaller (System Cleanup)         \e[0m"
echo -e "\e[31m======================================================\e[0m"

echo "This will permanently delete:"
echo " - The application code (/opt/syncpk)"
echo " - Systemd services, timers, and configuration drop-ins"

if [ "$PURGE" -eq 1 ]; then
    echo -e "\e[31m - The syncpk user\e[0m"
    echo -e "\e[31m - ALL DATA AND BACKUPS (/var/lib/syncpk, /var/backups/syncpk)\e[0m"
else
    echo "Data directory (/var/lib/syncpk) and backups will be kept by default for safety."
    echo "The syncpk user will also be kept so files aren't orphaned."
fi

if [ "$ASSUME_YES" -eq 0 ]; then
    if [ -t 0 ]; then
        read -p "Are you sure you want to proceed? (y/N) " -r REPLY < /dev/tty
    else
        echo "[Error] Script is running non-interactively. Pass -y or --yes to confirm uninstallation."
        exit 1
    fi

    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
fi

echo "[Info] Stopping and disabling systemd services..."
systemctl stop syncpk-server syncpk-updater.path syncpk-checker.timer 2>/dev/null || true
# Stop oneshot services if currently active
systemctl stop syncpk-updater.service syncpk-checker.service 2>/dev/null || true

systemctl disable syncpk-server syncpk-updater.path syncpk-checker.timer 2>/dev/null || true

echo "[Info] Removing systemd files..."
rm -f /etc/systemd/system/syncpk-server.service
rm -f /etc/systemd/system/syncpk-updater.path
rm -f /etc/systemd/system/syncpk-updater.service
rm -f /etc/systemd/system/syncpk-checker.timer
rm -f /etc/systemd/system/syncpk-checker.service
rm -rf /etc/systemd/system/syncpk-*.d /etc/systemd/system/syncpk-*.service.d
systemctl daemon-reload

echo "[Info] Deleting application code and temporary directories..."
rm -rf /opt/syncpk /opt/syncpk.prev /opt/syncpk.prev.tmp /opt/syncpk.old
rm -f /var/log/syncpk-install.log /var/log/syncpk-lxc-install.log

if [ "$PURGE" -eq 1 ]; then
    echo "[Info] Purging all data and backups..."
    rm -rf /var/lib/syncpk
    rm -rf /var/backups/syncpk

    echo "[Info] Removing syncpk user..."
    if id "syncpk" &>/dev/null; then
        userdel syncpk || true
    fi
fi

# Clean MOTD if present (created by Proxmox installer)
rm -f /etc/profile.d/syncpk-motd.sh

echo "======================================================"
echo -e "\e[32mSyncPK has been uninstalled successfully.\e[0m"
if [ "$PURGE" -eq 0 ]; then
    echo "If you also want to delete all your data and database, run:"
    echo "sudo rm -rf /var/lib/syncpk /var/backups/syncpk"
    echo "sudo userdel syncpk"
fi
echo "======================================================"
