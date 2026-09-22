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

# 2. Setup Configuration
echo "[Info] Launching configuration wizard..."
curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/setup_config.sh -o /tmp/setup_config.sh
chmod +x /tmp/setup_config.sh
/tmp/setup_config.sh
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
pct create $CTID $TEMPLATE_STORAGE:vztmpl/$TEMPLATE -storage $TARGET_STORAGE -password "$ROOT_PASSWORD" -arch amd64 -hostname syncpk -cores 1 -memory 512 -net0 name=eth0,bridge=vmbr0,ip=dhcp -unprivileged 1 -features nesting=1 -timezone host
pct start $CTID

echo "[Info] Waiting for the container to boot and get an IP..."
sleep 10
CT_IP=$(pct exec $CTID -- ip -4 addr show eth0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
echo "[Info] Assigned IP: $CT_IP"

# 4. Inyección y despliegue dentro del LXC
echo "[Info] Injecting environment configuration into LXC..."
pct exec $CTID -- mkdir -p /opt/syncpk
pct push $CTID .env /opt/syncpk/.env

echo "[Info] Launching automated system installer inside LXC..."
pct exec $CTID -- bash -c "cd /opt/syncpk && curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/setup_system.sh -o setup_system.sh && chmod +x setup_system.sh && ./setup_system.sh"
pct exec $CTID -- bash -c "apt-get update >/dev/null 2>&1 && apt-get install -y curl ca-certificates >/dev/null 2>&1 && cd /opt/syncpk && curl -s https://raw.githubusercontent.com/$GITHUB_USER/$GITHUB_REPO/$GITHUB_BRANCH/setup_system.sh -o setup_system.sh && chmod +x setup_system.sh && ./setup_system.sh"

source .env
whiptail --title "Installation Completed" --msgbox "SyncPK successfully installed.\n\nWeb Dashboard: http://$CT_IP:8000\n\nGenerated API Token: $API_TOKEN_RAW\n\nPlex Webhook: http://$CT_IP:8000/webhook/plex?token=$API_TOKEN_RAW\nKodi Webhook: http://$CT_IP:8000/webhook/kodi?token=$API_TOKEN_RAW\n\nConfigure the Plex Webhook in your Plex server settings, and enter the IP and API Token in your Kodi Addon." 18 75

echo "Installation completed! Server IP: $CT_IP"


