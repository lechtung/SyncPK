from fastapi import FastAPI, Request, Query, Depends, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import datetime
import json
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
import os
import base64
import hmac
import hashlib
import asyncio
import time
import threading
import queue

# Manual .env fallback
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

app = FastAPI()

# --- PLEX & SECURITY CONFIGURATION ---
PLEX_URL = os.getenv("PLEX_URL", "")
PLEX_TOKEN = os.getenv("PLEX_TOKEN", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
SALT = os.getenv("SALT", "")
WEB_HASH = os.getenv("WEB_HASH", "")
API_HASH = os.getenv("API_HASH", "")
HAS_PLEX_PASS = os.getenv("HAS_PLEX_PASS", "false").lower() == "true"

plex_headers = {"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN}

def verify_api_key(authorization: str = Header(None)):
    if not WEB_HASH:
        return True
    
    if not authorization or not authorization.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    # Strip any "Basic " strings to handle frontend cache bugs where it sends "Basic Basic password"
    token = authorization.replace("Basic ", "").strip()
    token_hash = hashlib.sha256((token + SALT).encode()).hexdigest()
    
    if not hmac.compare_digest(token_hash, WEB_HASH):
        raise HTTPException(status_code=401, detail="Invalid Web Password")
    return True

def verify_webhook_token(token: Optional[str] = Query(None)):
    if not API_HASH:
        return True
        
    if not token:
        raise HTTPException(status_code=401, detail="Missing webhook token")
        
    token_hash = hashlib.sha256((token + SALT).encode()).hexdigest()
    if not hmac.compare_digest(token_hash, API_HASH):
        raise HTTPException(status_code=401, detail="Invalid API Token")
    return True


def init_db():
    conn = sqlite3.connect("sync.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watch_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_type TEXT,
            title TEXT,
            show_title TEXT,
            season INTEGER,
            episode INTEGER,
            
            -- IDs INTERNOS DE LOS REPRODUCTORES --
            plex_guid TEXT,
            plex_show_guid TEXT,
            kodi_id INTEGER,
            kodi_show_id INTEGER,
            
            -- IDs GLOBALES --
            imdb_id TEXT,
            tmdb_id TEXT,
            tvdb_id TEXT,
            show_imdb_id TEXT,
            show_tmdb_id TEXT,
            show_tvdb_id TEXT,
            
            watched_at TEXT,
            origin TEXT,
            created_at TEXT,
            duration INTEGER DEFAULT 0,
            poster_path TEXT,
            fanart_path TEXT
        )
    """)
    try:
        cursor.execute("ALTER TABLE watch_history ADD COLUMN duration INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE watch_history ADD COLUMN poster_path TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE watch_history ADD COLUMN fanart_path TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()

import httpx
import asyncio
import os

os.makedirs("static/cache/posters", exist_ok=True)
os.makedirs("static/cache/fanarts", exist_ok=True)

tmdb_semaphore = asyncio.Semaphore(10)

async def download_tmdb_images(db_id, tmdb_id, media_type):
    if not TMDB_API_KEY or not tmdb_id:
        return
        
    async with tmdb_semaphore:
        url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}?api_key={TMDB_API_KEY}&language=es"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            if resp.status_code != 200:
                # Retry without language if it fails
                resp = await client.get(url.replace("&language=es", ""), timeout=10)
                if resp.status_code != 200: return
                
            data = resp.json()
            poster = data.get("poster_path")
            backdrop = data.get("backdrop_path")
            
            poster_local = None
            fanart_local = None
            
            if poster:
                img_url = f"https://image.tmdb.org/t/p/w185{poster}"
                img_resp = await client.get(img_url, timeout=15)
                if img_resp.status_code == 200:
                    local_path = f"static/cache/posters/{tmdb_id}.jpg"
                    with open(local_path, "wb") as f:
                        f.write(img_resp.content)
                    poster_local = f"/cache/posters/{tmdb_id}.jpg"
            
            if backdrop:
                img_url = f"https://image.tmdb.org/t/p/w300{backdrop}"
                img_resp = await client.get(img_url, timeout=15)
                if img_resp.status_code == 200:
                    local_path = f"static/cache/fanarts/{tmdb_id}.jpg"
                    with open(local_path, "wb") as f:
                        f.write(img_resp.content)
                    fanart_local = f"/cache/fanarts/{tmdb_id}.jpg"
    except Exception as e:
        print(f"Error asíncrono en TMDB para {tmdb_id}: {e}", flush=True)

def download_tmdb_images_sync(tmdb_id, media_type):
    if not TMDB_API_KEY or not tmdb_id:
        return None, None
        
    poster_local_path = f"static/cache/posters/{tmdb_id}.jpg"
    fanart_local_path = f"static/cache/fanarts/{tmdb_id}.jpg"
    
    poster_local = f"/cache/posters/{tmdb_id}.jpg" if os.path.exists(poster_local_path) else None
    fanart_local = f"/cache/fanarts/{tmdb_id}.jpg" if os.path.exists(fanart_local_path) else None
    
    if poster_local and fanart_local:
        return poster_local, fanart_local

    lang = os.getenv("SYNC_LANGUAGE", "en")
    url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}?api_key={TMDB_API_KEY}&language={lang}"
    import requests
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            resp = requests.get(url.replace(f"&language={lang}", ""), timeout=10)
            if resp.status_code != 200: 
                print(f"❌ Error TMDB ({resp.status_code}) para {tmdb_id}: {resp.text}", flush=True)
                return poster_local, fanart_local
            
        data = resp.json()
        poster = data.get("poster_path")
        backdrop = data.get("backdrop_path")
        
        if poster and not poster_local:
            img_url = f"https://image.tmdb.org/t/p/w185{poster}"
            img_resp = requests.get(img_url, timeout=15)
            if img_resp.status_code == 200:
                with open(poster_local_path, "wb") as f:
                    f.write(img_resp.content)
                poster_local = f"/cache/posters/{tmdb_id}.jpg"
        
        if backdrop and not fanart_local:
            img_url = f"https://image.tmdb.org/t/p/w300{backdrop}"
            img_resp = requests.get(img_url, timeout=15)
            if img_resp.status_code == 200:
                with open(fanart_local_path, "wb") as f:
                    f.write(img_resp.content)
                fanart_local = f"/cache/fanarts/{tmdb_id}.jpg"
                
    except Exception as e:
        print(f"Error descargando imágenes sincrónicas TMDB para {tmdb_id}: {e}")
        
    return poster_local, fanart_local


async def bulk_download_tmdb_images():
    print("Starting bulk TMDB image download for missing posters...")
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT tmdb_id FROM watch_history WHERE media_type='movie' AND poster_path IS NULL AND tmdb_id IS NOT NULL")
    movie_rows = cursor.fetchall()
    cursor.execute("SELECT DISTINCT show_tmdb_id FROM watch_history WHERE media_type='episode' AND poster_path IS NULL AND show_tmdb_id IS NOT NULL")
    show_rows = cursor.fetchall()
    conn.close()
    
    for row in movie_rows:
        await download_tmdb_images(None, row["tmdb_id"], "movie")
        await asyncio.sleep(0.1)
        
    for row in show_rows:
        await download_tmdb_images(None, row["show_tmdb_id"], "tv")
        await asyncio.sleep(0.1)
        
    settings = load_settings()
    if settings.get("sync_state") == 1:
        settings["sync_state"] = 2
        save_settings(settings)
    print("✅ Bulk TMDB image download completed!")

def extract_ids(guid_array):
    imdb_id = tmdb_id = tvdb_id = None
    for g in guid_array:
        gid = g.get("id", "")
        if gid.startswith("imdb://"): imdb_id = gid.split("://")[1]
        elif gid.startswith("tmdb://"): tmdb_id = gid.split("://")[1]
        elif gid.startswith("tvdb://"): tvdb_id = gid.split("://")[1]
    return imdb_id, tmdb_id, tvdb_id

def get_show_ids_from_plex(grandparent_key):
    url = f"{PLEX_URL}{grandparent_key}"
    try:
        req = urllib.request.Request(url, headers=plex_headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            metadata = data.get("MediaContainer", {}).get("Metadata", [])
            if metadata:
                guids = metadata[0].get("Guid", [])
                return extract_ids(guids)
    except Exception as e:
        print(f"Error sacando IDs de la serie: {e}")
    return None, None, None

def process_plex_payload(payload, cursor, is_bulk=False):
    if payload.get("event") != "media.scrobble": return False
        
    metadata = payload.get("Metadata", {})
    media_type = metadata.get("type") 
    title = metadata.get("title")
    plex_guid = metadata.get("guid") 
    
    watched_at_payload = metadata.get("watched_at")
    is_live_event = not watched_at_payload and not is_bulk
    
    watched_at = watched_at_payload
    if not watched_at:
        watched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    duration = int(metadata.get("duration", 0)) // 60000
    
    imdb_id, tmdb_id, tvdb_id = extract_ids(metadata.get("Guid", []))
    
    show_title = season = episode = plex_show_guid = None
    show_imdb_id = show_tmdb_id = show_tvdb_id = None
    
    if media_type == "episode":
        show_title = metadata.get("grandparentTitle")
        season = metadata.get("parentIndex")
        episode = metadata.get("index")
        plex_show_guid = metadata.get("grandparentGuid") 
        grandparent_key = metadata.get("grandparentKey")
        
        grandparent_guids = metadata.get("grandparentGuids")
        if grandparent_guids:
            show_imdb_id, show_tmdb_id, show_tvdb_id = extract_ids(grandparent_guids)
        elif grandparent_key:
            show_imdb_id, show_tmdb_id, show_tvdb_id = get_show_ids_from_plex(grandparent_key)

    existing_id = None
    if media_type == "episode":
        if tmdb_id:
            cursor.execute("SELECT id FROM watch_history WHERE media_type='episode' AND tmdb_id=?", (tmdb_id,))
        else:
            cursor.execute("SELECT id FROM watch_history WHERE media_type='episode' AND show_title=? AND season=? AND episode=?", (show_title, season, episode))
    else:
        if tmdb_id:
            cursor.execute("SELECT id FROM watch_history WHERE media_type='movie' AND tmdb_id=?", (tmdb_id,))
        else:
            cursor.execute("SELECT id FROM watch_history WHERE media_type='movie' AND title=?", (title,))
            
    row = cursor.fetchone()
    if row:
        existing_id = row[0]
        if is_live_event:
            cursor.execute("""
                UPDATE watch_history SET
                    plex_guid=?, plex_show_guid=?,
                    watched_at=?, origin='plex', created_at=?, duration=?
                WHERE id=?
            """, (plex_guid, plex_show_guid, watched_at, now_utc, duration, existing_id))
            action = "Live Update (Re-visionado)"
        else:
            cursor.execute("""
                UPDATE watch_history SET
                    plex_guid=?, plex_show_guid=?, duration=?
                WHERE id=?
            """, (plex_guid, plex_show_guid, duration, existing_id))
            action = "Bulk Update (Solo IDs)"
            
        if media_type == "episode":
            prefix = "Bulk Import" if is_bulk else "Plex PUSH"
            print(f"🔄 {prefix} ({action}): Serie '{show_title}' T{season}E{episode} - {title}")
        else:
            prefix = "Bulk Import" if is_bulk else "Plex PUSH"
            print(f"🔄 {prefix} ({action}): Película '{title}'")
    else:
        cursor.execute("""
            INSERT INTO watch_history (
                media_type, title, show_title, season, episode, 
                plex_guid, plex_show_guid, 
                imdb_id, tmdb_id, tvdb_id, show_imdb_id, show_tmdb_id, show_tvdb_id, 
                watched_at, origin, created_at, duration
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            media_type, title, show_title, season, episode, 
            plex_guid, plex_show_guid,
            imdb_id, tmdb_id, tvdb_id, show_imdb_id, show_tmdb_id, show_tvdb_id,
            watched_at, 'plex', now_utc, duration
        ))
        
        if media_type == "episode":
            prefix = "Bulk Import" if is_bulk else "Plex PUSH"
            print(f"✅ {prefix} (Nuevo): Serie '{show_title}' T{season}E{episode} - {title}")
        else:
            prefix = "Bulk Import" if is_bulk else "Plex PUSH"
            print(f"✅ {prefix} (Nuevo): Película '{title}'")
            
    try:
        db_id = existing_id if existing_id else cursor.lastrowid
        target_tmdb = tmdb_id if media_type == "movie" else show_tmdb_id
        if not target_tmdb and media_type == "episode": target_tmdb = tmdb_id # Fallback
        if target_tmdb:
            p_path, f_path = download_tmdb_images_sync(target_tmdb, "tv" if media_type == "episode" else "movie")
            if p_path or f_path:
                cursor.execute("UPDATE watch_history SET poster_path=COALESCE(?, poster_path), fanart_path=COALESCE(?, fanart_path) WHERE id=?", (p_path, f_path, db_id))
    except Exception as e:
        print(f"Error asignando carátulas Plex: {e}")
            
    return True

@app.post("/webhook/plex", dependencies=[Depends(verify_webhook_token)])
async def plex_webhook(request: Request):
    form = await request.form()
    payload_str = form.get("payload")
    if not payload_str: return {"status": "ignored"}
        
    payload = json.loads(payload_str)
    
    conn = sqlite3.connect("sync.db")
    cursor = conn.cursor()
    if process_plex_payload(payload, cursor):
        conn.commit()
    conn.close()
    
    return {"status": "success"}

@app.post("/webhook/kodi", dependencies=[Depends(verify_webhook_token)])
async def kodi_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}
        
    conn = sqlite3.connect("sync.db")
    cursor = conn.cursor()
    if process_kodi_payload(payload, cursor):
        conn.commit()
    conn.close()
    
    return {"status": "success"}

def process_kodi_payload(payload, cursor, is_bulk=False):
    if payload.get("event") != "media.scrobble": return False
        
    metadata = payload.get("Metadata", {})
    media_type = metadata.get("type") 
    title = metadata.get("title")
    kodi_id = metadata.get("kodi_id")
    
    watched_at_payload = metadata.get("watched_at")
    is_live_event = not watched_at_payload and not is_bulk
    
    watched_at = watched_at_payload
    if not watched_at:
        watched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    duration = int(metadata.get("duration", 0)) // 60
    
    unique_ids = metadata.get("unique_ids", {})
    imdb_id = unique_ids.get("imdb") or metadata.get("imdbnumber")
    tmdb_id = unique_ids.get("tmdb")
    tvdb_id = unique_ids.get("tvdb")
    
    if imdb_id and not tmdb_id and not tvdb_id and not imdb_id.startswith("tt"): 
        tmdb_id = imdb_id
        imdb_id = None
    
    show_title = season = episode = kodi_show_id = None
    show_imdb_id = show_tmdb_id = show_tvdb_id = None
    
    if media_type == "episode":
        show_title = metadata.get("grandparentTitle")
        season = metadata.get("parentIndex")
        episode = metadata.get("index")
        kodi_show_id = metadata.get("kodi_show_id")
        
        show_unique_ids = metadata.get("show_unique_ids", {})
        show_imdb_id = show_unique_ids.get("imdb")
        show_tmdb_id = show_unique_ids.get("tmdb")
        show_tvdb_id = show_unique_ids.get("tvdb")

    existing_id = None
    if media_type == "episode":
        if tmdb_id:
            cursor.execute("SELECT id FROM watch_history WHERE media_type='episode' AND tmdb_id=?", (tmdb_id,))
        else:
            cursor.execute("SELECT id FROM watch_history WHERE media_type='episode' AND show_title=? AND season=? AND episode=?", (show_title, season, episode))
    else:
        if tmdb_id:
            cursor.execute("SELECT id FROM watch_history WHERE media_type='movie' AND tmdb_id=?", (tmdb_id,))
        else:
            cursor.execute("SELECT id FROM watch_history WHERE media_type='movie' AND title=?", (title,))
            
    row = cursor.fetchone()
    if row:
        existing_id = row[0]
        if is_live_event:
            cursor.execute("""
                UPDATE watch_history SET
                    kodi_id=?, kodi_show_id=?,
                    watched_at=?, origin='kodi', created_at=?, duration=?
                WHERE id=?
            """, (kodi_id, kodi_show_id, watched_at, now_utc, duration, existing_id))
            action = "Live Update (Re-visionado)"
        else:
            cursor.execute("""
                UPDATE watch_history SET
                    kodi_id=?, kodi_show_id=?, duration=?
                WHERE id=?
            """, (kodi_id, kodi_show_id, duration, existing_id))
            action = "Bulk Update (Solo IDs)"
            
        if media_type == "episode":
            print(f"🔄 Kodi PUSH ({action}): Serie '{show_title}' T{season}E{episode} - {title}")
        else:
            print(f"🔄 Kodi PUSH ({action}): Película '{title}'")
    else:
        cursor.execute("""
            INSERT INTO watch_history (
                media_type, title, show_title, season, episode, 
                kodi_id, kodi_show_id, 
                imdb_id, tmdb_id, tvdb_id, show_imdb_id, show_tmdb_id, show_tvdb_id, 
                watched_at, origin, created_at, duration
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            media_type, title, show_title, season, episode, 
            kodi_id, kodi_show_id,
            imdb_id, tmdb_id, tvdb_id, show_imdb_id, show_tmdb_id, show_tvdb_id,
            watched_at, 'kodi', now_utc, duration
        ))
        
        if media_type == "episode":
            print(f"✅ Kodi PUSH (Nuevo): Serie '{show_title}' T{season}E{episode} - {title}")
        else:
            print(f"✅ Kodi PUSH (Nuevo): Película '{title}'")
            
    try:
        db_id = existing_id if existing_id else cursor.lastrowid
        target_tmdb = tmdb_id if media_type == "movie" else show_tmdb_id
        if not target_tmdb and media_type == "episode": target_tmdb = tmdb_id # Fallback
        if target_tmdb:
            p_path, f_path = download_tmdb_images_sync(target_tmdb, "tv" if media_type == "episode" else "movie")
            if p_path or f_path:
                cursor.execute("UPDATE watch_history SET poster_path=COALESCE(?, poster_path), fanart_path=COALESCE(?, fanart_path) WHERE id=?", (p_path, f_path, db_id))
    except Exception as e:
        print(f"Error asignando carátulas Kodi: {e}")
            
    # Asíncronamente marcar en Plex como visto usando su API
    if not existing_id:
        import threading
        s_season = int(season) if season is not None else None
        s_episode = int(episode) if episode is not None else None
        threading.Thread(target=scrobble_single_item_to_plex, args=(title, show_title, s_season, s_episode, media_type)).start()
            
    return True

def scrobble_single_item_to_plex(title, show_title, season, episode, media_type):
    try:
        plex_movies, plex_shows = get_plex_items_map()
        if media_type == "movie":
            matched = match_movie({"movie": {"title": title}}, plex_movies)
            if matched and matched.get("viewCount", 0) == 0:
                r_key = matched.get("ratingKey")
                requests.get(f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={r_key}", headers=plex_headers)
                print(f"✅ [Direct] Marked movie in Plex: {title}")
        elif media_type == "episode":
            s_key = match_show({"show": {"title": show_title}}, plex_shows)
            if s_key:
                r_eps = requests.get(f"{PLEX_URL}/library/metadata/{s_key}/allLeaves", headers=plex_headers)
                if r_eps.status_code == 200:
                    plex_eps = r_eps.json().get("MediaContainer", {}).get("Metadata", [])
                    for pep in plex_eps:
                        if pep.get("parentIndex") == season and pep.get("index") == episode:
                            if pep.get("viewCount", 0) == 0:
                                requests.get(f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={pep['ratingKey']}", headers=plex_headers)
                                print(f"✅ [Direct] Marked episode in Plex: {show_title} T{season}E{episode} - {title}")
                            break
    except Exception as e:
        print(f"Error en scrobble_single_item_to_plex: {e}")


@app.post("/webhook/kodi/bulk", dependencies=[Depends(verify_api_key)])
async def kodi_webhook_bulk(request: Request):
    try:
        payloads = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}
        
    if not isinstance(payloads, list):
        return {"status": "error", "message": "Expected a list"}
        
    conn = sqlite3.connect("sync.db")
    cursor = conn.cursor()
    count = 0
    for p in payloads:
        if process_kodi_payload(p, cursor, is_bulk=True):
            count += 1
            
    conn.commit()
    conn.close()
    return {"status": "success", "processed": count}

@app.get("/sync/all-items", dependencies=[Depends(verify_api_key)])
def get_all_items(client: Optional[str] = Query("kodi"), date_from: Optional[str] = Query(None)):
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = f"SELECT * FROM watch_history WHERE origin != '{client}'"
    
    if date_from:
        query += f" AND created_at >= '{date_from}'"
        
    cursor.execute(query)
    rows = cursor.fetchall()
    
    movies = []
    shows = defaultdict(lambda: {"title": "", "seasons": {}})
    
    for row in rows:
        item = dict(row)
        if item["media_type"] == "movie":
            movies.append({
                "id": item["id"],
                "movie": {
                    "title": item["title"],
                    "ids": {
                        "imdb": item["imdb_id"],
                        "tmdb": item["tmdb_id"],
                        "tvdb": item["tvdb_id"]
                    }
                },
                "watched_at": item["watched_at"]
            })
        elif item["media_type"] == "episode":
            show_key = f"{item['show_title']}_{item['show_tmdb_id']}"
            if not shows[show_key]["title"]:
                shows[show_key]["title"] = item["show_title"]
                
            season_num = item["season"]
            if season_num not in shows[show_key]["seasons"]:
                shows[show_key]["seasons"][season_num] = []
                
            shows[show_key]["seasons"][season_num].append({
                "id": item["id"],
                "number": item["episode"],
                "watched_at": item["watched_at"]
            })
            
    shows_list = []
    for show_key, show_data in shows.items():
        seasons_list = []
        for season_num, eps in show_data["seasons"].items():
            seasons_list.append({
                "number": season_num,
                "episodes": eps
            })
        shows_list.append({
            "show": {"title": show_data["title"]},
            "seasons": seasons_list
        })
        
    conn.close()
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"movies": movies, "shows": shows_list, "server_time": now_utc}

@app.get("/api/time")
def get_server_time():
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"server_time": now_utc}

# --- WEB DASHBOARD ENDPOINTS ---

class LoginRequest(BaseModel):
    password: str

@app.post("/api/login")
def login(req: LoginRequest):
    if not WEB_HASH:
        return {"success": True, "token": "Basic no-auth"}
        
    pwd_hash = hashlib.sha256((req.password + SALT).encode()).hexdigest()
    if hmac.compare_digest(pwd_hash, WEB_HASH):
        return {"success": True, "token": f"Basic {req.password}"}
    raise HTTPException(status_code=401, detail="Invalid password")

@app.get("/api/history")
def get_history(limit: int = 20, offset: int = 0, type: str = "all", year: str = "all", month: str = "all", search: str = "", authorization: str = Depends(verify_api_key)):
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT * FROM watch_history WHERE 1=1"
    params = []
    
    if type != "all":
        query += " AND media_type = ?"
        params.append(type)
    
    if year != "all":
        query += " AND strftime('%Y', watched_at) = ?"
        params.append(year)
        
    if month != "all":
        # Ensure two-digit format
        month_str = str(month).zfill(2)
        query += " AND strftime('%m', watched_at) = ?"
        params.append(month_str)
        
    if search:
        query += " AND (title LIKE ? OR show_title LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
        
    query += " ORDER BY watched_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    items = [dict(row) for row in rows]
    conn.close()
    return {"items": items}

@app.get("/api/stats")
def get_stats(type: str = "all", year: str = "all", month: str = "all", search: str = "", authorization: str = Depends(verify_api_key)):
    conn = sqlite3.connect("sync.db")
    cursor = conn.cursor()
    
    base_query = " FROM watch_history WHERE 1=1"
    params = []
    
    if type != "all":
        base_query += " AND media_type = ?"
        params.append(type)
    
    if year != "all":
        base_query += " AND strftime('%Y', watched_at) = ?"
        params.append(year)
        
    if month != "all":
        month_str = str(month).zfill(2)
        base_query += " AND strftime('%m', watched_at) = ?"
        params.append(month_str)
        
    if search:
        base_query += " AND (title LIKE ? OR show_title LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    
    # Añadimos la misma condición extra para media_type
    
    # Movies stats
    cursor.execute("SELECT COUNT(*), SUM(duration)" + base_query + " AND media_type = 'movie'", params)
    row = cursor.fetchone()
    movies_count = row[0] or 0
    movies_hours = round((row[1] or 0) / 60.0, 1)
    
    # Episodes stats
    cursor.execute("SELECT COUNT(*), SUM(duration)" + base_query + " AND media_type = 'episode'", params)
    row = cursor.fetchone()
    episodes_count = row[0] or 0
    episodes_hours = round((row[1] or 0) / 60.0, 1)
    # Obtener años disponibles globales
    cursor.execute("SELECT DISTINCT strftime('%Y', watched_at) FROM watch_history WHERE watched_at IS NOT NULL ORDER BY 1 DESC")
    available_years = [str(r[0]) for r in cursor.fetchall() if r[0]]
    
    conn.close()
    
    settings = load_settings()
    return {
        "movies_count": movies_count,
        "movies_hours": movies_hours,
        "episodes_count": episodes_count,
        "episodes_hours": episodes_hours,
        "sync_state": settings.get("sync_state", 0),
        "available_years": available_years
    }

import subprocess

@app.get("/api/logs")
def get_logs(authorization: str = Depends(verify_api_key)):
    try:
        # Pide las últimas 200 líneas del servicio en Proxmox
        out = subprocess.check_output(['journalctl', '-u', 'syncpk-server', '-n', '200', '--no-pager']).decode('utf-8')
        return {"logs": out}
    except Exception as e:
        return {"logs": f"Error leyendo logs: {e}"}

@app.get("/api/download_logs")
def download_logs():
    try:
        from fastapi.responses import Response
        # Pide TODO el historial del servicio en formato texto sin paginar
        out = subprocess.check_output(['journalctl', '-u', 'syncpk-server', '--no-pager']).decode('utf-8')
        return Response(content=out, media_type="text/plain", headers={"Content-Disposition": "attachment; filename=syncpk_journal.txt"})
    except Exception as e:
        return {"error": f"Error descargando logs: {e}"}

@app.delete("/api/history/{item_id}")
def delete_history_item(item_id: int, authorization: str = Depends(verify_api_key)):
    conn = sqlite3.connect("sync.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watch_history WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return {"success": True}

class UpdateHistoryRequest(BaseModel):
    watched_at: str
    scope: Optional[str] = "episode"
    sync_remote: bool = True
    dist_mode: Optional[str] = "same"
    dist_order: Optional[str] = "asc"
    eps_per_day: Optional[int] = 1
    eps_min: Optional[int] = 1
    eps_max: Optional[int] = 3
    end_date: Optional[str] = None
    dist_between_type: Optional[str] = "fixed"

def perform_plex_surgery(item: dict, watched_at_local: str):
    plex_guid = item.get("plex_guid")
    if not plex_guid: return False
    metadata_id = plex_guid.split("/")[-1]
    watched_at_graphql = watched_at_local.replace("Z", ".000Z")
    
    headers_fetch = {
        "Accept": "application/json", "Content-Type": "application/json",
        "x-plex-client-identifier": "7o448fp80hf1p7gbvqvvaklv", "x-plex-token": PLEX_TOKEN
    }
    url_graphql = "https://community.plex.tv/api"
    query_get = """
    query GetActivityFeed($first: PaginationInt!, $metadataID: ID, $types: [ActivityType!]!, $includeDescendants: Boolean = false) {
      activityFeed(first: $first, metadataID: $metadataID, types: $types, includeDescendants: $includeDescendants) {
        nodes { id }
      }
    }
    """
    payload_get = {
        "query": query_get,
        "variables": {"first": 24, "types": ["WATCH_HISTORY", "WATCH_SESSION"], "includeDescendants": True, "metadataID": metadata_id},
        "operationName": "GetActivityFeed"
    }
    
    node_id = None
    for intento in range(3):
        try:
            r = requests.post(url_graphql, headers=headers_fetch, json=payload_get, timeout=20)
            if r.status_code == 200:
                nodes = r.json().get("data", {}).get("activityFeed", {}).get("nodes", [])
                if nodes: node_id = nodes[0]["id"]
                break
            elif r.status_code == 429:
                time.sleep(5)
            else:
                break
        except Exception:
            time.sleep(2)
            
    if not node_id:
        print(f"No node for {item.get('title')}, triggering scrobble...")
        scrobble_single_item_to_plex(item.get("title"), item.get("show_title"), item.get("season"), item.get("episode"), item.get("media_type"))
        time.sleep(2)
        for intento in range(3):
            try:
                r = requests.post(url_graphql, headers=headers_fetch, json=payload_get, timeout=20)
                if r.status_code == 200:
                    nodes = r.json().get("data", {}).get("activityFeed", {}).get("nodes", [])
                    if nodes: node_id = nodes[0]["id"]
                    break
            except Exception:
                time.sleep(2)
                
    if node_id:
        headers_mutate = dict(headers_fetch)
        headers_mutate["origin"] = "https://app.plex.tv"
        mutation_graphql = """
        mutation updateActivityDate($id: ID!, $input: UpdateActivityInput!) {
          updateActivity(id: $id, input: $input) { id }
        }
        """
        payload_mut = {
            "query": mutation_graphql, "variables": {"id": node_id, "input": {"date": watched_at_graphql}}, "operationName": "updateActivityDate"
        }
        try:
            requests.post(url_graphql, headers=headers_mutate, json=payload_mut, timeout=20)
            print(f"💉 Surgery success in Plex Cloud for {item.get('title')} -> {watched_at_local}")
            return True
        except Exception as e:
            print(f"❌ Surgery failed for {item.get('title')}: {e}")
    else:
        print(f"❌ Surgery failed: Could not get Activity Node for {item.get('title')}")
    return False

@app.put("/api/history/{item_id}")
def update_history_item(item_id: int, req: UpdateHistoryRequest, authorization: str = Depends(verify_api_key)):
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get original item
    cursor.execute("SELECT * FROM watch_history WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
        
    scope = req.scope or "episode"
    
    # 1. Fetch all items that will be modified
    items_to_modify = []
    if item["media_type"] == "episode":
        if scope == "episode":
            cursor.execute("SELECT * FROM watch_history WHERE id = ?", (item_id,))
        elif scope == "show":
            cursor.execute("SELECT * FROM watch_history WHERE show_title = ?", (item["show_title"],))
        elif scope == "season":
            cursor.execute("SELECT * FROM watch_history WHERE show_title = ? AND season = ?", (item["show_title"], item["season"]))
        elif scope == "onwards":
            cursor.execute("SELECT * FROM watch_history WHERE show_title = ? AND (season > ? OR (season = ? AND episode >= ?))", (item["show_title"], item["season"], item["season"], item["episode"]))
    else:
        cursor.execute("SELECT * FROM watch_history WHERE id = ?", (item_id,))
        
    items_to_modify = [dict(r) for r in cursor.fetchall()]
    total_items = len(items_to_modify)
    if total_items == 0:
        conn.close()
        return {"success": True}
        
    # Sort chronologically (S1E1, S1E2...)
    sorted_items = sorted(items_to_modify, key=lambda x: (x.get("season", 0), x.get("episode", 0)))
    
    # 2. Perform Plex Cloud Surgery and Update DB with new watched_at & created_at
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Clean milliseconds from frontend if present
    clean_date = req.watched_at
    if "." in clean_date:
        clean_date = clean_date.split(".")[0] + "Z"
        
    current_date = datetime.datetime.strptime(clean_date, "%Y-%m-%dT%H:%M:%SZ")
    
    # Defaults for simple edit / missing scope
    dist_mode = getattr(req, "dist_mode", "same")
    if scope == "episode": dist_mode = "same"
    
    # Determine order
    order = getattr(req, "dist_order", "asc")
    if dist_mode == "between":
        end_date = current_date
        if hasattr(req, "end_date") and req.end_date:
            clean_end = req.end_date.split(".")[0] + "Z" if "." in req.end_date else req.end_date
            end_date = datetime.datetime.strptime(clean_end, "%Y-%m-%dT%H:%M:%SZ")
        order = "asc" if end_date >= current_date else "desc"
    
    import random, math
    days_counts = []
    
    if dist_mode == "same":
        days_counts = [total_items]
    elif dist_mode == "fixed":
        eps = getattr(req, "eps_per_day", 1)
        days_counts = [eps] * (total_items // eps)
        if total_items % eps > 0: days_counts.append(total_items % eps)
    elif dist_mode == "random":
        eps_min = getattr(req, "eps_min", 1)
        eps_max = getattr(req, "eps_max", 3)
        c_sum = 0
        while c_sum < total_items:
            r = random.randint(eps_min, eps_max)
            if c_sum + r > total_items: r = total_items - c_sum
            days_counts.append(r)
            c_sum += r
    elif dist_mode == "between":
        diff = abs((end_date.date() - current_date.date()).days) + 1
        if diff <= 0: diff = 1
        btype = getattr(req, "dist_between_type", "fixed")
        if btype == "fixed":
            base_r = total_items // diff
            rem = total_items % diff
            days_counts = [base_r] * diff
            for i in range(rem): days_counts[i] += 1
        else:
            base_r = total_items / diff
            eps_max = getattr(req, "eps_max", 3)
            max_allowed = max(eps_max, math.ceil(base_r) + 1)
            days_counts = [random.randint(1, max_allowed) for _ in range(diff)]
            while sum(days_counts) > total_items:
                m_val = max(days_counts)
                idx = days_counts.index(m_val)
                days_counts[idx] -= 1
            while sum(days_counts) < total_items:
                m_val = min(days_counts)
                idx = days_counts.index(m_val)
                days_counts[idx] += 1
                
    # Generate timestamps
    assigned_dates = []
    base_date = current_date
    for count in days_counts:
        if count <= 0: continue
        if count == 1:
            assigned_dates.append(base_date)
        else:
            if order == "asc":
                avail = (24*3600) - (base_date.hour*3600 + base_date.minute*60 + base_date.second) - 60
                if avail <= 0: avail = 3600
                interval = avail / count
                for i in range(count):
                    assigned_dates.append(base_date + datetime.timedelta(seconds=int(interval * i)))
            else:
                avail = (base_date.hour*3600 + base_date.minute*60 + base_date.second) - 60
                if avail <= 0: avail = 3600
                interval = avail / count
                for i in range(count):
                    assigned_dates.append(base_date - datetime.timedelta(seconds=int(interval * i)))
                    
        if order == "asc":
            base_date += datetime.timedelta(days=1)
        else:
            base_date -= datetime.timedelta(days=1)
            
    # Apply to items
    if order == "desc":
        sorted_items.reverse()
        
    for item, assign_dt in zip(sorted_items, assigned_dates):
        watched_str = assign_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        if req.sync_remote:
            perform_plex_surgery(item, watched_str)
            cursor.execute("UPDATE watch_history SET watched_at = ?, created_at = ? WHERE id = ?", (watched_str, now_utc, item["id"]))
        else:
            cursor.execute("UPDATE watch_history SET watched_at = ? WHERE id = ?", (watched_str, item["id"]))
        
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/dismiss-sync")
def dismiss_sync(authorization: str = Depends(verify_api_key)):
    settings = load_settings()
    settings["sync_state"] = 3
    save_settings(settings)
    return {"success": True}

@app.get("/api/config")
def get_config():
    # Only return API KEY to authenticated frontend (optional) but TMDB API_KEY is not secret
    return {"tmdb_api_key": TMDB_API_KEY}

# --- PLEX SYNC BACKGROUND LOGIC ---
import requests
import time

SETTINGS_FILE = "plex_settings.json"
SYNC_INTERVAL = 900

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {"last_sync_date": ""}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

def get_plex_libraries():
    try:
        r = requests.get(f"{PLEX_URL}/library/sections", headers=plex_headers)
        if r.status_code == 200:
            data = r.json()
            sections = data.get("MediaContainer", {}).get("Directory", [])
            return [{"key": s["key"], "type": s.get("type")} for s in sections if s.get("type") in ["movie", "show"]]
    except Exception as e:
        print(f"Error getting Plex libraries: {e}")
    return []

def get_real_plex_history_map():
    print("Fetching real playback history from Plex...")
    history_map = {}
    try:
        url = f"{PLEX_URL}/status/sessions/history/all"
        r = requests.get(url, headers=plex_headers)
        if r.status_code == 200:
            data = r.json()
            sessions = data.get("MediaContainer", {}).get("Metadata", [])
            for session in sessions:
                rating_key = session.get("ratingKey")
                viewed_at = session.get("viewedAt")
                if rating_key and viewed_at:
                    if rating_key not in history_map or viewed_at < history_map[rating_key]:
                        history_map[rating_key] = viewed_at
    except Exception as e:
        print(f"Error fetching real history: {e}")
        
    oldest_timestamp = 946684800
    if history_map:
        oldest_in_map = min(history_map.values())
        oldest_timestamp = oldest_in_map - 86400
        
    return history_map, oldest_timestamp

def get_oldest_date(rating_key, metadata_id, xml_watched_at):
    fechas = []
    if xml_watched_at:
        fechas.append(xml_watched_at)
        
    # Local History API
    try:
        hist_url = f"{PLEX_URL}/status/sessions/history/all?metadataItemID={rating_key}&X-Plex-Token={PLEX_TOKEN}"
        local_headers = {"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN}
        hr = requests.get(hist_url, headers=local_headers, timeout=5)
        
        if hr.status_code == 200:
            try:
                sessions = hr.json().get("MediaContainer", {}).get("Metadata", [])
                for s in sessions:
                    vat = s.get("viewedAt")
                    if vat:
                        utc_dt = datetime.datetime.utcfromtimestamp(vat)
                        fechas.append(utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ'))
            except Exception as json_err:
                print(f"⚠️ Error parsing JSON from local history for {rating_key}: {json_err}", flush=True)
        elif hr.status_code == 401:
            print(f"❌ Access denied (401) in local API for {rating_key}. Check Token.", flush=True)
        elif hr.status_code == 404:
            print(f"ℹ️ No local history for {rating_key} (404).", flush=True)
        else:
            print(f"⚠️ Error {hr.status_code} in local history for {rating_key}: {hr.text}", flush=True)
            
    except requests.exceptions.RequestException as req_err:
        print(f"❌ Connection error to local Plex server ({PLEX_URL}): {req_err}", flush=True)
    except Exception as e:
        print(f"❌ Unknown error processing local history for {rating_key}: {e}", flush=True)
        
    # GraphQL API (Cloud)
    # [TEST id ] 7o448fp80hf1p7gbvqvvaklv 
    url = "https://community.plex.tv/api"
    headers_fetch = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-plex-client-identifier": "b8x92tz3pq1g4f7mcy6k0w5n",
        "x-plex-token": PLEX_TOKEN
    }
    query_graphql = """
    query GetActivityFeed($first: PaginationInt!, $metadataID: ID, $types: [ActivityType!]!, $includeDescendants: Boolean = false) {
      activityFeed(first: $first, metadataID: $metadataID, types: $types, includeDescendants: $includeDescendants) {
        nodes {
          date
        }
      }
    }
    """
    payload = {
        "query": query_graphql,
        "variables": {
            "first": 24,
            "types": ["METADATA_MESSAGE", "RATING", "WATCH_HISTORY", "WATCHLIST", "POST", "WATCH_SESSION", "WATCH_RATING", "REVIEW", "WATCH_REVIEW"],
            "includeDescendants": True,
            "metadataID": metadata_id
        },
        "operationName": "GetActivityFeed"
    }
    for intento in range(3):
        try:
            r = requests.post(url, headers=headers_fetch, json=payload, timeout=20)
            if r.status_code == 200:
                nodes = r.json().get("data", {}).get("activityFeed", {}).get("nodes", [])
                for n in nodes:
                    if "date" in n:
                        fechas.append(n["date"])
                break
            elif r.status_code == 429:
                print(f"⚠️ RATE LIMIT GraphQL extracting date for {metadata_id}. Pausing 5s...", flush=True)
                time.sleep(5)
            else:
                break
        except Exception as e:
            if intento == 2:
                print(f"Error GraphQL for {metadata_id} after 3 retries: {e}")
            else:
                print(f"⚠️ Timeout/Error GraphQL for {metadata_id}, retrying ({intento+1}/3)...")
                time.sleep(2)
        
    if not fechas:
        return xml_watched_at
        
    return min(fechas)

def build_payload_from_plex(item, media_type, show_map=None):
    if show_map is None: show_map = {}
    
    last_viewed_at = item.get("lastViewedAt")
    if last_viewed_at:
        utc_dt = datetime.datetime.utcfromtimestamp(last_viewed_at)
        watched_at = utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        guid = item.get("guid", "")
        metadata_id = guid.split("/")[-1]
        rating_key = item.get("ratingKey")
        
        real_date = get_oldest_date(rating_key, metadata_id, watched_at)
        if real_date and real_date != watched_at:
            print(f"🔄 Date fixed from {watched_at} to {real_date} for: {item.get('title')}", flush=True)
            watched_at = real_date
    else:
        # Fallback si no tiene lastViewedAt pero tiene viewCount
        watched_at = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        
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
            "watched_at": watched_at,
            "duration": item.get("duration", 0)
        }
    }
    
    if media_type == "episode":
        payload["Metadata"]["grandparentTitle"] = item.get("grandparentTitle")
        payload["Metadata"]["parentIndex"] = item.get("parentIndex")
        payload["Metadata"]["index"] = item.get("index")
        payload["Metadata"]["grandparentKey"] = item.get("grandparentKey")
        
        # Recuperar IDs globales del Show usando el show_map (Fase A)
        parent_key = item.get("grandparentRatingKey")
        if parent_key and parent_key in show_map:
            show_data = show_map[parent_key]
            payload["Metadata"]["grandparentGuid"] = show_data["guid"]
            payload["Metadata"]["grandparentGuids"] = show_data["Guid"]
        else:
            if "grandparentGuid" in item:
                payload["Metadata"]["grandparentGuid"] = item.get("grandparentGuid")
            if "grandparentGuids" in item:
                payload["Metadata"]["grandparentGuids"] = item.get("grandparentGuids")
        
    return payload

def push_all_to_db():
    print("Starting FULL PUSH from Plex to local DB (Library Scan)...")
    
    # Check for test limit
    test_limit = 0
    if os.path.exists("sync_limit.txt"):
        try:
            with open("sync_limit.txt", "r") as f:
                test_limit = int(f.read().strip())
            print(f"⚠️ TEST MODE ACTIVE: Limiting to {test_limit} items.")
        except Exception:
            pass

    payloads = []
    sections = get_plex_libraries()
    
    # Fase A: Mapeo en Memoria de todas las Series (con paginación)
    show_map = {}
    for sec in sections:
        if sec["type"] == "show":
            start = 0
            size = 500
            while True:
                headers = plex_headers.copy()
                headers["X-Plex-Container-Start"] = str(start)
                headers["X-Plex-Container-Size"] = str(size)
                try:
                    r = requests.get(f"{PLEX_URL}/library/sections/{sec['key']}/all?includeGuids=1", headers=headers, timeout=60)
                    if r.status_code == 200:
                        items = r.json().get("MediaContainer", {}).get("Metadata", [])
                        if not items: break
                        for item in items:
                            show_map[item.get("ratingKey")] = {
                                "guid": item.get("guid"),
                                "Guid": item.get("Guid", [])
                            }
                        start += size
                    else:
                        break
                except Exception as e:
                    print(f"Error mapping shows in section {sec['key']}: {e}")
                    break

    # Fase B: Extracción e Inserción Inmediata
    conn = sqlite3.connect("sync.db")
    cursor = conn.cursor()
    count = 0
    seen_keys = set()
    
    for sec in sections:
        start = 0
        size = 500
        sec_processed = 0
        limit_reached = False
        
        while True:
            if limit_reached: break
            headers = plex_headers.copy()
            headers["X-Plex-Container-Start"] = str(start)
            headers["X-Plex-Container-Size"] = str(size)
            
            try:
                if sec["type"] == "movie":
                    r = requests.get(f"{PLEX_URL}/library/sections/{sec['key']}/all?includeGuids=1", headers=headers, timeout=60)
                elif sec["type"] == "show":
                    r = requests.get(f"{PLEX_URL}/library/sections/{sec['key']}/all?type=4&includeGuids=1", headers=headers, timeout=60)
                else:
                    break
                    
                if r.status_code == 200:
                    items = r.json().get("MediaContainer", {}).get("Metadata", [])
                    if not items: break
                    
                    for item in items:
                        if item.get("viewCount", 0) > 0:
                            actual_media_type = "movie" if sec["type"] == "movie" else "episode"
                            
                            if sec["type"] == "movie":
                                dedup_key = ("movie", item.get("title"))
                            else:
                                dedup_key = ("episode", item.get("grandparentTitle"), item.get("parentIndex"), item.get("index"))
                                
                            if dedup_key in seen_keys:
                                continue
                            seen_keys.add(dedup_key)
                                
                            p = build_payload_from_plex(item, actual_media_type, show_map)
                            
                            if process_plex_payload(p, cursor, is_bulk=True):
                                count += 1
                                
                            # Escribimos en BD al vuelo para verlo en el dashboard en tiempo real
                            conn.commit()
                            
                            sec_processed += 1
                            if test_limit > 0 and sec_processed >= test_limit:
                                limit_reached = True
                                break
                    start += size
                else:
                    break
            except Exception as e:
                print(f"Error scanning section {sec['key']}: {e}")
                break
            
    conn.close()
    print(f"✅ Initial Sync completed! {count} items processed.")
    
    if os.path.exists("_DUPLICATE_FIX"):
        try:
            os.remove("_DUPLICATE_FIX")
            print("🗑️ Archivo _DUPLICATE_FIX borrado tras finalizar la carga inicial.")
        except Exception as e:
            print(f"Error borrando _DUPLICATE_FIX: {e}")

def push_recent_to_db(last_sync_utc_str):
    print("Starting INCREMENTAL PUSH from Plex to local DB...")
    try:
        last_sync_dt = datetime.datetime.strptime(last_sync_utc_str, '%Y-%m-%dT%H:%M:%SZ')
        last_sync_ts = int(last_sync_dt.replace(tzinfo=datetime.timezone.utc).timestamp())
    except Exception:
        last_sync_ts = 0
        
    url = f"{PLEX_URL}/status/sessions/history/all"
    r = requests.get(url, headers=plex_headers)
    if r.status_code != 200: return
    
    sessions = r.json().get("MediaContainer", {}).get("Metadata", [])
    recent_sessions = [s for s in sessions if s.get("viewedAt", 0) >= last_sync_ts]
    
    if not recent_sessions:
        print("No new watches in Plex since last sync.")
        return
        
    print(f"Found {len(recent_sessions)} recent watches in Plex. Processing...")
    
    payloads = []
    for session in recent_sessions:
        r_key = session.get("ratingKey")
        viewed_at = session.get("viewedAt")
        if not r_key or not viewed_at: continue
            
        try:
            det_r = requests.get(f"{PLEX_URL}/library/metadata/{r_key}", headers=plex_headers)
            if det_r.status_code == 200:
                item_data = det_r.json().get("MediaContainer", {}).get("Metadata", [])[0]
                m_type = item_data.get("type")
                if m_type in ["movie", "episode"]:
                    history_map = {r_key: viewed_at}
                    payload = build_payload_from_plex(item_data, m_type, history_map, viewed_at)
                    payloads.append(payload)
        except Exception as e:
            pass
            
    if payloads:
        conn = sqlite3.connect("sync.db")
        cursor = conn.cursor()
        for p in payloads:
            process_plex_payload(p, cursor, is_bulk=False)
        conn.commit()
        conn.close()
        print(f"🚀 Incremental items processed.")

def get_plex_items_map():
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
        except Exception:
            pass
    return plex_movies, plex_shows

def match_movie(movie_data, plex_movies):
    title = movie_data.get("movie", {}).get("title", "").lower()
    for pm in plex_movies:
        if pm.get("title", "").lower() == title: return pm
    return None

def match_show(show_data, plex_shows):
    title = show_data.get("show", {}).get("title", "").lower()
    for ps in plex_shows:
        if ps.get("title", "").lower() == title: return ps.get("ratingKey")
    return None



def push_cloud_orphans_to_db():
    print("Starting SMART EXTRACTOR V2 from Plex Cloud to local DB...")
    
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Pre-load existing IDs by GUID to check for collisions
    cursor.execute("SELECT id, plex_guid, watched_at FROM watch_history WHERE plex_guid IS NOT NULL")
    local_items_by_guid = {}
    for r in cursor.fetchall():
        if r["plex_guid"]:
            local_items_by_guid[r["plex_guid"]] = {"id": r["id"], "watched_at": r["watched_at"]}
            
    url_graphql = "https://community.plex.tv/api"
    headers_fetch = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-plex-client-identifier": "7o448fp80hf1p7gbvqvvaklv",
        "x-plex-token": PLEX_TOKEN
    }
    
    query_primary = """
    query GetActivityFeed($first: PaginationInt!, $after: String, $types: [ActivityType!]!) {
      activityFeed(first: $first, after: $after, types: $types) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id date __typename
          metadataItem { 
             __typename title type guid index 
             parent { index title }
             grandparent { title guid }
          }
        }
      }
    }
    """
    
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
          date
          metadataItem {
            title type index guid
            parent { index title }
            grandparent { title guid }
          }
        }
        pageInfo { endCursor hasNextPage }
      }
    }
    """
    
    has_next = True
    page_cursor = None
    lang = os.getenv("SYNC_LANGUAGE", "en")
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    count_orphans = 0
    count_updates = 0
    
    processed_shows = set()
    
    def process_item_node(node, is_secondary=False):
        nonlocal count_orphans, count_updates
        cloud_date = node.get("date")
        meta = node.get("metadataItem")
        if not meta or not meta.get("guid"): return
        
        guid = meta.get("guid")
        
        if guid in local_items_by_guid:
            local_date = local_items_by_guid[guid]["watched_at"]
            if cloud_date and cloud_date < local_date:
                print(f"⬇️ Updating date from {local_date} to {cloud_date} for {meta.get('title')}")
                cursor.execute("UPDATE watch_history SET watched_at = ?, created_at = ? WHERE id = ?", (cloud_date, now_utc, local_items_by_guid[guid]["id"]))
                local_items_by_guid[guid]["watched_at"] = cloud_date
                count_updates += 1
                conn.commit()
        else:
            print(f"🌟 Orphan detected in Cloud: {meta.get('title')} ({cloud_date})")
            try:
                plex_metadata_id = guid.split("/")[-1]
                meta_url = f"https://metadata.provider.plex.tv/library/metadata/{plex_metadata_id}?X-Plex-Token={PLEX_TOKEN}&X-Plex-Language={lang}"
                m_resp = requests.get(meta_url, headers={"Accept": "application/json"}, timeout=10)
                if m_resp.status_code == 200:
                    m_data = m_resp.json().get("MediaContainer", {}).get("Metadata", [])
                    if m_data:
                        item = m_data[0]
                        m_type = item.get("type")
                        if m_type not in ["movie", "episode"]:
                            print(f"⚠️ Ignorando huérfano global (tipo '{m_type}'): {meta.get('title')}")
                            return
                            
                        actual_media_type = m_type
                        
                        p = build_payload_from_plex(item, actual_media_type)
                        p["Metadata"]["watched_at"] = cloud_date 
                        
                        if actual_media_type == "episode" and "grandparentGuid" in item:
                            gp_guid = item["grandparentGuid"]
                            gp_id = gp_guid.split("/")[-1]
                            try:
                                gp_url = f"https://metadata.provider.plex.tv/library/metadata/{gp_id}?X-Plex-Token={PLEX_TOKEN}"
                                gp_resp = requests.get(gp_url, headers={"Accept": "application/json"}, timeout=5)
                                if gp_resp.status_code == 200:
                                    gp_data = gp_resp.json().get("MediaContainer", {}).get("Metadata", [])
                                    if gp_data:
                                        p["Metadata"]["grandparentGuids"] = gp_data[0].get("Guid", [])
                            except Exception:
                                pass
                        
                        if process_plex_payload(p, cursor, is_bulk=True):
                            conn.commit()
                            count_orphans += 1
                            # Cache it to avoid retrying in the current loop
                            cursor.execute("SELECT id FROM watch_history WHERE plex_guid=?", (guid,))
                            new_r = cursor.fetchone()
                            if new_r:
                                local_items_by_guid[guid] = {"id": new_r["id"], "watched_at": cloud_date}
            except Exception as e:
                print(f"❌ Error rescuing orphan {guid}: {e}")

    def fetch_show_history_db(show_id, show_title):
        h_next = True
        p_cursor = None
        print(f"\n📡 [API] Obteniendo historial completo de la serie: {show_title} ({show_id})...")
        while h_next:
            payload_sec = {
                "query": query_secondary,
                "variables": {
                    "first": 50,
                    "after": p_cursor,
                    "types": ["WATCH_HISTORY"],
                    "includeDescendants": True,
                    "metadataID": show_id
                },
                "operationName": "GetActivityFeed"
            }
            try:
                resp_sec = requests.post(url_graphql, headers=headers_fetch, json=payload_sec, timeout=30)
                if resp_sec.status_code == 429:
                    retry = int(resp_sec.headers.get("Retry-After", "60"))
                    print(f"⏳ [429] Esperando {retry}s...")
                    time.sleep(retry)
                    continue
                if resp_sec.status_code != 200:
                    break
                data_sec = resp_sec.json().get("data", {}).get("activityFeed", {})
                nodes_sec = data_sec.get("nodes", [])
                for n_sec in nodes_sec:
                    m_sec = n_sec.get("metadataItem")
                    if m_sec and m_sec.get("type") == "EPISODE":
                        process_item_node(n_sec, is_secondary=True)
                p_info = data_sec.get("pageInfo", {})
                h_next = p_info.get("hasNextPage", False)
                p_cursor = p_info.get("endCursor")
                time.sleep(1)
            except Exception as e:
                print(f"Error fetching show history: {e}")
                break

    while has_next:
        payload = {
            "query": query_primary,
            "variables": {"first": 50, "after": page_cursor, "types": ["WATCH_HISTORY", "WATCH_SESSION"]},
            "operationName": "GetActivityFeed"
        }
        
        try:
            resp = requests.post(url_graphql, headers=headers_fetch, json=payload, timeout=20)
            if resp.status_code == 429:
                time.sleep(5)
                continue
            if resp.status_code != 200:
                print(f"Error {resp.status_code} fetching from Plex Cloud.")
                break
                
            data = resp.json().get("data", {}).get("activityFeed", {})
            nodes = data.get("nodes", [])
            page_info = data.get("pageInfo", {})
            
            if not nodes: break
            
            for node in nodes:
                meta = node.get("metadataItem")
                if not meta: continue
                m_type = meta.get("type")
                
                if m_type == "MOVIE":
                    process_item_node(node)
                elif m_type == "EPISODE":
                    gp = meta.get("grandparent")
                    if not gp:
                        process_item_node(node)
                        continue
                    show_guid = gp.get("guid")
                    show_title = gp.get("title")
                    if not show_guid:
                        process_item_node(node)
                        continue
                    show_id = show_guid.split("/")[-1]
                    if show_id in processed_shows:
                        continue
                    processed_shows.add(show_id)
                    fetch_show_history_db(show_id, show_title)
                elif m_type == "SHOW":
                    show_guid = meta.get("guid")
                    show_title = meta.get("title")
                    if not show_guid:
                        continue
                    show_id = show_guid.split("/")[-1]
                    if show_id in processed_shows:
                        continue
                    processed_shows.add(show_id)
                    fetch_show_history_db(show_id, show_title)
                    
            has_next = page_info.get("hasNextPage", False)
            page_cursor = page_info.get("endCursor")
            time.sleep(1)
            
        except Exception as e:
            print(f"Error connecting to GraphQL Plex Cloud: {e}")
            break
            
    conn.close()
    print(f"✅ Smart Extractor V2 completed! Inserted {count_orphans} orphans and updated {count_updates} dates.")


def run_sync():
    settings = load_settings()
    last_sync = settings.get("last_sync_date")
    is_first_sync = not last_sync
    
    if is_first_sync:
        push_all_to_db()
        push_cloud_orphans_to_db()

    else:
        push_recent_to_db(last_sync)
        
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    settings["last_sync_date"] = now_utc
    save_settings(settings)
    print(f"Sync completed. Date updated: {now_utc}")

async def sync_loop():
    while True:
        try:
            run_sync()
        except Exception as e:
            print(f"Error in sync loop: {e}")
        print(f"Sleeping {SYNC_INTERVAL} seconds...")
        await asyncio.sleep(SYNC_INTERVAL)

async def background_initial_task():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_sync)
    # Una vez terminada la carga en BD, descargamos las imágenes asíncronamente
    await bulk_download_tmdb_images()

@app.on_event("startup")
async def startup_event():
    init_db()
    
    settings = load_settings()
    last_sync = settings.get("last_sync_date")
    
    if not last_sync:
        print("First time setup: Triggering initial sync in background...")
        settings["sync_state"] = 1
        save_settings(settings)
        asyncio.create_task(background_initial_task())
        
    if not HAS_PLEX_PASS:
        print("Plex Pass NOT detected: Starting incremental sync loop...")
        asyncio.create_task(sync_loop())
    else:
        print("Plex Pass detected: Incremental sync loop disabled. Relying purely on webhooks.")
class ManualAddRequest(BaseModel):
    tmdb_id: int
    media_type: str
    title: str
    watched_at: str
    sync_remote: bool
    season: Optional[int] = None
    episode: Optional[int] = None

@app.get("/api/tmdb/search")
def search_tmdb(q: str, lang: str = "es", authorization: str = Depends(verify_api_key)):
    if not q or len(q) < 3:
        return {"results": []}
    
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&language={lang}&query={q}&page=1&include_adult=false"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            results = res.json().get("results", [])
            # Filter only movies and tv shows
            filtered = [r for r in results if r.get("media_type") in ["movie", "tv"]]
            return {"results": filtered}
    except Exception as e:
        print(f"Error searching TMDB: {e}")
    return {"results": []}

@app.post("/api/manual_add")
def manual_add(req: ManualAddRequest, authorization: str = Depends(verify_api_key)):
    try:
        # PLEX CLOUD WORKAROUND (Phase 1)
        plex_guid = None
        if req.sync_remote:
            try:
                # 1. Match item in Plex Cloud
                match_url = f"{PLEX_URL}/library/metadata/matches"
                params = {"title": req.title}
                if req.media_type == "movie":
                    params["type"] = "1"
                elif req.media_type == "episode":
                    params["type"] = "4" # Or 2 for show, but let's try title match
                
                # Fetch matching to find plex:// guid
                # Actually, a simpler way to match is using the TMDB ID!
                # provider.plex.tv allows searching by tmdb id:
                guid_query = f"tmdb://{req.tmdb_id}"
                
                # Let's search using the standard /library/metadata/matches
                params = {"title": req.title, "guid": guid_query}
                if req.media_type == "movie":
                    params["type"] = "1"
                else:
                    params["type"] = "2" # Search for the show first
                    
                print(f"Buscando {req.title} en Plex Cloud...")
                m_res = requests.get(match_url, headers=plex_headers, params=params, timeout=10)
                
                target_rating_key = None
                
                if m_res.status_code == 200:
                    matches = m_res.json().get("MediaContainer", {}).get("Metadata", [])
                    if matches:
                        # Found the movie or show
                        if req.media_type == "movie":
                            target_rating_key = matches[0].get("ratingKey")
                            plex_guid = matches[0].get("guid")
                        else:
                            # It's a show, we need to find the specific episode
                            show_key = matches[0].get("ratingKey")
                            ep_url = f"{PLEX_URL}/library/metadata/{show_key}/allLeaves"
                            ep_res = requests.get(ep_url, headers=plex_headers, timeout=10)
                            if ep_res.status_code == 200:
                                eps = ep_res.json().get("MediaContainer", {}).get("Metadata", [])
                                for ep in eps:
                                    if ep.get("parentIndex") == req.season and ep.get("index") == req.episode:
                                        target_rating_key = ep.get("ratingKey")
                                        plex_guid = ep.get("guid")
                                        break
                
                if target_rating_key:
                    # 2. Fake Scrobble (Records as "Today")
                    scrobble_url = f"{PLEX_URL}/:/scrobble?identifier=tv.plex.provider.metadata&key={target_rating_key}"
                    s_res = requests.get(scrobble_url, headers=plex_headers, timeout=10)
                    print(f"Scrobble ejecutado para {req.title}: {s_res.status_code}")
                    
                    time.sleep(2) # Esperamos 2 segundos para que se asiente en la base de datos de Plex
                    
                    # 3. Fetch Activity ID
                    query_activity = """
                    query GetActivityFeed($first: Int!) {
                      user {
                        activityFeed(first: $first) {
                          edges {
                            node {
                              id
                              metadata { title guid }
                            }
                          }
                        }
                      }
                    }
                    """
                    headers_graphql = {
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "X-Plex-Token": PLEX_TOKEN
                    }
                    payload_q = {"query": query_activity, "variables": {"first": 15}}
                    a_res = requests.post("https://community.plex.tv/api/graphql", headers=headers_graphql, json=payload_q, timeout=10)
                    
                    activity_id = None
                    if a_res.status_code == 200:
                        edges = a_res.json().get("data", {}).get("user", {}).get("activityFeed", {}).get("edges", [])
                        for edge in edges:
                            node = edge.get("node", {})
                            meta = node.get("metadata")
                            if meta and meta.get("guid") == plex_guid:
                                activity_id = node.get("id")
                                break
                                
                    # 4. GraphQL Date Surgery
                    if activity_id:
                        mutation_graphql = """
                        mutation updateActivityDate($id: ID!, $input: UpdateActivityInput!) {
                          updateActivity(id: $id, input: $input) { id }
                        }
                        """
                        # Convert to GraphQL Date format (YYYY-MM-DDThh:mm:ss.000Z)
                        d_obj = datetime.datetime.fromisoformat(req.watched_at.replace('Z', '+00:00'))
                        watched_at_graphql = d_obj.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                        
                        payload_mut = {
                            "query": mutation_graphql, 
                            "variables": {"id": activity_id, "input": {"date": watched_at_graphql}}, 
                            "operationName": "updateActivityDate"
                        }
                        
                        m_res = requests.post("https://community.plex.tv/api/graphql", headers=headers_graphql, json=payload_mut, timeout=10)
                        print(f"Cirugía temporal aplicada para {req.title}: {m_res.status_code}")
                    else:
                        print(f"No se encontró el Activity ID para hacer la cirugía.")
                else:
                    print(f"No se encontró el recurso en Plex Cloud para hacer scrobble.")
            except Exception as e:
                print(f"Error en Phase 1 (Plex Sync): {e}")

        # TMDB CACHE (Phase 2)
        poster_path = None
        fanart_path = None
        try:
            p_path, f_path = download_tmdb_images_sync(req.tmdb_id, "tv" if req.media_type == "episode" else "movie")
            poster_path = p_path
            fanart_path = f_path
        except Exception as e:
            print(f"Error descargando imágenes: {e}")

        # LOCAL DB INSERTION (Phase 3)
        conn = sqlite3.connect("sync.db")
        cursor = conn.cursor()
        
        # We save it as UTC string
        d_obj = datetime.datetime.fromisoformat(req.watched_at.replace('Z', '+00:00'))
        final_watched_at = d_obj.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        if req.media_type == "movie":
            cursor.execute("""
                INSERT INTO watch_history (origin, title, media_type, tmdb_id, watched_at, poster_path, fanart_path, plex_guid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ("manual", req.title, req.media_type, req.tmdb_id, final_watched_at, poster_path, fanart_path, plex_guid))
        else:
            # Assuming title from request is Show Title if it's an episode... 
            # Wait, the search result title is the show title. We don't know the episode title easily without another TMDB call.
            # Let's save it as "Episodio X" or fetch it if we want.
            ep_title = f"Episodio {req.episode}"
            cursor.execute("""
                INSERT INTO watch_history (origin, title, show_title, media_type, show_tmdb_id, season, episode, watched_at, poster_path, fanart_path, plex_guid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("manual", ep_title, req.title, req.media_type, req.tmdb_id, req.season, req.episode, final_watched_at, poster_path, fanart_path, plex_guid))
            
        conn.commit()
        conn.close()
        
        return {"status": "success"}
    except Exception as e:
        print(f"Error en manual_add: {e}")
        return {"status": "error", "message": str(e)}

# --- SERVE FRONTEND ---
# Mount the static folder at the end to avoid overwriting routes /api/
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

