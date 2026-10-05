#!/usr/bin/env bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo -e "\e[31m[ERROR] This script must be run as root (use sudo).\e[0m"
    exit 1
fi

echo "======================================================"
echo "          SyncPK Installer (System Setup)             "
echo "======================================================"

GITHUB_USER="lechtung"
GITHUB_REPO="SyncPK"
GITHUB_BRANCH="main"

CODE_DIR="/opt/syncpk"
DATA_DIR="/var/lib/syncpk"

echo "[Info] Creating dedicated system user 'syncpk'..."
if ! id "syncpk" &>/dev/null; then
    useradd -r -s /usr/sbin/nologin syncpk
fi

echo "[Info] Installing OS dependencies..."
apt-get update &>/dev/null
apt-get install -y curl python3 python3-venv python3-pip &>/dev/null

echo "[Info] Preparing directories..."
mkdir -p $CODE_DIR
mkdir -p $DATA_DIR

echo "[Info] Obteniendo la última versión de GitHub..."
LATEST_TAR_URL=$(curl -fsSL https://api.github.com/repos/$GITHUB_USER/$GITHUB_REPO/releases/latest | grep "tarball_url" | cut -d '"' -f 4 || true)

if [ -z "$LATEST_TAR_URL" ]; then
    echo -e "\e[31m[ERROR] No se pudo obtener la última release de GitHub.\e[0m"
    exit 1
fi

echo "[Info] Descargando y extrayendo código fuente..."
TMP_DIR=$(mktemp -d)
curl -fsSL "$LATEST_TAR_URL" | tar -xz -C "$TMP_DIR" --strip-components=1

# Copiar el contenido de la carpeta server al directorio principal
cp -r "$TMP_DIR/server/"* "$CODE_DIR/"
# Copiar .ver al código principal
cp "$TMP_DIR/.ver" "$CODE_DIR/.ver"

rm -rf "$TMP_DIR"

if [ ! -f $CODE_DIR/requirements.txt ] || ! grep -q "fastapi" $CODE_DIR/requirements.txt; then
    echo -e "fastapi\nuvicorn\nrequests\npython-dotenv\npython-multipart\nhttpx" > $CODE_DIR/requirements.txt
fi

echo "[Info] Configuring Python virtual environment..."
python3 -m venv $CODE_DIR/venv
$CODE_DIR/venv/bin/pip install -r $CODE_DIR/requirements.txt

echo "[Info] Applying security permissions..."
# El usuario root es dueño del código, los demás solo pueden leer/ejecutar
chown -R root:root $CODE_DIR
chmod -R u=rwX,go=rX $CODE_DIR
# El usuario syncpk es el dueño absoluto de los datos
chown -R syncpk:syncpk $DATA_DIR
chmod -R 750 $DATA_DIR

echo "[Info] Creating systemd service..."
cat << EOF > /etc/systemd/system/syncpk-server.service
[Unit]
Description=SyncPK Central Server
After=network.target

[Service]
User=syncpk
Group=syncpk
WorkingDirectory=$CODE_DIR
Environment="DATA_DIR=$DATA_DIR"
ExecStart=$CODE_DIR/venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

echo "[Info] Creating update systemd services..."

# 1. The checker service (User: syncpk)
cat << EOF > /etc/systemd/system/syncpk-checker.service
[Unit]
Description=SyncPK Update Checker
After=network.target

[Service]
Type=oneshot
User=syncpk
Environment="DATA_DIR=$DATA_DIR"
ExecStart=/bin/bash $CODE_DIR/check_update.sh
EOF

# 2. The tester timer
cat << EOF > /etc/systemd/system/syncpk-checker.timer
[Unit]
Description=Temporizador para SyncPK Checker

[Timer]
OnBootSec=5min
OnUnitActiveSec=24h
Unit=syncpk-checker.service

[Install]
WantedBy=timers.target
EOF

# 3. The update executor service (User: root)
cat << EOF > /etc/systemd/system/syncpk-updater.service
[Unit]
Description=Actualizador root de SyncPK

[Service]
Type=oneshot
User=root
ExecStartPre=/bin/rm -f $DATA_DIR/.trigger_update
ExecStart=/bin/bash -c "curl -fsSL https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/update.sh | bash"
EOF

# 4. The vigilante who shoots the executioner
cat << EOF > /etc/systemd/system/syncpk-updater.path
[Unit]
Description=Vigila peticiones de actualizacion de SyncPK

[Path]
PathExists=$DATA_DIR/.trigger_update
Unit=syncpk-updater.service

[Install]
WantedBy=multi-user.target
EOF

echo "[Info] Starting and enabling all systemd services..."
systemctl daemon-reload
systemctl enable --now syncpk-server.service
systemctl enable --now syncpk-checker.timer
systemctl enable --now syncpk-updater.path

LOCAL_IP=$(hostname -I | awk '{print $1}')

echo "================================================================"
echo -e "\e[32mInstallation completed successfully!\e[0m"
echo -e "Open your browser to launch the Web Setup Wizard:"
echo -e "➡️  \e[1mhttp://$LOCAL_IP:8000\e[0m"
echo "================================================================"