#!/usr/bin/env bash
# Usage: bash show_summary.sh <SERVER_IP> <API_TOKEN> <INSTALL_PATH>
SERVER_IP="$1"
API_TOKEN="$2"
INSTALL_PATH="${3:-/opt/syncpk}"

source .env

if [ -f "$INSTALL_PATH/static/locales/${SYNC_LANGUAGE}.json" ]; then
    JSON_PATH="$INSTALL_PATH/static/locales/${SYNC_LANGUAGE}.json"
else
    JSON_PATH="$INSTALL_PATH/static/locales/en.json"
fi

T_INST_PASS=$(jq -r '.summary_pass_instructions' "$JSON_PATH" | sed 's/\\n/\n/g')
T_INST_NOPASS=$(jq -r '.summary_nopass_instructions' "$JSON_PATH" | sed 's/\\n/\n/g')

if [ "$HAS_PLEX_PASS" == "true" ]; then
    PLEX_WEBHOOK="Plex Webhook: http://$SERVER_IP:8000/webhook/plex?token=$API_TOKEN\n"
    INSTRUCTIONS="$T_INST_PASS"
else
    PLEX_WEBHOOK=""
    INSTRUCTIONS="$T_INST_NOPASS"
fi

whiptail --title "Installation Completed" --msgbox \
"SyncPK successfully installed at $INSTALL_PATH.

Web Dashboard:  http://$SERVER_IP:8000

Generated API Token: $API_TOKEN

${PLEX_WEBHOOK}Kodi Webhook: http://$SERVER_IP:8000/webhook/kodi?token=$API_TOKEN

${INSTRUCTIONS}" 20 75
