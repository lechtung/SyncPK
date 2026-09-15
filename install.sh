#!/usr/bin/env bash

# Default configurations (¡Cámbialas cuando subas a GitHub!)
GITHUB_USER="lechtung"
GITHUB_REPO="SyncPK"
GITHUB_BRANCH="main"
INSTALL_DIR="/opt/syncpk"

# Function to show errors
function error() {
    echo -e "\e[31m[ERROR] $1\e[0m"
    exit 1
}

# Check that we are root
if [ "$EUID" -ne 0 ]; then
    error "This script must be run as root (use sudo)."
fi

# Install dependencies needed for the installer
echo "[Info] Installing base dependencies..."
apt-get update &>/dev/null
apt-get install -y whiptail curl jq base64 python3 python3-venv python3-pip &>/dev/null

# 1. Interactive form
PLEX_URL=$(whiptail --inputbox "Enter your Plex server URL (e.g., http://192.168.1.100:32400):" 10 60 "http://" --title "Plex Configuration" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

PLEX_TOKEN=$(whiptail --inputbox "Enter your Plex Token:" 10 60 --title "Plex Configuration" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

while true; do
    SYNC_PASSWORD=$(whiptail --passwordbox "Create a master password to protect your SyncPK server:" 10 60 --title "Security" 3>&1 1>&2 2>&3)
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

# 2. Software installation
echo "[Info] Preparing directory $INSTALL_DIR..."
mkdir -p $INSTALL_DIR

echo "[Info] Downloading files from GitHub..."
mkdir -p $INSTALL_DIR/static/locales
# In production, use raw.githubusercontent.com
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/main.py -o $INSTALL_DIR/main.py
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/plex_syncer.py -o $INSTALL_DIR/plex_syncer.py
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/index.html -o $INSTALL_DIR/static/index.html
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/style.css -o $INSTALL_DIR/static/style.css
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/app.js -o $INSTALL_DIR/static/app.js
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/en.json -o $INSTALL_DIR/static/locales/en.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/es.json -o $INSTALL_DIR/static/locales/es.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/requirements.txt -o $INSTALL_DIR/requirements.txt

# If files do not exist on GitHub yet, create dummies to prevent script failure
if [ ! -f $INSTALL_DIR/requirements.txt ] || ! grep -q "fastapi" $INSTALL_DIR/requirements.txt; then
    echo -e "fastapi\nuvicorn\nrequests\npython-dotenv\npython-multipart" > $INSTALL_DIR/requirements.txt
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

# Get local IP to display
LOCAL_IP=$(hostname -I | awk '{print $1}')

# Setup MOTD for SSH/Console login
echo "[Info] Configuring MOTD..."
cat << 'EOF' > /etc/profile.d/syncpk-motd.sh
#!/bin/bash
LOCAL_IP=$(hostname -I | awk '{print $1}')
echo -e "\e[32m"
echo "================================================="
echo "               SyncPK Server Active              "
echo "================================================="
echo " Web Dashboard: http://$LOCAL_IP:8000"
echo " Kodi Webhook:  http://$LOCAL_IP:8000/webhook/kodi?token=$SYNC_PASSWORD_B64"
echo " Plex Webhook:  http://$LOCAL_IP:8000/webhook/plex?token=$SYNC_PASSWORD_B64"
echo "================================================="
echo -e "\e[0m"
EOF
chmod +x /etc/profile.d/syncpk-motd.sh

whiptail --title "Installation Completed" --msgbox "SyncPK successfully installed in $INSTALL_DIR.\n\nWeb Dashboard: http://$LOCAL_IP:8000\n\nPlex Webhook: http://$LOCAL_IP:8000/webhook/plex?token=$SYNC_PASSWORD_B64\nKodi Webhook: http://$LOCAL_IP:8000/webhook/kodi?token=$SYNC_PASSWORD_B64\n\nConfigure the Plex Webhook in your Plex server settings, and enter the IP and password in your Kodi Addon." 14 75

echo "Installation completed! Server IP: $LOCAL_IP"


