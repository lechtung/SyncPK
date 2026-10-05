#!/bin/bash
set -euo pipefail

CODE_DIR="/opt/syncpk"
cd "$CODE_DIR" || exit 1

systemctl stop syncpk-server

echo "[Info] Obteniendo la última versión de GitHub..."
LATEST_TAR_URL=$(curl -fsSL https://api.github.com/repos/lechtung/SyncPK/releases/latest | grep "tarball_url" | cut -d '"' -f 4)

if [ -z "$LATEST_TAR_URL" ]; then
    echo "[Error] No se pudo obtener la última release."
    systemctl start syncpk-server
    exit 1
fi

echo "[Info] Descargando y extrayendo..."
TMP_DIR=$(mktemp -d)
curl -fsSL "$LATEST_TAR_URL" | tar -xz -C "$TMP_DIR" --strip-components=1

echo "[Info] Actualizando archivos..."
# Copiar el contenido de la carpeta server al directorio principal
cp -r "$TMP_DIR/server/"* "$CODE_DIR/"
# Si no existe .env.example, lo copiamos (ahora bajará directo de server/.env.example)

echo "[Info] Instalando dependencias de Python..."
"$CODE_DIR/venv/bin/pip" install -r "$CODE_DIR/requirements.txt"

echo "[Info] Actualizando versión local..."
cp "$TMP_DIR/.ver" "$CODE_DIR/.ver"

rm -rf "$TMP_DIR"

chown -R root:root "$CODE_DIR"
chmod -R u=rwX,go=rX "$CODE_DIR"

systemctl start syncpk-server
echo "[Info] Actualización completada con éxito."