#!/bin/bash

systemctl stop syncpk-server

curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/main.py

# Download .conf only if it doesn't exist to avoid overwriting user preferences
if [ ! -f .conf ]; then
    curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/.conf
fi

curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/requirements.txt
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/.ver

mkdir -p static/locales
cd static
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/index.html
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/app.js
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/style.css
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/favicon.ico
cd locales
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/en.json
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/es.json
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/de.json
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/fr.json
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/it.json
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/pt.json
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/ja.json
curl -s -O https://raw.githubusercontent.com/lechtung/SyncPK/main/server/static/locales/zh.json
cd ../..

systemctl start syncpk-server