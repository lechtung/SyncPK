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

HAS_PLEX_PASS=$(whiptail --yesno "Do you have an active Plex Pass subscription?" 10 60 --title "Plex Configuration" 3>&1 1>&2 2>&3; echo $?)
if [ "$HAS_PLEX_PASS" -eq 0 ]; then
    HAS_PLEX_PASS="true"
else
    HAS_PLEX_PASS="false"
fi

# Plex PIN Auth
echo "[Info] Requesting Plex authentication PIN..."
PLEX_CLIENT_ID="syncpk-installer-$RANDOM-$RANDOM"
PIN_RESPONSE=$(curl -s -X POST "https://plex.tv/api/v2/pins?strong=true" -H "Accept: application/json" -H "X-Plex-Product: SyncPK" -H "X-Plex-Client-Identifier: $PLEX_CLIENT_ID")
PIN_ID=$(echo "$PIN_RESPONSE" | jq -r '.id')
PIN_CODE=$(echo "$PIN_RESPONSE" | jq -r '.code')
AUTH_URL="https://app.plex.tv/auth#?clientID=$PLEX_CLIENT_ID&code=$PIN_CODE&context[device][product]=SyncPK"

whiptail --msgbox "Plex Authentication Required!\n\nOn the next screen, you will see a link. Copy it and open it in your browser. The script will wait for you to authorize." 10 60
clear
echo -e "\n============================================="
echo -e "🔗 PLEX AUTHORIZATION LINK:"
echo -e "$AUTH_URL"
echo -e "=============================================\n"
echo "[Info] Waiting for you to authorize in your browser (it will auto-resume)..."
PLEX_TOKEN=""
while [ -z "$PLEX_TOKEN" ] || [ "$PLEX_TOKEN" == "null" ]; do
    sleep 3
    CHECK_RESPONSE=$(curl -s -X GET "https://plex.tv/api/v2/pins/$PIN_ID" -H "Accept: application/json" -H "X-Plex-Client-Identifier: $PLEX_CLIENT_ID")
    PLEX_TOKEN=$(echo "$CHECK_RESPONSE" | jq -r '.authToken')
done
echo "[Info] Plex authentication successful!"

while true; do
    SYNC_PASSWORD=$(whiptail --passwordbox "Create a master password for the Web Dashboard:" 10 60 --title "Security" 3>&1 1>&2 2>&3)
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

# Generate secure hashes and tokens
SALT=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 16 | head -n 1)
WEB_HASH=$(echo -n "${SYNC_PASSWORD}${SALT}" | sha256sum | awk '{print $1}')

API_TOKEN=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 32 | head -n 1)
API_TOKEN="sk_syncpk_${API_TOKEN}"
API_HASH=$(echo -n "${API_TOKEN}${SALT}" | sha256sum | awk '{print $1}')

# 2. Software installation
echo "[Info] Preparing directory $INSTALL_DIR..."
mkdir -p $INSTALL_DIR

echo "[Info] Downloading files from GitHub..."
mkdir -p $INSTALL_DIR/static/locales
# In production, use raw.githubusercontent.com
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/main.py -o $INSTALL_DIR/main.py
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/index.html -o $INSTALL_DIR/static/index.html
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/style.css -o $INSTALL_DIR/static/style.css
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/app.js -o $INSTALL_DIR/static/app.js
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/en.json -o $INSTALL_DIR/static/locales/en.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/es.json -o $INSTALL_DIR/static/locales/es.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/favicon.ico -o $INSTALL_DIR/static/favicon.ico
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/requirements.txt -o $INSTALL_DIR/requirements.txt

# If files do not exist on GitHub yet, create dummies to prevent script failure
if [ ! -f $INSTALL_DIR/requirements.txt ] || ! grep -q "fastapi" $INSTALL_DIR/requirements.txt; then
    echo -e "fastapi\nuvicorn\nrequests\npython-dotenv\npython-multipart\nhttpx" > $INSTALL_DIR/requirements.txt
fi

echo "[Info] Configuring environment variables (.env)..."
cat << EOF > $INSTALL_DIR/.env
PLEX_URL=$PLEX_URL
PLEX_TOKEN=$PLEX_TOKEN
HAS_PLEX_PASS=$HAS_PLEX_PASS
SALT=$SALT
WEB_HASH=$WEB_HASH
API_HASH=$API_HASH
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

systemctl daemon-reload
systemctl enable syncpk-server
systemctl start syncpk-server

# Get local IP to display
LOCAL_IP=$(hostname -I | awk '{print $1}')

# Setup MOTD for SSH/Console login
echo "[Info] Configuring MOTD..."
cat << EOF2 > /etc/profile.d/syncpk-motd.sh
#!/bin/bash
LOCAL_IP=\$(hostname -I | awk '{print \$1}')
echo -e "\e[32m"
echo "================================================="
echo "               SyncPK Server Active              "
echo "================================================="
echo " Web Dashboard: http://\$LOCAL_IP:8000"
echo " Webhook Token: $API_TOKEN"
echo " Kodi Webhook:  http://\$LOCAL_IP:8000/webhook/kodi?token=$API_TOKEN"
echo " Plex Webhook:  http://\$LOCAL_IP:8000/webhook/plex?token=$API_TOKEN"
echo "================================================="
echo -e "\e[0m"
EOF2
chmod +x /etc/profile.d/syncpk-motd.sh

whiptail --title "Installation Completed" --msgbox "SyncPK successfully installed in $INSTALL_DIR.\n\nWeb Dashboard: http://$LOCAL_IP:8000\n\nGenerated API Token: $API_TOKEN\n\nPlex Webhook: http://$LOCAL_IP:8000/webhook/plex?token=$API_TOKEN\nKodi Webhook: http://$LOCAL_IP:8000/webhook/kodi?token=$API_TOKEN\n\nConfigure the Plex Webhook in your Plex server settings, and enter the IP and API Token in your Kodi Addon." 18 75

echo "Installation completed! Server IP: $LOCAL_IP"


