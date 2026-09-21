import sqlite3
import datetime
import random
import time
import requests
import os
import sys

# Forzar codificación UTF-8 en consola
sys.stdout.reconfigure(encoding='utf-8')

PLEX_URL = "http://192.168.178.21:32400"
PLEX_TOKEN = os.getenv("PLEX_TOKEN")
if not PLEX_TOKEN:
    # Usamos el token proporcionado por el usuario en su CURL como fallback
    PLEX_TOKEN = "MrzRJmz1xsCL3xxzzK_b"

SHOW = "Silicon Valley"
TARGET_SEASON = 2

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
    print(f"🔪 Starting Plex Temporal Surgery for {SHOW} Season {TARGET_SEASON}...")
    
    # 19 de Septiembre de 2023 a las 12:00 UTC (Hacia abajo)
    current_date = datetime.datetime(2023, 9, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
    counter = 0
    target = random.randint(1, 4)
    
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    total_episodios_modificados = 0

    url_graphql = "https://community.plex.tv/api"
    
    print(f"\n📺 Processing Migration for: {SHOW} (Season {TARGET_SEASON})")
    
    # 1. Obtener la serie y todos sus episodios de Plex directamente
    r_show = requests.get(f"{PLEX_URL}/search?type=2&query={SHOW}", headers={"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN})
    show_key = None
    if r_show.status_code == 200:
        md = r_show.json().get("MediaContainer", {}).get("Metadata", [])
        if md:
            # Pillamos el primero que coincida (suele ser la serie correcta)
            show_key = md[0].get("ratingKey")
            
    if not show_key:
        print(f"  ❌ No se encontró la serie '{SHOW}' en Plex local.")
        return
        
    r_eps = requests.get(f"{PLEX_URL}/library/metadata/{show_key}/allLeaves", headers={"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN})
    plex_episodes = {}
    if r_eps.status_code == 200:
        for item in r_eps.json().get("MediaContainer", {}).get("Metadata", []):
            s = item.get("parentIndex")
            e = item.get("index")
            if s is not None and e is not None:
                plex_episodes[(s, e)] = item
    
    # Buscar episodios de la T2 de Silicon Valley
    episodes_to_fix = [ep for (s, e), ep in plex_episodes.items() if s == TARGET_SEASON]
    # Ordenar por número de episodio descendente, porque vamos restando horas a la fecha para que el orden cronológico sea correcto
    episodes_to_fix.sort(key=lambda x: x.get("index", 0), reverse=True)
    
    if not episodes_to_fix:
        print(f"  ⚠️ No episodes found for {SHOW} Season {TARGET_SEASON} in Plex.")
        return
        
    for plex_ep in episodes_to_fix:
        episode = plex_ep.get("index")
        rating_key = plex_ep.get("ratingKey")
        plex_guid = plex_ep.get("guid", "")
        metadata_id = plex_guid.split("/")[-1]
        
        # Formatos de fecha objetivo
        watched_at_graphql = current_date.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        watched_at_local = current_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        # PASO 1: SCROBBLE (Crear el evento en Plex local)
        # Esto marcará el capítulo como visto si no lo estaba, o actualizará su fecha si ya lo estaba.
        r_scrobble = requests.get(f"{PLEX_URL}/:/scrobble?key={rating_key}&identifier=com.plexapp.plugins.library", headers={"X-Plex-Token": PLEX_TOKEN}, timeout=10)
        if r_scrobble.status_code != 200:
            print(f"  ❌ Error haciendo scrobble de {SHOW} S{TARGET_SEASON:02d}E{episode:02d} (HTTP {r_scrobble.status_code}). Saltando.")
            continue
        
        print(f"  💉 Scrobble inyectado a {SHOW} S{TARGET_SEASON:02d}E{episode:02d}. Esperando a que suba a la nube...")
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
        for i in range(3):
            try:
                r = requests.post(url_graphql, headers=headers_fetch, json=payload_get, timeout=10)
                if r.status_code == 200:
                    nodes = r.json().get("data", {}).get("activityFeed", {}).get("nodes", [])
                    if nodes:
                        node_id_to_mutate = nodes[0]["id"]
                    break
                elif r.status_code == 429:
                    print(f"  ⏳ Rate limit searching node. Waiting 5s...")
                    time.sleep(5)
                else:
                    break
            except Exception as e:
                pass
                
        # PASO 3: MUTACIÓN
        if node_id_to_mutate:
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
            
            try:
                rm = requests.post(url_graphql, headers=headers_mutate, json=payload_mut, timeout=10)
                if rm.status_code == 200:
                    print(f"  ✅ [ÉXITO] {SHOW} S{TARGET_SEASON:02d}E{episode:02d} movido al {watched_at_local}")
                    total_episodios_modificados += 1
                else:
                    print(f"  ❌ [ERROR MUTACIÓN] S{TARGET_SEASON:02d}E{episode:02d} HTTP {rm.status_code}")
            except Exception as e:
                print(f"  ❌ [EXCEPCIÓN MUTACIÓN] S{TARGET_SEASON:02d}E{episode:02d}: {e}")
        else:
            print(f"  ❌ No se pudo recuperar el Activity Node ID para S{TARGET_SEASON:02d}E{episode:02d}.")
            
        # PASO 4: Actualizar DB local si el episodio existe ahí
        cursor.execute("UPDATE watch_history SET watched_at=? WHERE show_title=? AND season=? AND episode=?", 
                       (watched_at_local, SHOW, TARGET_SEASON, episode))
        conn.commit()
            
        # Retroceder el tiempo de 30 a 90 minutos para el siguiente episodio hacia atrás
        current_date -= datetime.timedelta(minutes=random.randint(30, 90))
        
        counter += 1
        if counter >= target:
            # Simulamos saltos de días para que no parezca que vimos toda la temporada en 24h
            current_date -= datetime.timedelta(days=random.randint(1, 3))
            counter = 0
            target = random.randint(1, 4)

    print(f"\n🎉 Cirugía completada. {total_episodios_modificados} episodios de {SHOW} T{TARGET_SEASON} operados.")
    conn.close()

if __name__ == "__main__":
    main()
