#!/usr/bin/env bash

# Check that we are root
if [ "$EUID" -ne 0 ]; then
    echo -e "\e[31m[ERROR] This script must be run as root (use sudo).\e[0m"
    exit 1
fi

echo "======================================================"
echo "          SyncPK Installer (Baremetal/Linux)          "
echo "======================================================"

# 1. Run the interactive configuration wizard
echo "[Info] Launching interactive setup..."
bash setup_config.sh
if [ $? -ne 0 ]; then
    echo -e "\e[31m[ERROR] Configuration was aborted or failed.\e[0m"
    exit 1
fi

# 2. Run the system setup
echo "[Info] Launching system setup..."
bash setup_system.sh
if [ $? -ne 0 ]; then
    echo -e "\e[31m[ERROR] System setup failed.\e[0m"
    exit 1
fi

echo "======================================================"
echo "          Installation Completed Successfully!        "
echo "======================================================"

# Load env to show final summary
source /opt/syncpk/.env 2>/dev/null || source .env 2>/dev/null
LOCAL_IP=$(hostname -I | awk '{print $1}')
bash show_summary.sh "$LOCAL_IP" "$API_TOKEN_RAW" "/opt/syncpk"
