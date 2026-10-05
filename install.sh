#!/usr/bin/env bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo -e "\e[31m[ERROR] This script must be run as root (use sudo).\e[0m"
    exit 1
fi

echo "======================================================"
echo "          SyncPK Installer (System Setup)             "
echo "======================================================"

CODE_DIR="/opt/syncpk"
DATA_DIR="/var/lib/syncpk"
API_URL="https://api.github.com/repos/lechtung/SyncPK/releases/latest"

if [ -d "$CODE_DIR" ] || [ -d "$DATA_DIR" ]; then
    echo -e "\e[33m[Warning] SyncPK seems to be already installed on this system.\e[0m"
    echo "If you want to update, run: sudo $CODE_DIR/update.sh"
    echo "If you want to reinstall, delete $CODE_DIR and $DATA_DIR first."
    exit 1
fi

echo "[Info] Creating dedicated system user 'syncpk'..."
if ! id "syncpk" &>/dev/null; then
    useradd -r -s /usr/sbin/nologin syncpk
fi

echo "[Info] Installing OS dependencies..."
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

echo "[Info] Preparing directories..."
mkdir -p $CODE_DIR
mkdir -p $DATA_DIR

echo "[Info] Fetching latest version from GitHub..."
TMP_DIR=$(mktemp -d)
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

curl -fsSL --max-time 30 "$API_URL" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tarball_url"])' > "$TMP_DIR/url" || { echo -e "\e[31m[ERROR] Could not fetch download URL\e[0m"; exit 1; }

TAR_URL=$(cat "$TMP_DIR/url")
if [ -z "$TAR_URL" ]; then
    echo -e "\e[31m[ERROR] Download URL is empty.\e[0m"
    exit 1
fi

echo "[Info] Downloading and extracting source code..."
mkdir -p "$TMP_DIR/src"
curl -fsSL --max-time 300 "$TAR_URL" | tar -xz -C "$TMP_DIR/src" --strip-components=1 || { echo -e "\e[31m[ERROR] Failed to download/extract source code\e[0m"; exit 1; }

# Copiar el contenido
cp -a "$TMP_DIR/src/server/"* "$CODE_DIR/"
if [ -f "$TMP_DIR/src/.ver" ]; then
    cp "$TMP_DIR/src/.ver" "$CODE_DIR/.ver"
fi
# Copiar el actualizador local y el checker
cp -a "$TMP_DIR/src/update.sh" "$CODE_DIR/update.sh"
if [ -f "$TMP_DIR/src/check_update.sh" ]; then
    cp -a "$TMP_DIR/src/check_update.sh" "$CODE_DIR/check_update.sh"
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
# Propietario del código es root
chown -R root:root $CODE_DIR
find $CODE_DIR -type d -exec chmod 755 {} +
find $CODE_DIR -type f -exec chmod 644 {} +
chmod +x $CODE_DIR/update.sh
if [ -f "$CODE_DIR/check_update.sh" ]; then
    chmod +x $CODE_DIR/check_update.sh
fi

# Propietario de los datos es syncpk
chown -R syncpk:syncpk $DATA_DIR
find $DATA_DIR -type d -exec chmod 750 {} +
find $DATA_DIR -type f -exec chmod 640 {} +

echo "[Info] Installing systemd services..."
if [ -d "$TMP_DIR/src/server/systemd" ]; then
    cp -a "$TMP_DIR/src/server/systemd/"* /etc/systemd/system/
else
    echo -e "\e[31m[ERROR] No systemd files found in the release.\e[0m"
    exit 1
fi

echo "[Info] Starting and enabling all systemd services..."
systemctl daemon-reload
systemctl enable --now syncpk-server.service
systemctl enable --now syncpk-checker.timer
systemctl enable --now syncpk-updater.path

LOCAL_IP=$(hostname -I | awk '{print $1}')

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

echo "================================================================"
echo -e "\e[32mInstallation completed successfully!\e[0m"
echo -e "Open your browser to launch the Web Setup Wizard:"
echo -e "➡️  \e[1mhttp://$LOCAL_IP:8000\e[0m"
echo "================================================================"