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
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# --- PLEX & SECURITY CONFIGURATION ---
PLEX_URL = os.getenv("PLEX_URL", "http://192.168.178.21:32400")
PLEX_TOKEN = os.getenv("PLEX_TOKEN", "")
SYNC_PASSWORD_B64 = os.getenv("SYNC_PASSWORD_B64", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")

plex_headers = {"Accept": "application/xml", "X-Plex-Token": PLEX_TOKEN}

def verify_api_key(request: Request):
    if not SYNC_PASSWORD_B64:
        return True
    
    # Try Header first
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Basic "):
        token = auth_header.split(" ")[1]
        if token == SYNC_PASSWORD_B64:
            return True
            
    # Try Cookie
    cookie_token = request.cookies.get("syncpk_token")
    if cookie_token == SYNC_PASSWORD_B64:
        return True
        
    raise HTTPException(status_code=401, detail="Unauthorized")

def verify_webhook_token(token: Optional[str] = Query(None)):
    if not SYNC_PASSWORD_B64:
        return True
    if token != SYNC_PASSWORD_B64:
        raise HTTPException(status_code=401, detail="Invalid webhook token")
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
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

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
                    watched_at=?, origin='plex', created_at=?
                WHERE id=?
            """, (plex_guid, plex_show_guid, watched_at, now_utc, existing_id))
            action = "Live Update (Re-visionado)"
        else:
            cursor.execute("""
                UPDATE watch_history SET
                    plex_guid=?, plex_show_guid=?
                WHERE id=?
            """, (plex_guid, plex_show_guid, existing_id))
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
                watched_at, origin, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            media_type, title, show_title, season, episode, 
            plex_guid, plex_show_guid,
            imdb_id, tmdb_id, tvdb_id, show_imdb_id, show_tmdb_id, show_tvdb_id,
            watched_at, 'plex', now_utc
        ))
        
        if media_type == "episode":
            print(f"✅ Plex PUSH (Nuevo): Serie '{show_title}' T{season}E{episode}")
        else:
            print(f"✅ Plex PUSH (Nuevo): Película '{title}'")
            
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

@app.post("/webhook/plex/bulk", dependencies=[Depends(verify_webhook_token)])
async def plex_webhook_bulk(request: Request):
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
        if process_plex_payload(p, cursor, is_bulk=True):
            count += 1
            
    conn.commit()
    conn.close()
    return {"status": "success", "processed": count}

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
                    watched_at=?, origin='kodi', created_at=?
                WHERE id=?
            """, (kodi_id, kodi_show_id, watched_at, now_utc, existing_id))
            action = "Live Update (Re-visionado)"
        else:
            cursor.execute("""
                UPDATE watch_history SET
                    kodi_id=?, kodi_show_id=?
                WHERE id=?
            """, (kodi_id, kodi_show_id, existing_id))
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
                watched_at, origin, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            media_type, title, show_title, season, episode, 
            kodi_id, kodi_show_id,
            imdb_id, tmdb_id, tvdb_id, show_imdb_id, show_tmdb_id, show_tvdb_id,
            watched_at, 'kodi', now_utc
        ))
        
        if media_type == "episode":
            print(f"✅ Kodi PUSH (Nuevo): Serie '{show_title}' T{season}E{episode}")
        else:
            print(f"✅ Kodi PUSH (Nuevo): Película '{title}'")
            
    return True

@app.post("/webhook/kodi", dependencies=[Depends(verify_webhook_token)])
async def kodi_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}
        
    conn = sqlite3.connect("sync.db")
    cursor = conn.cursor()
    process_kodi_payload(payload, cursor)
    conn.commit()
    conn.close()
    return {"status": "success"}
    
@app.post("/webhook/kodi/bulk", dependencies=[Depends(verify_webhook_token)])
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

from fastapi.responses import JSONResponse

@app.post("/api/login")
def login(req: LoginRequest):
    # The password in the frontend comes in plain text, convert it to B64 to check
    b64_pwd = base64.b64encode(req.password.encode()).decode()
    if b64_pwd == SYNC_PASSWORD_B64 or not SYNC_PASSWORD_B64:
        response = JSONResponse({"success": True, "token": b64_pwd})
        # Set a cookie that lasts for 10 years (315360000 seconds)
        response.set_cookie(key="syncpk_token", value=b64_pwd, max_age=315360000, httponly=False)
        return response
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
def get_stats(authorization: str = Depends(verify_api_key)):
    conn = sqlite3.connect("sync.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM watch_history WHERE media_type = 'movie'")
    movies_count = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM watch_history WHERE media_type = 'episode'")
    episodes_count = cursor.fetchone()[0] or 0
    cursor.execute("SELECT DISTINCT strftime('%Y', watched_at) FROM watch_history WHERE watched_at IS NOT NULL ORDER BY 1 DESC")
    available_years = [row[0] for row in cursor.fetchall() if row[0]]
    
    conn.close()
    
    return {
        "movies_count": movies_count,
        "episodes_count": episodes_count,
        "available_years": available_years
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

# --- SERVE FRONTEND ---
# Mount the static folder at the end to avoid overwriting routes /api/
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
