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

apt-get update &>/dev/null
apt-get install -y whiptail curl &>/dev/null

echo "[Info] Searching for storages compatible with LXC containers..."
STORAGES=$(pvesm status -content rootdir | awk 'NR>1 {print $1}')
if [ -z "$STORAGES" ]; then
    error "No container-compatible storage (rootdir) found."
fi

STORAGE_MENU=()
for s in $STORAGES; do
    STORAGE_MENU+=("$s" "")
done

TARGET_STORAGE=\((whiptail --title "SyncPK - Storage Selection" --menu "Select the storage drive for the new LXC container:" 15 50 4 "\){STORAGE_MENU[@]}" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

while true; do
    ROOT_PASSWORD=$(whiptail --passwordbox "Enter a root password for the LXC container:" 10 60 --title "SyncPK - Security" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi

    ROOT_PASSWORD_CONFIRM=$(whiptail --passwordbox "Confirm the root password:" 10 60 --title "SyncPK - Security" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi

    if [ "\(ROOT_PASSWORD" == "\)ROOT_PASSWORD_CONFIRM" ]; then
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

echo "[Info] Fetching latest Debian 12 template version..."
pveam update &>/dev/null
LATEST_TEMPLATE=$(pveam available | grep debian-12-standard | awk '{print $2}' | sort -V | tail -n 1)

if [ -z "$LATEST_TEMPLATE" ]; then
    error "Could not find a valid Debian 12 template."
fi

echo "[Info] Downloading/Checking template..."
if ! pvesm list \(TEMPLATE_STORAGE --content vztmpl | grep -q "\)LATEST_TEMPLATE"; then
    pveam download \(TEMPLATE_STORAGE\)LATEST_TEMPLATE &>/dev/null || error "Failed to download the template."
fi

echo "[Info] Creating CT container $CTID..."
pct create \(CTID\)TEMPLATE_STORAGE:vztmpl/\(LATEST_TEMPLATE -storage\)TARGET_STORAGE -password "$ROOT_PASSWORD" -arch amd64 -hostname syncpk -cores 1 -memory 512 -net0 name=eth0,bridge=vmbr0,ip=dhcp -unprivileged 1 -features nesting=1 -timezone host
pct start $CTID

echo "[Info] Waiting for the container to boot and get an IP..."
sleep 10
CT_IP=\((pct exec\)CTID -- ip -4 addr show eth0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}')
echo "[Info] Assigned IP: $CT_IP"

echo "[Info] Launching automated system installer inside LXC..."
pct exec \(CTID -- bash -c "apt-get update >/dev/null 2>&1 && apt-get install -y curl ca-certificates >/dev/null 2>&1 && curl -s https://raw.githubusercontent.com/\)GITHUB_USER/\(GITHUB_REPO/\)GITHUB_BRANCH/install.sh | bash"
