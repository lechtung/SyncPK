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
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# --- PLEX & SECURITY CONFIGURATION ---
PLEX_URL = os.getenv("PLEX_URL", "http://192.168.178.21:32400")
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
    
    token = authorization.split(" ")[1]
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

async def download_tmdb_images(db_id, tmdb_id, media_type):
    if not TMDB_API_KEY or not tmdb_id:
        return
        
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
                    
            if poster_local or fanart_local:
                conn = sqlite3.connect("sync.db")
                cursor = conn.cursor()
                if poster_local and fanart_local:
                    cursor.execute("UPDATE watch_history SET poster_path=?, fanart_path=? WHERE id=?", (poster_local, fanart_local, db_id))
                elif poster_local:
                    cursor.execute("UPDATE watch_history SET poster_path=? WHERE id=?", (poster_local, db_id))
                elif fanart_local:
                    cursor.execute("UPDATE watch_history SET fanart_path=? WHERE id=?", (fanart_local, db_id))
                conn.commit()
                conn.close()
                print(f"✅ Descargadas y Cacheadas imágenes de {media_type} {tmdb_id}")
    except Exception as e:
        print(f"Error downloading TMDB images for {tmdb_id}: {e}")

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
            root = ET.fromstring(response.read())
            directory = root.find(".//Directory")
            if directory is not None:
                guids = [{"id": g.get("id")} for g in directory.findall("Guid")]
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
        
        if grandparent_key:
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
            print(f"🔄 Plex PUSH ({action}): Serie '{show_title}' T{season}E{episode}")
        else:
            print(f"🔄 Plex PUSH ({action}): Película '{title}'")
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
            print(f"✅ Plex PUSH (Nuevo): Serie '{show_title}' T{season}E{episode}")
        else:
            print(f"✅ Plex PUSH (Nuevo): Película '{title}'")
            
    try:
        db_id = existing_id if existing_id else cursor.lastrowid
        loop = asyncio.get_running_loop()
        target_tmdb = tmdb_id if media_type == "movie" else show_tmdb_id
        if not target_tmdb and media_type == "episode": target_tmdb = tmdb_id # Fallback
        if target_tmdb:
            loop.create_task(download_tmdb_images(db_id, target_tmdb, "tv" if media_type == "episode" else "movie"))
    except RuntimeError:
        pass
            
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
            print(f"🔄 Kodi PUSH ({action}): Serie '{show_title}' T{season}E{episode}")
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
            print(f"✅ Kodi PUSH (Nuevo): Serie '{show_title}' T{season}E{episode}")
        else:
            print(f"✅ Kodi PUSH (Nuevo): Película '{title}'")
            
    try:
        db_id = existing_id if existing_id else cursor.lastrowid
        loop = asyncio.get_running_loop()
        target_tmdb = tmdb_id if media_type == "movie" else show_tmdb_id
        if not target_tmdb and media_type == "episode": target_tmdb = tmdb_id # Fallback
        if target_tmdb:
            loop.create_task(download_tmdb_images(db_id, target_tmdb, "tv" if media_type == "episode" else "movie"))
    except RuntimeError:
        pass
            
    # Asíncronamente marcar en Plex como visto usando su API
    if kodi_id:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(push_scrobble_to_plex(tmdb_id, title, show_title, season, episode, media_type))
        except RuntimeError:
            pass
            
    return True

async def push_scrobble_to_plex(tmdb_id, title, show_title, season, episode, media_type):
    # Lógica para hacer match en Plex y marcar como visto
    pass


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
    return {"movies": movies, "shows": shows_list}

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
    
    conn.close()
    return {
        "movies_count": movies_count,
        "movies_hours": movies_hours,
        "episodes_count": episodes_count,
        "episodes_hours": episodes_hours
    }

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
    
    if item["media_type"] == "episode":
        if scope == "episode":
            cursor.execute("UPDATE watch_history SET watched_at = ? WHERE id = ?", (req.watched_at, item_id))
        elif scope == "show":
            cursor.execute("UPDATE watch_history SET watched_at = ? WHERE show_title = ?", (req.watched_at, item["show_title"]))
        elif scope == "season":
            cursor.execute("UPDATE watch_history SET watched_at = ? WHERE show_title = ? AND season = ?", (req.watched_at, item["show_title"], item["season"]))
        elif scope == "onwards":
            cursor.execute("UPDATE watch_history SET watched_at = ? WHERE show_title = ? AND (season > ? OR (season = ? AND episode >= ?))", (req.watched_at, item["show_title"], item["season"], item["season"], item["episode"]))
    else:
        # Movies only support single update
        cursor.execute("UPDATE watch_history SET watched_at = ? WHERE id = ?", (req.watched_at, item_id))
        
    conn.commit()
    conn.close()
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

def build_payload_from_plex(item, media_type, show_map=None):
    if show_map is None: show_map = {}
    
    last_viewed_at = item.get("lastViewedAt")
    if last_viewed_at:
        utc_dt = datetime.datetime.utcfromtimestamp(last_viewed_at)
        watched_at = utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
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
            # En process_plex_payload, grandparentKey lanza una consulta de red `get_show_ids_from_plex` si queremos, 
            # pero dado que ya los tenemos aquí, podemos emular que los inyectamos (process_plex_payload espera leer grandparentKey de la API de Plex, 
            # pero podemos mejorar esto para evitar que process_plex_payload haga solicitudes HTTP innecesarias).
        
    return payload

def push_all_to_db():
    print("Starting FULL PUSH from Plex to local DB (Library Scan)...")
    payloads = []
    sections = get_plex_libraries()
    
    # Fase A: Mapeo en Memoria de todas las Series
    show_map = {}
    for sec in sections:
        if sec["type"] == "show":
            try:
                r = requests.get(f"{PLEX_URL}/library/sections/{sec['key']}/all?includeGuids=1", headers=plex_headers, timeout=60)
                if r.status_code == 200:
                    items = r.json().get("MediaContainer", {}).get("Metadata", [])
                    for item in items:
                        show_map[item.get("ratingKey")] = {
                            "guid": item.get("guid"),
                            "Guid": item.get("Guid", [])
                        }
            except Exception as e:
                print(f"Error mapping shows in section {sec['key']}: {e}")

    # Fase B: Extracción de Películas y Episodios Vistos
    # Deduplicación en memoria por (media_type, ID)
    seen_items = {}
    
    for sec in sections:
        try:
            if sec["type"] == "movie":
                r = requests.get(f"{PLEX_URL}/library/sections/{sec['key']}/all?includeGuids=1", headers=plex_headers, timeout=120)
                if r.status_code == 200:
                    items = r.json().get("MediaContainer", {}).get("Metadata", [])
                    for item in items:
                        if item.get("viewCount", 0) > 0:
                            p = build_payload_from_plex(item, "movie", show_map)
                            dedup_key = ("movie", item.get("title"))
                            seen_items[dedup_key] = p
                            
            elif sec["type"] == "show":
                # Timeout masivo para parsear todos los episodios de golpe
                r = requests.get(f"{PLEX_URL}/library/sections/{sec['key']}/all?type=4&includeGuids=1", headers=plex_headers, timeout=300)
                if r.status_code == 200:
                    episodes = r.json().get("MediaContainer", {}).get("Metadata", [])
                    for ep in episodes:
                        if ep.get("viewCount", 0) > 0:
                            p = build_payload_from_plex(ep, "episode", show_map)
                            dedup_key = ("episode", ep.get("grandparentTitle"), ep.get("parentIndex"), ep.get("index"))
                            seen_items[dedup_key] = p
        except Exception as e:
            print(f"Error scanning section {sec['key']}: {e}")
            
    payloads = list(seen_items.values())
    
    if payloads:
        print(f"🚀 Processing {len(payloads)} watched items internally... this might take a bit")
        conn = sqlite3.connect("sync.db")
        cursor = conn.cursor()
        
        count = 0
        for p in payloads:
            if process_plex_payload(p, cursor, is_bulk=True):
                count += 1
                
        conn.commit()
        conn.close()
        print(f"✅ Initial Sync completed! {count} items processed.")

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

def pull_from_server_and_scrobble(date_from=None):
    print("Starting DB -> PLEX pull...")
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT * FROM watch_history WHERE origin != 'plex'"
    if date_from: query += f" AND created_at >= '{date_from}'"
    
    cursor.execute(query)
    rows = cursor.fetchall()
    
    movies = []
    shows = defaultdict(lambda: {"title": "", "seasons": {}})
    
    for row in rows:
        item = dict(row)
        if item["media_type"] == "movie":
            movies.append({"movie": {"title": item["title"]}})
        elif item["media_type"] == "episode":
            show_key = f"{item['show_title']}_{item['show_tmdb_id']}"
            if not shows[show_key]["title"]: shows[show_key]["title"] = item["show_title"]
            season_num = item["season"]
            if season_num not in shows[show_key]["seasons"]: shows[show_key]["seasons"][season_num] = []
            shows[show_key]["seasons"][season_num].append({"number": item["episode"]})
            
    conn.close()
    
    shows_list = [{"show": {"title": data["title"]}, "seasons": [{"number": s_n, "episodes": eps} for s_n, eps in data["seasons"].items()]} for _, data in shows.items()]
    
    if not movies and not shows_list:
        return
        
    print(f"Found {len(movies)} movies and {len(shows_list)} shows from Kodi to mark in Plex.")
    plex_movies, plex_shows = get_plex_items_map()
    
    for m in movies:
        matched = match_movie(m, plex_movies)
        if matched and matched.get("viewCount", 0) == 0:
            r_key = matched.get("ratingKey")
            requests.get(f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={r_key}", headers=plex_headers)
            print(f"Marked movie in Plex: {m['movie']['title']}")
            
    for s in shows_list:
        s_key = match_show(s, plex_shows)
        if s_key:
            s_title = s["show"]["title"]
            for season in s.get("seasons", []):
                s_num = season.get("number")
                r_eps = requests.get(f"{PLEX_URL}/library/metadata/{s_key}/allLeaves", headers=plex_headers)
                if r_eps.status_code == 200:
                    plex_eps = r_eps.json().get("MediaContainer", {}).get("Metadata", [])
                    for ep in season.get("episodes", []):
                        e_num = ep.get("number")
                        for pep in plex_eps:
                            if pep.get("parentIndex") == s_num and pep.get("index") == e_num:
                                if pep.get("viewCount", 0) == 0:
                                    requests.get(f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={pep['ratingKey']}", headers=plex_headers)
                                    print(f"Marked episode in Plex: {s_title} T{s_num}E{e_num}")
                                break

def run_sync():
    settings = load_settings()
    last_sync = settings.get("last_sync_date")
    is_first_sync = not last_sync
    
    pull_from_server_and_scrobble(last_sync)
    
    if is_first_sync:
        push_all_to_db()
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

@app.on_event("startup")
async def startup_event():
    init_db()
    
    settings = load_settings()
    last_sync = settings.get("last_sync_date")
    
    if not last_sync:
        print("First time setup: Triggering initial sync...")
        run_sync() # Run it synchronously so it blocks or just run it once
        
    if not HAS_PLEX_PASS:
        print("Plex Pass NOT detected: Starting incremental sync loop...")
        asyncio.create_task(sync_loop())
    else:
        print("Plex Pass detected: Incremental sync loop disabled. Relying purely on webhooks.")

# --- SERVE FRONTEND ---
# Mount the static folder at the end to avoid overwriting routes /api/
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

