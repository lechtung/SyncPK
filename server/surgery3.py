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
    
    # El rango estricto es del 20 al 24 de Septiembre de 2023
    
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
            
        # NUEVO: Obtener la serie y todos sus episodios de Plex directamente
        r_show = requests.get(f"{PLEX_URL}/search?type=2&query={show}", headers={"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN})
        show_key = None
        if r_show.status_code == 200:
            md = r_show.json().get("MediaContainer", {}).get("Metadata", [])
            if md:
                show_key = md[0].get("ratingKey")
                
        if not show_key:
            print(f"  ❌ No se encontró la serie {show} en Plex local.")
            continue
            
        r_eps = requests.get(f"{PLEX_URL}/library/metadata/{show_key}/allLeaves", headers={"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN})
        plex_episodes = {}
        if r_eps.status_code == 200:
            for item in r_eps.json().get("MediaContainer", {}).get("Metadata", []):
                s = item.get("parentIndex")
                e = item.get("index")
                if s and e:
                    plex_episodes[(s, e)] = item
            
        # Distribución estricta pedida: 3 el 24, 3 el 23, 2 el 22, 3 el 21, 3 el 20
        # Como el bucle procesa del S1E14 al S1E1, mapeamos los días en ese mismo orden
        days = [24, 24, 24, 23, 23, 23, 22, 22, 21, 21, 21, 20, 20, 20]
        # Horas decrecientes para mantener el orden cronológico dentro del mismo día
        hours = [22, 21, 20, 22, 21, 20, 21, 20, 22, 21, 20, 22, 21, 20]
            
        for idx, ep in enumerate(episodes):
            db_id = ep["id"]
            season = ep["season"]
            episode = ep["episode"]
            
            # Buscamos el episodio en Plex por Temporada y Capítulo (así ignoramos el GUID viejo de sync.db)
            plex_ep = plex_episodes.get((season, episode))
            if not plex_ep:
                print(f"  ❌ No se pudo encontrar Firefly S{season}E{episode} en Plex local. Saltando.")
                continue
                
            rating_key = plex_ep.get("ratingKey")
            plex_guid = plex_ep.get("guid", "")
            metadata_id = plex_guid.split("/")[-1]
            
            # Asignar la fecha estricta pre-calculada
            current_date = datetime.datetime(2023, 9, days[idx], hours[idx], 0, 0, tzinfo=datetime.timezone.utc)
            
            # Formatos de fecha objetivo
            watched_at_graphql = current_date.strftime('%Y-%m-%dT%H:%M:%S.000Z')
            watched_at_local = current_date.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            needs_scrobble = True
                
            # PASO 1: SCROBBLE (Crear el evento en Plex local si no existía)
            if needs_scrobble:
                # 1.2 Forzar el Scrobble con el ratingKey actualizado
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
            cursor.execute("UPDATE watch_history SET watched_at=?, plex_guid=? WHERE id=?", (watched_at_local, plex_guid, db_id))
            conn.commit()
            total_episodios_modificados += 1
            print(f"  ✅ [LOCAL DB] {show} S{season}E{episode} -> {watched_at_local}")
                
            # Pequeño respiro para no ahogar la API local ni la nube
            time.sleep(1)
            
    conn.close()
    print(f"\n🎉 Cirugía Inception completada! {total_episodios_modificados} episodios modificados al pasado.")

if __name__ == "__main__":
    main()
