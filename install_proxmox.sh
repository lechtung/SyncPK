#!/usr/bin/env bash

# Default configurations (Change them when uploading to GitHub!)
GITHUB_USER="lechtung"
GITHUB_REPO="SyncPK"
GITHUB_BRANCH="main"

# Function to show errors
function error() {
    echo -e "\e[31m[ERROR] $1\e[0m"
    exit 1
}

# Check that we are root on Proxmox
if [ "$EUID" -ne 0 ]; then
    error "This script must be run as root (administrator privileges)."
fi

if ! command -v pvesm &> /dev/null; then
    error "This script must be run on the Proxmox HOST, not inside a container."
fi

# Install dependencies needed for the installer
apt-get update &>/dev/null
apt-get install -y whiptail curl jq base64 &>/dev/null

# 1. Storage autodiscovery
echo "[Info] Searching for storages compatible with LXC containers..."
STORAGES=$(pvesm status -content rootdir | awk 'NR>1 {print $1}')
if [ -z "$STORAGES" ]; then
    error "No container-compatible storage (rootdir) found."
fi

# Build menu for whiptail
STORAGE_MENU=()
for s in $STORAGES; do
    STORAGE_MENU+=("$s" "")
done

TARGET_STORAGE=$(whiptail --title "LXC Storage" --menu "Select the disk to install SyncPK on:" 15 50 4 "${STORAGE_MENU[@]}" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

# 2. Interactive form
PLEX_URL=$(whiptail --inputbox "Enter your Plex server URL (e.g., http://192.168.1.100:32400):" 10 60 "http://" --title "Plex Configuration" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

PLEX_TOKEN=$(whiptail --inputbox "Enter your Plex Token:" 10 60 --title "Plex Configuration" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

while true; do
    ROOT_PASSWORD=$(whiptail --passwordbox "Create a ROOT password for the Proxmox LXC container (for SSH/Console):" 10 60 --title "LXC Security" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi

    ROOT_PASSWORD_CONFIRM=$(whiptail --passwordbox "Confirm your ROOT password:" 10 60 --title "LXC Security" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi

    if [ "$ROOT_PASSWORD" == "$ROOT_PASSWORD_CONFIRM" ]; then
        break
    else
        whiptail --msgbox "Passwords do not match. Please try again." 8 45 --title "Error"
    fi
done

while true; do
    SYNC_PASSWORD=$(whiptail --passwordbox "Create a master password for SyncPK (Kodi addon & Web Dashboard):" 10 60 --title "SyncPK Security" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi

    SYNC_PASSWORD_CONFIRM=$(whiptail --passwordbox "Confirm your master password:" 10 60 --title "Security" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi

    if [ "$SYNC_PASSWORD" == "$SYNC_PASSWORD_CONFIRM" ]; then
        break
    else
        whiptail --msgbox "Passwords do not match. Please try again." 8 45 --title "Error"
    fi
done

TMDB_API_KEY=$(whiptail --inputbox "Enter your TMDB API Key (Free at themoviedb.org) to load posters:" 10 60 --title "TMDB (The Movie Database)" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

# Convert password to Base64
SYNC_PASSWORD_B64=$(echo -n "$SYNC_PASSWORD" | base64)

# 3. Download template and create LXC
CTID=$(pvesh get /cluster/nextid)

# Find a storage that supports templates (vztmpl)
TEMPLATE_STORAGE=$(pvesm status -content vztmpl | awk 'NR>1 {print $1}' | head -n 1)
if [ -z "$TEMPLATE_STORAGE" ]; then
    error "No storage found that supports LXC templates (vztmpl)."
fi

echo "[Info] Fetching latest Debian 12 template version..."
pveam update &>/dev/null
LATEST_TEMPLATE=$(pveam available | grep debian-12-standard | awk '{print $2}' | sort -V | tail -n 1)

if [ -z "$LATEST_TEMPLATE" ]; then
    error "Could not find a valid Debian 12 template. Please check your Proxmox internet connection."
fi

# Fetch and sort local templates from TEMPLATE_STORAGE
LOCAL_TEMPLATES=$(pvesm list $TEMPLATE_STORAGE --content vztmpl | awk 'NR>1 {print $1}' | cut -d'/' -f2)

if echo "$LOCAL_TEMPLATES" | grep -q "$LATEST_TEMPLATE"; then
    DISP_LATEST=$LATEST_TEMPLATE
    if [ ${#DISP_LATEST} -gt 35 ]; then DISP_LATEST="${DISP_LATEST:0:32}..."; fi
    OPTIONS=( "1" "Use latest Debian 12 ($DISP_LATEST) [OK]" )
    LOCAL_TEMPLATES=$(echo "$LOCAL_TEMPLATES" | grep -v "$LATEST_TEMPLATE")
else
    DISP_LATEST=$LATEST_TEMPLATE
    if [ ${#DISP_LATEST} -gt 35 ]; then DISP_LATEST="${DISP_LATEST:0:32}..."; fi
    OPTIONS=( "1" "Download Debian 12 ($DISP_LATEST)" )
fi

DEBIAN_TEMPLATES=$(echo "$LOCAL_TEMPLATES" | grep "debian" | sort -rV)
OTHER_TEMPLATES=$(echo "$LOCAL_TEMPLATES" | grep -v "debian" | sort -rV)

idx=2
while read -r t; do
    if [ "$idx" -le 11 ] && [ -n "$t" ]; then
        DISP_T=$t
        if [ ${#DISP_T} -gt 45 ]; then DISP_T="${DISP_T:0:42}..."; fi
        OPTIONS+=( "$idx" "Use local: $DISP_T" )
        eval "LOCAL_TPL_${idx}='$t'"
        idx=$((idx+1))
    fi
done <<< "$(echo -e "${DEBIAN_TEMPLATES}\n${OTHER_TEMPLATES}" | grep -v '^$')"

CHOICE=$(whiptail --title "OS Template Selection" --menu "Choose a template for the LXC container.\nNOTE: SyncPK is fully tested and supported on DEBIAN." 20 80 10 "${OPTIONS[@]}" 3>&1 1>&2 2>&3)

if [ -z "$CHOICE" ]; then
    error "Installation cancelled by user."
fi

if [ "$CHOICE" == "1" ]; then
    TEMPLATE=$LATEST_TEMPLATE
    echo "[Info] Checking if latest template is already downloaded..."
    if pvesm list $TEMPLATE_STORAGE --content vztmpl | grep -q "$TEMPLATE"; then
        echo "[Info] Template already exists locally, skipping download."
    else
        echo "[Info] Downloading latest template..."
        pveam download $TEMPLATE_STORAGE $TEMPLATE &>/dev/null || error "Failed to download the template."
    fi
else
    eval "TEMPLATE=\$LOCAL_TPL_${CHOICE}"
    echo "[Info] Proceeding with selected local template: $TEMPLATE"
fi

echo "[Info] Creating CT container $CTID..."
pct create $CTID $TEMPLATE_STORAGE:vztmpl/$TEMPLATE -storage $TARGET_STORAGE -password "$ROOT_PASSWORD" -arch amd64 -hostname syncpk -cores 1 -memory 512 -net0 name=eth0,bridge=vmbr0,ip=dhcp -unprivileged 1 -features nesting=1
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
# In production, use raw.githubusercontent.com
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

# Setup MOTD for SSH/Console login
echo "[Info] Configuring MOTD..."
pct exec $CTID -- bash -c "cat << 'EOF' > /etc/profile.d/syncpk-motd.sh
#!/bin/bash
LOCAL_IP=\$(hostname -I | awk '{print \$1}')
echo -e \"\e[32m\"
echo \"=================================================\"
echo \"               SyncPK Server Active              \"
echo \"=================================================\"
echo \" Web Dashboard: http://\$LOCAL_IP:8000\"
echo \" Kodi Webhook:  http://\$LOCAL_IP:8000/webhook/kodi\"
echo \" Plex Webhook:  http://\$LOCAL_IP:8000/webhook/plex\"
echo \"=================================================\"
echo -e \"\e[0m\"
EOF"
pct exec $CTID -- chmod +x /etc/profile.d/syncpk-motd.sh

whiptail --title "Installation Completed" --msgbox "SyncPK successfully installed.\n\nWeb Dashboard: http://$CT_IP:8000\n\nPlex Webhook: http://$CT_IP:8000/webhook/plex\nKodi Webhook: http://$CT_IP:8000/webhook/kodi\n\nConfigure the Plex Webhook in your Plex server settings, and enter the IP and password in your Kodi Addon." 14 75

echo "Installation completed! Server IP: $CT_IP"


