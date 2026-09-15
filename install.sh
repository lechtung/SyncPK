#!/usr/bin/env bash

# Default configurations (¡Cámbialas cuando subas a GitHub!)
GITHUB_USER="lechtung"
GITHUB_REPO="SyncPK"
GITHUB_BRANCH="main"
INSTALL_DIR="/opt/syncpk"

# Función para mostrar errores
function error() {
    echo -e "\e[31m[ERROR] $1\e[0m"
    exit 1
}

# Check that we are root
if [ "$EUID" -ne 0 ]; then
    error "Este script debe ejecutarse como root (usa sudo)."
fi

# Instalar dependencias necesarias para el instalador
echo "[Info] Installing base dependencies..."
apt-get update &>/dev/null
apt-get install -y whiptail curl jq base64 python3 python3-venv python3-pip &>/dev/null

# 1. Formulario interactivo
PLEX_URL=$(whiptail --inputbox "Enter your Plex server URL (ej: http://192.168.1.100:32400):" 10 60 "http://" --title "Configuración Plex" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

PLEX_TOKEN=$(whiptail --inputbox "Enter your Plex Token:" 10 60 --title "Configuración Plex" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

SYNC_PASSWORD=$(whiptail --passwordbox "Crea una contraseña maestra para proteger tu servidor SyncPK:" 10 60 --title "Security" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

TMDB_API_KEY=$(whiptail --inputbox "Enter your TMDB API Key (Free at themoviedb.org) para cargar carátulas:" 10 60 --title "TMDB (The Movie Database)" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

# Convertir contraseña a Base64
SYNC_PASSWORD_B64=$(echo -n "$SYNC_PASSWORD" | base64)

# 2. Instalación de software
echo "[Info] Preparing directory $INSTALL_DIR..."
mkdir -p $INSTALL_DIR

echo "[Info] Downloading files from GitHub..."
mkdir -p $INSTALL_DIR/static/locales
# En producción, usa raw.githubusercontent.com
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/main.py -o $INSTALL_DIR/main.py
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/plex_syncer.py -o $INSTALL_DIR/plex_syncer.py
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/index.html -o $INSTALL_DIR/static/index.html
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/style.css -o $INSTALL_DIR/static/style.css
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/app.js -o $INSTALL_DIR/static/app.js
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/en.json -o $INSTALL_DIR/static/locales/en.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/es.json -o $INSTALL_DIR/static/locales/es.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/requirements.txt -o $INSTALL_DIR/requirements.txt

# Si los archivos no existen en GitHub aún, creamos unos dummies por si falla para que el script no rompa
if [ ! -f $INSTALL_DIR/requirements.txt ] || ! grep -q "fastapi" $INSTALL_DIR/requirements.txt; then
    echo -e "fastapi\nuvicorn\nrequests\npython-dotenv" > $INSTALL_DIR/requirements.txt
fi

echo "[Info] Configuring environment variables (.env)..."
cat << EOF > $INSTALL_DIR/.env
PLEX_URL=$PLEX_URL
PLEX_TOKEN=$PLEX_TOKEN
SYNC_PASSWORD_B64=$SYNC_PASSWORD_B64
TMDB_API_KEY=$TMDB_API_KEY
EOF

echo "[Info] Configuring virtual environment..."
python3 -m venv $INSTALL_DIR/venv
$INSTALL_DIR/venv/bin/pip install -r $INSTALL_DIR/requirements.txt

# 3. Servicios Systemd
echo "[Info] Creating systemd services..."
cat << EOF > /etc/systemd/system/syncpk-server.service
[Unit]
Description=SyncPK Central Server
After=network.target

[Service]
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat << EOF > /etc/systemd/system/syncpk-plex.service
[Unit]
Description=Plex Syncer Client (Pull/Push to SyncPK)
After=network.target syncpk-server.service

[Service]
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/plex_syncer.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable syncpk-server syncpk-plex
systemctl start syncpk-server syncpk-plex

# Obtener IP local para mostrarla
LOCAL_IP=$(hostname -I | awk '{print $1}')

whiptail --title "Instalación Completada" --msgbox "SyncPK successfully installed in $INSTALL_DIR.\n\nServer deployed at IP: $LOCAL_IP\n\nDo not forget to enter the IP ($LOCAL_IP) y tu contraseña en los ajustes del Addon de Kodi." 12 60

echo "¡Instalación completada! IP del Servidor: $LOCAL_IP"

