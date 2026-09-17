#!/bin/bash
# Script de Reset para SyncPK (Proxmox)
# Este script reinicia el entorno, borra la DB y descarga la última versión de GitHub.

echo "========================================"
echo "   RESETEANDO ENTORNO SYNCPK V7...      "
echo "========================================"

# 1. Parar el servicio
echo "[1/7] Asesinando el servicio syncpk-server (SIGKILL)..."
systemctl kill --signal=SIGKILL syncpk-server 2>/dev/null
systemctl stop syncpk-server

# 2. Ir a la carpeta del servidor
cd /root/sync_server

# 3. Borrar la base de datos y el registro de la última sincronización
echo "[2/7] Borrando base de datos, ajustes antiguos y flags..."
rm -f sync.db plex_settings.json _DUPLICATE_FIX

echo ""
echo "[2.5/7] ¿Quieres activar el modo FIX GLOBAL de duplicados de Plex (14 y 15 sep)?"
echo "        (Escribe 's' o 'S' para activar, pulsa INTRO para ignorar)"
read -p "        Respuesta: " FIX_INPUT

if [[ "$FIX_INPUT" == "s" || "$FIX_INPUT" == "S" ]]; then
    echo "        -> MODO FIX: Activando purga de duplicados de Plex (_DUPLICATE_FIX)"
    touch _DUPLICATE_FIX
else
    echo "        -> Modo fix ignorado."
fi
echo ""

# 4. Descargar los nuevos archivos desde GitHub
echo "[3/7] Descargando última versión de los archivos desde GitHub..."
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/main.py

mkdir -p static/locales
cd static
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/index.html
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/app.js
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/style.css
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/favicon.ico
cd locales
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/en.json
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/es.json
cd /root/sync_server

# 5. Preguntar por el limitador interactivo
echo ""
echo "[4/7] ¿Quieres establecer un límite de elementos para escanear?"
echo "      (Ejemplo: escribe 100 para probar rápido, o pulsa INTRO para escaneo completo)"
read -p "      Límite: " LIMIT_INPUT

if [ -z "$LIMIT_INPUT" ]; then
    echo "      -> Sin límite. Se borrará sync_limit.txt si existe."
    rm -f sync_limit.txt
else
    echo "      -> Límite establecido a $LIMIT_INPUT elementos."
    echo "$LIMIT_INPUT" > sync_limit.txt
fi
echo ""

# 6. Vaciar la papelera de logs antiguos
echo "[5/7] Limpiando el registro de logs del sistema (journal)..."
journalctl --vacuum-time=1s > /dev/null 2>&1

# 7. Arrancar el servicio
echo "[6/7] Arrancando syncpk-server..."
systemctl start syncpk-server

echo "[7/7] ¡Listo! Mostrando el log en directo (Pulsa Ctrl+C para salir)..."
echo "====================================="
sleep 2
journalctl -u syncpk-server -f
