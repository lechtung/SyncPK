#!/usr/bin/env bash
set -euo pipefail

FORCE=0
for arg in "$@"; do
    if [ "$arg" == "--force" ]; then
        FORCE=1
    fi
done

if [ "$EUID" -ne 0 ]; then
    echo -e "\e[31m[ERROR] This script must be run as root (use sudo).\e[0m"
    exit 1
fi

ARCH=$(uname -m)
if [ "$ARCH" != "x86_64" ] && [ "$ARCH" != "aarch64" ]; then
    echo -e "\e[31m[ERROR] Unsupported architecture: $ARCH. Only x86_64 and aarch64 are supported.\e[0m"
    exit 1
fi

echo "======================================================"
echo "          SyncPK Installer (System Setup)             "
echo "======================================================"

CODE_DIR="/opt/syncpk"
DATA_DIR="/var/lib/syncpk"
API_URL="https://api.github.com/repos/lechtung/SyncPK/releases/latest"

if [ -d "$CODE_DIR" ] && [ "$FORCE" -eq 0 ]; then
    echo -e "\e[33m[Warning] SyncPK seems to be already installed on this system.\e[0m"
    echo "If you want to update, run: sudo $CODE_DIR/update.sh"
    echo "If you want to reinstall, run this script with --force flag."
    exit 1
fi

echo "[Info] Validating environment..."
if ! command -v python3 >/dev/null 2>&1; then
    echo -e "\e[33m[Warning] python3 not found, it will be installed via apt-get.\e[0m"
else
    if ! python3 -c 'import sys; exit(0) if sys.version_info >= (3,9) else exit(1)'; then
        echo -e "\e[31m[ERROR] Python version must be >= 3.9. Current version is $(python3 --version).\e[0m"
        exit 1
    fi
fi

INSTALL_SUCCESS=0
TMP_DIR=$(mktemp -d)
cleanup() { 
    rm -rf "$TMP_DIR"
    if [ "$INSTALL_SUCCESS" -eq 0 ] && [ -d "$CODE_DIR" ]; then
        echo -e "\e[31m[ERROR] Installation failed. Rolling back partial changes...\e[0m"
        rm -rf "$CODE_DIR"
    fi
}
trap cleanup EXIT ERR

if [ "$FORCE" -eq 1 ] && [ -d "$CODE_DIR" ]; then
    echo "[Info] Force mode: stopping services and cleaning up old installation..."
    systemctl stop syncpk-server syncpk-checker.timer syncpk-updater.path 2>/dev/null || true
    rm -rf "$CODE_DIR"
fi

echo "[Info] Creating dedicated system user 'syncpk'..."
if ! id "syncpk" &>/dev/null; then
    useradd -r -s /usr/sbin/nologin -d "$DATA_DIR" syncpk
fi

echo "[Info] Installing OS dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update > /var/log/syncpk-install.log 2>&1 || {
    tail -n 20 /var/log/syncpk-install.log
    echo -e "\e[31m[ERROR] apt-get update failed. Check the log.\e[0m"
    exit 1
}
apt-get install -y curl python3 python3-venv ca-certificates >> /var/log/syncpk-install.log 2>&1 || {
    tail -n 20 /var/log/syncpk-install.log
    echo -e "\e[31m[ERROR] Package installation failed. Check the log.\e[0m"
    exit 1
}

# Second check after apt-get just in case it was installed
if ! python3 -c 'import sys; exit(0) if sys.version_info >= (3,9) else exit(1)'; then
    echo -e "\e[31m[ERROR] Python version must be >= 3.9. Current version is $(python3 --version).\e[0m"
    exit 1
fi

echo "[Info] Preparing directories..."
mkdir -p $CODE_DIR

if [ -d "$DATA_DIR" ]; then
    echo -e "\e[33m[Info] Existing data directory found at $DATA_DIR. Retaining data...\e[0m"
else
    mkdir -p "$DATA_DIR"
fi

echo "[Info] Fetching latest version from GitHub..."
curl -fsSL --max-time 30 "$API_URL" > "$TMP_DIR/release.json" || { echo -e "\e[31m[ERROR] Could not fetch release info\e[0m"; exit 1; }
TAR_URL=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tarball_url", ""))' < "$TMP_DIR/release.json")
RELEASE_TAG=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name", "unknown"))' < "$TMP_DIR/release.json")

if [ -z "$TAR_URL" ]; then
    echo -e "\e[31m[ERROR] Download URL is empty.\e[0m"
    exit 1
fi

echo "[Info] Downloading and extracting source code..."
mkdir -p "$TMP_DIR/src"
curl -fsSL --max-time 300 "$TAR_URL" | tar -xz -C "$TMP_DIR/src" --strip-components=1 || { echo -e "\e[31m[ERROR] Failed to download/extract source code\e[0m"; exit 1; }

cp -a "$TMP_DIR/src/server/." "$CODE_DIR/"

VERSION_NUMBER="${RELEASE_TAG#[vV]}"
echo "$VERSION_NUMBER" > "$CODE_DIR/.ver"
cp -a "$TMP_DIR/src/update.sh" "$CODE_DIR/update.sh"
if [ -f "$TMP_DIR/src/check_update.sh" ]; then
    cp -a "$TMP_DIR/src/check_update.sh" "$CODE_DIR/check_update.sh"
fi
if [ -f "$TMP_DIR/src/uninstall.sh" ]; then
    cp -a "$TMP_DIR/src/uninstall.sh" "$CODE_DIR/uninstall.sh"
fi

if [ ! -f "$CODE_DIR/requirements.txt" ]; then
    echo -e "\e[31m[ERROR] requirements.txt not found in the release.\e[0m"
    exit 1
fi

echo "[Info] Configuring Python virtual environment..."
python3 -m venv $CODE_DIR/venv
$CODE_DIR/venv/bin/pip install -q -r $CODE_DIR/requirements.txt || {
    echo -e "\e[31m[ERROR] Failed to install Python dependencies.\e[0m"
    exit 1
}

echo "[Info] Applying security permissions..."
chown -R root:root $CODE_DIR
find $CODE_DIR -type d ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 755 {} +
find $CODE_DIR -type f ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 644 {} +
chmod +x $CODE_DIR/update.sh
if [ -f "$CODE_DIR/check_update.sh" ]; then
    chmod +x $CODE_DIR/check_update.sh
fi

chown -R syncpk:syncpk $DATA_DIR
find $DATA_DIR -type d -exec chmod 750 {} +
find $DATA_DIR -type f -exec chmod 640 {} +

echo "[Info] Installing systemd services..."
if [ -d "$TMP_DIR/src/server/systemd" ]; then
    install -m 644 -o root -g root "$TMP_DIR/src/server/systemd/"* /etc/systemd/system/
else
    echo -e "\e[31m[ERROR] No systemd files found in the release.\e[0m"
    exit 1
fi

echo "[Info] Starting and enabling all systemd services..."
systemctl daemon-reload
systemctl enable --now syncpk-server.service
systemctl enable --now syncpk-checker.timer
systemctl enable --now syncpk-updater.path

LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP="127.0.0.1"
fi

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
    echo -e "\e[31m[ERROR] Service is unresponsive. Check the logs:\e[0m"
    journalctl -u syncpk-server -n 30 --no-pager
    exit 1
fi

INSTALL_SUCCESS=1

echo "================================================================"
echo -e "\e[32mInstallation completed successfully!\e[0m"
echo -e "Open your browser to launch the Web Setup Wizard:"
echo -e "➡️  \e[1mhttp://$LOCAL_IP:8000\e[0m"
echo "================================================================"