from fastapi import FastAPI, Request, Query, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
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

app = FastAPI()
security = HTTPBasic()

# --- CONFIGURACIÓN PLEX ---
PLEX_URL = "http://192.168.178.21:32400"
PLEX_TOKEN = "XYHZz-RvzKQzZX_TuJix"
plex_headers = {"Accept": "application/xml", "X-Plex-Token": PLEX_TOKEN}

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
        if is_live_event:
            watched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            watched_at = "1970-01-01T00:00:00Z"
    
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

@app.post("/webhook/plex")
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

@app.post("/webhook/plex/bulk")
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
        if is_live_event:
            watched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            watched_at = "1970-01-01T00:00:00Z"
        
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

@app.post("/webhook/kodi")
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
    
@app.post("/webhook/kodi/bulk")
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

@app.get("/sync/all-items")
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

# --- WEB DASHBOARD APIs ---

def verify_token(credentials: HTTPBasicCredentials = Depends(security)):
    stored_b64 = os.environ.get("SYNC_PASSWORD_B64", "")
    if not stored_b64:
        return True
    req_pwd_b64 = base64.b64encode(credentials.password.encode('utf-8')).decode('utf-8')
    if req_pwd_b64 != stored_b64:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

class LoginRequest(BaseModel):
    password: str

@app.post("/api/login")
def api_login(req: LoginRequest):
    stored_b64 = os.environ.get("SYNC_PASSWORD_B64", "")
    req_pwd_b64 = base64.b64encode(req.password.encode('utf-8')).decode('utf-8')
    if req_pwd_b64 == stored_b64 or not stored_b64:
        token = base64.b64encode(f"syncpk:{req.password}".encode('utf-8')).decode('utf-8')
        return {"token": token}
    raise HTTPException(status_code=401, detail="Invalid password")

@app.get("/api/config")
def api_config():
    return {"tmdb_api_key": os.environ.get("TMDB_API_KEY", "")}

@app.get("/api/stats")
def api_stats(auth: bool = Depends(verify_token)):
    conn = sqlite3.connect("sync.db")
    c = conn.cursor()
    c.execute("SELECT count(*) FROM watch_history WHERE media_type='movie'")
    m_count = c.fetchone()[0]
    c.execute("SELECT count(*) FROM watch_history WHERE media_type='episode'")
    e_count = c.fetchone()[0]
    conn.close()
    return {"movies_count": m_count, "episodes_count": e_count}

@app.get("/api/history")
def api_history(limit: int = 20, offset: int = 0, type: str = 'all', year: str = 'all', month: str = 'all', search: str = '', auth: bool = Depends(verify_token)):
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    query = "SELECT * FROM watch_history WHERE 1=1"
    params = []
    
    if type != 'all':
        query += " AND media_type=?"
        params.append(type)
        
    if search:
        query += " AND (title LIKE ? OR show_title LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
        
    query += " ORDER BY watched_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    
    if year != 'all' or month != 'all':
        filtered = []
        for r in rows:
            dt = r.get("watched_at", "")
            if len(dt) >= 10:
                r_year = dt[0:4]
                r_month = dt[5:7].lstrip('0')
                if year != 'all' and r_year != year: continue
                if month != 'all' and r_month != month: continue
            filtered.append(r)
        rows = filtered
        
    return {"items": rows}

@app.delete("/api/history/{item_id}")
def api_delete_history(item_id: int, auth: bool = Depends(verify_token)):
    conn = sqlite3.connect("sync.db")
    c = conn.cursor()
    c.execute("DELETE FROM watch_history WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

class UpdateHistoryReq(BaseModel):
    watched_at: str
    
@app.put("/api/history/{item_id}")
def api_update_history(item_id: int, req: UpdateHistoryReq, auth: bool = Depends(verify_token)):
    conn = sqlite3.connect("sync.db")
    c = conn.cursor()
    c.execute("UPDATE watch_history SET watched_at=? WHERE id=?", (req.watched_at, item_id))
    conn.commit()
    conn.close()
    return {"status": "updated"}

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def read_root():
    return FileResponse("static/index.html")