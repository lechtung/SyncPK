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

    # Acquire exclusive install/update lock in /run
    exec 9> /run/syncpk_update.lock
    if ! flock -n 9; then
        echo -e "\e[31m[ERROR] Another SyncPK install or update process is currently running.\e[0m"
        exit 1
    fi

    CODE_DIR="/opt/syncpk"
    DATA_DIR="/var/lib/syncpk"
    API_URL="https://api.github.com/repos/lechtung/SyncPK/releases/latest"

    # Extract effective listening port from systemd unit and drop-in overrides
    get_port() {
        local p
        p=$(systemctl cat syncpk-server 2>/dev/null | grep -oP -- '--port[=\s]+\K[0-9]+' | tail -n1)
        echo "${p:-8000}"
    }

    # Extract effective listening host for healthcheck (fallback to 127.0.0.1 for 0.0.0.0 or default)
    get_host() {
        local h
        h=$(systemctl cat syncpk-server 2>/dev/null | grep -oP -- '--host[=\s]+\K[^\s]+' | tail -n1)
        h=$(echo "$h" | tr -d '"'\'' ')
        if [ -z "$h" ] || [ "$h" = "0.0.0.0" ] || [ "$h" = "::" ]; then
            echo "127.0.0.1"
        elif [[ "$h" == *:* ]]; then
            echo "[$h]"
        else
            echo "$h"
        fi
    }

    check_port_in_use() {
        local port="$1"
        local in_use=0
        if command -v ss >/dev/null 2>&1; then
            if ss -tuln 2>/dev/null | grep -qE "[: ]${port}\b"; then
                in_use=1
            fi
        elif command -v lsof >/dev/null 2>&1; then
            if lsof -iTCP:"${port}" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
                in_use=1
            fi
        fi
        echo "$in_use"
    }

    find_best_python() {
        # 1. If standard 'python3' binary exists and satisfies >= 3.10, prefer it directly
        if command -v python3 >/dev/null 2>&1; then
            if python3 -c 'import sys; exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
                echo "python3"
                return 0
            fi
        fi

        # 2. Otherwise (e.g. python3 is < 3.10 or not yet aliased), dynamically discover
        # any python3.<minor> in PATH that satisfies >= 3.10 without hardcoding version numbers
        local best_cmd=""
        local best_minor=9  # We require at least Python 3.10

        local IFS=':'
        local dir candidate name minor
        for dir in $PATH; do
            [ -d "$dir" ] || continue
            for candidate in "$dir"/python3.[0-9]*; do
                [ -x "$candidate" ] || continue
                name="${candidate##*/}"
                # Strictly match 'python3.<digits>' to ignore tools like python3.11-config
                if [[ "$name" =~ ^python3\.[0-9]+$ ]]; then
                    minor=$("$candidate" -c 'import sys; print(sys.version_info.minor if sys.version_info.major == 3 else -1)' 2>/dev/null || echo -1)
                    if [[ "$minor" =~ ^[0-9]+$ ]] && [ "$minor" -gt "$best_minor" ]; then
                        best_minor="$minor"
                        best_cmd="$name"
                    fi
                fi
            done
        done

        if [ -n "$best_cmd" ]; then
            echo "$best_cmd"
            return 0
        fi

        return 1
    }

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

    PYTHON_CMD=$(find_best_python || true)

    INSTALL_SUCCESS=0
    ROLLED_BACK=0
    ABORTED_BY_USER=0
    TMP_DIR=$(mktemp -d)

    cleanup() { 
        set +e  # Ensure rollback commands continue even if an intermediate step fails
        if [ "$ROLLED_BACK" -eq 1 ]; then
            return
        fi
        ROLLED_BACK=1

        rm -rf "$TMP_DIR"
        if [ "$INSTALL_SUCCESS" -eq 0 ]; then
            if [ "$ABORTED_BY_USER" -eq 1 ]; then
                echo -e "\e[33m[Info] Installation aborted by user. Reverting changes...\e[0m"
            else
                echo -e "\e[31m[ERROR] Installation failed. Rolling back partial changes...\e[0m"
            fi
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
        NOLOGIN_BIN=$(command -v nologin 2>/dev/null || which nologin 2>/dev/null || echo "/usr/sbin/nologin")
        useradd -r -s "$NOLOGIN_BIN" -d "$DATA_DIR" syncpk
    fi

    echo "[Info] Installing OS dependencies..."
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get -o DPkg::Lock::Timeout=120 update > /var/log/syncpk-install.log 2>&1 || {
            tail -n 20 /var/log/syncpk-install.log
            echo -e "\e[31m[ERROR] apt-get update failed. Check /var/log/syncpk-install.log\e[0m"
            exit 1
        }
        PKGS="python3 python3-venv ca-certificates"
        if ! command -v curl >/dev/null 2>&1; then
            PKGS="curl $PKGS"
        fi
        apt-get -o DPkg::Lock::Timeout=120 install -y $PKGS >> /var/log/syncpk-install.log 2>&1 || {
            tail -n 20 /var/log/syncpk-install.log
            echo -e "\e[31m[ERROR] Package installation failed. Check /var/log/syncpk-install.log\e[0m"
            exit 1
        }
    elif command -v dnf >/dev/null 2>&1; then
        # On RHEL 9 / AlmaLinux 9 / Rocky 9, default python3 is 3.9. Install python3.11
        PY_PKG="python3"
        if [ -f /etc/os-release ]; then
            OS_ID=$(grep -E '^ID=' /etc/os-release | cut -d= -f2 | tr -d '"'\'' ')
            if [[ "$OS_ID" =~ ^(almalinux|rocky|rhel|centos)$ ]]; then
                PY_PKG="python3.11"
            fi
        fi
        CURL_PKG=""
        if ! command -v curl >/dev/null 2>&1; then
            CURL_PKG="curl"
        fi
        TAR_PKG=""
        if ! command -v tar >/dev/null 2>&1; then
            TAR_PKG="tar"
        fi
        dnf install -y $CURL_PKG $TAR_PKG "$PY_PKG" ca-certificates > /var/log/syncpk-install.log 2>&1 || {
            tail -n 20 /var/log/syncpk-install.log
            echo -e "\e[31m[ERROR] dnf install failed. Check /var/log/syncpk-install.log\e[0m"
            exit 1
        }
    elif command -v pacman >/dev/null 2>&1; then
        if [ ! -d /etc/pacman.d/gnupg ]; then
            pacman-key --init > /var/log/syncpk-install.log 2>&1 || true
            pacman-key --populate archlinux >> /var/log/syncpk-install.log 2>&1 || true
        fi
        if [ -f /etc/pacman.conf ]; then
            sed -i 's/^#DisableSandbox/DisableSandbox/' /etc/pacman.conf 2>/dev/null || true
            grep -q "^DisableSandbox" /etc/pacman.conf || sed -i '/\[options\]/a DisableSandbox' /etc/pacman.conf 2>/dev/null || true
        fi
        pacman -Syu --needed --noconfirm curl python ca-certificates tar > /var/log/syncpk-install.log 2>&1 || {
            tail -n 20 /var/log/syncpk-install.log
            echo -e "\e[31m[ERROR] pacman install failed. If repositories are out of date, run 'pacman -Syu' and retry.\e[0m"
            exit 1
        }
    else
        echo -e "\e[31m[ERROR] Unsupported package manager. Please install curl, python3, and venv manually or use Docker.\e[0m"
        exit 1
    fi

    # Resolve best python binary (>= 3.10) dynamically without hardcoding versions
    PYTHON_CMD=$(find_best_python || true)
    if [ -z "$PYTHON_CMD" ]; then
        DETECTED_VER=$( (command -v python3 >/dev/null 2>&1 && python3 --version 2>&1) || echo "none" )
        echo -e "\e[31m[ERROR] Python version must be >= 3.10. Detected version: ${DETECTED_VER}.\e[0m"
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
    TAR_URL=$("$PYTHON_CMD" -c 'import json,sys; print(json.load(sys.stdin).get("tarball_url", ""))' < "$TMP_DIR/release.json")
    RELEASE_TAG=$("$PYTHON_CMD" -c 'import json,sys; print(json.load(sys.stdin).get("tag_name", "unknown"))' < "$TMP_DIR/release.json")

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
    if [ -f "$TMP_DIR/src/install.sh" ]; then
        cp -a "$TMP_DIR/src/install.sh" "$CODE_DIR/install.sh"
    fi
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
    "$PYTHON_CMD" -m venv "$CODE_DIR/venv"
    "$CODE_DIR/venv/bin/pip" install -q -r "$CODE_DIR/requirements.txt" || {
        echo -e "\e[31m[ERROR] Failed to install Python dependencies.\e[0m"
        exit 1
    }

    echo "[Info] Applying security permissions..."
    chown -R root:root "$CODE_DIR"
    find "$CODE_DIR" -type d ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 755 {} +
    find "$CODE_DIR" -type f ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 644 {} +
    chmod +x "$CODE_DIR/update.sh"
    if [ -f "$CODE_DIR/install.sh" ]; then
        chmod +x "$CODE_DIR/install.sh"
    fi
    if [ -f "$CODE_DIR/check_update.sh" ]; then
        chmod +x "$CODE_DIR/check_update.sh"
    fi
    if [ -f "$CODE_DIR/uninstall.sh" ]; then
        chmod +x "$CODE_DIR/uninstall.sh"
    fi

    if [ -f "$CODE_DIR/.env.example" ]; then
        cp -f "$CODE_DIR/.env.example" "$DATA_DIR/.env.example"
        chmod 644 "$DATA_DIR/.env.example"
    fi

    chown -R syncpk:syncpk "$DATA_DIR"
    find "$DATA_DIR" -type d -exec chmod 750 {} +
    find "$DATA_DIR" -type f -exec chmod 640 {} +

    # SELinux context restoration if present
    if command -v restorecon >/dev/null 2>&1; then
        restorecon -R "$CODE_DIR" "$DATA_DIR" 2>/dev/null || true
    fi

    echo "[Info] Installing systemd services..."
    if [ -d "$TMP_DIR/src/server/systemd" ]; then
        install -m 644 -o root -g root "$TMP_DIR/src/server/systemd/"* /etc/systemd/system/
        if command -v restorecon >/dev/null 2>&1; then
            restorecon -R /etc/systemd/system/syncpk-* 2>/dev/null || true
        fi
    else
        echo -e "\e[31m[ERROR] No systemd files found in the release.\e[0m"
        exit 1
    fi

    # Check for port occupancy before starting service
    PORT=$(get_port)
    if [ "$(check_port_in_use "$PORT")" -eq 1 ]; then
        echo -e "\e[33m[Warning] Port ${PORT} appears to be already in use by another service on this machine.\e[0m"
        if [ -t 0 ]; then
            read -p "Do you want to proceed anyway? (y/N) " -r REPLY < /dev/tty
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                ABORTED_BY_USER=1
                exit 1
            fi
        else
            echo -e "\e[33m[Warning] Continuing installation. If port conflict persists, service may fail to start.\e[0m"
        fi
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
    HEALTH_HOST=$(get_host)
    HEALTH_OK=0
    for i in $(seq 1 45); do
        if curl -fsSL --max-time 5 "http://${HEALTH_HOST}:${PORT}/api/time" >/dev/null 2>&1; then
            HEALTH_OK=1
            break
        fi
        sleep 2
    done

    if [ "$HEALTH_OK" -eq 0 ]; then
        if systemctl is-active -q syncpk-server; then
            echo -e "\e[33m[Warning] Service is active but took longer than expected to respond on port ${PORT}.\e[0m"
            echo -e "Installation completed, but please check the logs: journalctl -u syncpk-server -n 30"
            HEALTH_OK=1
        else
            echo -e "\e[31m[ERROR] Service failed to start. Check the logs:\e[0m"
            journalctl -u syncpk-server -n 30 --no-pager
            exit 1
        fi
    fi

    INSTALL_SUCCESS=1

    # Setup Welcome Message (MOTD) for interactive logins
    if [ -d "/etc/profile.d" ]; then
        {
            cat << 'EOF' | sed 's/^[ ]\{12\}//' > /etc/profile.d/syncpk-motd.sh
            case $- in *i*) ;; *) return ;; esac
            _syncpk_motd() {
                local LOCAL_IP PORT
                LOCAL_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')
                if [ -z "$LOCAL_IP" ]; then LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}'); fi
                if [ -z "$LOCAL_IP" ]; then LOCAL_IP="127.0.0.1"; fi
                PORT=$(systemctl cat syncpk-server 2>/dev/null | grep -oP -- '--port[=\s]+\K[0-9]+' | tail -n1)
                PORT=${PORT:-8000}
                echo ""
                echo "================================================================"
                echo -e " \e[32m\e[1mSyncPK - Two-Way Kodi & Plex Sync Server\e[0m"
                echo "================================================================"
                echo -e " \e[1mWeb Dashboard:\e[0m http://${LOCAL_IP}:${PORT}"
                echo "================================================================"
                echo ""
            }
            _syncpk_motd
            unset -f _syncpk_motd
EOF
            chmod +x /etc/profile.d/syncpk-motd.sh
        } 2>/dev/null || true
    fi

    echo "================================================================"
    echo -e " \e[32m\e[1mSyncPK - Installation Completed Successfully!\e[0m"
    echo "================================================================"
    echo -e "Open your browser to launch the Web Setup Wizard:"
    echo -e "➡️  \e[1mhttp://${LOCAL_IP}:${PORT}\e[0m"

    # Firewall friendly notices (capture in variable to prevent SIGPIPE under pipefail)
    UFW_STATUS=$(ufw status 2>/dev/null || true)
    FW_STATE=$(firewall-cmd --state 2>/dev/null || true)
    if command -v ufw >/dev/null 2>&1 && echo "$UFW_STATUS" | grep -qw "active"; then
        echo -e "\e[33m[Note] UFW firewall is active. If needed, allow port ${PORT}: sudo ufw allow ${PORT}/tcp\e[0m"
    elif command -v firewall-cmd >/dev/null 2>&1 && echo "$FW_STATE" | grep -qw "running"; then
        echo -e "\e[33m[Note] firewalld is active. Allow port ${PORT}: sudo firewall-cmd --add-port=${PORT}/tcp --permanent && sudo firewall-cmd --reload\e[0m"
    fi
    echo "================================================================"
}

main "$@"