#!/usr/bin/env bash
# Usage: bash show_summary.sh <SERVER_IP> <API_TOKEN> <INSTALL_PATH>
SERVER_IP="$1"
API_TOKEN="$2"
INSTALL_PATH="${3:-/opt/syncpk}"

whiptail --title "Installation Completed" --msgbox \
"SyncPK successfully installed at $INSTALL_PATH.

Web Dashboard:  http://$SERVER_IP:8000

Generated API Token: $API_TOKEN

Plex Webhook: http://$SERVER_IP:8000/webhook/plex?token=$API_TOKEN
Kodi Webhook: http://$SERVER_IP:8000/webhook/kodi?token=$API_TOKEN

Configure the Plex Webhook in your Plex server settings,
and enter the IP and API Token in your Kodi Addon." 20 75
