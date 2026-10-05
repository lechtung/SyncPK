#!/usr/bin/env bash
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

echo "[Info] Obteniendo la última versión de GitHub..."
# Point 1: API Check, atomic download.
curl -fsSL --max-time 30 "$API_URL" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["tarball_url"])' > "$TMP_DIR/url" || { echo "[Error] No se pudo obtener la URL de descarga"; exit 1; }

TAR_URL=$(cat "$TMP_DIR/url")
if [ -z "$TAR_URL" ]; then
    echo "[Error] URL de descarga vacía."
    exit 1
fi

echo "[Info] Descargando y extrayendo código fuente..."
mkdir -p "$TMP_DIR/src"
curl -fsSL --max-time 300 "$TAR_URL" | tar -xz -C "$TMP_DIR/src" --strip-components=1 || { echo "[Error] Fallo al descargar/extraer"; exit 1; }

echo "[Info] Descargando dependencias..."
mkdir -p "$TMP_DIR/wheels"
# Download before stopping service
"$PIP_BIN" download -q -r "$TMP_DIR/src/server/requirements.txt" -d "$TMP_DIR/wheels" || { echo "[Error] Fallo al descargar dependencias de Python"; exit 1; }

# ---- Ventana de parada mínima ----
echo "[Info] Deteniendo servicio..."
systemctl stop syncpk-server

echo "[Info] Creando copia de seguridad local (código y BD)..."
rm -rf "$CODE_DIR.prev"
cp -a "$CODE_DIR" "$CODE_DIR.prev"
mkdir -p "$DATA_DIR/backup"
# backup de bd de forma segura (copia simple, SQLite puede requerir sqlite3 pero la API está parada, así que es seguro)
if ls "$DATA_DIR"/sync.db* 1> /dev/null 2>&1; then
    cp -a "$DATA_DIR"/sync.db* "$DATA_DIR/backup/"
fi

echo "[Info] Limpiando código viejo..."
# Point 11: Borrar todo excepto venv (y variables locales si las hubiera)
find "$CODE_DIR" -mindepth 1 -maxdepth 1 ! -name 'venv' ! -name '.ver' ! -name 'update.sh' -exec rm -rf {} +

echo "[Info] Aplicando nuevo código..."
# Copiar server/
cp -a "$TMP_DIR/src/server/"* "$CODE_DIR/"
# Point 2: Actualizar update.sh y check_update.sh
cp -a "$TMP_DIR/src/update.sh" "$CODE_DIR/update.sh"
if [ -f "$TMP_DIR/src/check_update.sh" ]; then
    cp -a "$TMP_DIR/src/check_update.sh" "$CODE_DIR/check_update.sh"
fi

# Point 9: Copiar archivos systemd y hacer daemon-reload
if [ -d "$TMP_DIR/src/server/systemd" ]; then
    cp -a "$TMP_DIR/src/server/systemd/"* /etc/systemd/system/
    systemctl daemon-reload
fi

echo "[Info] Instalando nuevas dependencias offline..."
"$PIP_BIN" install -q --no-index --find-links "$TMP_DIR/wheels" -r "$CODE_DIR/requirements.txt" || {
    echo "[Error] Fallo al instalar dependencias, restaurando backup..."
    rm -rf "$CODE_DIR"
    mv "$CODE_DIR.prev" "$CODE_DIR"
    exit 1
}

echo "[Info] Actualizando versión local..."
if [ -f "$TMP_DIR/src/.ver" ]; then
    cp "$TMP_DIR/src/.ver" "$CODE_DIR/.ver"
fi

# Point 6: Permisos
echo "[Info] Configurando permisos..."
chown -R root:root "$CODE_DIR"
find "$CODE_DIR" -type d -exec chmod 755 {} +
find "$CODE_DIR" -type f -exec chmod 644 {} +
chmod +x "$CODE_DIR/update.sh"
if [ -f "$CODE_DIR/check_update.sh" ]; then
    chmod +x "$CODE_DIR/check_update.sh"
fi

echo "[Info] Iniciando servicio..."
systemctl start syncpk-server

echo "[Info] Comprobando salud del servicio..."
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
    echo "[Error] El servicio no responde tras la actualización. Restaurando versión anterior..."
    systemctl stop syncpk-server
    rm -rf "$CODE_DIR"
    mv "$CODE_DIR.prev" "$CODE_DIR"
    systemctl start syncpk-server
    echo "[Error] Rollback completado."
    exit 1
fi

echo "[Info] Actualización completada con éxito."