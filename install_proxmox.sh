#!/usr/bin/env bash

GITHUB_USER="lechtung"
GITHUB_REPO="SyncPK"
GITHUB_BRANCH="main"

function error() {
    echo -e "\e[31m[ERROR] $1\e[0m"
    exit 1
}

if [ "$EUID" -ne 0 ]; then
    error "This script must be run as root (administrator privileges)."
fi

if ! command -v pvesm &> /dev/null; then
    error "This script must be run on the Proxmox HOST, not inside a container."
fi

if ! command -v whiptail &> /dev/null || ! command -v curl &> /dev/null; then
    apt-get update &>/dev/null
    apt-get install -y whiptail curl &>/dev/null
fi

echo "[Info] Searching for storages compatible with LXC containers..."
STORAGES=$(pvesm status -content rootdir | awk 'NR>1 {print $1}')
if [ -z "$STORAGES" ]; then
    error "No container-compatible storage (rootdir) found."
fi

STORAGE_MENU=()
for s in $STORAGES; do
    STORAGE_MENU+=("$s" "")
done

TARGET_STORAGE=$(whiptail --title "SyncPK - Storage Selection" --menu "Select the storage drive for the new LXC container:" 15 50 4 "${STORAGE_MENU[@]}" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

while true; do
    ROOT_PASSWORD=$(whiptail --passwordbox "Enter a root password for the LXC container (min 5 chars):" 10 60 --title "SyncPK - Security" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi

    if [ ${#ROOT_PASSWORD} -lt 5 ]; then
        whiptail --msgbox "The password must be at least 5 characters long." 8 45 --title "Error"
        continue
    fi

    ROOT_PASSWORD_CONFIRM=$(whiptail --passwordbox "Confirm the root password:" 10 60 --title "SyncPK - Security" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi

    if [ "$ROOT_PASSWORD" == "$ROOT_PASSWORD_CONFIRM" ]; then
        break
    else
        whiptail --msgbox "Passwords do not match. Please try again." 8 45 --title "Error"
    fi
done

CTID=$(pvesh get /cluster/nextid)
TEMPLATE_STORAGE=$(pvesm status -content vztmpl | awk 'NR>1 {print $1}' | head -n 1)

if [ -z "$TEMPLATE_STORAGE" ]; then
    error "No storage found that supports LXC templates (vztmpl)."
fi

echo "[Info] Searching for downloaded LXC templates..."
DOWNLOADED_TEMPLATES=$(pvesm list $TEMPLATE_STORAGE --content vztmpl | awk 'NR>1 {print $1}')
if [ -z "$DOWNLOADED_TEMPLATES" ]; then
    error "No templates downloaded in $TEMPLATE_STORAGE. Please download a template first via Proxmox UI."
fi

TEMPLATE_MENU=()
for t in $DOWNLOADED_TEMPLATES; do
    name=$(basename "$t")
    TEMPLATE_MENU+=("$t" "$name")
done

LATEST_TEMPLATE=$(whiptail --title "SyncPK - Template Selection" --menu "Select the base image for the LXC container:" 15 70 6 "${TEMPLATE_MENU[@]}" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

echo "[Info] Creating CT container $CTID..."
pct create $CTID $LATEST_TEMPLATE -storage $TARGET_STORAGE -rootfs $TARGET_STORAGE:8 -password "$ROOT_PASSWORD" -arch amd64 -hostname syncpk -cores 1 -memory 512 -net0 name=eth0,bridge=vmbr0,ip=dhcp -unprivileged 1 -features nesting=1 -timezone host -onboot 1 || error "Failed to create LXC container (pct create failed)."

pct start $CTID

echo "[Info] Waiting for the container to get network access..."
NET_OK=0
for i in $(seq 1 30); do
    if pct exec $CTID -- getent hosts raw.githubusercontent.com >/dev/null 2>&1; then
        NET_OK=1
        break
    fi
    sleep 2
done

if [ "$NET_OK" -eq 0 ]; then
    error "No network access in the LXC container (Check bridge and DHCP). Debug with: pct enter $CTID"
fi

CT_IP=$(pct exec $CTID -- ip -4 addr show eth0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
echo "[Info] Assigned IP: $CT_IP"

echo "[Info] Launching automated system installer inside LXC..."
pct exec $CTID -- bash -c "set -euo pipefail; apt-get update >/dev/null 2>&1 && apt-get install -y curl ca-certificates >/dev/null 2>&1 && curl -fsSL https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/install.sh > install.sh && bash install.sh" || error "install.sh failed inside LXC."

echo "[Info] Configuring Welcome Message (MOTD)..."
pct exec $CTID -- bash -c "cat << 'EOF' > /etc/profile.d/syncpk-motd.sh
#!/bin/bash
LOCAL_IP=\$(hostname -I | awk '{print \$1}')
echo \"\"
echo \"================================================================\"
echo -e \" \e[32m\e[1mSyncPK - Two-Way Kodi & Plex Sync Server\e[0m\"
echo \"================================================================\"
echo -e \" \e[1mWeb Dashboard:\e[0m http://\$LOCAL_IP:8000\"
echo -e \" \e[1mKodi Webhook:\e[0m  http://\$LOCAL_IP:8000/webhook/kodi\"
echo -e \" \e[1mPlex Webhook:\e[0m  http://\$LOCAL_IP:8000/webhook/plex\"
echo \"(Make sure to append your ?token=... in Plex/Kodi if configured)\"
echo \"================================================================\"
echo \"\"
EOF"
pct exec $CTID -- chmod +x /etc/profile.d/syncpk-motd.sh

echo "================================================================"
echo -e "\e[32mInstallation completed successfully!\e[0m"
echo -e "Open your browser to launch the Web Setup Wizard:"
echo -e "➡️  \e[1mhttp://$CT_IP:8000\e[0m"
echo "================================================================"
