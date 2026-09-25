#!/usr/bin/env bash

GITHUB_USER=${1:-"lechtung"}
GITHUB_REPO=${2:-"SyncPK"}
GITHUB_BRANCH=${3:-"main"}

# Install dependencies needed for the config wizard if not present
if ! command -v whiptail &> /dev/null || ! command -v jq &> /dev/null || ! command -v curl &> /dev/null; then
    echo "[Info] Installing base dependencies for wizard..."
    apt-get update &>/dev/null
    apt-get install -y whiptail curl jq &>/dev/null
fi

# Detect Timezone to guess initial installation language
TZ=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null)
DETECTED_LANG="en"
case "$TZ" in
    Europe/Madrid|America/Mexico_City|America/Buenos_Aires|America/Bogota|America/Lima) DETECTED_LANG="es" ;;
    Europe/Berlin|Europe/Vienna|Europe/Zurich) DETECTED_LANG="de" ;;
    Europe/Paris|America/Montreal) DETECTED_LANG="fr" ;;
    Europe/Rome) DETECTED_LANG="it" ;;
    Europe/Lisbon|America/Sao_Paulo) DETECTED_LANG="pt" ;;
    Asia/Tokyo) DETECTED_LANG="ja" ;;
    Asia/Shanghai|Asia/Chongqing) DETECTED_LANG="zh" ;;
    *) DETECTED_LANG="en" ;;
esac

# Try to load detected lang JSON to translate the prompt itself
# Look locally first (for local executions), otherwise download
JSON_PATH="server/static/locales/${DETECTED_LANG}.json"
if [ -f "$JSON_PATH" ]; then
    cp "$JSON_PATH" /tmp/temp_lang.json
else
    curl -s "https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/${DETECTED_LANG}.json" -o /tmp/temp_lang.json
fi

PROMPT_TEXT="Choose your preferred language for the installation wizard:"
if [ -f /tmp/temp_lang.json ]; then
    VAL=$(jq -r '.install_lang_prompt' /tmp/temp_lang.json)
    if [ "$VAL" != "null" ] && [ -n "$VAL" ]; then
        PROMPT_TEXT="$VAL"
    fi
fi

# Ask for Installation Language
INSTALL_LANG=$(whiptail --menu "$PROMPT_TEXT" 16 60 8 \
"en" "English" \
"es" "Español" \
"de" "Deutsch" \
"fr" "Français" \
"it" "Italiano" \
"pt" "Português" \
"zh" "中文" \
"ja" "日本語" \
--default-item "$DETECTED_LANG" \
3>&1 1>&2 2>&3)

if [ $? -ne 0 ] || [ -z "$INSTALL_LANG" ]; then 
    INSTALL_LANG="en" 
fi

# Download the final selected language JSON to parse texts for the rest of the scripts
echo "[Info] Loading language pack ($INSTALL_LANG)..."
JSON_PATH="server/static/locales/${INSTALL_LANG}.json"
if [ -f "$JSON_PATH" ]; then
    cp "$JSON_PATH" /tmp/install.json
else
    curl -s "https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/server/static/locales/${INSTALL_LANG}.json" -o /tmp/install.json
fi

echo "$INSTALL_LANG" > /tmp/install_lang
