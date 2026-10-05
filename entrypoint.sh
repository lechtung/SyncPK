#!/bin/sh
set -e

# Asegurar que el directorio de datos existe
mkdir -p /app/data

# Arreglar los permisos del volumen montado solo si difiere (Optimización - Punto 6)
if [ "$(stat -c '%u' /app/data)" != "1000" ]; then
    chown -R syncpkuser:syncpkuser /app/data
fi

# Restringir umask para que los ficheros creados sean privados (Punto 6)
umask 077

# Ejecutar el comando pasado (uvicorn) quitándonos los permisos de root
exec gosu syncpkuser "$@"
