#!/usr/bin/env bash

main() {
GITHUB_USER="lechtung"
GITHUB_REPO="SyncPK"
CTID=""
CT_CREATED=0

    SPINNER_PID=""

    stop_spinner() {
        if [ -n "$SPINNER_PID" ] && kill -0 "$SPINNER_PID" 2>/dev/null; then
            kill "$SPINNER_PID" 2>/dev/null
            wait "$SPINNER_PID" 2>/dev/null || true
            SPINNER_PID=""
        fi
        printf "\r\e[K\e[?25h" # clear line and restore cursor
    }

    msg_info() {
        stop_spinner
        local msg="$1"
        printf "\e[?25l" # hide cursor
        (
            local chars=( ". " ": " ":." "::" ".:" " ." )
            local i=0
            while true; do
                printf "\r \e[93m%s\e[0m \e[93m%s\e[0m" "${chars[i]}" "$msg"
                i=$(( (i + 1) % ${#chars[@]} ))
                sleep 0.15
            done
        ) &
        SPINNER_PID=$!
    }

    msg_ok() {
        stop_spinner
        printf "\r\e[K \e[1;32m✔\e[0m \e[32m%s\e[0m\n" "$1"
    }

    msg_err() {
        stop_spinner
        printf "\r\e[K \e[31m✖\e[0m \e[31m%s\e[0m\n" "$1"
    }

    cleanup_on_error() {
        trap - EXIT
        stop_spinner
        if [ "$CT_CREATED" -eq 1 ] && [ -n "$CTID" ] && pvesm status &>/dev/null; then
            echo -e "\n \e[31m✖\e[0m \e[1;31mInstallation failed.\e[0m"
            if whiptail --title "SyncPK - Installation Failed" --yesno "An error occurred during the installation.\n\nDo you want to destroy the incomplete LXC container ($CTID)?" 10 60; then
                msg_info "Destroying container $CTID..."
                pct stop $CTID 2>/dev/null || true
                pct destroy $CTID 2>/dev/null || true
                msg_ok "Container destroyed."
            else
                msg_ok "Container $CTID kept as-is."
            fi
        fi
        exit 1
    }

    error() {
        stop_spinner
        echo ""
        msg_err "$1"
        cleanup_on_error
    }

trap 'stop_spinner' EXIT
trap 'cleanup_on_error' INT TERM

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

# Detect CIDR subnet mask from the host bridge (e.g. 24, 23, 16)
DETECTED_CIDR=$(ip -o -4 addr show dev "$BRIDGE" 2>/dev/null | awk '{print $4}' | head -n1 | cut -d/ -f2)
SUBNET_CIDR="${DETECTED_CIDR:-24}"

NET_CONFIG="ip=dhcp"
CT_IP=""

if [ -n "$HOST_GW" ] && [ -n "$SUBNET_PREFIX" ]; then
    if whiptail --title "SyncPK - Network Configuration" \
        --yes-button "DHCP (Default)" \
        --no-button "Static IP" \
        --yesno "Select network configuration method for the LXC container:\n\nDetected Gateway: ${HOST_GW}\nDetected Subnet:  ${SUBNET_PREFIX}.0/${SUBNET_CIDR}\n\nUse DHCP (recommended)?\nOr select 'Static IP' to specify a fixed address in this subnet." \
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

                # Allow user to confirm or customize subnet mask CIDR
                SUBNET_CIDR_INPUT=$(whiptail --title "SyncPK - Subnet Mask" \
                    --inputbox "Subnet mask prefix in CIDR notation:\nDetected on ${BRIDGE}: /${SUBNET_CIDR}\n\nPress Enter to accept or type a different mask (e.g. 24, 23, 16):" \
                    12 65 "$SUBNET_CIDR" 3>&1 1>&2 2>&3)
                if [ $? -ne 0 ]; then exit 1; fi
                SUBNET_CIDR_INPUT=$(echo "$SUBNET_CIDR_INPUT" | tr -d '/')
                if [[ "$SUBNET_CIDR_INPUT" =~ ^[0-9]+$ ]] && [ "$SUBNET_CIDR_INPUT" -ge 8 ] && [ "$SUBNET_CIDR_INPUT" -le 30 ]; then
                    SUBNET_CIDR="$SUBNET_CIDR_INPUT"
                fi

                # Check if chosen IP is already active on the network
                if ping -c 1 -W 1 "$CT_IP" &>/dev/null; then
                    whiptail --title "SyncPK - IP Warning" --msgbox \
                        "Warning: The IP ${CT_IP} is currently responding to ping on your network.\n\nMake sure it is not assigned to another active device to avoid IP conflicts." \
                        11 65
                fi

                NET_CONFIG="ip=${CT_IP}/${SUBNET_CIDR},gw=${HOST_GW}"
                whiptail --title "SyncPK - Static IP" --msgbox \
                    "Static IP configured: ${CT_IP}/${SUBNET_CIDR}\nGateway: ${HOST_GW}\n\nMake sure this IP is excluded from your router's DHCP pool or configured with a static reservation to prevent conflicts." \
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

while true; do
    RAM_INPUT=$(whiptail --title "SyncPK - RAM Allocation" \
        --inputbox "Enter container RAM in MiB (minimum 256 MiB):" 10 60 "512" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi
    if [[ "$RAM_INPUT" =~ ^[0-9]+$ ]] && [ "$RAM_INPUT" -ge 256 ]; then
        RAM_SIZE="$RAM_INPUT"
        break
    else
        whiptail --title "Error - Invalid RAM" \
            --msgbox "Invalid RAM value: '$RAM_INPUT'. Please enter a numeric value greater than or equal to 256 MiB." 10 60
    fi
done

if [ "$RAM_SIZE" -lt 1024 ]; then
    BUILD_RAM=1024
    DO_DOWNGRADE=1
    RAM_SUMMARY=" \e[36m🛠️  RAM Size: \e[1;32m${RAM_SIZE} MiB\e[0m \e[36m(1024 MiB temporary during installation)\e[0m"
else
    BUILD_RAM="$RAM_SIZE"
    DO_DOWNGRADE=0
    RAM_SUMMARY=" \e[36m🛠️  RAM Size: \e[1;32m${RAM_SIZE} MiB\e[0m"
fi

DISK_SIZE=4
while true; do
    DISK_INPUT=$(whiptail --title "SyncPK - Disk Allocation" \
        --inputbox "Enter container disk size in GB (minimum 2 GB):" 10 60 "4" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then exit 1; fi
    if [[ "$DISK_INPUT" =~ ^[0-9]+$ ]] && [ "$DISK_INPUT" -ge 2 ]; then
        DISK_SIZE="$DISK_INPUT"
        break
    else
        whiptail --title "Error - Invalid Disk Size" \
            --msgbox "Invalid disk size: '$DISK_INPUT'. Please enter a numeric value greater than or equal to 2 GB." 10 60
    fi
done

CTID=$(pvesh get /cluster/nextid)
TEMPLATE_STORAGE=$(pvesm status -content vztmpl | awk 'NR>1 {print $1}' | head -n 1)

if [ -z "$TEMPLATE_STORAGE" ]; then
    error "No storage found that supports LXC templates (vztmpl)."
fi

pveam update &>/dev/null
DOWNLOADED_TEMPLATES=$(pvesm list $TEMPLATE_STORAGE --content vztmpl | awk 'NR>1 {print $1}')

# Offer only the single latest stable version per supported distro family from pveam
PVEAM_SYSTEM=$(pveam available 2>/dev/null | awk '$1=="system" {print $2}')
AVAILABLE_TEMPLATES=()
for tpl in \
    $(echo "$PVEAM_SYSTEM" | grep -E '^debian-(1[2-9]|[2-9][0-9])-standard' | sort -V) \
    $(echo "$PVEAM_SYSTEM" | grep -E '^ubuntu-(2[2468]\.04)-standard' | sort -V) \
    $(echo "$PVEAM_SYSTEM" | grep -E '^fedora-(3[8-9]|[4-9][0-9])-default' | sort -V | tail -n 1) \
    $(echo "$PVEAM_SYSTEM" | grep -E '^almalinux-([9-9]|[1-9][0-9])-default' | sort -V) \
    $(echo "$PVEAM_SYSTEM" | grep -E '^rockylinux-([9-9]|[1-9][0-9])-default' | sort -V) \
    $(echo "$PVEAM_SYSTEM" | grep -E '^archlinux-base' | sort -V | tail -n 1); do
    [ -n "$tpl" ] && AVAILABLE_TEMPLATES+=("$tpl")
done

TEMPLATE_MENU=()
declare -A TPL_MAP
idx=1
DEFAULT_ITEM=""

for t in $DOWNLOADED_TEMPLATES; do
    filename=$(basename "$t")
    # Only offer modern supported distros (requires Python 3.10+ and systemd)
    if ! echo "$filename" | grep -qE '^(debian-(1[2-9]|[2-9][0-9])|ubuntu-(2[2-9]|[3-9][0-9])\.|fedora-(3[8-9]|[4-9][0-9])|almalinux-([9-9]|[1-9][0-9])|rockylinux-([9-9]|[1-9][0-9])|archlinux-base)'; then continue; fi
    shortname=$(echo "$filename" | sed -E 's/(-standard|-default|_amd64.*\.tar\.[a-z]+|_arm64.*\.tar\.[a-z]+|\.tar\.[a-z]+)//g')
    TEMPLATE_MENU+=("$idx" "[Local] $shortname")
    TPL_MAP[$idx]="$filename"
    if [[ "$filename" == *"debian-13"* ]]; then
        DEFAULT_ITEM="$idx"
    fi
    ((idx++))
done

for t in "${AVAILABLE_TEMPLATES[@]}"; do
    if ! echo "$DOWNLOADED_TEMPLATES" | grep -q "$t"; then
        shortname=$(echo "$t" | sed -E 's/(-standard|-default|_amd64.*\.tar\.[a-z]+|_arm64.*\.tar\.[a-z]+|\.tar\.[a-z]+)//g')
        TEMPLATE_MENU+=("$idx" "[Download] $shortname")
        TPL_MAP[$idx]="$t"
        if [ -z "$DEFAULT_ITEM" ] && [[ "$t" == *"debian-13"* ]]; then
            DEFAULT_ITEM="$idx"
        fi
        ((idx++))
    fi
done

if [ -z "$DEFAULT_ITEM" ]; then
    for k in "${!TPL_MAP[@]}"; do
        if [[ "${TPL_MAP[$k]}" == *"debian-12"* ]]; then
            DEFAULT_ITEM="$k"
            break
        fi
    done
fi

if [ -z "$DEFAULT_ITEM" ] && [ ${#TEMPLATE_MENU[@]} -gt 0 ]; then
    DEFAULT_ITEM="1"
fi

if [ ${#TEMPLATE_MENU[@]} -eq 0 ]; then
    error "No templates found locally or remotely."
fi

CHOICE=$(whiptail --title "SyncPK - Template Selection" --default-item "$DEFAULT_ITEM" --menu "Select the base image for the LXC container (Debian 13 recommended):" 18 60 8 "${TEMPLATE_MENU[@]}" 3>&1 1>&2 2>&3)
if [ $? -ne 0 ]; then exit 1; fi

LATEST_TEMPLATE_FILE="${TPL_MAP[$CHOICE]}"
DISTRO_NAME=$(echo "$LATEST_TEMPLATE_FILE" | sed -E 's/(-standard|-default|_amd64.*\.tar\.[a-z]+|_arm64.*\.tar\.[a-z]+|\.tar\.[a-z]+)//g')

# Clear screen after whiptail dialogs and display Proxmox style banner
clear 2>/dev/null || echo -e "\n\n"

echo -e "\e[36m"
cat << "EOF"
   ______                 ____  __ __
  / ___/__  ______  _____/ __ \/ //_/
  \__ \/ / / / __ \/ ___/ /_/ / ,<   
 ___/ / /_/ / / / / /__/ ____/ /| |  
/____/\__, /_/ /_/\___/_/   /_/ |_|  
     /____/                          
EOF
echo -e "\e[0m"

NODE=$(hostname 2>/dev/null || echo "pve")
PVE_VER=$(pveversion 2>/dev/null | cut -d/ -f2 | awk '{print $1}' || echo "PVE")
KERNEL=$(uname -r 2>/dev/null || echo "unknown")

echo -e " \e[36m⚙️  Using Configuration on node \e[1;32m${NODE}\e[0m"
echo -e " \e[36m💡  PVE Version \e[1;32m${PVE_VER}\e[0m \e[36m(Kernel: \e[1;32m${KERNEL}\e[0m\e[36m)\e[0m"
echo -e " \e[36m🆔  Container ID: \e[1;32m${CTID}\e[0m"
echo -e " \e[36m📦  Operating System: \e[4;32m${DISTRO_NAME}\e[0m"
echo -e " \e[36m🖥️  Container Type: \e[4;32mUnprivileged\e[0m"
echo -e " \e[36m💾  Disk Size: \e[1;32m${DISK_SIZE} GB\e[0m"
echo -e " \e[36m🧠  CPU Cores: \e[1;32m1\e[0m"
echo -e "$RAM_SUMMARY"
echo -e " \e[36m🚀  Creating SyncPK LXC using the above settings\e[0m"
echo ""

msg_ok "Storage '${TARGET_STORAGE}' validated"

if ! pvesm list "$TEMPLATE_STORAGE" --content vztmpl | grep -q "$LATEST_TEMPLATE_FILE"; then
    msg_info "Downloading template (${LATEST_TEMPLATE_FILE})..."
    HOST_LOG=$(mktemp)
    if ! pveam download "$TEMPLATE_STORAGE" "$LATEST_TEMPLATE_FILE" > "$HOST_LOG" 2>&1; then
        tail -n 15 "$HOST_LOG"
        rm -f "$HOST_LOG"
        error "Failed to download the template."
    fi
    rm -f "$HOST_LOG"
    msg_ok "Template downloaded (${LATEST_TEMPLATE_FILE})"
else
    msg_ok "Template ready (${LATEST_TEMPLATE_FILE})"
fi

LATEST_TEMPLATE="$TEMPLATE_STORAGE:vztmpl/$LATEST_TEMPLATE_FILE"

# Detect host architecture for LXC container
PVE_ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
if [ "$PVE_ARCH" = "aarch64" ] || [ "$PVE_ARCH" = "arm64" ]; then
    CT_ARCH="arm64"
else
    CT_ARCH="amd64"
fi

# Create container with temporary memory if needed to prevent OOM killer during pip builds
msg_info "Creating LXC container ${CTID} (${CT_ARCH})..."
HOST_LOG=$(mktemp)
if ! pct create $CTID "$LATEST_TEMPLATE" -storage "$TARGET_STORAGE" -rootfs "$TARGET_STORAGE:$DISK_SIZE" -arch "$CT_ARCH" -hostname syncpk -cores 1 -memory "$BUILD_RAM" -net0 name=eth0,bridge="$BRIDGE",$NET_CONFIG -unprivileged 1 -features nesting=1 -timezone host -onboot 1 > "$HOST_LOG" 2>&1; then
    tail -n 15 "$HOST_LOG"
    rm -f "$HOST_LOG"
    error "Failed to create LXC container (pct create failed)."
fi
rm -f "$HOST_LOG"
CT_CREATED=1
msg_ok "Created LXC container ${CTID} (${CT_ARCH})"

msg_info "Starting container ${CTID}..."
HOST_LOG=$(mktemp)
if ! pct start $CTID > "$HOST_LOG" 2>&1; then
    tail -n 15 "$HOST_LOG"
    rm -f "$HOST_LOG"
    error "Failed to start container $CTID."
fi
rm -f "$HOST_LOG"
msg_ok "Container ${CTID} started"

msg_info "Configuring root password..."
HOST_LOG=$(mktemp)
if ! printf '%s\n' "root:$ROOT_PASSWORD" | pct exec $CTID -- chpasswd > "$HOST_LOG" 2>&1; then
    tail -n 15 "$HOST_LOG"
    rm -f "$HOST_LOG"
    error "Failed to set container root password."
fi
rm -f "$HOST_LOG"
msg_ok "Root password configured"

msg_info "Waiting for network connection..."
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
if [ -z "$CT_IP" ]; then
    CT_IP=$(pct exec $CTID -- ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n 1)
fi
msg_ok "Network connection established (IP: ${CT_IP:-unknown})"

msg_info "Updating container packages..."
pct exec $CTID -- bash -c "
set -euo pipefail
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update &>/dev/null && apt-get install -y curl ca-certificates &>/dev/null
elif command -v pacman >/dev/null 2>&1; then
    if [ ! -d /etc/pacman.d/gnupg ]; then
        pacman-key --init &>/dev/null
        pacman-key --populate archlinux &>/dev/null || true
    fi
    if [ -f /etc/pacman.conf ]; then
        sed -i 's/^#DisableSandbox/DisableSandbox/' /etc/pacman.conf 2>/dev/null || true
        grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/\[options\]/a DisableSandbox' /etc/pacman.conf 2>/dev/null || true
    fi
    if ! command -v curl >/dev/null 2>&1; then
        pacman -Syu --noconfirm --needed curl ca-certificates &>/dev/null
    fi
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y curl ca-certificates &>/dev/null
fi
" || error "Failed to prepare container dependencies."
msg_ok "Container packages updated"

msg_info "Installing SyncPK and dependencies..."
pct exec $CTID -- bash -c "
set -euo pipefail
export SYNCPK_PROXMOX=1
curl -fsSL https://github.com/$GITHUB_USER/$GITHUB_REPO/releases/latest/download/install.sh > install.sh
bash install.sh > /var/log/syncpk-lxc-install.log 2>&1
rm -f install.sh
" || {
    pct exec $CTID -- tail -n 25 /var/log/syncpk-lxc-install.log 2>/dev/null || true
    error "install.sh failed inside LXC. Check /var/log/syncpk-lxc-install.log inside container."
}
msg_ok "SyncPK successfully installed"

if [ "$DO_DOWNGRADE" -eq 1 ]; then
    msg_info "Optimizing container memory to ${RAM_SIZE} MiB..."
    pct set $CTID -memory "$RAM_SIZE" &>/dev/null || true
    msg_ok "Optimized container memory to ${RAM_SIZE} MiB"
fi

# Refresh CT_IP if not already known
if [ -z "$CT_IP" ] || [ "$CT_IP" = "unknown" ]; then
    CT_IP=$(pct exec $CTID -- ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n 1 || echo "")
fi

    stop_spinner
    echo ""
    echo -e " \e[1;32m✔ Completed Successfully!\e[0m"
echo ""
echo -e " \e[36m🚀  SyncPK is ready! Access the Web Dashboard at:\e[0m"
echo -e "   \e[1;32mhttp://${CT_IP:-<container-ip>}:8000\e[0m"
echo ""
}

main "$@"
