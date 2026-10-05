#!/usr/bin/env bash
{
set -euo pipefail

CODE_DIR="/opt/syncpk"
DATA_DIR="/var/lib/syncpk"
API_URL="https://api.github.com/repos/lechtung/SyncPK/releases/latest"

PYTHON_BIN="$CODE_DIR/venv/bin/python3"
PIP_BIN="$CODE_DIR/venv/bin/pip"

TMP_DIR=$(mktemp -d)

cleanup() {
    rm -rf "$TMP_DIR"
    if ! systemctl is-active -q syncpk-server; then
        systemctl start syncpk-server || true
    fi
}
trap cleanup EXIT

echo "[Info] Fetching latest version from GitHub..."
# Point 1: API Check, atomic download.
curl -fsSL --max-time 30 "$API_URL" > "$TMP_DIR/api_resp.json" || { echo "[Error] Could not fetch release info"; exit 1; }
TAR_URL=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("tarball_url", ""))' < "$TMP_DIR/api_resp.json")
RELEASE_TAG=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("tag_name", "unknown"))' < "$TMP_DIR/api_resp.json")

echo "[Info] Found release: $RELEASE_TAG"


if [ -z "$TAR_URL" ]; then
    echo "[Error] Download URL is empty."
    exit 1
fi

echo "[Info] Downloading and extracting source code..."
mkdir -p "$TMP_DIR/src"
curl -fsSL --max-time 300 "$TAR_URL" | tar -xz -C "$TMP_DIR/src" --strip-components=1 || { echo "[Error] Failed to download/extract"; exit 1; }

echo "[Info] Downloading dependencies..."
mkdir -p "$TMP_DIR/wheels"
# Download before stopping service
"$PIP_BIN" download -q -r "$TMP_DIR/src/server/requirements.txt" -d "$TMP_DIR/wheels" || { echo "[Error] Failed to download Python dependencies"; exit 1; }

# ---- Ventana de parada mínima ----
echo "[Info] Stopping service..."
systemctl stop syncpk-server

echo "[Info] Creating local backup (code and DB)..."
rm -rf "$CODE_DIR.prev"
cp -a "$CODE_DIR" "$CODE_DIR.prev"
mkdir -p "$DATA_DIR/backup"
# backup de bd de forma segura (copia simple, SQLite puede requerir sqlite3 pero la API está parada, así que es seguro)
if ls "$DATA_DIR"/sync.db* 1> /dev/null 2>&1; then
    cp -a "$DATA_DIR"/sync.db* "$DATA_DIR/backup/"
fi

echo "[Info] Cleaning old code..."
# Point 11: Borrar todo excepto venv (y variables locales si las hubiera)
find "$CODE_DIR" -mindepth 1 -maxdepth 1 ! -name 'venv' ! -name '.ver' ! -name 'update.sh' ! -name 'check_update.sh' -exec rm -rf {} +

echo "[Info] Applying new code..."
# Copiar server/
cp -a "$TMP_DIR/src/server/." "$CODE_DIR/"
# Point 2: Actualizar update.sh y check_update.sh
cp -a "$TMP_DIR/src/update.sh" "$CODE_DIR/update.sh.tmp"
mv "$CODE_DIR/update.sh.tmp" "$CODE_DIR/update.sh"
if [ -f "$TMP_DIR/src/check_update.sh" ]; then
    cp -a "$TMP_DIR/src/check_update.sh" "$CODE_DIR/check_update.sh.tmp"
    mv "$CODE_DIR/check_update.sh.tmp" "$CODE_DIR/check_update.sh"
fi

# Point 9: Copiar archivos systemd y hacer daemon-reload
if [ -d "$TMP_DIR/src/server/systemd" ]; then
    cp -a "$TMP_DIR/src/server/systemd/"* /etc/systemd/system/
    systemctl daemon-reload
fi

echo "[Info] Installing new dependencies offline..."
"$PIP_BIN" install -q --no-index --find-links "$TMP_DIR/wheels" -r "$CODE_DIR/requirements.txt" || {
    echo "[Error] Failed to install dependencies, restoring backup..."
    rm -rf "$CODE_DIR"
    mv "$CODE_DIR.prev" "$CODE_DIR"
    exit 1
}

echo "[Info] Updating local version..."
if [ -f "$TMP_DIR/src/.ver" ]; then
    cp "$TMP_DIR/src/.ver" "$CODE_DIR/.ver"
fi

# Point 6: Permisos
echo "[Info] Configuring permissions..."
chown -R root:root "$CODE_DIR"
find "$CODE_DIR" -type d ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 755 {} +
find "$CODE_DIR" -type f ! -path "*/venv/*" ! -path "*/.git/*" -exec chmod 644 {} +
chmod +x "$CODE_DIR/update.sh"
if [ -f "$CODE_DIR/check_update.sh" ]; then
    chmod +x "$CODE_DIR/check_update.sh"
fi

echo "[Info] Starting service..."
systemctl start syncpk-server

echo "[Info] Checking service health..."
# Healthcheck: retry for up to 30 seconds
HEALTH_OK=0
for i in $(seq 1 15); do
    if curl -fsSL --max-time 5 "http://127.0.0.1:8000/api/time" >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    sleep 2
done

if [ "$HEALTH_OK" -eq 0 ]; then
    echo "[Error] Service is unresponsive after update. Restoring previous version..."
    systemctl stop syncpk-server
    rm -rf "$CODE_DIR"
    mv "$CODE_DIR.prev" "$CODE_DIR"
    systemctl start syncpk-server
    echo "[Error] Rollback completed."
    exit 1
fi

echo "[Info] Update completed successfully."
}