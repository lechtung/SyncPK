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

SYNC_LANGUAGE=$(whiptail --menu "Choose your preferred language for metadata:" 16 60 8 \
"en" "English" \
"es" "Español" \
"de" "Deutsch" \
"fr" "Français" \
"it" "Italiano" \
"pt" "Português" \
"zh" "Chinese (Simplified)" \
"ja" "Japanese" 3>&1 1>&2 2>&3)

if [ $? -ne 0 ] || [ -z "$SYNC_LANGUAGE" ]; then 
    SYNC_LANGUAGE="en" 
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
# Save the API_TOKEN as well for MOTD generation later
API_TOKEN_RAW=$API_TOKEN
EOF

echo "[Info] Configuration saved successfully in .env."
