import os
import sys
import requests
import urllib.parse

if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

PLEX_TOKEN = os.environ.get("PLEX_TOKEN")
if not PLEX_TOKEN:
    print("❌ PLEX_TOKEN no encontrado en .env")
    sys.exit(1)

if len(sys.argv) < 2:
    print("⚠️ Uso: python test_cloud_scrobble.py \"Nombre de la pelicula\"")
    sys.exit(1)

movie_name = sys.argv[1]
print(f"🔍 Buscando '{movie_name}' en la base de datos global de Plex (Discover)...")

search_url = f"https://metadata.provider.plex.tv/library/search?query={urllib.parse.quote(movie_name)}&limit=3&searchTypes=movies&includeMetadata=1"
headers = {
    "Accept": "application/json",
    "x-plex-token": PLEX_TOKEN,
    "x-plex-client-identifier": "7o448fp80hf1p7gbvqvvaklv"
}

resp = requests.get(search_url, headers=headers)
if resp.status_code != 200:
    print(f"❌ Error buscando en Plex: {resp.status_code} - {resp.text}")
    sys.exit(1)

data = resp.json().get("MediaContainer", {})
metadata = data.get("Metadata", [])

if not metadata:
    print(f"❌ No se encontró la película '{movie_name}' en Plex Cloud.")
    sys.exit(1)

# Pillamos el primer resultado
movie = metadata[0]
title = movie.get("title")
year = movie.get("year")
rating_key = movie.get("ratingKey")

print(f"✅ Encontrada: {title} ({year}) - Cloud ID: {rating_key}")

print(f"🚀 Intentando marcarla como vista en la nube...")
# Endpoint para marcar como visto en el Cloud (Discover)
scrobble_url = f"https://metadata.provider.plex.tv/actions/scrobbles?key={rating_key}&identifier=tv.plex.provider.metadata"

resp_scrobble = requests.get(scrobble_url, headers=headers)

if resp_scrobble.status_code == 200:
    print(f"🎉 ¡ÉXITO! '{title}' marcada como vista en Plex Cloud.")
    print("Revisa tu aplicación web oficial de Plex (Activity Feed) a ver si aparece.")
else:
    print(f"❌ Falló el scrobble. Código {resp_scrobble.status_code}")
    print(resp_scrobble.text)
