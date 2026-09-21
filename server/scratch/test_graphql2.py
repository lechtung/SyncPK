#v2
import os
import json
import time
import requests

if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

PLEX_TOKEN = os.environ.get("PLEX_TOKEN")

url_graphql = "https://community.plex.tv/api"
headers_fetch = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-plex-client-identifier": "7o448fp80hf1p7gbvqvvaklv",
    "x-plex-token": PLEX_TOKEN
}

query_graphql = """
query GetActivityFeed($first: PaginationInt!, $after: String, $types: [ActivityType!]!, $includeDescendants: Boolean = false) {
  activityFeed(
    first: $first
    after: $after
    types: $types
    includeDescendants: $includeDescendants
  ) {
    nodes {
      __typename
      date
      id
      metadataItem {
        id
        title
        type
        index
        guid
        parent { index title type }
        grandparent { index title type guid }
      }
      ... on ActivityWatchSession {
        episodeCount
      }
    }
    pageInfo { endCursor hasNextPage }
  }
}
"""

def fetch_all_history():
    has_next = True
    page_cursor = None
    all_nodes = []
    page = 1
    
    print("🚀 Iniciando extracción del historial completo de Plex Cloud...")
    
    while has_next:
        payload = {
            "query": query_graphql,
            "variables": {
                "first": 50,
                "after": page_cursor,
                "types": ["WATCH_HISTORY", "WATCH_SESSION"],
                "includeDescendants": True
            },
            "operationName": "GetActivityFeed"
        }
        
        print(f"📄 Solicitando página {page}...")
        
        try:
            resp = requests.post(url_graphql, headers=headers_fetch, json=payload, timeout=30)
            
            if resp.status_code == 429:
                # Extraer la cabecera Retry-After si Plex nos la proporciona
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait_time = int(retry_after)
                    print(f"⏳ [429 Rate Limit] Plex nos pide esperar exactamente {wait_time} segundos...")
                else:
                    wait_time = 60
                    print(f"⏳ [429 Rate Limit] Plex no mandó cabecera. Esperando {wait_time} segundos por seguridad...")
                
                time.sleep(wait_time)
                continue  # Reintenta exactamente la misma página (mismo cursor)
                
            if resp.status_code != 200:
                print(f"❌ Error HTTP {resp.status_code}: {resp.text}")
                break
                
            data = resp.json().get("data", {}).get("activityFeed", {})
            nodes = data.get("nodes", [])
            page_info = data.get("pageInfo", {})
            
            if not nodes:
                print("⚠️ No hay más nodos en esta página.")
                break
                
            all_nodes.extend(nodes)
            print(f"✅ Página {page} obtenida ({len(nodes)} nodos). Total acumulado: {len(all_nodes)}")
            
            has_next = page_info.get("hasNextPage", False)
            page_cursor = page_info.get("endCursor")
            page += 1
            
            # Pausa artificial cortita entre páginas exitosas para no agobiar al servidor y evitar 429s
            time.sleep(1)
            
        except requests.exceptions.Timeout:
            print("⚠️ Timeout de red! Esperando 10 segundos antes de reintentar la página...")
            time.sleep(10)
            continue
        except Exception as e:
            print(f"❌ Error crítico en red: {e}")
            break
            
    # Guardamos todo el volcado paginado en un solo JSON
    print(f"💾 Guardando {len(all_nodes)} registros en graphql_dump_v2.json...")
    with open("graphql_dump_v2.json", "w", encoding="utf-8") as f:
        json.dump(all_nodes, f, indent=2)
    print("🎉 Proceso finalizado.")

if __name__ == "__main__":
    fetch_all_history()
