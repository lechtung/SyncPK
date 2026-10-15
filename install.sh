#!/usr/bin/env bash
set -euo pipefail

main() {
    FORCE=0
    for arg in "$@"; do
        if [ "$arg" == "--force" ]; then
            FORCE=1
        fi
    done

    if [ "$EUID" -ne 0 ]; then
        echo -e "\e[31m[ERROR] This script must be run as root (use sudo).\e[0m"
        exit 1
    fi

    ARCH=$(uname -m)
    if [ "$ARCH" != "x86_64" ] && [ "$ARCH" != "aarch64" ]; then
        echo -e "\e[31m[ERROR] Unsupported architecture: $ARCH. Only x86_64 and aarch64 are supported.\e[0m"
        exit 1
    fi

    echo "======================================================"
    echo "          SyncPK Installer (System Setup)             "
    echo "======================================================"

    CODE_DIR="/opt/syncpk"
    DATA_DIR="/var/lib/syncpk"
    API_URL="https://api.github.com/repos/lechtung/SyncPK/releases/latest"

    if [ -d "$CODE_DIR" ] && [ "$FORCE" -eq 0 ]; then
        echo -e "\e[33m[Warning] SyncPK seems to be already installed on this system.\e[0m"
        echo "If you want to update, run: sudo $CODE_DIR/update.sh"
        echo "If you want to reinstall, run this script with --force flag."
        exit 1
    fi

    echo "[Info] Validating environment..."
    if [ ! -d "/run/systemd/system" ]; then
        echo -e "\e[31m[ERROR] systemd must be running as PID 1 on this system.\e[0m"
        echo "Baremetal installation requires systemd. For containers or WSL without systemd, please use Docker."
        exit 1
    fi

    if ! command -v python3 >/dev/null 2>&1; then
        echo -e "\e[33m[Warning] python3 not found, it will be installed via system package manager.\e[0m"
    else
        if ! python3 -c 'import sys; exit(0) if sys.version_info >= (3,9) else exit(1)'; then
            echo -e "\e[31m[ERROR] Python version must be >= 3.9. Current version is $(python3 --version).\e[0m"
            exit 1
        fi
    fi

    INSTALL_SUCCESS=0
    ROLLED_BACK=0
    TMP_DIR=$(mktemp -d)

    cleanup() { 
        if [ "$ROLLED_BACK" -eq 1 ]; then
            return
        fi
        ROLLED_BACK=1

        rm -rf "$TMP_DIR"
        if [ "$INSTALL_SUCCESS" -eq 0 ]; then
            echo -e "\e[31m[ERROR] Installation failed. Rolling back partial changes...\e[0m"
            systemctl disable --now syncpk-server syncpk-checker.timer syncpk-updater.path 2>/dev/null || true
            rm -f /etc/systemd/system/syncpk-*.service /etc/systemd/system/syncpk-*.timer /etc/systemd/system/syncpk-*.path 2>/dev/null || true
            systemctl daemon-reload 2>/dev/null || true
            rm -rf "$CODE_DIR"
            if [ -d "$CODE_DIR.old" ]; then
                echo "[Info] Restoring previous installation..."
                mv "$CODE_DIR.old" "$CODE_DIR"
                if [ -d "$CODE_DIR/systemd" ]; then
                    install -m 644 -o root -g root "$CODE_DIR/systemd/"* /etc/systemd/system/ 2>/dev/null || true
                fi
                systemctl daemon-reload 2>/dev/null || true
                systemctl reset-failed syncpk-server 2>/dev/null || true
                systemctl enable --now syncpk-server syncpk-checker.timer syncpk-updater.path 2>/dev/null || true
            fi
        else
            rm -rf "$CODE_DIR.old"
        fi
    }
    trap cleanup EXIT

    if [ "$FORCE" -eq 1 ] && [ -d "$CODE_DIR" ]; then
        echo "[Info] Force mode: stopping existing services..."
        systemctl stop syncpk-server syncpk-checker.timer syncpk-updater.path 2>/dev/null || true
        rm -rf "$CODE_DIR.old"
        mv "$CODE_DIR" "$CODE_DIR.old"
    fi

    echo "[Info] Creating dedicated system user 'syncpk'..."
    if ! id "syncpk" &>/dev/null; then
        useradd -r -s /usr/sbin/nologin -d "$DATA_DIR" syncpk
    fi

    echo "[Info] Installing OS dependencies..."
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get -o DPkg::Lock::Timeout=120 update > /var/log/syncpk-install.log 2>&1 || {
            tail -n 20 /var/log/syncpk-install.log
            echo -e "\e[31m[ERROR] apt-get update failed. Check /var/log/syncpk-install.log\e[0m"
            exit 1
        }
        apt-get -o DPkg::Lock::Timeout=120 install -y curl python3 python3-venv ca-certificates >> /var/log/syncpk-install.log 2>&1 || {
            tail -n 20 /var/log/syncpk-install.log
            echo -e "\e[31m[ERROR] Package installation failed. Check /var/log/syncpk-install.log\e[0m"
            exit 1
        }
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y curl python3 ca-certificates > /var/log/syncpk-install.log 2>&1 || {
            tail -n 20 /var/log/syncpk-install.log
            echo -e "\e[31m[ERROR] dnf install failed. Check /var/log/syncpk-install.log\e[0m"
            exit 1
        }
    elif command -v pacman >/dev/null 2>&1; then
        pacman -S --needed --noconfirm curl python ca-certificates > /var/log/syncpk-install.log 2>&1 || {
            tail -n 20 /var/log/syncpk-install.log
            echo -e "\e[31m[ERROR] pacman install failed. Check /var/log/syncpk-install.log\e[0m"
            exit 1
        }
    else
        echo -e "\e[31m[ERROR] Unsupported package manager. Please install curl, python3, and venv manually or use Docker.\e[0m"
        exit 1
    fi

    # Second check after package install
    if ! python3 -c 'import sys; exit(0) if sys.version_info >= (3,9) else exit(1)'; then
        echo -e "\e[31m[ERROR] Python version must be >= 3.9. Current version is $(python3 --version).\e[0m"
        exit 1
    fi

    echo "[Info] Preparing directories..."
    mkdir -p "$CODE_DIR"

    if [ -d "$DATA_DIR" ]; then
        echo -e "\e[33m[Info] Existing data directory found at $DATA_DIR. Retaining data...\e[0m"
    else
        mkdir -p "$DATA_DIR"
    fi

    echo "[Info] Fetching latest version from GitHub..."
    curl -fsSL --max-time 30 "$API_URL" > "$TMP_DIR/release.json" || { echo -e "\e[31m[ERROR] Could not fetch release info\e[0m"; exit 1; }
    TAR_URL=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tarball_url", ""))' < "$TMP_DIR/release.json")
    RELEASE_TAG=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name", "unknown"))' < "$TMP_DIR/release.json")

    if [ -z "$TAR_URL" ]; then
        echo -e "\e[31m[ERROR] Download URL is empty.\e[0m"
        exit 1
    fi

    echo "[Info] Downloading and extracting source code..."
    mkdir -p "$TMP_DIR/src"
    curl -fsSL --max-time 300 "$TAR_URL" | tar -xz -C "$TMP_DIR/src" --strip-components=1 || { echo -e "\e[31m[ERROR] Failed to download/extract source code\e[0m"; exit 1; }

    cp -a "$TMP_DIR/src/server/." "$CODE_DIR/"

    VERSION_NUMBER="${RELEASE_TAG#[vV]}"
    echo "$VERSION_NUMBER" > "$CODE_DIR/.ver"
    cp -a "$TMP_DIR/src/update.sh" "$CODE_DIR/update.sh"
    if [ -f "$TMP_DIR/src/check_update.sh" ]; then
        cp -a "$TMP_DIR/src/check_update.sh" "$CODE_DIR/check_update.sh"
    fi
    if [ -f "$TMP_DIR/src/uninstall.sh" ]; then
        cp -a "$TMP_DIR/src/uninstall.sh" "$CODE_DIR/uninstall.sh"
    fi

    if [ ! -f "$CODE_DIR/requirements.txt" ]; then
        echo -e "\e[31m[ERROR] requirements.txt not found in the release.\e[0m"
        exit 1
    fi

    echo "[Info] Configuring Python virtual environment..."
    python3 -m venv "$CODE_DIR/venv"
    "$CODE_DIR/venv/bin/pip" install -q -r "$CODE_DIR/requirements.txt" || {
        echo -e "\e[31m[ERROR] Failed to install Python dependencies.\e[0m"
        exit 1
    }

    echo "[Info] Applying security permissions..."
    chown -R root:root "$CODE_DIR"
    find "$CODE_DIR" -type d ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 755 {} +
    find "$CODE_DIR" -type f ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 644 {} +
    chmod +x "$CODE_DIR/update.sh"
    if [ -f "$CODE_DIR/check_update.sh" ]; then
        chmod +x "$CODE_DIR/check_update.sh"
    fi
    if [ -f "$CODE_DIR/uninstall.sh" ]; then
        chmod +x "$CODE_DIR/uninstall.sh"
    fi

    if [ -f "$CODE_DIR/.env.example" ] && [ ! -f "$DATA_DIR/.env.bak" ]; then
        cp "$CODE_DIR/.env.example" "$DATA_DIR/.env.bak"
    fi

    chown -R syncpk:syncpk "$DATA_DIR"
    find "$DATA_DIR" -type d -exec chmod 750 {} +
    find "$DATA_DIR" -type f -exec chmod 640 {} +
    if [ -f "$DATA_DIR/.env.bak" ]; then
        chmod 600 "$DATA_DIR/.env.bak"
    fi

    # SELinux context restoration if present
    if command -v restorecon >/dev/null 2>&1; then
        restorecon -R "$CODE_DIR" "$DATA_DIR" 2>/dev/null || true
    fi

    echo "[Info] Installing systemd services..."
    if [ -d "$TMP_DIR/src/server/systemd" ]; then
        install -m 644 -o root -g root "$TMP_DIR/src/server/systemd/"* /etc/systemd/system/
    else
        echo -e "\e[31m[ERROR] No systemd files found in the release.\e[0m"
        exit 1
    fi

    echo "[Info] Starting and enabling all systemd services..."
    systemctl daemon-reload
    systemctl reset-failed syncpk-server 2>/dev/null || true
    systemctl enable --now syncpk-server.service
    systemctl enable --now syncpk-checker.timer
    systemctl enable --now syncpk-updater.path

    # Primary routable LAN IP detection
    LOCAL_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')
    if [ -z "$LOCAL_IP" ]; then
        LOCAL_IP=$( (hostname -I 2>/dev/null || true) | awk '{print $1}')
    fi
    if [ -z "$LOCAL_IP" ]; then
        LOCAL_IP="127.0.0.1"
    fi

    echo "[Info] Checking service health (up to 90s)..."
    HEALTH_OK=0
    for i in $(seq 1 45); do
        if curl -fsSL --max-time 5 "http://127.0.0.1:8000/api/time" >/dev/null 2>&1; then
            HEALTH_OK=1
            break
        fi
        sleep 2
    done

    if [ "$HEALTH_OK" -eq 0 ]; then
        if systemctl is-active -q syncpk-server; then
            echo -e "\e[33m[Warning] Service is active but took longer than expected to respond to HTTP healthcheck.\e[0m"
            echo -e "Installation completed, but please check the logs: journalctl -u syncpk-server -n 30"
            HEALTH_OK=1
        else
            echo -e "\e[31m[ERROR] Service failed to start. Check the logs:\e[0m"
            journalctl -u syncpk-server -n 30 --no-pager
            exit 1
        fi
    fi

    INSTALL_SUCCESS=1

    echo "================================================================"
    echo -e "\e[32mInstallation completed successfully!\e[0m"
    echo -e "Open your browser to launch the Web Setup Wizard:"
    echo -e "➡️  \e[1mhttp://$LOCAL_IP:8000\e[0m"

    # Firewall friendly notices
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qw "active"; then
        echo -e "\e[33m[Note] UFW firewall is active. If needed, allow port 8000: sudo ufw allow 8000/tcp\e[0m"
    elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state 2>/dev/null | grep -qw "running"; then
        echo -e "\e[33m[Note] firewalld is active. Allow port 8000: sudo firewall-cmd --add-port=8000/tcp --permanent && sudo firewall-cmd --reload\e[0m"
    fi
    echo "================================================================"
}

main "$@"