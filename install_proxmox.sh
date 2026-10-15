#!/usr/bin/env bash

GITHUB_USER="lechtung"
GITHUB_REPO="SyncPK"
CTID=""
CT_CREATED=0

function cleanup_on_error() {
    if [ "$CT_CREATED" -eq 1 ] && [ -n "$CTID" ] && pvesm status &>/dev/null; then
        echo -e "\n\e[31m[ERROR] Installation failed.\e[0m"
        if whiptail --title "SyncPK - Installation Failed" --yesno "An error occurred during the installation.\n\nDo you want to destroy the incomplete LXC container ($CTID)?" 10 60; then
            echo "[Info] Destroying container $CTID..."
            pct stop $CTID 2>/dev/null || true
            pct destroy $CTID 2>/dev/null || true
            echo "[Info] Container destroyed."
        else
            echo "[Info] Container $CTID kept as-is."
        fi
    fi
    exit 1
}

function error() {
    echo -e "\e[31m[ERROR] $1\e[0m"
    cleanup_on_error
}

trap cleanup_on_error INT TERM

if [ "$EUID" -ne 0 ]; then
    echo -e "\e[31m[ERROR] This script must be run as root (administrator privileges).\e[0m"
    exit 1
fi

if ! command -v pvesm &> /dev/null; then
    echo -e "\e[31m[ERROR] This script must be run on the Proxmox HOST, not inside a container.\e[0m"
    exit 1
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
        BRIDGE=$(whiptail --inputbox "Enter the network bridge to use for the LXC container (usually vmbr0):" 10 60 "vmbr0" --title "SyncPK - Network" 3>&1 1>&2 2>&3)
        if [ $? -ne 0 ]; then exit 1; fi
        if [ -z "$BRIDGE" ]; then BRIDGE="vmbr0"; fi
        break
    else
        whiptail --msgbox "Passwords do not match. Please try again." 8 45 --title "Error"
    fi
done

# --- Network configuration: DHCP or Static IP ---
HOST_GW=$(ip route | awk '/default/ {print $3; exit}')
SUBNET_PREFIX=""
if [ -n "$HOST_GW" ]; then
    SUBNET_PREFIX=$(echo "$HOST_GW" | sed 's/\.[0-9]*$//')
fi

NET_CONFIG="ip=dhcp"
CT_IP=""

if [ -n "$HOST_GW" ] && [ -n "$SUBNET_PREFIX" ]; then
    if whiptail --title "SyncPK - Network Configuration" \
        --yes-button "DHCP (Default)" \
        --no-button "Static IP" \
        --yesno "Select network configuration method for the LXC container:\n\nDetected Gateway: ${HOST_GW}\nDetected Subnet:  ${SUBNET_PREFIX}.0/24\n\nUse DHCP (recommended)?\nOr select 'Static IP' to specify a fixed address in this subnet." \
        13 68; then
        NET_CONFIG="ip=dhcp"
    else
        while true; do
            LAST_OCTET=$(whiptail --title "SyncPK - Static IP" \
                --inputbox "Subnet detected: ${SUBNET_PREFIX}.x\nGateway: ${HOST_GW}\n\nEnter the last octet for the container's static IP (1-254):\nResulting IP will be: ${SUBNET_PREFIX}.[YOUR_INPUT]" \
                12 65 "" 3>&1 1>&2 2>&3)
            if [ $? -ne 0 ]; then exit 1; fi

            # Validate: must be a number between 1 and 254
            if [[ "$LAST_OCTET" =~ ^[0-9]+$ ]] && [ "$((10#$LAST_OCTET))" -ge 1 ] && [ "$((10#$LAST_OCTET))" -le 254 ]; then
                LAST_OCTET=$((10#$LAST_OCTET))
                CT_IP="${SUBNET_PREFIX}.${LAST_OCTET}"
                NET_CONFIG="ip=${CT_IP}/24,gw=${HOST_GW}"
                whiptail --title "SyncPK - Static IP" --msgbox \
                    "Static IP configured: ${CT_IP}\nGateway: ${HOST_GW}\n\nMake sure this IP is excluded from your router's DHCP pool or configured with a static reservation to prevent conflicts." \
                    11 65
                break
            else
                whiptail --title "Error" --msgbox "Invalid value: '$LAST_OCTET'. Please enter a number between 1 and 254." 8 55
            fi
        done
    fi
else
    # Fallback to DHCP if default gateway could not be determined
    NET_CONFIG="ip=dhcp"
fi

CTID=$(pvesh get /cluster/nextid)
TEMPLATE_STORAGE=$(pvesm status -content vztmpl | awk 'NR>1 {print $1}' | head -n 1)

if [ -z "$TEMPLATE_STORAGE" ]; then
    error "No storage found that supports LXC templates (vztmpl)."
fi

echo "[Info] Gathering local and available templates..."
pveam update &>/dev/null
DOWNLOADED_TEMPLATES=$(pvesm list $TEMPLATE_STORAGE --content vztmpl | awk 'NR>1 {print $1}')
AVAILABLE_TEMPLATES=$(pveam available | awk '$1=="system" && $2 ~ /^(debian-12-standard|debian-13-standard|ubuntu-24\.04-standard)/ {print $2}')

TEMPLATE_MENU=()
declare -A TPL_MAP
idx=1
DEFAULT_ITEM=""

for t in $DOWNLOADED_TEMPLATES; do
    filename=$(basename "$t")
    # Only offer apt-compatible templates (Debian/Ubuntu); skip Alpine, Rocky, etc.
    if ! echo "$filename" | grep -qE '^(debian|ubuntu)-'; then continue; fi
    shortname=$(echo "$filename" | sed -E 's/(-standard|_amd64.*\.tar\.[a-z]+)//g')
    TEMPLATE_MENU+=("$idx" "[Local] $shortname")
    TPL_MAP[$idx]="$filename"
    if [[ "$filename" == *"debian-13-standard"* ]]; then
        DEFAULT_ITEM="$idx"
    fi
    ((idx++))
done

for t in $AVAILABLE_TEMPLATES; do
    if ! echo "$DOWNLOADED_TEMPLATES" | grep -q "$t"; then
        shortname=$(echo "$t" | sed -E 's/(-standard|_amd64.*\.tar\.[a-z]+)//g')
        TEMPLATE_MENU+=("$idx" "[Download] $shortname")
        TPL_MAP[$idx]="$t"
        if [ -z "$DEFAULT_ITEM" ] && [[ "$t" == *"debian-13-standard"* ]]; then
            DEFAULT_ITEM="$idx"
        fi
        ((idx++))
    fi
done

if [ -z "$DEFAULT_ITEM" ] && [ ${#TEMPLATE_MENU[@]} -gt 0 ]; then
    DEFAULT_ITEM="1"
fi

if [ ${#TEMPLATE_MENU[@]} -eq 0 ]; then
    error "No templates found locally or remotely."
fi

CHOICE=$(whiptail --title "SyncPK - Template Selection" --default-item "$DEFAULT_ITEM" --menu "Select the base image for the LXC container (Debian 13 recommended):" 18 60 8 "${TEMPLATE_MENU[@]}" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

LATEST_TEMPLATE_FILE="${TPL_MAP[$CHOICE]}"

if ! pvesm list $TEMPLATE_STORAGE --content vztmpl | grep -q "$LATEST_TEMPLATE_FILE"; then
    echo "[Info] Downloading selected template ($LATEST_TEMPLATE_FILE)..."
    pveam download $TEMPLATE_STORAGE $LATEST_TEMPLATE_FILE &>/dev/null || error "Failed to download the template."
fi

LATEST_TEMPLATE="$TEMPLATE_STORAGE:vztmpl/$LATEST_TEMPLATE_FILE"

# Medium Priority: Install with 1024MB to avoid OOM killer during pip compilation
echo "[Info] Creating CT container $CTID with 1024MB RAM for installation..."
pct create $CTID $LATEST_TEMPLATE -storage $TARGET_STORAGE -rootfs $TARGET_STORAGE:8 -password "$ROOT_PASSWORD" -arch amd64 -hostname syncpk -cores 1 -memory 1024 -net0 name=eth0,bridge=$BRIDGE,$NET_CONFIG -unprivileged 1 -features nesting=1 -timezone host -onboot 1 || error "Failed to create LXC container (pct create failed)."
CT_CREATED=1

pct start $CTID || error "Failed to start container $CTID."

echo "[Info] Waiting for the container to get network access..."
NET_OK=0
for i in $(seq 1 30); do
    if pct exec $CTID -- getent hosts github.com >/dev/null 2>&1; then
        NET_OK=1
        break
    fi
    sleep 2
done

if [ "$NET_OK" -eq 0 ]; then
    error "No network access in the LXC container (Check bridge and DHCP/static config). Debug with: pct enter $CTID"
fi

# Resolve CT IP: use known static IP or detect via DHCP
if [ -n "$CT_IP" ]; then
    echo "[Info] Static IP: $CT_IP"
else
    CT_IP=$(pct exec $CTID -- ip -4 addr show eth0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n 1)
    echo "[Info] DHCP-assigned IP: $CT_IP"
fi

echo "[Info] Launching automated system installer inside LXC..."
# Use GitHub Releases API endpoint for install.sh to fix main vs release desync

echo "[Info] Configuring Welcome Message (MOTD)..."
pct exec $CTID -- bash -c "cat << 'EOF' > /etc/profile.d/syncpk-motd.sh
case \$- in *i*) ;; *) return ;; esac
LOCAL_IP=\$(hostname -I 2>/dev/null | awk '{print \$1}')
if [ -z \"\$LOCAL_IP\" ]; then LOCAL_IP=\"127.0.0.1\"; fi
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

pct exec $CTID -- bash -c "set -euo pipefail; apt-get update && apt-get install -y curl ca-certificates && curl -fsSL https://github.com/$GITHUB_USER/$GITHUB_REPO/releases/latest/download/install.sh > install.sh && bash install.sh && rm -f install.sh" || error "install.sh failed inside LXC."

pct set $CTID -memory 512 &>/dev/null || true
