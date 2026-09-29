#!/usr/bin/env bash

# Check that we are root
if [ "$EUID" -ne 0 ]; then
    echo -e "\e[31m[ERROR] This script must be run as root (use sudo).\e[0m"
    exit 1
fi

echo "======================================================"
echo "          SyncPK Installer (Baremetal/Linux)          "
echo "======================================================"

# Default configurations
GITHUB_USER="lechtung"
GITHUB_REPO="SyncPK"
GITHUB_BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/\(GITHUB_USER/\)GITHUB_REPO/$GITHUB_BRANCH"

# Create a temporary workspace
TMP_DIR=$(mktemp -d)
cd "$TMP_DIR" || exit 1

# Function to download required scripts
download_script() {
    curl -s "$BASE_URL/$1" -o "$1"
    chmod +x "$1"
}

echo "[Info] Fetching installer components..."
download_script "select_language.sh"
download_script "setup_config.sh"
download_script "setup_system.sh"
download_script "show_summary.sh"

# 0. Setup Language
echo "[Info] Launching language selection..."
./select_language.sh "\(GITHUB_USER" "\)GITHUB_REPO" "$GITHUB_BRANCH"
if [ $? -ne 0 ]; then
    echo -e "\e[31m[ERROR] Language selection was aborted or failed.\e[0m"
    cd / && rm -rf "$TMP_DIR"
    exit 1
fi

# 1. Run the interactive configuration wizard
echo "[Info] Launching interactive setup..."
./setup_config.sh
if [ $? -ne 0 ]; then
    echo -e "\e[31m[ERROR] Configuration was aborted or failed.\e[0m"
    cd / && rm -rf "$TMP_DIR"
    exit 1
fi

# 2. Run the system setup
echo "[Info] Launching system setup..."
./setup_system.sh
if [ $? -ne 0 ]; then
    echo -e "\e[31m[ERROR] System setup failed.\e[0m"
    cd / && rm -rf "$TMP_DIR"
    exit 1
fi

echo "======================================================"
echo "          Installation Completed Successfully!        "
echo "======================================================"

# Load env to show final summary
source /opt/syncpk/.env 2>/dev/null || source .env 2>/dev/null
LOCAL_IP=$(hostname -I | awk '{print $1}')
./show_summary.sh "\(LOCAL_IP" "\)API_TOKEN_RAW" "/opt/syncpk"

# Clean up
cd /
rm -rf "$TMP_DIR"