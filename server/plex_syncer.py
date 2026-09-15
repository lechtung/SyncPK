import requests
import json
import time
import os
import datetime
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURACIÓN ---
PLEX_URL = os.getenv("PLEX_URL", "http://192.168.178.21:32400")
PLEX_TOKEN = os.getenv("PLEX_TOKEN", "")
SERVER_URL = "http://127.0.0.1:8000" # Runs in the same LXC
SYNC_PASSWORD_B64 = os.getenv("SYNC_PASSWORD_B64", "")
SETTINGS_FILE = "plex_settings.json"
SYNC_INTERVAL = 900 # 15 minutes in seconds

plex_headers = {"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN}
server_headers = {}
if SYNC_PASSWORD_B64:
    server_headers["Authorization"] = f"Basic {SYNC_PASSWORD_B64}"

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {"last_sync_date": ""}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

def get_plex_libraries():
    # Obtener todas las librerías de video
    try:
        r = requests.get(f"{PLEX_URL}/library/sections", headers=plex_headers)
        if r.status_code == 200:
            data = r.json()
            sections = data.get("MediaContainer", {}).get("Directory", [])
            # Filter only movies and shows
            return [s["key"] for s in sections if s.get("type") in ["movie", "show"]]
    except Exception as e:
        print(f"Error obteniendo librerías de Plex: {e}")
    return []

def push_all_to_server():
    print("Starting FULL PUSH from Plex to local server...")
    payloads = []
    
    sections = get_plex_libraries()
    for sec_id in sections:
        try:
            # Obtener todos los elementos de la librería y filtramos en Python
            r = requests.get(f"{PLEX_URL}/library/sections/{sec_id}/all", headers=plex_headers)
            if r.status_code != 200:
                continue
                
            data = r.json()
            items = data.get("MediaContainer", {}).get("Metadata", [])
            
            for item in items:
                m_type = item.get("type")
                
                # If it's a show, we request episodes
                if m_type == "show":
                    r_eps = requests.get(f"{PLEX_URL}/library/metadata/{item['ratingKey']}/allLeaves", headers=plex_headers)
                    if r_eps.status_code == 200:
                        eps_data = r_eps.json()
                        episodes = eps_data.get("MediaContainer", {}).get("Metadata", [])
                        for ep in episodes:
                            # We only care about watched items
                            if ep.get("viewCount", 0) > 0:
                                payloads.append(build_payload_from_plex(ep, "episode"))
                elif m_type == "movie":
                    # We only care about watched movies
                    if item.get("viewCount", 0) > 0:
                        payloads.append(build_payload_from_plex(item, "movie"))
                    
        except Exception as e:
            print(f"Error escaneando sección {sec_id}: {e}")
            
    if payloads:
        print(f"🚀 [FULL PUSH] Enviando {len(payloads)} items al servidor central...")
        try:
            r = requests.post(f"{SERVER_URL}/webhook/plex/bulk", json=payloads, headers=server_headers)
            print(f"Server response: {r.status_code} - {r.text}")
        except Exception as e:
            print(f"Error sending bulk: {e}")

def build_payload_from_plex(item, media_type):
    # Build a payload compatible with our main.py from Plex JSON
    
    watched_at = ""
    if item.get("lastViewedAt"):
        # Convert Unix timestamp to UTC str
        utc_dt = datetime.datetime.utcfromtimestamp(item["lastViewedAt"])
        watched_at = utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        
    guids = []
    if item.get("Guid"):
        guids = item["Guid"]
        
    payload = {
        "event": "media.scrobble",
        "Metadata": {
            "type": "episode" if media_type == "episode" else "movie",
            "title": item.get("title"),
            "guid": item.get("guid"),
            "Guid": guids,
            "watched_at": watched_at
        }
    }
    
    if media_type == "episode":
        payload["Metadata"]["grandparentTitle"] = item.get("grandparentTitle")
        payload["Metadata"]["parentIndex"] = item.get("parentIndex")
        payload["Metadata"]["index"] = item.get("index")
        payload["Metadata"]["grandparentGuid"] = item.get("grandparentGuid")
        payload["Metadata"]["grandparentKey"] = item.get("grandparentKey")
        
    return payload

def get_plex_items_map():
    # Descarga todos los items de Plex para hacer el cruce rápido en memoria
    print("Mapeando librería de Plex...")
    plex_movies = []
    plex_shows = []
    sections = get_plex_libraries()
    
    for sec_id in sections:
        try:
            r = requests.get(f"{PLEX_URL}/library/sections/{sec_id}/all", headers=plex_headers)
            if r.status_code == 200:
                items = r.json().get("MediaContainer", {}).get("Metadata", [])
                for item in items:
                    if item.get("type") == "movie":
                        plex_movies.append(item)
                    elif item.get("type") == "show":
                        plex_shows.append(item)
        except Exception as e:
            print(f"Error mapeando sección {sec_id}: {e}")
            
    return plex_movies, plex_shows

def match_movie(movie_data, plex_movies):
    title = movie_data.get("movie", {}).get("title", "").lower()
    for pm in plex_movies:
        if pm.get("title", "").lower() == title:
            return pm.get("ratingKey")
    return None

def match_show(show_data, plex_shows):
    title = show_data.get("show", {}).get("title", "").lower()
    for ps in plex_shows:
        if ps.get("title", "").lower() == title:
            return ps.get("ratingKey")
    return None

def pull_from_server_and_scrobble(date_from=None):
    print("Starting PULL from local server...")
    
    url = f"{SERVER_URL}/sync/all-items?client=plex"
    if date_from:
        url += f"&date_from={date_from}"
    print(f"🌐 Solicitando novedades a: {url}")
        
    try:
        r = requests.get(url, headers=server_headers)
        if r.status_code != 200:
            print(f"Error connecting to server: {r.status_code}")
            return
            
        data = r.json()
        movies = data.get("movies", [])
        shows = data.get("shows", [])
        
        if not movies and not shows:
            print("Nothing new in the server to send to Plex.")
            return
            
        print(f"Received from server: {len(movies)} películas y {len(shows)} shows to mark in Plex.")
        
        plex_movies, plex_shows = get_plex_items_map()
        # Procesar Películas
        for m in movies:
            r_key = match_movie(m, plex_movies)
            if r_key:
                scrobble_url = f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={r_key}"
                sr = requests.get(scrobble_url, headers=plex_headers)
                if sr.status_code == 200:
                    print(f"Marcada película en Plex: {m['movie']['title']}")
        # Process Shows
        for s in shows:
            s_key = match_show(s, plex_shows)
            if s_key:
                # If we have the show, iterate its seasons/episodes
                s_title = s["show"]["title"]
                for season in s.get("seasons", []):
                    s_num = season.get("number")
                    
                    # Request episodes of this show from Plex
                    r_eps = requests.get(f"{PLEX_URL}/library/metadata/{s_key}/allLeaves", headers=plex_headers)
                    if r_eps.status_code == 200:
                        plex_eps = r_eps.json().get("MediaContainer", {}).get("Metadata", [])
                        
                        for ep in season.get("episodes", []):
                            e_num = ep.get("number")
                            
                            # Find match of the episode in Plex
                            for pep in plex_eps:
                                if pep.get("parentIndex") == s_num and pep.get("index") == e_num:
                                    scrobble_url = f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={pep['ratingKey']}"
                                    sr = requests.get(scrobble_url, headers=plex_headers)
                                    if sr.status_code == 200:
                                        print(f"Marked episode in Plex: {s_title} T{s_num}E{e_num}")
                                    break
    except Exception as e:
        print(f"Error in PULL: {e}")

def run_sync():
    settings = load_settings()
    last_sync = settings.get("last_sync_date")
    
    is_first_sync = not last_sync
    
    # 1. PULL from Server
    pull_from_server_and_scrobble(last_sync)
    
    # 2. FULL PUSH from Plex (only if it's the first time)
    if is_first_sync:
        push_all_to_server()
        
    # Update date
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    settings["last_sync_date"] = now_utc
    save_settings(settings)
    print(f"Sincronización completada. Fecha actualizada: {now_utc}")

if __name__ == "__main__":
    print("Starting Plex Syncer service...")
    while True:
        try:
            run_sync()
        except Exception as e:
            print(f"Error in main loop: {e}")
            
        print(f"Sleeping {SYNC_INTERVAL} seconds...")
        time.sleep(SYNC_INTERVAL)

