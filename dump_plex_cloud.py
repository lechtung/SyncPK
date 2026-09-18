import requests
import json
import time
import os

#v2
# Intentamos leer el PLEX_TOKEN del .env 
PLEX_TOKEN = ""
try:
    with open(".env", "r") as f:
        for line in f:
            if line.startswith("PLEX_TOKEN"):
                PLEX_TOKEN = line.split("=")[1].strip().strip('"').strip("'")
except Exception as e:
    print(f"No se pudo leer .env: {e}")

if not PLEX_TOKEN:
    PLEX_TOKEN = input("No se encontró PLEX_TOKEN. Pégalo aquí: ").strip()

headers = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-plex-client-identifier": "7o448fp80hf1p7gbvqvvaklv",
    "x-plex-token": PLEX_TOKEN
}

url_graphql = "https://community.plex.tv/api"

# Query GraphQL. Hemos usado Fragments on Movie/Episode porque en GraphQL 
# los metadatos polimórficos se piden así.
query = """
query GetActivityFeed($first: PaginationInt!, $after: String, $types: [ActivityType!]!) {
  activityFeed(first: $first, after: $after, types: $types) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      date
      __typename
      metadataItem {
        __typename
        title
        guid
      }
    }
  }
}
"""

def dump_history():
    print("Iniciando descarga del historial desde Plex Cloud...")
    has_next = True
    cursor = None
    all_nodes = []
    
    page = 1
    while has_next:
        print(f"Descargando página {page}...")
        payload = {
            "query": query,
            "variables": {
                "first": 50, # Pedimos de 50 en 50
                "after": cursor,
                "types": ["WATCH_HISTORY", "WATCH_SESSION"]
            },
            "operationName": "GetActivityFeed"
        }
        
        resp = requests.post(url_graphql, headers=headers, json=payload, timeout=20)
        
        if resp.status_code == 429:
            print("Plex nos pide que esperemos (Rate Limit)... pausando 5s.")
            time.sleep(5)
            continue
            
        if resp.status_code != 200:
            print(f"Error {resp.status_code}: {resp.text}")
            break
            
        data = resp.json().get("data", {}).get("activityFeed", {})
        nodes = data.get("nodes", [])
        page_info = data.get("pageInfo", {})
        
        if not nodes:
            break
            
        all_nodes.extend(nodes)
        
        has_next = page_info.get("hasNextPage", False)
        cursor = page_info.get("endCursor")
        page += 1
        time.sleep(0.5) # Pequeña pausa para no saturar la API
        
    print(f"¡Completado! Se han descargado {len(all_nodes)} registros históricos.")
    
    with open("plex_cloud_history_dump.json", "w", encoding="utf-8") as f:
        json.dump(all_nodes, f, indent=4, ensure_ascii=False)
        
    print("Guardado en disco como: plex_cloud_history_dump.json")

if __name__ == "__main__":
    dump_history()
