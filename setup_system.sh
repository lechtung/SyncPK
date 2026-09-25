#!/usr/bin/env bash

# Function to show errors
function error() {
    echo -e "\e[31m[ERROR] $1\e[0m"
    exit 1
}

# Ensure .env exists
if [ ! -f ".env" ]; then
    error ".env file not found! Please run setup_config.sh first."
fi

# Load environment variables (including API_TOKEN_RAW for MOTD)
source .env

GITHUB_USER="lechtung"
GITHUB_REPO="SyncPK"
GITHUB_BRANCH="main"
INSTALL_DIR="/opt/syncpk"

# 1. Install dependencies
echo "[Info] Installing OS dependencies..."
apt-get update &>/dev/null
apt-get install -y curl python3 python3-venv python3-pip &>/dev/null

# 2. Software installation
echo "[Info] Preparing directory $INSTALL_DIR..."
mkdir -p $INSTALL_DIR

echo "[Info] Downloading files from GitHub..."
mkdir -p $INSTALL_DIR/static/locales
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/main.py -o $INSTALL_DIR/main.py
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/index.html -o $INSTALL_DIR/static/index.html
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/style.css -o $INSTALL_DIR/static/style.css
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/app.js -o $INSTALL_DIR/static/app.js
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/en.json -o $INSTALL_DIR/static/locales/en.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/es.json -o $INSTALL_DIR/static/locales/es.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/de.json -o $INSTALL_DIR/static/locales/de.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/fr.json -o $INSTALL_DIR/static/locales/fr.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/it.json -o $INSTALL_DIR/static/locales/it.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/pt.json -o $INSTALL_DIR/static/locales/pt.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/ja.json -o $INSTALL_DIR/static/locales/ja.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/zh.json -o $INSTALL_DIR/static/locales/zh.json
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/favicon.ico -o $INSTALL_DIR/static/favicon.ico
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/requirements.txt -o $INSTALL_DIR/requirements.txt

# Copy the generated .env file to the installation directory
if [ "$(realpath .env 2>/dev/null)" != "$(realpath $INSTALL_DIR/.env 2>/dev/null)" ]; then
    cp .env $INSTALL_DIR/.env
fi

# Fallback requirements
if [ ! -f $INSTALL_DIR/requirements.txt ] || ! grep -q "fastapi" $INSTALL_DIR/requirements.txt; then
    echo -e "fastapi\nuvicorn\nrequests\npython-dotenv\npython-multipart\nhttpx" > $INSTALL_DIR/requirements.txt
fi

echo "[Info] Configuring virtual environment..."
python3 -m venv $INSTALL_DIR/venv
$INSTALL_DIR/venv/bin/pip install -r $INSTALL_DIR/requirements.txt

# 3. Systemd Services
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
echo " Webhook Token: $API_TOKEN_RAW"
echo " Kodi Webhook:  http://\$LOCAL_IP:8000/webhook/kodi?token=$API_TOKEN_RAW"
EOF2

if [ "$HAS_PLEX_PASS" == "true" ]; then
    echo "echo \" Plex Webhook:  http://\$LOCAL_IP:8000/webhook/plex?token=$API_TOKEN_RAW\"" >> /etc/profile.d/syncpk-motd.sh
fi

cat << EOF2 >> /etc/profile.d/syncpk-motd.sh
echo "================================================="
echo -e "\e[0m"
EOF2
chmod +x /etc/profile.d/syncpk-motd.sh

echo "[Info] Installation completed! Server IP: $LOCAL_IP"
echo "[Info] Web Dashboard: http://$LOCAL_IP:8000"
echo "[Info] API Token: $API_TOKEN_RAW"
