#!/usr/bin/env bash

# Function to show errors
function error() {
    echo -e "\e[31m[ERROR] $1\e[0m"
    exit 1
}

# Install dependencies needed for the config wizard if not present
if ! command -v whiptail &> /dev/null || ! command -v jq &> /dev/null || ! command -v curl &> /dev/null; then
    echo "[Info] Installing base dependencies for wizard..."
    apt-get update &>/dev/null
    apt-get install -y whiptail curl jq &>/dev/null
fi

if [ -f /tmp/install_lang ]; then
    INSTALL_LANG=$(cat /tmp/install_lang)
else
    INSTALL_LANG="en"
fi

if [ ! -f /tmp/install.json ]; then
    echo "[Info] Loading language pack ($INSTALL_LANG)..."
    JSON_PATH="server/static/locales/${INSTALL_LANG}.json"
    if [ -f "$JSON_PATH" ]; then
        cp "$JSON_PATH" /tmp/install.json
    else
        curl -s "https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/${INSTALL_LANG}.json" -o /tmp/install.json
    fi
fi

# Helper to read translation keys
function t() {
    jq -r ".$1" /tmp/install.json | sed 's/\\n/\n/g'
}

T_PLEX_URL=$(t "install_plex_url")
T_PLEX_PASS=$(t "install_plex_pass")
T_PLEX_AUTH=$(t "install_plex_auth")
T_PWD=$(t "install_pwd")
T_PWD_CONFIRM=$(t "install_pwd_confirm")
T_PWD_ERR=$(t "install_pwd_error")
T_TMDB=$(t "install_tmdb")
T_SYNC_LANG=$(t "install_sync_lang")
T_DASH_LANG=$(t "install_dash_lang")
T_DEBUG=$(t "install_debug")

# 1. Interactive form
PLEX_URL=$(whiptail --inputbox "$T_PLEX_URL" 10 60 "http://" --title "Plex Configuration" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

HAS_PLEX_PASS=$(whiptail --yesno "$T_PLEX_PASS" 10 60 --title "Plex Configuration" 3>&1 1>&2 2>&3; echo $?)
if [ "$HAS_PLEX_PASS" -eq 0 ]; then
    HAS_PLEX_PASS="true"
else
    HAS_PLEX_PASS="false"
fi

# Plex PIN Auth
echo "[Info] Requesting Plex authentication PIN..."
PLEX_CLIENT_ID="SPK-$(uuidgen)"
PIN_RESPONSE=$(curl -s -X POST "https://plex.tv/api/v2/pins?strong=true" -H "Accept: application/json" -H "X-Plex-Product: SyncPK" -H "X-Plex-Client-Identifier: $PLEX_CLIENT_ID")
PIN_ID=$(echo "$PIN_RESPONSE" | jq -r '.id')
PIN_CODE=$(echo "$PIN_RESPONSE" | jq -r '.code')
AUTH_URL="https://app.plex.tv/auth#?clientID=$PLEX_CLIENT_ID&code=$PIN_CODE&context[device][product]=SyncPK"

whiptail --msgbox "$T_PLEX_AUTH" 10 60
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
    SYNC_PASSWORD=$(whiptail --passwordbox "$T_PWD" 10 60 --title "Security" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi

    SYNC_PASSWORD_CONFIRM=$(whiptail --passwordbox "$T_PWD_CONFIRM" 10 60 --title "Security" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi

    if [ "$SYNC_PASSWORD" == "$SYNC_PASSWORD_CONFIRM" ]; then
        break
    else
        whiptail --msgbox "$T_PWD_ERR" 8 45 --title "Error"
    fi
done

TMDB_API_KEY=$(whiptail --inputbox "$T_TMDB" 10 60 --title "TMDB (The Movie Database)" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

DASHBOARD_LANGUAGE=$(whiptail --menu "$T_DASH_LANG" 16 60 9 \
"auto" "Browser Default" \
"en" "English" \
"es" "Español" \
"de" "Deutsch" \
"fr" "Français" \
"it" "Italiano" \
"pt" "Português" \
"zh" "Chinese (Simplified)" \
"ja" "Japanese" \
--default-item "auto" 3>&1 1>&2 2>&3)

if [ $? -ne 0 ] || [ -z "$DASHBOARD_LANGUAGE" ]; then 
    DASHBOARD_LANGUAGE="auto" 
fi

SYNC_LANGUAGE=$(whiptail --menu "$T_SYNC_LANG" 16 60 8 \
"en" "English" \
"es" "Español" \
"de" "Deutsch" \
"fr" "Français" \
"it" "Italiano" \
"pt" "Português" \
"zh" "Chinese (Simplified)" \
"ja" "Japanese" \
--default-item "$INSTALL_LANG" 3>&1 1>&2 2>&3)

if [ $? -ne 0 ] || [ -z "$SYNC_LANGUAGE" ]; then 
    SYNC_LANGUAGE="en" 
fi

if whiptail --yesno "$T_DEBUG" 10 60 --defaultno --title "Backend Configuration" 3>&1 1>&2 2>&3; then
    DEBUG_MODE="true"
else
    DEBUG_MODE="false"
fi

# Generate secure hashes and tokens
SALT=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 16 | head -n 1)
WEB_HASH=$(echo -n "${SYNC_PASSWORD}${SALT}" | sha256sum | awk '{print $1}')

API_TOKEN=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 32 | head -n 1)
API_TOKEN="sk_syncpk_${API_TOKEN}"
API_HASH=$(echo -n "${API_TOKEN}${SALT}" | sha256sum | awk '{print $1}')

echo "[Info] Saving configuration to .env file..."
cat << EOF > .env
PLEX_URL=$PLEX_URL
PLEX_TOKEN=$PLEX_TOKEN
HAS_PLEX_PASS=$HAS_PLEX_PASS
SALT=$SALT
WEB_HASH=$WEB_HASH
API_HASH=$API_HASH
TMDB_API_KEY=$TMDB_API_KEY
SYNC_LANGUAGE=$SYNC_LANGUAGE
DASHBOARD_LANGUAGE=$DASHBOARD_LANGUAGE
DEBUG=$DEBUG_MODE
# Save the API_TOKEN as well for MOTD generation later
API_TOKEN_RAW=$API_TOKEN
PLEX_CLIENT_ID=$PLEX_CLIENT_ID
EOF

echo "[Info] Configuration saved successfully in .env."
cp .env .env.bak
echo "[Info] Backup saved to .env.bak."

if [ ! -f .conf ]; then
    if [ -f .conf.example ]; then
        cp .conf.example .conf
        echo "[Info] Created .conf from .conf.example"
    else
        echo "# Controls the opacity of the dark mask over fanart images (0.0 to 1.0)" > .conf
        echo "FANART_MASK_OPACITY=0.3" >> .conf
        echo "[Info] Created new .conf"
    fi
fi
