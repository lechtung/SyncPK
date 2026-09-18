import sqlite3
import datetime
import random
import time
import requests
import os
import sys

# v2
# Forzar codificación UTF-8 en consola
sys.stdout.reconfigure(encoding='utf-8')

PLEX_URL = "http://192.168.178.21:32400"
PLEX_TOKEN = os.getenv("PLEX_TOKEN")
if not PLEX_TOKEN:
    # Usamos el token proporcionado por el usuario en su CURL como fallback
    PLEX_TOKEN = "MrzRJmz1xsCL3xxzzK_b"

SHOWS = [
    "The Walking Dead",
    "Spartacus: Dioses de la arena",
    "Firefly"
]

headers_fetch = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-plex-client-identifier": "7o448fp80hf1p7gbvqvvaklv",
    "x-plex-token": PLEX_TOKEN
}

# Cabeceras completas de la mutación por seguridad
headers_mutate = {
    "accept": "*/*",
    "accept-language": "es-ES,es;q=0.9,de-DE;q=0.8,de;q=0.7,en-US;q=0.6,en;q=0.5",
    "content-type": "application/json",
    "origin": "https://app.plex.tv",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "x-plex-client-identifier": "7o448fp80hf1p7gbvqvvaklv",
    "x-plex-product": "Plex Web",
    "x-plex-token": PLEX_TOKEN,
    "x-plex-version": "4.160.0"
}

query_graphql = """
query GetActivityFeed($first: PaginationInt!, $metadataID: ID, $types: [ActivityType!]!, $includeDescendants: Boolean = false) {
  activityFeed(first: $first, metadataID: $metadataID, types: $types, includeDescendants: $includeDescendants) {
    nodes {
      id
      date
    }
  }
}
"""

mutation_graphql = """
mutation updateActivityDate($id: ID!, $input: UpdateActivityInput!) {
  updateActivity(id: $id, input: $input) {
    id
  }
}
"""

def main():
    print("🔪 Starting Plex Temporal Surgery...")
    
    # 14 de Diciembre de 2023 a las 12:00 UTC (Para que caiga seguro en el día 14 en España)
    current_date = datetime.datetime(2023, 12, 14, 12, 0, 0, tzinfo=datetime.timezone.utc)
    counter = 0
    target = random.randint(1, 4)
    
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    total_episodios_modificados = 0

    url_graphql = "https://community.plex.tv/api"
    
    for show in SHOWS:
        print(f"\n📺 Processing Show: {show}")
        cursor.execute("SELECT * FROM watch_history WHERE media_type='episode' AND show_title=? ORDER BY season DESC, episode DESC", (show,))
        episodes = cursor.fetchall()
        
        if not episodes:
            print(f"  ⚠️ No episodes found in sync.db for '{show}'")
            continue
            
        for ep in episodes:
            db_id = ep["id"]
            plex_guid = ep["plex_guid"]
            metadata_id = plex_guid.split("/")[-1]
            season = ep["season"]
            episode = ep["episode"]
            
            # Formatos de fecha objetivo
            watched_at_graphql = current_date.strftime('%Y-%m-%dT%H:%M:%S.000Z')
            watched_at_local = current_date.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # PASO 0: Evitar Scrobble para los episodios que ya se procesaron en la tirada fallida
            needs_scrobble = True
            
            if show == "The Walking Dead" and season == 11 and episode >= 4:
                print(f"  ℹ️ {show} S{season}E{episode} - Nodo ya generado en pasada anterior. Saltando Scrobble.")
                needs_scrobble = False
                
            # PASO 1: SCROBBLE (Crear el evento en Plex local si no existía)
            if needs_scrobble:
                # 1.1 Necesitamos extraer el ratingKey local consultando a Plex por su guid global
                rating_key = None
                r_search = requests.get(f"{PLEX_URL}/library/all?guid={plex_guid}", headers={"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN}, timeout=10)
                if r_search.status_code == 200:
                    md = r_search.json().get("MediaContainer", {}).get("Metadata", [])
                    if md:
                        rating_key = md[0].get("ratingKey")
                        
                if not rating_key:
                    print(f"  ❌ No se pudo encontrar ratingKey local para {show} S{season}E{episode}. Saltando.")
                    continue
                
                # 1.2 Forzar el Scrobble
                r_scrobble = requests.get(f"{PLEX_URL}/:/scrobble?key={rating_key}&identifier=com.plexapp.plugins.library", headers={"X-Plex-Token": PLEX_TOKEN}, timeout=10)
                if r_scrobble.status_code != 200:
                    print(f"  ❌ Error haciendo scrobble de {show} S{season}E{episode} (HTTP {r_scrobble.status_code}). Saltando.")
                    continue
                
                print(f"  💉 Scrobble inyectado a {show} S{season}E{episode}. Esperando a que suba a la nube...")
                time.sleep(2)
            
            # PASO 2: OBTENER NODO (Polling)
            payload_get = {
                "query": query_graphql,
                "variables": {
                    "first": 24,
                    "types": ["WATCH_HISTORY", "WATCH_SESSION"],
                    "includeDescendants": True,
                    "metadataID": metadata_id
                },
                "operationName": "GetActivityFeed"
            }
            
            node_id_to_mutate = None
            max_retries = 10
            
            for attempt in range(max_retries):
                try:
                    r = requests.post(url_graphql, headers=headers_fetch, json=payload_get, timeout=10)
                    if r.status_code == 200:
                        nodes = r.json().get("data", {}).get("activityFeed", {}).get("nodes", [])
                        if nodes:
                            node_id_to_mutate = nodes[0]["id"]
                            break # Encontrado!
                        else:
                            print(f"  ⏳ GraphQL vacío, el nodo aún no ha sincronizado. Reintento {attempt+1}/{max_retries}...")
                            time.sleep(3)
                    elif r.status_code == 429:
                        print("  ⏳ Rate limit al buscar nodo. Esperando 5s...")
                        time.sleep(5)
                    elif r.status_code == 504:
                        print("  ⏳ Plex Cloud caído (504 Gateway Timeout). Esperando 15s...")
                        time.sleep(15)
                    else:
                        print(f"  ⚠️ HTTP {r.status_code} inesperado buscando nodo. Esperando 3s...")
                        time.sleep(3)
                except Exception as e:
                    print(f"  ❌ Error de conexión buscando nodo: {e}. Esperando 5s...")
                    time.sleep(5)
            
            if not node_id_to_mutate:
                print(f"  ❌ Agotados los reintentos. Plex Cloud no devolvió el nodo para S{season}E{episode}. Se quedará sin mutar.")
                continue
                
            # PASO 3: MUTACIÓN
            payload_mut = {
                "query": mutation_graphql,
                "variables": {
                    "id": node_id_to_mutate,
                    "input": {
                        "date": watched_at_graphql
                    }
                },
                "operationName": "updateActivityDate"
            }
            
            mutated = False
            for attempt in range(3): # Hasta 3 intentos por rate limit
                try:
                    mr = requests.post(url_graphql, headers=headers_mutate, json=payload_mut, timeout=10)
                    if mr.status_code == 200:
                        errors = mr.json().get("errors")
                        if errors:
                            err = errors[0]
                            if err.get("extensions", {}).get("code") == "RATE_LIMITED":
                                retry_after = err.get("extensions", {}).get("retryAfter", 10)
                                print(f"  ⏳ Rate limit mutando. Esperando {retry_after}s...")
                                time.sleep(retry_after + 1)
                                continue
                            else:
                                print(f"  ❌ Error GraphQL mutando: {errors}")
                                break
                        else:
                            print(f"  ✅ [NUBE+LOCAL] {show} S{season}E{episode} -> {watched_at_local}")
                            mutated = True
                            break
                    elif mr.status_code == 429:
                        print("  ⏳ 429 Rate limit mutando. Esperando 5s...")
                        time.sleep(5)
                        continue
                    else:
                        print(f"  ❌ HTTP {mr.status_code} al mutar.")
                        break
                except Exception as e:
                    print(f"  ❌ Excepción en mutación: {e}")
                    break
            
            if not mutated:
                continue
                
            # PASO 4: ACTUALIZAR BD LOCAL INCONDICIONALMENTE
            cursor.execute("UPDATE watch_history SET watched_at=? WHERE id=?", (watched_at_local, db_id))
            conn.commit()
            total_episodios_modificados += 1
            
            # GESTIÓN DE TIEMPO Y DÍAS
            # Restamos entre 40 y 60 minutos para el siguiente episodio (que es anterior)
            current_date -= datetime.timedelta(minutes=random.randint(40, 60))
            
            counter += 1
            if counter >= target:
                # Saltamos al día anterior y reseteamos la hora a las 12:00 UTC
                current_date = current_date.replace(hour=12, minute=0, second=0, microsecond=0)
                current_date -= datetime.timedelta(days=1)
                counter = 0
                target = random.randint(1, 4)
                
            # Pequeño respiro para no ahogar la API local ni la nube
            time.sleep(1)
            
    conn.close()
    print(f"\n🎉 Cirugía Inception completada! {total_episodios_modificados} episodios modificados al pasado.")

if __name__ == "__main__":
    main()
