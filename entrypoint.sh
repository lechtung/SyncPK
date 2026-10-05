#!/bin/sh
set -e

# Asegurar que el directorio de datos existe
mkdir -p /app/data

# Arreglar los permisos del volumen montado
chown -R syncpkuser:syncpkuser /app/data

# Ejecutar el comando pasado (uvicorn) quitándonos los permisos de root
exec su-exec syncpkuser "$@"
