import os
import json
import requests
import time

if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

PLEX_TOKEN = os.environ.get("PLEX_TOKEN")
headers_fetch = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-plex-client-identifier": "7o448fp80hf1p7gbvqvvaklv",
    "x-plex-token": PLEX_TOKEN
}

# La query secundaria descubierta por el usuario (limpiada para sacar solo lo esencial)
query_secondary = """
query GetActivityFeed($first: PaginationInt!, $after: String, $metadataID: ID, $types: [ActivityType!]!, $includeDescendants: Boolean = false) {
  activityFeed(
    first: $first
    after: $after
    metadataID: $metadataID
    types: $types
    includeDescendants: $includeDescendants
  ) {
    nodes {
      __typename
      date
      metadataItem {
        title
        type
        index
        parent { index title }
        grandparent { title }
      }
    }
    pageInfo { endCursor hasNextPage }
  }
}
"""

def fetch_show_history(show_id, show_title, out_file):
    has_next = True
    page_cursor = None
    
    print(f"\n📡 [API] Obteniendo historial completo de la serie: {show_title} ({show_id})...")
    
    while has_next:
        payload = {
            "query": query_secondary,
            "variables": {
                "first": 50,
                "after": page_cursor,
                "types": ["WATCH_HISTORY"], # Solo nos interesa cuando lo vio, no las reviews ni ratings
                "includeDescendants": True,
                "metadataID": show_id
            },
            "operationName": "GetActivityFeed"
        }
        
        resp = requests.post("https://community.plex.tv/api", headers=headers_fetch, json=payload, timeout=30)
        
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", "60"))
            print(f"⏳ [429] Esperando {retry}s...")
            time.sleep(retry)
            continue
            
        if resp.status_code != 200:
            print(f"❌ Error HTTP {resp.status_code}")
            break
            
        data = resp.json().get("data", {}).get("activityFeed", {})
        nodes = data.get("nodes", [])
        
        for n in nodes:
            meta = n.get("metadataItem")
            if not meta or meta.get("type") != "EPISODE":
                continue
                
            date = n.get("date")
            ep_idx = meta.get("index")
            ep_title = meta.get("title")
            parent = meta.get("parent") or {}
            s_idx = parent.get("index", "?")
            
            line = f"SERIE: {show_title} - T{s_idx}E{ep_idx} - {ep_title} | Visto el: {date}"
            print("  " + line)
            out_file.write(line + "\n")
            
        page_info = data.get("pageInfo", {})
        has_next = page_info.get("hasNextPage", False)
        page_cursor = page_info.get("endCursor")
        time.sleep(1) # Seguridad

def process_dump():
    dump_path = "scratch/graphql_dump_v2.json"
    out_path = "scratch/resultado_procesado.txt"
    
    if not os.path.exists(dump_path):
        print(f"❌ No se encuentra {dump_path}")
        return
        
    with open(dump_path, "r", encoding="utf-8") as f:
        all_nodes = json.load(f)
        
    processed_shows = set()
    
    with open(out_path, "w", encoding="utf-8") as out_file:
        out_file.write("=== HISTORIAL PROCESADO (SMART EXTRACTOR V2) ===\n\n")
        
        print(f"🔍 Procesando {len(all_nodes)} nodos del dump principal...")
        for node in all_nodes:
            meta = node.get("metadataItem")
            if not meta:
                continue
                
            m_type = meta.get("type")
            
            # Si es película, va directo
            if m_type == "MOVIE":
                title = meta.get("title")
                date = node.get("date")
                line = f"PELÍCULA: {title} | Visto el: {date}"
                print(line)
                out_file.write(line + "\n")
                
            # Si es episodio, extraemos la serie y aplicamos tu algoritmo
            elif m_type == "EPISODE":
                gp = meta.get("grandparent")
                if not gp:
                    continue # Huérfano sin serie, raro en V2 pero por si acaso
                    
                show_guid = gp.get("guid")
                show_title = gp.get("title")
                
                if not show_guid:
                    continue
                    
                show_id = show_guid.split("/")[-1]
                
                # LA MAGIA: Si ya la procesamos, saltamos
                if show_id in processed_shows:
                    continue
                    
                processed_shows.add(show_id)
                fetch_show_history(show_id, show_title, out_file)

if __name__ == "__main__":
    process_dump()
    print("\n🎉 Proceso finalizado. Revisa scratch/resultado_procesado.txt")
