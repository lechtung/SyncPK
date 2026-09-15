#!/usr/bin/env bash

# Default configurations (¡Cámbialas cuando subas a GitHub!)
GITHUB_USER="lechtung"
GITHUB_REPO="SyncPK"
GITHUB_BRANCH="main"

# Función para mostrar errores
function error() {
    echo -e "\e[31m[ERROR] $1\e[0m"
    exit 1
}

# Check that we are root en Proxmox
if [ "$EUID" -ne 0 ]; then
    error "Este script debe ejecutarse como root (privilegios de administrador)."
fi

if ! command -v pvesm &> /dev/null; then
    error "Este script debe ejecutarse en el HOST de Proxmox, no dentro de un contenedor."
fi

# Instalar dependencias necesarias para el instalador
apt-get update &>/dev/null
apt-get install -y whiptail curl jq base64 &>/dev/null

# 1. Autodescubrimiento de almacenamiento
echo "[Info] Buscando almacenamientos compatibles con contenedores LXC..."
STORAGES=$(pvesm status -content rootdir | awk 'NR>1 {print $1}')
if [ -z "$STORAGES" ]; then
    error "No se ha encontrado ningún almacenamiento compatible con contenedores (rootdir)."
fi

# Construir menú para whiptail
STORAGE_MENU=()
for s in $STORAGES; do
    STORAGE_MENU+=("$s" "")
done

TARGET_STORAGE=$(whiptail --title "Almacenamiento LXC" --menu "Selecciona el disco donde instalar SyncPK:" 15 50 4 "${STORAGE_MENU[@]}" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

# 2. Formulario interactivo
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

# 3. Descarga de plantilla y creación del LXC
CTID=$(pvesh get /cluster/nextid)
TEMPLATE="debian-12-standard_12.2-1_amd64.tar.zst"

echo "[Info] Descargando plantilla Debian 12..."
pveam update &>/dev/null
pveam download $TARGET_STORAGE $TEMPLATE &>/dev/null || error "Fallo al descargar la plantilla."

echo "[Info] Creando contenedor CT $CTID..."
pct create $CTID $TARGET_STORAGE:vztmpl/$TEMPLATE -arch amd64 -hostname syncpk -cores 1 -memory 512 -net0 name=eth0,bridge=vmbr0,ip=dhcp -unprivileged 1 -features nesting=1
pct start $CTID

echo "[Info] Waiting for the container to boot and get an IP..."
sleep 10
CT_IP=$(pct exec $CTID -- ip -4 addr show eth0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
echo "[Info] Assigned IP: $CT_IP"

# 4. Instalación de software dentro del LXC
echo "[Info] Installing Python and dependencies in the container..."
pct exec $CTID -- apt-get update
pct exec $CTID -- apt-get install -y python3 python3-venv python3-pip curl

echo "[Info] Downloading files from GitHub..."
pct exec $CTID -- mkdir -p /root/sync_server/static/locales
# En producción, usa raw.githubusercontent.com
pct exec $CTID -- curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/main.py -o /root/sync_server/main.py
pct exec $CTID -- curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/plex_syncer.py -o /root/sync_server/plex_syncer.py
pct exec $CTID -- curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/index.html -o /root/sync_server/static/index.html
pct exec $CTID -- curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/style.css -o /root/sync_server/static/style.css
pct exec $CTID -- curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/app.js -o /root/sync_server/static/app.js
pct exec $CTID -- curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/en.json -o /root/sync_server/static/locales/en.json
pct exec $CTID -- curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/es.json -o /root/sync_server/static/locales/es.json

pct exec $CTID -- bash -c "echo -e 'fastapi\nuvicorn\nrequests\npython-dotenv' > /root/sync_server/requirements.txt"

echo "[Info] Configuring environment variables (.env)..."
pct exec $CTID -- bash -c "cat << 'EOF' > /root/sync_server/.env
PLEX_URL=$PLEX_URL
PLEX_TOKEN=$PLEX_TOKEN
SYNC_PASSWORD_B64=$SYNC_PASSWORD_B64
TMDB_API_KEY=$TMDB_API_KEY
EOF"

echo "[Info] Configuring virtual environment..."
pct exec $CTID -- python3 -m venv /root/sync_server/venv
pct exec $CTID -- /root/sync_server/venv/bin/pip install -r /root/sync_server/requirements.txt

# 5. Servicios Systemd
echo "[Info] Creating systemd services..."
pct exec $CTID -- bash -c "cat << 'EOF' > /etc/systemd/system/syncpk-server.service
[Unit]
Description=SyncPK Central Server
After=network.target

[Service]
User=root
WorkingDirectory=/root/sync_server
ExecStart=/root/sync_server/venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF"

pct exec $CTID -- bash -c "cat << 'EOF' > /etc/systemd/system/syncpk-plex.service
[Unit]
Description=Plex Syncer Client (Pull/Push to SyncPK)
After=network.target syncpk-server.service

[Service]
User=root
WorkingDirectory=/root/sync_server
ExecStart=/root/sync_server/venv/bin/python3 /root/sync_server/plex_syncer.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF"

pct exec $CTID -- systemctl daemon-reload
pct exec $CTID -- systemctl enable syncpk-server syncpk-plex
pct exec $CTID -- systemctl start syncpk-server syncpk-plex

whiptail --title "Instalación Completada" --msgbox "SyncPK instalado exitosamente.\n\nServer deployed at IP: $CT_IP\n\nDo not forget to enter the IP ($CT_IP) y tu contraseña en los ajustes del Addon de Kodi." 12 60

echo "¡Instalación completada! IP del Servidor: $CT_IP"

