import sqlite3
import datetime
import random
import time
import requests
import os
import sys

# Forzar codificación UTF-8 en consola de Windows para las tildes
sys.stdout.reconfigure(encoding='utf-8')

PLEX_URL = "http://192.168.178.21:32400"
PLEX_TOKEN = os.getenv("PLEX_TOKEN")
if not PLEX_TOKEN:
    # Usamos el token proporcionado por el usuario en su CURL como fallback
    PLEX_TOKEN = "MrzRJmz1xsCL3xxzzK_b"

SHOWS = [
    "Tokyo Vice",
    "Hijos de la anarquía",
    "Los informáticos",
    "Ozark",
    "Shōgun",
    "Silicon Valley",
    "Dexter",
    "Fallout",
    "Así nos ven",
    "A dos metros bajo tierra",
    "La caída de la casa Usher",
    "Hollywood",
    "Chernobyl"
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
    
    # 10 de Junio de 2024 a las 12:00 del mediodía UTC (Para que caiga seguro en el día 10 en España)
    current_date = datetime.datetime(2024, 6, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)
    counter = 0
    target = random.randint(1, 4)
    
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    total_episodios_modificados = 0
    
    url = "https://community.plex.tv/api"
    
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
            
            # Formatos de fecha
            watched_at_graphql = current_date.strftime('%Y-%m-%dT%H:%M:%S.000Z')
            watched_at_local = current_date.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # 1. Actualizar BD Local INCONDICIONALMENTE
            cursor.execute("UPDATE watch_history SET watched_at=? WHERE id=?", (watched_at_local, db_id))
            conn.commit()
            
            # 2. Consultar GraphQL para obtener el ID del nodo
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
            try:
                # Bucle de reintento para el GET
                for i in range(3):
                    r = requests.post(url, headers=headers_fetch, json=payload_get, timeout=10)
                    if r.status_code == 200:
                        nodes = r.json().get("data", {}).get("activityFeed", {}).get("nodes", [])
                        if nodes:
                            # Pillamos el primer nodo (suele haber 1 si se marcó toda la serie de golpe)
                            node_id_to_mutate = nodes[0]["id"]
                        break
                    elif r.status_code == 429:
                        print(f"  ⏳ Rate limit searching node. Waiting 5s...")
                        time.sleep(5)
                    else:
                        break
            except Exception as e:
                print(f"  ❌ Error searching node for S{season}E{episode}: {e}")
                
            # 3. Lanzar Mutación a Plex Cloud
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
                
                # Bucle de reintento para el MUTATE
                while True:
                    try:
                        mr = requests.post(url, headers=headers_mutate, json=payload_mut, timeout=10)
                        if mr.status_code == 200:
                            errors = mr.json().get("errors")
                            if errors:
                                rate_limited = False
                                retry_after = 5
                                for err in errors:
                                    if err.get("extensions", {}).get("code") == "RATE_LIMITED":
                                        rate_limited = True
                                        retry_after = err.get("extensions", {}).get("retryAfter", 10)
                                        
                                if rate_limited:
                                    print(f"  ⏳ Rate limit mutating. Waiting {retry_after}s...")
                                    time.sleep(retry_after + 1)
                                    continue # Reintenta
                                else:
                                    print(f"  ❌ Error mutating GraphQL: {errors}")
                                    break
                            else:
                                print(f"  ✅ [CLOUD+LOCAL] {show} S{season}E{episode} -> {watched_at_local}")
                                break
                        elif mr.status_code == 429:
                            print("  ⏳ 429 Rate limit mutating. Waiting 5s...")
                            time.sleep(5)
                            continue
                        else:
                            print(f"  ❌ HTTP {mr.status_code} on mutate.")
                            break
                    except Exception as e:
                        print(f"  ❌ Exception in mutation: {e}")
                        break
            else:
                print(f"  ⚠️ [LOCAL ONLY] {show} S{season}E{episode} -> {watched_at_local} (No record in Plex Cloud)")
            
            total_episodios_modificados += 1
            
            # Restamos entre 40 y 60 minutos para el siguiente episodio (que es anterior en la serie)
            current_date -= datetime.timedelta(minutes=random.randint(40, 60))
            
            # Gestión de avance de días
            counter += 1
            if counter >= target:
                # Saltamos al día anterior y reseteamos la hora a las 12:00 UTC
                current_date = current_date.replace(hour=12, minute=0, second=0, microsecond=0)
                current_date -= datetime.timedelta(days=1)
                counter = 0
                target = random.randint(1, 4)
                
            time.sleep(0.5) # Un pequeño respiro de medio segundo para no saturar
            
    conn.close()
    print(f"\n🎉 Surgery completed! {total_episodios_modificados} episodes modified.")

if __name__ == "__main__":
    main()
