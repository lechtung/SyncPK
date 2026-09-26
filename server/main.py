#v8
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

#### TODOLIST ####

# last minute found: python plex api: https://github.com/pushingkarmaorg/python-plexapi
# check the plex api of the previous proyect
# delete or replace the "x-plex-client-identifier" header, problably is not needed.
# add the watchlist option, we can have it in dashboard and when one of the whatchlist items is added to bd, automatically deleted from watchlist
# more statistics options
# maybe a small mark in the dashboard to see what items are not in the plex library anymore (need to see how we can detect when the user delete something in plex)
# manual option to re-scan the plex library to import in our local db

# FUTURAS CONFIGURACIONES DE UI (Para añadir en .conf o UI Dashboard)
# - TAMAÑOS: Ancho y alto de las tarjetas (Poster y Fanart) para ajustarlo al gusto.
# - PREFERENCIA DE ARTE: Usar el Fanart/Poster propio del episodio, o forzar siempre el de la temporada/serie.
# - TIPOGRAFÍA: 
    # * Selección de fuente (Integración con Google Fonts o fuentes del sistema).
    # * Tamaños y colores individuales para: Título, Subtítulo, Hora y Encabezado de fecha.
# - COLORES Y FORMAS:
    # * Color de fondo general de la aplicación.
    # * Color de acento principal (reemplazar el actual por defecto).
    # * Color de los componentes (combobox/dropdowns).
    # * Color del botón de acción flotante (menú inferior derecho).
    # * Nivel de desenfoque y opacidad del fondo (efecto glassmorphism).
    # * Bordes redondeados vs Bordes rectos (border-radius).
# - DISEÑO: 
    # * Espaciado entre las tarjetas (grid gap).
    # * Ocultar/Mostrar metadatos específicos (ej. ocultar duración, o subtítulo).
    # * Formato de fecha (ej. DD/MM/YYYY vs MM/DD/YYYY).

# Pondria en la pantalla de configuracion arriba, como un selector de pestaña, o botones para seleccionar secciones, algo, y tendria tres
# Servicio, UI, Scrapping (o como cojones se escriba) y cada uno con su correspondiente configuración   

#### DONE ####

# implement something to check if the use has pless pass, maybe with the api to get user information?
# Auto Update desde el repo, bueno, que pregunte al menos... o bien ponemos un parámetro. 


# Manual .env fallback
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

# Load .conf UI settings
if os.path.exists(".conf"):
    with open(".conf", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")
else:
    # Create default .conf if it doesn't exist
    default_conf = """# General Configuration

# ui section

# Controls the opacity of the dark mask over fanart images (0.0 to 1.0)
FANART_MASK_OPACITY=0.3

# update section

# If a new version is available, it will be stored here
UPDATE_AVAILABLE=

# If the user ignores a specific version, it will be stored here
IGNORED_UPDATE_VERSION=

# If the user wants to be notified of new updates (true/false)
NOTIFY_UPDATES=true
"""
    with open(".conf", "w") as f:
        f.write(default_conf)

app = FastAPI()

# --- PLEX & SECURITY CONFIGURATION ---
PLEX_URL = os.getenv("PLEX_URL", "")
PLEX_TOKEN = os.getenv("PLEX_TOKEN", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
SALT = os.getenv("SALT", "")
WEB_HASH = os.getenv("WEB_HASH", "")
API_HASH = os.getenv("API_HASH", "")
HAS_PLEX_PASS = os.getenv("HAS_PLEX_PASS", "false").lower() == "true"
PLEX_CLIENT_ID = os.getenv("PLEX_CLIENT_ID", "syncpk-default")
FANART_MASK_OPACITY = os.getenv("FANART_MASK_OPACITY", "0.5")

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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deleted_history (
            id INTEGER PRIMARY KEY,
            media_type TEXT,
            title TEXT,
            show_title TEXT,
            season INTEGER,
            episode INTEGER,
            tmdb_id TEXT,
            show_tmdb_id TEXT,
            deleted_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_cleanup_deleted_movie
        AFTER INSERT ON watch_history
        WHEN new.media_type = 'movie'
        BEGIN
            DELETE FROM deleted_history 
            WHERE media_type = 'movie' 
              AND (tmdb_id = new.tmdb_id OR (tmdb_id IS NULL AND title = new.title));
        END;
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_cleanup_deleted_episode
        AFTER INSERT ON watch_history
        WHEN new.media_type = 'episode'
        BEGIN
            DELETE FROM deleted_history 
            WHERE media_type = 'episode' 
              AND (show_tmdb_id = new.show_tmdb_id OR (show_tmdb_id IS NULL AND show_title = new.show_title))
              AND season = new.season 
              AND episode = new.episode;
        END;
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
        
    lang = os.getenv("SYNC_LANGUAGE", "en")
    async with tmdb_semaphore:
        url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}?api_key={TMDB_API_KEY}&language={lang}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            if resp.status_code != 200:
                # Retry without language if it fails
                resp = await client.get(url.replace(f"&language={lang}", ""), timeout=10)
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

def download_episode_fanart_sync(thumb_url, metadata_id):
    if not thumb_url or not metadata_id: return None
    
    fanart_local_path = f"static/cache/fanarts/ep_{metadata_id}.jpg"
    fanart_local = f"/cache/fanarts/ep_{metadata_id}.jpg" if os.path.exists(fanart_local_path) else None
    
    if fanart_local:
        return fanart_local
        
    # Arreglar URLs relativas de Plex y usar transcodificador
    if thumb_url.startswith("/"):
        if "/photo/:/transcode" not in thumb_url:
            import urllib.parse
            encoded_url = urllib.parse.quote_plus(thumb_url)
            thumb_url = f"{PLEX_URL}/photo/:/transcode?width=300&height=169&minSize=1&upscale=1&url={encoded_url}&X-Plex-Token={PLEX_TOKEN}"
        else:
            thumb_url = f"{PLEX_URL}{thumb_url}&X-Plex-Token={PLEX_TOKEN}" if "?" in thumb_url else f"{PLEX_URL}{thumb_url}?X-Plex-Token={PLEX_TOKEN}"
            
    # Replace /original/ with /w300/ or similar if it's a tmdb url
    if "/original/" in thumb_url:
        thumb_url = thumb_url.replace("/original/", "/w300/")
        
    import requests
    try:
        img_resp = requests.get(thumb_url, timeout=15)
        if img_resp.status_code == 200:
            with open(fanart_local_path, "wb") as f:
                f.write(img_resp.content)
            return f"/cache/fanarts/ep_{metadata_id}.jpg"
    except Exception as e:
        print(f"Error downloading episode fanart for {metadata_id}: {e}")
        
    return None

def download_episode_fanart_tmdb_sync(show_tmdb_id, season, episode):
    if not TMDB_API_KEY or not show_tmdb_id or season is None or episode is None:
        return None
        
    filename = f"tmdb_ep_{show_tmdb_id}_s{season}e{episode}.jpg"
    local_path = f"static/cache/fanarts/{filename}"
    
    if os.path.exists(local_path):
        return f"/cache/fanarts/{filename}"
        
    url = f"https://api.themoviedb.org/3/tv/{show_tmdb_id}/season/{season}/episode/{episode}?api_key={TMDB_API_KEY}"
    import requests
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            still = r.json().get("still_path")
            if still:
                img_url = f"https://image.tmdb.org/t/p/w300{still}"
                img_resp = requests.get(img_url, timeout=15)
                if img_resp.status_code == 200:
                    os.makedirs("static/cache/fanarts", exist_ok=True)
                    with open(local_path, "wb") as f:
                        f.write(img_resp.content)
                    return f"/cache/fanarts/{filename}"
    except Exception as e:
        print(f"Error fetching TMDB episode fanart: {e}", flush=True)
    return None

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
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] process_plex_payload: Processing webhook event '{payload.get('event')}'")
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
    if row and not is_live_event:
        existing_id = row[0]
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
        if row and is_live_event:
            action = "Live Update (Re-visionado - NEW ROW)"
        else:
            action = "Nuevo Registro"
            
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
            print(f"✅ {prefix} ({action}): Serie '{show_title}' T{season}E{episode} - {title}")
        else:
            prefix = "Bulk Import" if is_bulk else "Plex PUSH"
            print(f"✅ {prefix} ({action}): Película '{title}'")
            
    try:
        db_id = existing_id if existing_id else cursor.lastrowid
        target_tmdb = tmdb_id if media_type == "movie" else show_tmdb_id
        if not target_tmdb and media_type == "episode": target_tmdb = tmdb_id # Fallback
        
        # Download show poster/fanart or movie poster/fanart
        if target_tmdb:
            p_path, f_path = download_tmdb_images_sync(target_tmdb, "tv" if media_type == "episode" else "movie")
            if p_path or f_path:
                cursor.execute("UPDATE watch_history SET poster_path=COALESCE(?, poster_path), fanart_path=COALESCE(?, fanart_path) WHERE id=?", (p_path, f_path, db_id))
        
        # Download specific episode fanart (try Plex thumb first, fallback to TMDB)
        if media_type == "episode":
            ep_fanart = None
            if metadata.get("thumb"):
                ep_metadata_id = plex_guid.split("/")[-1] if plex_guid else str(db_id)
                ep_fanart = download_episode_fanart_sync(metadata.get("thumb"), ep_metadata_id)
            if not ep_fanart and target_tmdb:
                ep_fanart = download_episode_fanart_tmdb_sync(target_tmdb, season, episode)
                
            if ep_fanart:
                cursor.execute("UPDATE watch_history SET fanart_path=? WHERE id=?", (ep_fanart, db_id))
                
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
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] process_kodi_payload: Processing webhook event '{payload.get('event')}'")
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
    if row and not is_live_event:
        existing_id = row[0]
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
        if row and is_live_event:
            action = "Live Update (Re-visionado - NEW ROW)"
        else:
            action = "Nuevo Registro"
            
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
            print(f"✅ Kodi PUSH ({action}): Serie '{show_title}' T{season}E{episode} - {title}")
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
            
    # Asynchronously mark as watched in Plex using its API
    if not existing_id:
        import threading
        s_season = int(season) if season is not None else None
        s_episode = int(episode) if episode is not None else None
        threading.Thread(target=scrobble_kodi_webhook_to_plex, args=(title, show_title, s_season, s_episode, media_type, tmdb_id, tvdb_id, show_tmdb_id, show_tvdb_id)).start()
            
    return True

def scrobble_kodi_webhook_to_plex(title, show_title, season, episode, media_type, tmdb_id=None, tvdb_id=None, show_tmdb_id=None, show_tvdb_id=None):
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] scrobble_kodi_webhook_to_plex: Triggering local scrobble for {media_type} '{title}'")
    try:
        plex_movies, plex_shows = get_plex_items_map()
        if media_type == "movie":
            matched = match_movie({"movie": {"title": title}}, plex_movies, tmdb_id)
            if matched:
                r_key = matched.get("ratingKey")
                requests.get(f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={r_key}", headers=plex_headers)
                print(f"✅ [Direct] Marked movie in Plex: {title}")
        elif media_type == "episode":
            s_key = match_show({"show": {"title": show_title}}, plex_shows, show_tmdb_id)
            if s_key:
                r_eps = requests.get(f"{PLEX_URL}/library/metadata/{s_key}/allLeaves", headers=plex_headers)
                if r_eps.status_code == 200:
                    plex_eps = r_eps.json().get("MediaContainer", {}).get("Metadata", [])
                    for pep in plex_eps:
                        if pep.get("parentIndex") == season and pep.get("index") == episode:
                            requests.get(f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={pep['ratingKey']}", headers=plex_headers)
                            print(f"✅ [Direct] Marked episode in Plex: {show_title} T{season}E{episode} - {title}")
                            break
    except Exception as e:
        print(f"Error en scrobble_kodi_webhook_to_plex: {e}")


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

class ConfirmSyncRequest(BaseModel):
    ids: list[int]

@app.post("/sync/confirm-kodi")
def confirm_kodi_sync(req: ConfirmSyncRequest):
    if not req.ids:
        return {"status": "success", "deleted": 0}
    conn = sqlite3.connect("sync.db")
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in req.ids)
    cursor.execute(f"DELETE FROM deleted_history WHERE id IN ({placeholders})", req.ids)
    conn.commit()
    conn.close()
    return {"status": "success", "deleted": len(req.ids)}

@app.get("/sync/all-items", dependencies=[Depends(verify_api_key)])
def get_all_items(client: Optional[str] = Query("kodi"), date_from: Optional[str] = Query(None)):
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT * FROM watch_history WHERE 1=1"
    
    if date_from:
        query += f" AND created_at >= '{date_from}'"
        query += f" AND origin != '{client}'"
        
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
        
    cursor.execute("SELECT * FROM deleted_history WHERE deleted_at >= ?", (date_from,))
    deleted_rows = cursor.fetchall()
    deleted_movies = []
    deleted_shows = []
    
    for row in deleted_rows:
        item = dict(row)
        if item["media_type"] == "movie":
            deleted_movies.append(item)
        elif item["media_type"] == "episode":
            deleted_shows.append(item)
        
    conn.close()
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "movies": movies,
        "shows": shows_list,
        "deleted_movies": deleted_movies,
        "deleted_shows": deleted_shows,
        "server_time": now_utc
    }

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
    
    # Add the same extra condition for media_type
    
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
    # Get global available years
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
        # Request the last 200 lines of the service in Proxmox
        out = subprocess.check_output(['journalctl', '-u', 'syncpk-server', '-n', '200', '--no-pager']).decode('utf-8')
        return {"logs": out}
    except Exception as e:
        return {"logs": f"Error leyendo logs: {e}"}

@app.get("/api/download_logs")
def download_logs():
    try:
        from fastapi.responses import Response
        # Request FULL text history of the service without pagination
        out = subprocess.check_output(['journalctl', '-u', 'syncpk-server', '--no-pager']).decode('utf-8')
        return Response(content=out, media_type="text/plain", headers={"Content-Disposition": "attachment; filename=syncpk_journal.txt"})
    except Exception as e:
        return {"error": f"Error descargando logs: {e}"}

@app.delete("/api/history/{item_id}")
def delete_history_item(item_id: int, scope: str = "episode", sync_remote: bool = False, authorization: str = Depends(verify_api_key)):
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] delete_history_item: Deleting item_id {item_id}, scope={scope}, sync_remote={sync_remote}")
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM watch_history WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return {"success": False, "status": "error", "errors": ["Item not found"]}
        
    item = dict(row)
    items_to_delete = get_items_for_scope(item, scope, cursor)
    
    total_items = len(items_to_delete)
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] Found {total_items} items to delete from local database.")
    success_count = 0
    errors = []
    
    for d_item in items_to_delete:
        should_delete_db = True
        if os.getenv("DEBUG") == "true":
            print(f"[DEBUG] Processing deletion for episode: {d_item.get('title')}")
        if sync_remote:
            success = unscrobble_plex(d_item)
            if not success:
                should_delete_db = False
                errors.append(f"Fallo al eliminar en Plex para: {d_item.get('title')}")
                print(f"Skipping DB delete for {d_item.get('title')} because Plex unscrobble failed.")
        
        if should_delete_db:
            success_count += 1
            if sync_remote:
                now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                cursor.execute("""
                    INSERT OR REPLACE INTO deleted_history (id, media_type, title, show_title, season, episode, tmdb_id, show_tmdb_id, deleted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (d_item.get("id"), d_item.get("media_type"), d_item.get("title"), d_item.get("show_title"), d_item.get("season"), d_item.get("episode"), d_item.get("tmdb_id"), d_item.get("show_tmdb_id"), now_utc))
            
            if d_item.get("id"):
                cursor.execute("DELETE FROM watch_history WHERE id = ?", (d_item["id"],))
            elif d_item.get("plex_guid"):
                cursor.execute("DELETE FROM watch_history WHERE plex_guid = ?", (d_item.get("plex_guid"),))
                
    conn.commit()
    conn.close()
    
    if success_count == total_items:
        return {"success": True, "status": "success"}
    elif success_count > 0:
        return {"success": True, "status": "partial", "errors": errors}
    else:
        return {"success": False, "status": "error", "errors": errors}

# --- UNIFIED PLEX ACTIVITY FEED FUNCTION ---
def get_plex_activity_nodes(metadata_id, types=None, max_timeout=600):
    if types is None:
        types = ["WATCH_HISTORY", "WATCH_SESSION"]
        
    import requests, time
    url_graphql = "https://community.plex.tv/api"
    headers_fetch = {
        "Accept": "application/json", "Content-Type": "application/json",
        "x-plex-client-identifier": PLEX_CLIENT_ID, "x-plex-token": PLEX_TOKEN
    }
    
    query = """
    query GetActivityFeed($first: PaginationInt!, $metadataID: ID, $types: [ActivityType!]!, $includeDescendants: Boolean = false) {
      activityFeed(first: $first, metadataID: $metadataID, types: $types, includeDescendants: $includeDescendants) {
        nodes { id date }
      }
    }
    """
    payload = {
        "query": query,
        "variables": {"first": 24, "metadataID": metadata_id, "types": types, "includeDescendants": True},
        "operationName": "GetActivityFeed"
    }

    start_time = time.time()
    while True:
        try:
            r = requests.post(url_graphql, headers=headers_fetch, json=payload, timeout=20)
            if r.status_code == 200:
                # Retorna inmediatamente lo que haya (vacío o lleno)
                return r.json().get("data", {}).get("activityFeed", {}).get("nodes", [])
            elif r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 5))
                time.sleep(retry_after)
                continue
            else:
                return []
        except Exception:
            return []

        if (time.time() - start_time) >= max_timeout:
            return []
        
        time.sleep(5)

def mutate_plex_activity(node_id, action, watched_at_graphql=None, title="", max_timeout=600):
    import requests, time
    url = "https://community.plex.tv/api"
    headers = {
        "Accept": "application/json", "Content-Type": "application/json",
        "x-plex-token": PLEX_TOKEN, "x-plex-client-identifier": PLEX_CLIENT_ID,
        "origin": "https://app.plex.tv"
    }
    
    if action == "delete":
        payload = {
            "query": "mutation removeActivity($input: RemoveActivityInput!) {\n  removeActivity(input: $input)\n}\n",
            "variables": {"input": {"id": node_id, "type": "WATCH_HISTORY"}},
            "operationName": "removeActivity"
        }
        msg = f"🗑️ Deleting Plex Cloud activity {node_id}"
    elif action == "update_date":
        payload = {
            "query": "mutation updateActivityDate($id: ID!, $input: UpdateActivityInput!) {\n  updateActivity(id: $id, input: $input) { id }\n}\n",
            "variables": {"id": node_id, "input": {"date": watched_at_graphql}},
            "operationName": "updateActivityDate"
        }
        msg = f"💉 Surgery Plex Cloud (Date Update) {node_id}"
    else:
        return False
        
    start_time = time.time()
    while True:
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=10)
            if r.status_code == 200:
                resp_json = r.json()
                import os
                if os.getenv("DEBUG") == "true":
                    print(f"[DEBUG] mutate_plex_activity response for '{title}': {resp_json}", flush=True)
                    
                if "errors" in resp_json:
                    print(f"⚠️ Mutation error in Plex Cloud for '{title}': {resp_json['errors']}", flush=True)
                else:
                    print(f"{msg} for '{title}': HTTP 200", flush=True)
                    return True
            elif r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 5))
                time.sleep(retry_after)
                continue
        except Exception:
            pass
            
        if (time.time() - start_time) >= max_timeout:
            print(f"❌ Error in mutation '{action}' for '{title}': Timeout", flush=True)
            return False
            
        time.sleep(5)
# -------------------------------------------

def unscrobble_plex(item):
    import requests
    import datetime
    import os
    
    is_debug = os.getenv("DEBUG") == "true"
    
    plex_guid = item.get("plex_guid")
    metadata_id = None
    if plex_guid:
        metadata_id = plex_guid.split("/")[-1]
        
    success_cloud = True
    should_unscrobble_local = True
    
    title = item.get('title')
    
    if metadata_id:
        if is_debug: print(f"[DEBUG] [unscrobble] Fetching Plex activity nodes for '{title}' (metadata_id: {metadata_id})", flush=True)
        nodes = get_plex_activity_nodes(metadata_id)
        
        if nodes:
            total_nodes = len(nodes)
            if is_debug: print(f"[DEBUG] [unscrobble] Found {total_nodes} activity node(s) for '{title}'.", flush=True)
            
            target_date_str = item.get("watched_at")
            nodes_to_delete = []
            
            if target_date_str:
                try:
                    td_str = target_date_str.replace("Z", "").split(".")[0]
                    target_date = datetime.datetime.strptime(td_str, "%Y-%m-%dT%H:%M:%S")
                    if is_debug: print(f"[DEBUG] [unscrobble] Looking for activity matching date: {target_date_str}", flush=True)
                    
                    for node in nodes:
                        nd_str = node.get("date")
                        if nd_str:
                            nd_clean = nd_str.replace("Z", "").split(".")[0]
                            nd = datetime.datetime.strptime(nd_clean, "%Y-%m-%dT%H:%M:%S")
                            diff = abs((nd - target_date).total_seconds())
                            if diff < 3600:
                                nodes_to_delete.append(node)
                except Exception as e:
                    if is_debug: print(f"[DEBUG] [unscrobble] Error parsing dates: {e}", flush=True)
                    pass
            
            if not nodes_to_delete:
                if is_debug: print(f"[DEBUG] [unscrobble] No exact date match found. Deleting ALL {total_nodes} activities.", flush=True)
                nodes_to_delete = nodes
            else:
                if is_debug: print(f"[DEBUG] [unscrobble] Found {len(nodes_to_delete)} matching activit(ies) to delete.", flush=True)
                
            if len(nodes_to_delete) < total_nodes:
                remaining = total_nodes - len(nodes_to_delete)
                if is_debug: print(f"[DEBUG] [unscrobble] Warning: {remaining} other activity node(s) will remain. SKIPPING local unscrobble.", flush=True)
                should_unscrobble_local = False
            else:
                if is_debug: print(f"[DEBUG] [unscrobble] All activities will be deleted. Local unscrobble will proceed.", flush=True)
                
            for node in nodes_to_delete:
                node_id = node.get("id")
                if is_debug: print(f"[DEBUG] [unscrobble] Deleting node {node_id} from Plex Cloud for '{title}'...", flush=True)
                res = mutate_plex_activity(node_id, "delete", title=title)
                if not res:
                    if is_debug: print(f"[DEBUG] [unscrobble] FAILED to delete node {node_id}.", flush=True)
                    success_cloud = False
                else:
                    if is_debug: print(f"[DEBUG] [unscrobble] Successfully deleted node {node_id}.", flush=True)
        else:
            if is_debug: print(f"[DEBUG] [unscrobble] No activity nodes found in Plex Cloud for '{title}'.", flush=True)
                    
    if not success_cloud:
        if is_debug: print(f"[DEBUG] [unscrobble] Aborting unscrobble due to Cloud API failure.", flush=True)
        return False
        
    if should_unscrobble_local:
        if is_debug: print(f"[DEBUG] [unscrobble] Executing local Plex unscrobble for '{title}'...", flush=True)
        media_type = item.get("media_type")
        show_title = item.get("show_title")
        tmdb_id = item.get("tmdb_id")
        show_tmdb_id = item.get("show_tmdb_id")
        season = item.get("season")
        episode = item.get("episode")
        
        plex_movies, plex_shows = get_plex_items_map()
        rating_key = None
        
        if media_type == "movie":
            matched = match_movie({"movie": {"title": title}}, plex_movies, tmdb_id)
            if matched: rating_key = matched.get("ratingKey")
        else:
            matched = match_show({"show": {"title": show_title}}, plex_shows, show_tmdb_id)
            if matched:
                parent_key = matched.get("ratingKey")
                ep_url = f"{PLEX_URL}/library/metadata/{parent_key}/allLeaves"
                try:
                    req = urllib.request.Request(ep_url, headers=plex_headers)
                    with urllib.request.urlopen(req) as res:
                        tree = ET.fromstring(res.read())
                        for v in tree.findall("Video"):
                            if int(v.get("parentIndex", -1)) == season and int(v.get("index", -1)) == episode:
                                rating_key = v.get("ratingKey")
                                break
                except: pass
                
        if rating_key:
            try:
                url = f"{PLEX_URL}/:/unscrobble?identifier=com.plexapp.plugins.library&key={rating_key}"
                requests.get(url, headers=plex_headers, timeout=10)
                if is_debug: print(f"[DEBUG] [unscrobble] Local unscrobble SUCCESS for '{title}' (key={rating_key})", flush=True)
            except Exception as e:
                if is_debug: print(f"[DEBUG] [unscrobble] Local unscrobble FAILED for '{title}': {e}", flush=True)
        else:
            if is_debug: print(f"[DEBUG] [unscrobble] Local unscrobble skipped. Could not find local ratingKey for '{title}'", flush=True)
            
    return True
            
    if rating_key:
        try:
            url = f"{PLEX_URL}/:/unscrobble?identifier=com.plexapp.plugins.library&key={rating_key}"
            requests.get(url, headers=plex_headers, timeout=10)
            print(f"🗑️ Local Unscrobble for {title} (key={rating_key})")
        except: pass

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
    
    # --- NUEVO UNIFICADO ---
    node_id = None
    nodes = get_plex_activity_nodes(metadata_id)
    
    if nodes:
        node_id = nodes[0].get("id")
    else:
        print(f"No node for {item.get('title')}, triggering CLOUD scrobble...")
        cloud_headers = {
            "Accept": "application/json", "Content-Type": "application/json",
            "x-plex-client-identifier": PLEX_CLIENT_ID, "x-plex-token": PLEX_TOKEN
        }
        scrobble_url = f"https://metadata.provider.plex.tv/actions/scrobble?key={metadata_id}&identifier=tv.plex.provider.metadata"
        try:
            s_res = requests.get(scrobble_url, headers=cloud_headers, timeout=10)
            if s_res.status_code == 200:
                print(f"  ✅ Cloud Scrobble executed for {item.get('title')}")
        except Exception as e:
            print(f"  ❌ Cloud Scrobble error: {e}")
            return False
            
        print(f"  Waiting for Plex Cloud to process scrobble...")
        start_time = time.time()
        max_wait = 600
        while (time.time() - start_time) < max_wait:
            nodes = get_plex_activity_nodes(metadata_id)
            if nodes:
                node_id = nodes[0].get("id")
                break
            time.sleep(5)
    # -------------------------------------------------------------
            
    if node_id:
        success = mutate_plex_activity(node_id, "update_date", watched_at_graphql=watched_at_graphql, title=item.get("title"))
        if success:
            print(f"💉 Surgery success in Plex Cloud for {item.get('title')} -> {watched_at_local}")
            return True
    else:
        print(f"❌ Surgery failed: Could not get Activity Node for {item.get('title')} after waiting")
    return False

def get_cloud_episodes_for_scope(plex_show_guid, ref_season, ref_episode, scope):
    if not plex_show_guid: return []
    show_id = plex_show_guid.split("/")[-1]
    
    lang = os.getenv("SYNC_LANGUAGE", "en")
    headers = {
        "Accept": "application/json",
        "X-Plex-Token": PLEX_TOKEN,
        "X-Plex-Language": lang,
        "X-Plex-Container-Start":"0",
        "X-Plex-Container-Size":"99"
    }
    
    seasons_url = f"https://metadata.provider.plex.tv/library/metadata/{show_id}/children"
    try:
        r = requests.get(seasons_url, headers=headers, timeout=10)
        if r.status_code != 200: return []
    except Exception as e:
        print(f"Error fetching seasons from Cloud: {e}")
        return []
        
    seasons_data = r.json().get("MediaContainer", {}).get("Metadata", [])
    
    episodes = []
    try:
        ref_season = int(ref_season)
        ref_episode = int(ref_episode)
    except:
        pass
        
    for s in seasons_data:
        s_index = s.get("index")
        if s_index is None: continue
        try:
            s_index = int(s_index)
        except:
            continue
            
        if scope == "season" and s_index != ref_season:
            continue
        if scope == "exact_episode" and s_index != ref_season:
            continue
        if scope == "onwards" and s_index < ref_season:
            continue
        if scope == "backwards" and s_index > ref_season:
            continue
            
        season_rating_key = s.get("ratingKey")
        ep_url = f"https://metadata.provider.plex.tv/library/metadata/{season_rating_key}/children"
        try:
            r_ep = requests.get(ep_url, headers=headers, timeout=10)
            if r_ep.status_code == 200:
                eps_data = r_ep.json().get("MediaContainer", {}).get("Metadata", [])
                for ep in eps_data:
                    ep_index = ep.get("index")
                    if ep_index is None: continue
                    try: ep_index = int(ep_index)
                    except: continue
                    
                    if scope == "exact_episode" and ep_index != ref_episode:
                        continue
                    if scope == "onwards" and s_index == ref_season and ep_index < ref_episode:
                        continue
                    if scope == "backwards" and s_index == ref_season and ep_index > ref_episode:
                        continue
                    episodes.append(ep)
        except Exception as e:
            print(f"Error fetching episodes for season {s_index}: {e}")
            
    return episodes


def get_items_for_scope(item: dict, scope: str, cursor) -> list:
    import os
    items_to_modify = []
    
    if item.get("media_type") == "episode" and scope != "episode":
        cloud_eps = get_cloud_episodes_for_scope(item.get("plex_show_guid"), item.get("season"), item.get("episode"), scope)
        if cloud_eps:
            for ep in cloud_eps:
                items_to_modify.append({
                    "id": None, 
                    "title": ep.get("title", f"Episodio {ep.get('index')}"),
                    "show_title": item.get("show_title"),
                    "media_type": "episode",
                    "season": ep.get("parentIndex"),
                    "episode": ep.get("index"),
                    "plex_guid": ep.get("guid"),
                    "plex_show_guid": item.get("plex_show_guid"),
                    "show_tmdb_id": item.get("show_tmdb_id"),
                    "duration": int(ep.get("duration", 0)) // 60000 if ep.get("duration") else 0,
                    "thumb": ep.get("thumb"),
                    "Guid": ep.get("Guid", []),
                    "poster_path": item.get("poster_path")
                })
        else:
            if scope == "show":
                cursor.execute("SELECT * FROM watch_history WHERE show_title = ?", (item.get("show_title"),))
            elif scope == "season":
                cursor.execute("SELECT * FROM watch_history WHERE show_title = ? AND season = ?", (item.get("show_title"), item.get("season")))
            elif scope == "onwards":
                cursor.execute("SELECT * FROM watch_history WHERE show_title = ? AND (season > ? OR (season = ? AND episode >= ?))", (item.get("show_title"), item.get("season"), item.get("season"), item.get("episode")))
            elif scope == "backwards":
                cursor.execute("SELECT * FROM watch_history WHERE show_title = ? AND (season < ? OR (season = ? AND episode <= ?))", (item.get("show_title"), item.get("season"), item.get("season"), item.get("episode")))
            
            for r in cursor.fetchall():
                r_dict = dict(r)
                if not any(x.get("id") == r_dict["id"] for x in items_to_modify):
                    items_to_modify.append(r_dict)
    else:
        if item.get("id"):
            cursor.execute("SELECT * FROM watch_history WHERE id = ?", (item["id"],))
            items_to_modify = [dict(r) for r in cursor.fetchall()]
        else:
            items_to_modify = [item]
            
    return items_to_modify

@app.put("/api/history/{item_id}")

def update_history_item(item_id: int, req: UpdateHistoryRequest, authorization: str = Depends(verify_api_key)):
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM watch_history WHERE id = ?", (item_id,))
    item_row = cursor.fetchone()
    
    if not item_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
        
    item = dict(item_row)
    scope = req.scope or "episode"
    
    items_to_modify = []
    
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] update_history_item: Modifying {item['title']}, Scope: {scope}, Dist: {getattr(req, 'dist_mode', 'same')}")
        print(f"[DEBUG] plex_show_guid of the source item: {item.get('plex_show_guid')}")
    
    items_to_modify = get_items_for_scope(item, scope, cursor)
        
    total_items = len(items_to_modify)
    
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] Total items to modify: {total_items}")
    conn.close()
    
    if total_items == 0:
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
        
    success_count = 0
    errors = []
    for item, assign_dt in zip(sorted_items, assigned_dates):
        watched_str = assign_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        should_update_db = True
        if req.sync_remote:
            success = perform_plex_surgery(item, watched_str)
            if not success:
                should_update_db = False
                error_msg = f"Fallo en Plex Cloud para: {item.get('title')}"
                errors.append(error_msg)
                print(f"Skipping DB update for {item.get('title')} because Plex surgery failed.")
                
        if should_update_db:
            success_count += 1
            conn_update = sqlite3.connect("sync.db")
            conn_update.row_factory = sqlite3.Row
            c_update = conn_update.cursor()
            
            p_guid = item.get("plex_guid")
            c_update.execute("SELECT id FROM watch_history WHERE plex_guid = ?", (p_guid,))
            row = c_update.fetchone()
            
            if row:
                if req.sync_remote:
                    c_update.execute("UPDATE watch_history SET watched_at = ?, created_at = ? WHERE id = ?", (watched_str, now_utc, row["id"]))
                else:
                    c_update.execute("UPDATE watch_history SET watched_at = ? WHERE id = ?", (watched_str, row["id"]))
            else:
                imdb_id, tmdb_id, tvdb_id = extract_ids(item.get("Guid", []))
                c_update.execute("""
                    INSERT INTO watch_history (origin, title, show_title, media_type, season, episode, plex_guid, plex_show_guid, imdb_id, tmdb_id, tvdb_id, show_tmdb_id, watched_at, created_at, duration, poster_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, ('manual', item.get("title"), item.get("show_title"), 'episode', item.get("season"), item.get("episode"), p_guid, item.get("plex_show_guid"), imdb_id, tmdb_id, tvdb_id, item.get("show_tmdb_id"), watched_str, now_utc, item.get("duration", 0), item.get("poster_path")))
                new_id = c_update.lastrowid
                
                if item.get("thumb"):
                    ep_metadata_id = p_guid.split("/")[-1]
                    ep_fanart = download_episode_fanart_sync(item.get("thumb"), ep_metadata_id)
                    if ep_fanart:
                        c_update.execute("UPDATE watch_history SET fanart_path = ? WHERE id = ?", (ep_fanart, new_id))
            
            conn_update.commit()
            conn_update.close()
        
    if success_count == total_items:
        return {"success": True, "status": "success"}
    elif success_count > 0:
        return {"success": True, "status": "partial", "errors": errors}
    else:
        return {"success": False, "status": "error", "errors": errors}

@app.post("/api/dismiss-sync")
def dismiss_sync(authorization: str = Depends(verify_api_key)):
    settings = load_settings()
    settings["sync_state"] = 3
    save_settings(settings)
    return {"success": True}


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
            pass # No local history for this item
    except requests.exceptions.RequestException as req_err:
        print(f"❌ Connection error to local Plex server ({PLEX_URL}): {req_err}", flush=True)
    except Exception as e:
        print(f"❌ Unknown error processing local history for {rating_key}: {e}", flush=True)
        
# NOTE: We have removed the individual slow GraphQL query (get_plex_activity_nodes) 
    # from this phase. Smart Extractor V2 is now responsible for querying dates from the cloud.
        
    if not fechas:
        return xml_watched_at
        
    return min(fechas)

def build_payload_from_plex(item, media_type, show_map=None):
    if show_map is None: show_map = {}
    
    guid = item.get("guid", "")
    
    # Completely ignore any content that does not have a real match in Plex (local/none agent)
    if "tv.plex.agents.none" in guid or "local://" in guid:
        return None
        
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
        # Fallback if no lastViewedAt but has viewCount
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
            "thumb": item.get("thumb"),
            "watched_at": watched_at,
            "duration": item.get("duration", 0)
        }
    }
    
    if media_type == "episode":
        payload["Metadata"]["grandparentTitle"] = item.get("grandparentTitle")
        payload["Metadata"]["parentIndex"] = item.get("parentIndex")
        payload["Metadata"]["index"] = item.get("index")
        payload["Metadata"]["grandparentKey"] = item.get("grandparentKey")
        
        # Recover global Show IDs using show_map (Phase A)
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
    
    # Phase A: In-Memory Mapping of all Shows (with pagination)
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

    # Phase B: Immediate Extraction and Insertion
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
                            if not p:
                                continue
                                
                            if process_plex_payload(p, cursor, is_bulk=True):
                                count += 1
                                
                            # Write to DB on the fly to see it in the dashboard in real time
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
                    if payload:
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

def match_movie(movie_data, plex_movies, tmdb_id=None):
    title = movie_data.get("movie", {}).get("title", "").lower()
    for pm in plex_movies:
        pm_title = pm.get("title", "").lower()
        if pm_title == title or title in pm_title:
            return pm
    return None

def match_show(show_data, plex_shows, show_tmdb_id=None):
    title = show_data.get("show", {}).get("title", "").lower()
    import difflib
    for ps in plex_shows:
        ps_title = ps.get("title", "").lower()
        if ps_title == title or title in ps_title or ps_title in title:
            return ps.get("ratingKey")
        if difflib.SequenceMatcher(None, ps_title, title).ratio() > 0.85:
            return ps.get("ratingKey")
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
        "x-plex-client-identifier": PLEX_CLIENT_ID,
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
    show_titles_cache = {}
    
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
                        if not p:
                            return
                        p["Metadata"]["watched_at"] = cloud_date 
                        
                        if actual_media_type == "episode" and "grandparentGuid" in item:
                            gp_guid = item["grandparentGuid"]
                            
                            if gp_guid not in show_titles_cache:
                                # 1. Buscar en BD local para heredar el nombre
                                cursor.execute("SELECT show_title FROM watch_history WHERE plex_show_guid = ? LIMIT 1", (gp_guid,))
                                row_show = cursor.fetchone()
                                local_title = row_show["show_title"] if (row_show and row_show["show_title"]) else None
                                
                                # 2. Buscar en Plex Cloud para rellenar Guids y por si acaso el nombre no está local
                                cloud_guids = []
                                cloud_title = None
                                gp_id = gp_guid.split("/")[-1]
                                try:
                                    gp_url = f"https://metadata.provider.plex.tv/library/metadata/{gp_id}?X-Plex-Token={PLEX_TOKEN}&X-Plex-Language={lang}"
                                    gp_resp = requests.get(gp_url, headers={"Accept": "application/json"}, timeout=5)
                                    if gp_resp.status_code == 200:
                                        gp_data = gp_resp.json().get("MediaContainer", {}).get("Metadata", [])
                                        if gp_data:
                                            cloud_title = gp_data[0].get("title")
                                            cloud_guids = gp_data[0].get("Guid", [])
                                except Exception:
                                    pass
                                    
                                # Guardar en caché el nombre final (prioridad local) y los guids
                                show_titles_cache[gp_guid] = {
                                    "title": local_title or cloud_title,
                                    "guids": cloud_guids
                                }
                                
                            cached_data = show_titles_cache[gp_guid]
                            if cached_data["title"]:
                                p["Metadata"]["grandparentTitle"] = cached_data["title"]
                            if cached_data["guids"]:
                                p["Metadata"]["grandparentGuids"] = cached_data["guids"]
                                
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
        
        retries_primary = 0
        try:
            resp = requests.post(url_graphql, headers=headers_fetch, json=payload, timeout=20)
            if resp.status_code == 429:
                time.sleep(5)
                continue
            if resp.status_code != 200:
                print(f"Error {resp.status_code} fetching from Plex Cloud.")
                break
                
            resp_json = resp.json()
            if "errors" in resp_json:
                print(f"⚠️ Error interno en GraphQL de Plex Cloud: {resp_json['errors']}")
                retries_primary += 1
                if retries_primary > 3:
                    print("❌ Demasiados fallos consecutivos en Plex Cloud. Saltando...")
                    break
                time.sleep(5)
                continue
                
            data = resp_json.get("data")
            if not data:
                break
            
            data = data.get("activityFeed", {})
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
    # Once DB loading is finished, download images asynchronously
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
    force_rewatch: bool = False

@app.get("/api/tmdb/search")
def search_tmdb(q: str, lang: str = None, authorization: str = Depends(verify_api_key)):
    if not q or len(q) < 3:
        return {"results": []}
    
    # Force server configured language
    sync_lang = os.getenv("SYNC_LANGUAGE", "es")
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&language={sync_lang}&query={q}&page=1&include_adult=false"
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
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] manual_add: Triggered for {req.media_type} '{req.title}', sync_remote={req.sync_remote}")
    try:
        # --- PHASE 0: Check for duplicate in local DB ---
        conn_check = sqlite3.connect("sync.db")
        conn_check.row_factory = sqlite3.Row
        cur_check = conn_check.cursor()
        if req.media_type == "movie":
            cur_check.execute(
                "SELECT id, watched_at FROM watch_history WHERE media_type='movie' AND tmdb_id=?",
                (req.tmdb_id,)
            )
        else:
            cur_check.execute(
                "SELECT id, watched_at FROM watch_history WHERE media_type='episode' AND show_tmdb_id=? AND season=? AND episode=?",
                (req.tmdb_id, req.season, req.episode)
            )
        existing = cur_check.fetchone()
        conn_check.close()

        if existing and not req.force_rewatch:
            print(f"[manual_add] Duplicate detected for '{req.title}' - asking for re-watch confirmation.")
            return {"status": "confirm_rewatch", "last_watched": existing["watched_at"]}

        # --- PHASE 1: Plex Lookup (ALWAYS - to get plex_guid for future operations) ---
        plex_guid = None
        target_rating_key = None
        try:
            # 1. Search in Plex Cloud (discover.provider.plex.tv)
            import urllib.parse
            import os
            
            search_type = "movies" if req.media_type == "movie" else "tv"
            search_lang = os.getenv("SYNC_LANGUAGE", "es")
            cloud_headers = {
                "Accept": "application/json",
                "x-plex-token": PLEX_TOKEN,
                "x-plex-client-identifier": PLEX_CLIENT_ID
            }
            if search_lang:
                cloud_headers["x-plex-language"] = search_lang

            # First, get the original_title and EXACT YEAR from TMDB
            search_titles = [req.title]
            target_year = None
            found_duration = 0
            found_thumb = None
            found_show_guid = None
            
            if req.tmdb_id and TMDB_API_KEY:
                tmdb_type = "movie" if req.media_type == "movie" else "tv"
                tmdb_url = f"https://api.themoviedb.org/3/{tmdb_type}/{req.tmdb_id}?api_key={TMDB_API_KEY}"
                try:
                    tmdb_res = requests.get(tmdb_url, timeout=5)
                    if tmdb_res.status_code == 200:
                        data = tmdb_res.json()
                        orig_title = data.get("original_title" if req.media_type == "movie" else "original_name")
                        if orig_title and orig_title.lower() != req.title.lower():
                            search_titles.append(orig_title)
                        
                        # Extract release year
                        date_str = data.get("release_date" if req.media_type == "movie" else "first_air_date", "")
                        if date_str and len(date_str) >= 4:
                            target_year = int(date_str[:4])
                            
                        # Extract duration fallback (TMDB provides it in minutes)
                        if req.media_type == "movie":
                            found_duration = data.get("runtime") or 0
                        else:
                            ep_runs = data.get("episode_run_time", [])
                            if ep_runs:
                                found_duration = ep_runs[0]
                except Exception as e:
                    print(f"[manual_add] Failed getting extra data from TMDB: {e}")

            # Search iterating over titles (first local, then original)
            for title_to_search in search_titles:
                print(f"[manual_add] Searching '{title_to_search}' in Plex Cloud (Discover)...")
                cloud_search_url = (
                    f"https://discover.provider.plex.tv/library/search"
                    f"?query={urllib.parse.quote(title_to_search)}&limit=15"
                    f"&searchTypes={search_type}&includeMetadata=1&searchProviders=discover"
                )
                
                c_res = requests.get(cloud_search_url, headers=cloud_headers, timeout=15)
                if c_res.status_code == 200:
                    cloud_meta = []
                    search_results = c_res.json().get("MediaContainer", {}).get("SearchResults", [])
                    for sr in search_results:
                        for item in sr.get("SearchResult", []):
                            if "Metadata" in item:
                                cloud_meta.append(item["Metadata"])
                                
                    if cloud_meta:
                        # Iterate results to find a YEAR match (+/- 1 year margin)
                        for item in cloud_meta:
                            item_year = item.get("year")
                            
                            # If we have TMDB year, verify it matches
                            if target_year and item_year:
                                if abs(int(item_year) - target_year) > 1:
                                    continue # Not our year, skip to next
                                    
                            # Perfect match found!
                            if req.media_type == "movie":
                                target_rating_key = item.get("ratingKey")
                                plex_guid = item.get("guid")
                                if item.get("duration"): found_duration = int(item.get("duration")) // 60000
                                print(f"[manual_add] Exact match! Found in Plex Cloud: {item.get('title')} ({item_year}) (key={target_rating_key})")
                                break
                            else:
                                show_key = item.get("ratingKey")
                                show_guid = item.get("guid")
                                found_show_guid = show_guid
                                print(f"[manual_add] Found show in Plex Cloud: {item.get('title')} ({item_year}) (key={show_key})")
                                
                                # Resolve exact episode
                                eps = get_cloud_episodes_for_scope(show_guid, req.season, req.episode, "exact_episode")
                                if eps:
                                    target_rating_key = eps[0].get("ratingKey")
                                    plex_guid = eps[0].get("guid")
                                    if eps[0].get("duration"): found_duration = int(eps[0].get("duration")) // 60000
                                    if eps[0].get("thumb"): found_thumb = eps[0].get("thumb")
                                    print(f"[manual_add] Exact episode match: S{req.season}E{req.episode} (key={target_rating_key})")
                                else:
                                    print(f"[manual_add] Could not resolve season {req.season} ep {req.episode} in Cloud!")
                                    target_rating_key = None # fail the scrobble gracefully
                                break
                                
                        if target_rating_key:
                            break # If found, stop searching for other titles
                else:
                    print(f"[manual_add] Plex Cloud search failed for '{title_to_search}': HTTP {c_res.status_code}")

            if not target_rating_key:
                print(f"[manual_add] '{req.title}' NOT FOUND in Plex Cloud after exhausting attempts or year mismatch.")

        except Exception as e:
            print(f"[manual_add] Error in Plex lookup for '{req.title}': {e}")

        # --- PHASE 1b: Plex Scrobble + Date Surgery (only if sync_remote is requested) ---
        if req.sync_remote and target_rating_key:
            try:
                # --- NUEVO UNIFICADO ---
                activity_id = None
                ep_meta_id = plex_guid.split("/")[-1]
                
                # FIRST: Check if it already has an activity! (Unless forcing re-watch)
                existing_nodes = None
                if not req.force_rewatch:
                    existing_nodes = get_plex_activity_nodes(ep_meta_id)
                    
                if existing_nodes:
                    activity_id = existing_nodes[0].get("id")
                    print(f"[manual_add] Found existing activity for '{req.title}', skipping scrobble.")
                else:
                    # 2. Direct Cloud Scrobble
                    scrobble_url = f"https://metadata.provider.plex.tv/actions/scrobble?key={target_rating_key}&identifier=tv.plex.provider.metadata"
                    s_res = requests.get(scrobble_url, headers=cloud_headers, timeout=10)
                    print(f"[manual_add] Cloud Scrobble executed for '{req.title}': HTTP {s_res.status_code}")
                    
                    time.sleep(3) # Wait for scrobble to settle in Plex
                    
                    print(f"[manual_add] Waiting for Plex Cloud to process scrobble...")
                    start_time = time.time()
                    while (time.time() - start_time) < 600:
                        nodes = get_plex_activity_nodes(ep_meta_id)
                        if nodes:
                            activity_id = nodes[0].get("id")
                            break
                        time.sleep(5)
                # -------------------------------------------------------------

                if activity_id:
                    d_obj = datetime.datetime.fromisoformat(req.watched_at.replace('Z', '+00:00'))
                    watched_at_graphql = d_obj.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    mutate_plex_activity(activity_id, "update_date", watched_at_graphql=watched_at_graphql, title=req.title)
                else:
                    print(f"[manual_add] Activity ID not found for surgery in '{req.title}' after waiting")

            except Exception as e:
                print(f"[manual_add] Error in Plex scrobble/surgery for '{req.title}': {e}")
                
            # LOCAL SCROBBLE (As requested by user to ensure local watch state)
            try:
                import threading
                ep_title = f"Episode {req.episode}" if req.media_type == "episode" else req.title
                s_season = int(req.season) if req.season is not None else None
                s_episode = int(req.episode) if req.episode is not None else None
                movie_tmdb = req.tmdb_id if req.media_type == "movie" else None
                show_tmdb = req.tmdb_id if req.media_type == "episode" else None
                threading.Thread(target=scrobble_kodi_webhook_to_plex, args=(ep_title, req.title, s_season, s_episode, req.media_type, movie_tmdb, None, show_tmdb, None)).start()
            except Exception as e:
                print(f"[manual_add] Error triggering local scrobble: {e}")

        # --- PHASE 2: Download TMDB images ---
        poster_path = None
        fanart_path = None
        try:
            p_path, f_path = download_tmdb_images_sync(req.tmdb_id, "tv" if req.media_type == "episode" else "movie")
            poster_path = p_path
            fanart_path = f_path
            
            # If it's an episode and we found its thumb in Plex Cloud, use that instead of the TV show fanart
            if req.media_type == "episode" and found_thumb and plex_guid:
                ep_meta_id = plex_guid.split("/")[-1]
                ep_fanart = download_episode_fanart_sync(found_thumb, ep_meta_id)
                if ep_fanart:
                    fanart_path = ep_fanart
                    
        except Exception as e:
            print(f"[manual_add] Error downloading images for '{req.title}': {e}")

        conn = sqlite3.connect("sync.db")
        cursor = conn.cursor()
        d_obj = datetime.datetime.fromisoformat(req.watched_at.replace('Z', '+00:00'))
        final_watched_at = d_obj.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        final_origin = "manual" if req.sync_remote else "kodi"

        if req.media_type == "movie":
            cursor.execute("""
                INSERT INTO watch_history (origin, title, media_type, tmdb_id, watched_at, poster_path, fanart_path, plex_guid, duration)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (final_origin, req.title, req.media_type, req.tmdb_id, final_watched_at, poster_path, fanart_path, plex_guid, found_duration))
        else:
            ep_title = f"Episode {req.episode}"
            cursor.execute("""
                INSERT INTO watch_history (origin, title, show_title, media_type, show_tmdb_id, season, episode, watched_at, poster_path, fanart_path, plex_guid, plex_show_guid, duration)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (final_origin, ep_title, req.title, req.media_type, req.tmdb_id, req.season, req.episode, final_watched_at, poster_path, fanart_path, plex_guid, found_show_guid, found_duration))

        conn.commit()
        conn.close()
        print(f"[manual_add] '{req.title}' saved to local DB successfully.")
        return {"status": "success"}

    except Exception as e:
        print(f"[manual_add] Unexpected error: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/api/ui_config")
def get_ui_config():
    return {
        "fanart_mask_opacity": FANART_MASK_OPACITY
    }

# --- CONFIGURATION ---
@app.get("/api/config", dependencies=[Depends(verify_api_key)])
def get_config():
    return {
        "plex_url": PLEX_URL,
        "plex_token": PLEX_TOKEN,
        "tmdb_api_key": TMDB_API_KEY,
        "sync_language": os.getenv("SYNC_LANGUAGE", "es"),
        "dashboard_language": os.getenv("DASHBOARD_LANGUAGE", "auto"),
        "plex_client_id": PLEX_CLIENT_ID,
        "debug_mode": os.getenv("DEBUG", "false") == "true",
        "auto_update": os.getenv("AUTO_UPDATE", "true") == "true",
        "notify_updates": os.getenv("NOTIFY_UPDATES", "true").lower() == "true"
    }

class ConfigPayload(BaseModel):
    plex_url: str
    plex_token: str
    tmdb_api_key: str
    sync_language: str
    dashboard_language: str
    plex_client_id: str
    debug_mode: Optional[bool] = False
    auto_update: Optional[bool] = True
    notify_updates: Optional[bool] = True
    master_password: Optional[str] = None
    force_rescan: Optional[bool] = False

@app.post("/api/config", dependencies=[Depends(verify_api_key)])
def save_config(payload: ConfigPayload):
    env_path = ".env"
    env_vars = {}
    
    # Read current environment
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip().strip('"').strip("'")
    
    # Update values
    env_vars["PLEX_URL"] = payload.plex_url
    env_vars["PLEX_TOKEN"] = payload.plex_token
    env_vars["TMDB_API_KEY"] = payload.tmdb_api_key
    env_vars["SYNC_LANGUAGE"] = payload.sync_language
    env_vars["DASHBOARD_LANGUAGE"] = payload.dashboard_language
    env_vars["PLEX_CLIENT_ID"] = payload.plex_client_id
    env_vars["DEBUG"] = "true" if payload.debug_mode else "false"
    env_vars["AUTO_UPDATE"] = "true" if payload.auto_update else "false"
    env_vars["NOTIFY_UPDATES"] = "true" if payload.notify_updates else "false"
    
    has_plex_pass = None
    if payload.plex_token and payload.plex_client_id:
        try:
            req = urllib.request.Request("https://plex.tv/api/v2/user")
            req.add_header("Accept", "application/json")
            req.add_header("X-Plex-Client-Identifier", payload.plex_client_id)
            req.add_header("X-Plex-Token", payload.plex_token)
            with urllib.request.urlopen(req) as response:
                user_data = json.loads(response.read().decode())
                subscription = user_data.get("subscription") or {}
                has_pass = subscription.get("active") is True
                env_vars["HAS_PLEX_PASS"] = "true" if has_pass else "false"
                has_plex_pass = has_pass
                global HAS_PLEX_PASS
                HAS_PLEX_PASS = has_pass
        except Exception as e:
            print(f"Error checking plex pass: {e}")
    
    if payload.master_password:
        salt = env_vars.get("SALT", os.getenv("SALT", ""))
        new_hash = hashlib.sha256((payload.master_password + salt).encode()).hexdigest()
        env_vars["WEB_HASH"] = new_hash
    
    # Save to file
    with open(env_path, "w", encoding="utf-8") as f:
        for k, v in env_vars.items():
            f.write(f'{k}="{v}"\n')
            os.environ[k] = str(v)
            
    # Update variables in memory
    global PLEX_URL, PLEX_TOKEN, TMDB_API_KEY, WEB_HASH, PLEX_CLIENT_ID
    PLEX_URL = payload.plex_url
    PLEX_TOKEN = payload.plex_token
    TMDB_API_KEY = payload.tmdb_api_key
    PLEX_CLIENT_ID = payload.plex_client_id
    if payload.master_password:
        WEB_HASH = env_vars.get("WEB_HASH")
        
    return {"status": "success", "has_plex_pass": has_plex_pass}

@app.post("/api/config/restore", dependencies=[Depends(verify_api_key)])
def restore_config():
    import shutil
    if not os.path.exists(".env.bak"):
        return {"status": "error", "message": "No backup found (.env.bak)"}
    
    shutil.copy(".env.bak", ".env")
    
    # Reload config into memory
    env_vars = {}
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                val = v.strip().strip('"').strip("'")
                env_vars[k.strip()] = val
                os.environ[k.strip()] = val
                
    global PLEX_URL, PLEX_TOKEN, TMDB_API_KEY, WEB_HASH, PLEX_CLIENT_ID
    PLEX_URL = env_vars.get("PLEX_URL", "")
    PLEX_TOKEN = env_vars.get("PLEX_TOKEN", "")
    TMDB_API_KEY = env_vars.get("TMDB_API_KEY", "")
    PLEX_CLIENT_ID = env_vars.get("PLEX_CLIENT_ID", "syncpk-default")
    WEB_HASH = env_vars.get("WEB_HASH", "")
    
    return {"status": "success"}
        
    if payload.force_rescan:
        def rescan_task():
            print(f"Starting rescan to adapt to language: {payload.sync_language}")
            try:
                conn = sqlite3.connect("sync.db")
                cursor = conn.cursor()
                cursor.execute("SELECT id, tmdb_id, show_tmdb_id, media_type, season, episode FROM watch_history")
                rows = cursor.fetchall()
                
                for row in rows:
                    h_id, m_tmdb_id, s_tmdb_id, m_type, s_season, s_ep = row
                    
                    # Use new language
                    lang_param = f"&language={payload.sync_language}"
                    
                    if m_type == "movie" and m_tmdb_id:
                        # Re-download metadata
                        tmdb_url = f"https://api.themoviedb.org/3/movie/{m_tmdb_id}?api_key={payload.tmdb_api_key}{lang_param}"
                        res = requests.get(tmdb_url)
                        if res.status_code == 200:
                            data = res.json()
                            title = data.get("title", "")
                            p_path = f"https://image.tmdb.org/t/p/w500{data.get('poster_path')}" if data.get("poster_path") else ""
                            f_path = f"https://image.tmdb.org/t/p/original{data.get('backdrop_path')}" if data.get("backdrop_path") else ""
                            
                            cursor.execute("UPDATE watch_history SET title=?, poster_path=?, fanart_path=? WHERE id=?", (title, p_path, f_path, h_id))
                    
                    elif m_type == "episode" and s_tmdb_id:
                        # Extract episode metadata
                        tmdb_url = f"https://api.themoviedb.org/3/tv/{s_tmdb_id}/season/{s_season}/episode/{s_ep}?api_key={payload.tmdb_api_key}{lang_param}"
                        res = requests.get(tmdb_url)
                        if res.status_code == 200:
                            data = res.json()
                            ep_title = data.get("name", f"Episodio {s_ep}")
                            p_path = f"https://image.tmdb.org/t/p/w500{data.get('still_path')}" if data.get("still_path") else ""
                            
                            # And the show title
                            show_url = f"https://api.themoviedb.org/3/tv/{s_tmdb_id}?api_key={payload.tmdb_api_key}{lang_param}"
                            s_res = requests.get(show_url)
                            if s_res.status_code == 200:
                                show_data = s_res.json()
                                s_title = show_data.get("name", "")
                                f_path = f"https://image.tmdb.org/t/p/original{show_data.get('backdrop_path')}" if show_data.get("backdrop_path") else ""
                                
                                cursor.execute("UPDATE watch_history SET title=?, show_title=?, poster_path=?, fanart_path=? WHERE id=?", (ep_title, s_title, p_path, f_path, h_id))
                                
                conn.commit()
                conn.close()
                print("Massive rescan completed successfully.")
            except Exception as e:
                print(f"Error in rescan_task: {e}")
                
        threading.Thread(target=rescan_task, daemon=True).start()
        
    return {"status": "success"}

class UpdateIgnoreRequest(BaseModel):
    ignore_version: str = ""
    never_notify: bool = False

@app.get("/api/update/status", dependencies=[Depends(verify_api_key)])
def update_status():
    update_available = os.getenv("UPDATE_AVAILABLE", "")
    ignored_version = os.getenv("IGNORED_UPDATE_VERSION", "")
    notify_updates = os.getenv("NOTIFY_UPDATES", "true").lower() == "true"
    
    if update_available and update_available != ignored_version and notify_updates:
        return {"has_update": True, "version": update_available}
    return {"has_update": False}

@app.post("/api/update/ignore", dependencies=[Depends(verify_api_key)])
def update_ignore(req: UpdateIgnoreRequest):
    # Update .conf
    conf_lines = []
    if os.path.exists(".conf"):
        with open(".conf", "r") as f:
            conf_lines = f.readlines()
            
    # Set IGNORED_UPDATE_VERSION and NOTIFY_UPDATES in .conf
    new_conf_lines = []
    for line in conf_lines:
        if line.startswith("IGNORED_UPDATE_VERSION="): continue
        if line.startswith("NOTIFY_UPDATES="): continue
        if line.startswith("UPDATE_AVAILABLE="): continue
        new_conf_lines.append(line)
        
    if req.ignore_version:
        new_conf_lines.append(f"IGNORED_UPDATE_VERSION={req.ignore_version}\n")
        os.environ["IGNORED_UPDATE_VERSION"] = req.ignore_version
        
    if req.never_notify:
        new_conf_lines.append("NOTIFY_UPDATES=false\n")
        os.environ["NOTIFY_UPDATES"] = "false"
    else:
        new_conf_lines.append("NOTIFY_UPDATES=true\n")
        os.environ["NOTIFY_UPDATES"] = "true"
        
    # Clear UPDATE_AVAILABLE
    new_conf_lines.append("UPDATE_AVAILABLE=\n")
    os.environ["UPDATE_AVAILABLE"] = ""
        
    with open(".conf", "w") as f:
        f.writelines(new_conf_lines)
        
    return {"status": "success"}

@app.post("/api/update/trigger", dependencies=[Depends(verify_api_key)])
def update_trigger():
    import subprocess
    try:
        # We start the systemd service. We don't wait for it because it will restart our service!
        subprocess.Popen(["sudo", "systemctl", "start", "syncpk-updater.service"])
        return {"status": "success", "message": "Update triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- SERVE FRONTEND ---
# Mount the static folder at the end to avoid overwriting routes /api/
os.makedirs("static", exist_ok=True)
@app.get("/")
def serve_index():
    index_path = os.path.join("static", "index.html")
    if not os.path.exists(index_path):
        return HTMLResponse("index.html not found", status_code=404)
        
    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    dashboard_lang = os.getenv("DASHBOARD_LANGUAGE", "auto")
    inject_script = f"<script>window.DASHBOARD_LANG = '{dashboard_lang}';</script>"
    content = content.replace("<head>", f"<head>\n    {inject_script}", 1)
    
    return HTMLResponse(content=content)

app.mount("/", StaticFiles(directory="static"), name="static")
