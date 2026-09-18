#!/bin/bash

systemctl stop syncpk-server
cd /root/sync_server

echo "[1/7] Download files..."
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

systemctl start syncpk-server