import asyncio
import base64
import ctypes
import datetime
import difflib
import hashlib
import hmac
import httpx
import json
import logging
import os
import queue
import random, math
import re
import requests
import requests, time
import secrets
import shutil
import sqlite3
import string
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from fastapi import FastAPI, Request, Query, Depends, HTTPException, Header, Cookie, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from collections import defaultdict
from collections import deque
from contextlib import closing
from logging.handlers import RotatingFileHandler
from pydantic import BaseModel
from typing import Optional, List

#v8

# WebSocket Manager para eventos en tiempo real
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

DATA_DIR = os.getenv("DATA_DIR", ".")
ENV_PATH = os.path.join(DATA_DIR, ".env")
DB_PATH = os.path.join(DATA_DIR, "sync.db")
SETTINGS_FILE = os.path.join(DATA_DIR, "plex_settings.json")
LOG_FILE = os.path.join(DATA_DIR, "syncpk.log")
CACHE_PATH = os.path.join(DATA_DIR, "cache") 

# Shared state for background rescan task
rescan_status = {"running": False, "done": False}



logging.basicConfig(
    handlers=[RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=1, encoding='utf-8')],
    level=logging.INFO,
    format='%(message)s'
)

class DualLogger(object):
    def __init__(self, is_stderr=False):
        self.terminal = sys.stderr if is_stderr else sys.__stdout__
        self.logger = logging.getLogger('SyncPK')
        self.buf = ""
        self.lock = threading.Lock()
        
    def write(self, message):
        with self.lock:
            self.terminal.write(message)
        self.terminal.flush()
        self.buf += message
        if '\n' in self.buf:
            lines = self.buf.split('\n')
            for line in lines[:-1]:
                if line.strip():
                    self.logger.info(line.strip())
            self.buf = lines[-1]
            
    def flush(self):
        with self.lock:
            self.terminal.flush()
        if self.buf.strip():
            self.logger.info(self.buf.strip())
            self.buf = ""

sys.stdout = DualLogger(is_stderr=False)
sys.stderr = DualLogger(is_stderr=True)

#### TODOLIST ####

# last minute found: python plex api: https://github.com/pushingkarmaorg/python-plexapi
# check the plex api of the previous proyect
# delete or replace the "x-plex-client-identifier" header, problably is not needed.
# add the watchlist option, we can have it in dashboard and when one of the whatchlist items is added to bd, automatically deleted from watchlist
# more statistics options
# maybe a small mark in the dashboard to see what items are not in the plex library anymore (need to see how we can detect when the user delete something in plex)
# manual option to re-scan the plex library to import in our local db
# ahora al pulsar sobre una tarjeta, se abre su enlace de themoviedatabase. Habia pensado en qeu cuando hicieramos scraping, descargaramos toda
# la información que necesitamos, el titulo original, el director, los actores.. y lo guardamos todo en otra tabla, una de scraping (incluidos los enlaces a el arte)
# en el registro original dejamos una fk a dicho registro. 
# al pulsar mostramos una pantalla nuestra interna donde mostramos todo. 
# si se cambia cualquier cosa de apariencia, se debe recargar el interfaz automaticamente al guardar. Ademas, se deben aplicar los cambios a las tarjetas... obvio

# irnos al historial de descargas de xbytes y recuperar TODAS las peliculas que nos hemos bajado y hacer una especie de pyton que sea capaz de conectarse a la bd 
# y crear de forma automatica registros de visualización (intentando respetar la fecha de descarga, pero buscando una adecuada). 
# Lo suyo es que despues intentara hacer el scrooble en plex...

#### DONE ####

# implement something to check if the use has pless pass, maybe with the api to get user information?
# Auto Update desde el repo, bueno, que pregunte al menos... o bien ponemos un parámetro. 
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


# Manual .env fallback
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")



app = FastAPI()

@app.websocket("/ws/updates")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # We don't expect messages from client, just keep connection open
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# --- SETUP GUARD MIDDLEWARE ---
# Blocks all /api/* calls (except /api/setup) when the .env has not been created yet.
@app.middleware("http")
async def setup_guard(request, call_next):
    path = request.url.path
    if not os.path.exists(ENV_PATH):
        # Allow: setup endpoint, time healthcheck, static assets, and the root HTML route
        if path.startswith("/api/") and not path.startswith("/api/setup") and path != "/api/time":
            return JSONResponse(
                status_code=403,
                content={"error": "SyncPK is not configured yet. Complete the setup wizard first."}
            )
    return await call_next(request)

# --- CUSTOM ACCESS LOG MIDDLEWARE ---

logging.basicConfig(level=logging.INFO, format='\x1b[32m%(levelname)s\x1b[0m:     %(message)s')
access_logger = logging.getLogger("syncpk.access")

@app.middleware("http")
async def access_log_middleware(request, call_next):
    start_time = time.time()
    response = await call_next(request)
    
    path = request.url.path
    # Prevent spam in physical logs if there is no error
    if response.status_code in [200, 304] and (path.startswith("/cache/") or path.startswith("/static/") or path == "/favicon.ico"):
        return response
        
    process_time = (time.time() - start_time) * 1000
    client_ip = request.client.host if request.client else "127.0.0.1"
    
    # Colored format like Uvicorn
    color = "\x1b[32m" if response.status_code < 400 else "\x1b[31m"
    access_logger.info(f'{client_ip} - "\x1b[1m{request.method} {path} HTTP/1.1\x1b[0m" {color}{response.status_code}\x1b[0m ({process_time:.2f}ms)')
    
    return response

# --- PLEX & SECURITY CONFIGURATION ---
PLEX_URL = os.getenv("PLEX_URL", "")
PLEX_TOKEN = os.getenv("PLEX_TOKEN", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
SALT = os.getenv("SALT", "")          # Used for API token verification (SHA-256, high-entropy token)
WEB_SALT = os.getenv("WEB_SALT", "")  # Dedicated salt for the web password (scrypt KDF)
WEB_HASH = os.getenv("WEB_HASH", "")
API_HASH = os.getenv("API_HASH", "")
HAS_PLEX_PASS = os.getenv("HAS_PLEX_PASS", "false").lower() == "true"
PLEX_CLIENT_ID = os.getenv("PLEX_CLIENT_ID", "syncpk-default")
FANART_MASK_OPACITY = os.getenv("FANART_MASK_OPACITY", "0.3")

plex_headers = {"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN}

def reload_settings():
    """Re-read all global security/config variables from the .env file.
    Call this any time the .env is written (setup wizard, save config, restore)."""
    global PLEX_URL, PLEX_TOKEN, TMDB_API_KEY, SALT, WEB_SALT, WEB_HASH, \
           API_HASH, HAS_PLEX_PASS, PLEX_CLIENT_ID, FANART_MASK_OPACITY, plex_headers
    # Re-parse .env into os.environ first
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ[_k.strip()] = _v.strip().strip('"').strip("'")
    # Reassign all globals
    PLEX_URL           = os.getenv("PLEX_URL", "")
    PLEX_TOKEN         = os.getenv("PLEX_TOKEN", "")
    TMDB_API_KEY       = os.getenv("TMDB_API_KEY", "")
    SALT               = os.getenv("SALT", "")
    WEB_SALT           = os.getenv("WEB_SALT", "")
    WEB_HASH           = os.getenv("WEB_HASH", "")
    API_HASH           = os.getenv("API_HASH", "")
    HAS_PLEX_PASS      = os.getenv("HAS_PLEX_PASS", "false").lower() == "true"
    PLEX_CLIENT_ID     = os.getenv("PLEX_CLIENT_ID", "syncpk-default")
    FANART_MASK_OPACITY = os.getenv("FANART_MASK_OPACITY", "0.3")
    plex_headers       = {"Accept": "application/json", "X-Plex-Token": PLEX_TOKEN}

def verify_api_key(
    authorization: str = Header(None),
    syncpk_session: str = Cookie(None)
):
    if not WEB_HASH:
        raise HTTPException(status_code=401, detail="Setup incomplete: Password not configured")
    
    token = None
    
    # 1. Intentar leer del Header (para clientes API externos o KODI)
    if authorization and authorization.startswith("Basic "):
        token = authorization.replace("Basic ", "").strip()
        
    # 2. Intentar leer de la Cookie (para el dashboard web)
    elif syncpk_session:
        # La cookie ya contiene el hash directo (web_hash), podemos compararlo
        if hmac.compare_digest(syncpk_session, WEB_HASH):
            return True
        raise HTTPException(status_code=401, detail="Invalid Session Cookie")
    
    # Si viene por Header, calculamos el hash y comprobamos
    if token:
        token_hash = hashlib.sha256((token + SALT).encode()).hexdigest()
        if hmac.compare_digest(token_hash, WEB_HASH):
            return True
            
    raise HTTPException(status_code=401, detail="Missing or invalid authentication")

def verify_webhook_token(token: Optional[str] = Query(None)):
    if not API_HASH:
        return True
        
    if not token:
        raise HTTPException(status_code=401, detail="Missing webhook token")
        
    token_hash = hashlib.sha256((token + SALT).encode()).hexdigest()
    if not hmac.compare_digest(token_hash, API_HASH):
        raise HTTPException(status_code=401, detail="Invalid API Token")
    return True



def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db_connection()
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
            fanart_path TEXT,
            year INTEGER DEFAULT NULL,
            show_year INTEGER DEFAULT NULL
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

    # Point 13: Cleanup tombstones older than 5 years
    try:
        five_years_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365*5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor.execute("DELETE FROM deleted_history WHERE deleted_at < ?", (five_years_ago,))
    except Exception as e:
        print(f"Error cleaning up tombstones: {e}")

    conn.commit()
    conn.close()

init_db()


os.makedirs(os.path.join(CACHE_PATH, "posters"), exist_ok=True)
os.makedirs(os.path.join(CACHE_PATH, "fanarts"), exist_ok=True)

# --- UNIFIED ARTWORK LOGIC ---

def tmdb_get(url, **kwargs):
    headers = kwargs.get("headers", {})
    if len(TMDB_API_KEY) > 40:
        headers["Authorization"] = f"Bearer {TMDB_API_KEY}"
        if "?" in url:
            url = re.sub(r"&?api_key=[^&]+", "", url)
            url = url.replace("?&", "?").rstrip("?")
    else:
        if "?" not in url:
            url += f"?api_key={TMDB_API_KEY}"
        elif "api_key=" not in url:
            url += f"&api_key={TMDB_API_KEY}"
    kwargs["headers"] = headers
    kwargs.setdefault("timeout", 15)
    return requests.get(url, **kwargs)

def _get_tmdb_size(desired_px, valid_sizes):
    for size in valid_sizes:
        if size == 'original': return size
        try:
            px = int(size[1:])
            if px >= desired_px: return size
        except: pass
    return 'original'

def _get_artwork_sizes(poster_pref, fanart_pref, is_episode):
    poster_q = os.getenv("POSTER_QUALITY", "w185")
    fanart_q = os.getenv("FANART_QUALITY", "w300")
    ui_poster_w = int(os.getenv("UI_POSTER_W", "108"))
    ui_fanart_w = int(os.getenv("UI_FANART_W", "288"))

    p_size = poster_q
    if poster_q == "dynamic":
        p_size = _get_tmdb_size(ui_poster_w, ['w92', 'w154', 'w185', 'w342', 'w500', 'w780', 'original'])

    f_size = fanart_q
    if fanart_q == "dynamic":
        if is_episode:
            f_size = _get_tmdb_size(ui_fanart_w, ['w92', 'w185', 'w300', 'original'])
        else:
            f_size = _get_tmdb_size(ui_fanart_w, ['w300', 'w780', 'w1280', 'original'])
            
    return p_size, f_size

def _extract_filename(tmdb_id, is_poster, media_type, show_tmdb_id, season, episode, p_size, f_size):
    sync_lang = os.getenv("SYNC_LANGUAGE", "en")
    poster_pref = os.getenv("POSTER_PREF", "show")
    fanart_pref = os.getenv("FANART_PREF", "episode")
    pref_tag = poster_pref if is_poster else fanart_pref
    
    if media_type == "movie": 
        size_str = p_size if is_poster else f_size
        return f"movie_{tmdb_id}_{sync_lang}_{size_str}.jpg"
        
    if is_poster:
        return f"show_{show_tmdb_id}_s{season}_poster_{pref_tag}_{sync_lang}_{p_size}.jpg" if pref_tag == "season" else f"show_{show_tmdb_id}_poster_{pref_tag}_{sync_lang}_{p_size}.jpg"
        
    if fanart_pref == "episode": 
        return f"show_{show_tmdb_id}_s{season}e{episode}_fanart_{pref_tag}_{sync_lang}_{f_size}.jpg"
        
    return f"show_{show_tmdb_id}_fanart_{pref_tag}_{sync_lang}_{f_size}.jpg"

# Global semaphore for downloads to prevent rate limits
artwork_semaphore = asyncio.Semaphore(15)

async def _download_single_image(client, url_suffix, local_filename, folder, target_size):
    if not url_suffix: return False
    local_path = os.path.join(CACHE_PATH, folder, local_filename)
    if os.path.exists(local_path): return True
    img_url = f"https://image.tmdb.org/t/p/{target_size}{url_suffix}"
    async with artwork_semaphore:
        for attempt in range(4):
            try:
                resp = await client.get(img_url, timeout=15)
                if resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status_code == 200:
                    os.makedirs(os.path.join(CACHE_PATH, folder), exist_ok=True)
                    tmp_path = local_path + ".tmp"
                    with open(tmp_path, "wb") as f:
                        f.write(resp.content)
                    os.replace(tmp_path, local_path)
                    return True
                return False
            except Exception:
                await asyncio.sleep(2 ** attempt)
        return False

async def fetch_json(client, url):
    async with artwork_semaphore:
        for attempt in range(4):
            try:
                resp = await client.get(url, timeout=15)
                if resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status_code == 200:
                    return resp.json()
                return None
            except Exception:
                await asyncio.sleep(2 ** attempt)
        return None

async def download_artwork_async(client, media_type, tmdb_id, show_tmdb_id, season, episode, title, show_title):
    # Determine sizes and filenames
    sync_lang = os.getenv("SYNC_LANGUAGE", "en")
    poster_pref = os.getenv("POSTER_PREF", "show")
    fanart_pref = os.getenv("FANART_PREF", "episode")
    
    is_episode = (media_type == "episode" and fanart_pref == "episode")
    p_size, f_size = _get_artwork_sizes(poster_pref, fanart_pref, is_episode)
    
    p_filename = _extract_filename(tmdb_id, True, media_type, show_tmdb_id, season, episode, p_size, f_size)
    f_filename = _extract_filename(tmdb_id, False, media_type, show_tmdb_id, season, episode, p_size, f_size)
    
    updated_info = {"title": title, "show_title": show_title, "poster_path": p_filename, "fanart_path": f_filename}
    
    has_poster = os.path.exists(os.path.join(CACHE_PATH, "posters", p_filename))
    has_fanart = os.path.exists(os.path.join(CACHE_PATH, "fanarts", f_filename))
    
    if has_poster and has_fanart:
        return updated_info
        
    lang_param = f"&language={sync_lang}"
    if not TMDB_API_KEY:
        return updated_info
        
    if media_type == "movie" and tmdb_id:
        data = await fetch_json(client, f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}{lang_param}")
        if data:
            updated_info["title"] = data.get("title", title)
            await _download_single_image(client, data.get("poster_path"), p_filename, "posters", p_size)
            await _download_single_image(client, data.get("backdrop_path"), f_filename, "fanarts", f_size)

    elif media_type == "episode" and show_tmdb_id:
        ep_data, show_data, season_data = await asyncio.gather(
            fetch_json(client, f"https://api.themoviedb.org/3/tv/{show_tmdb_id}/season/{season}/episode/{episode}?api_key={TMDB_API_KEY}{lang_param}"),
            fetch_json(client, f"https://api.themoviedb.org/3/tv/{show_tmdb_id}?api_key={TMDB_API_KEY}{lang_param}"),
            fetch_json(client, f"https://api.themoviedb.org/3/tv/{show_tmdb_id}/season/{season}?api_key={TMDB_API_KEY}{lang_param}") if poster_pref == "season" else asyncio.sleep(0)
        )
        
        if show_data:
            updated_info["show_title"] = show_data.get("name", show_title)
            
        if ep_data:
            updated_info["title"] = ep_data.get("name", title)
            f_url = ep_data.get("still_path")
            if fanart_pref == "show" and show_data: f_url = show_data.get("backdrop_path")
            await _download_single_image(client, f_url, f_filename, "fanarts", f_size)
            
        if poster_pref == "season" and season_data:
            await _download_single_image(client, season_data.get("poster_path"), p_filename, "posters", p_size)
        elif poster_pref == "show" and show_data:
            await _download_single_image(client, show_data.get("poster_path"), p_filename, "posters", p_size)

    return updated_info

def download_artwork_sync(media_type, tmdb_id, show_tmdb_id, season, episode, title, show_title):
    async def wrapper():
        async with httpx.AsyncClient() as client:
            return await download_artwork_async(client, media_type, tmdb_id, show_tmdb_id, season, episode, title, show_title)
            
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(wrapper())
    finally:
        loop.close()
# --- END UNIFIED ARTWORK LOGIC ---

async def execute_full_rescan(sync_lang, tmdb_key, poster_pref, fanart_pref, is_debug=False, main_loop=None):
    print(f"[rescan] Starting full library rescan for language: {sync_lang}")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, tmdb_id, show_tmdb_id, media_type, season, episode, title, show_title, poster_path, fanart_path FROM watch_history")
        rows = cursor.fetchall()
        total = len(rows)
        print(f"[rescan] {total} items to process.")

        # Announce total immediately so frontend shows "0 de X" before first batch arrives
        await manager.broadcast(json.dumps({"type": "step3_init", "total": total}))

        async def process_item(client, idx, row):
            h_id, m_tmdb_id, s_tmdb_id, m_type, s_season, s_ep, db_title, db_show_title, db_poster, db_fanart = row
            target_tmdb = m_tmdb_id if m_type == "movie" else s_tmdb_id
            
            # Use unified logic!
            updated_info = await download_artwork_async(
                client, m_type, target_tmdb, s_tmdb_id, s_season, s_ep, db_title, db_show_title
            )
            updated_info["id"] = h_id
            updated_info["m_type"] = m_type
            return updated_info
        def _notify_frontend(batch_data):
            """Schedule a batch_update WS broadcast (non-blocking, called from sync context inside async)."""
            if not main_loop or not batch_data: return
            try:
                msg = json.dumps({"type": "batch_update", "items": batch_data, "total": total})
                asyncio.run_coroutine_threadsafe(manager.broadcast(msg), main_loop)
            except: pass

        async with httpx.AsyncClient() as client:
            chunk_size = 50
            for i in range(0, len(rows), chunk_size):
                chunk_rows = rows[i:i+chunk_size]
                tasks = [process_item(client, i + idx, row) for idx, row in enumerate(chunk_rows, 1)]
                results = await asyncio.gather(*tasks)
                
                batch_updates = []
                for res in results:
                    if res["m_type"] == "movie":
                        cursor.execute("UPDATE watch_history SET title=?, poster_path=?, fanart_path=? WHERE id=?", 
                                       (res["title"], res["poster_path"], res["fanart_path"], res["id"]))
                    else:
                        cursor.execute("UPDATE watch_history SET title=?, show_title=?, poster_path=?, fanart_path=? WHERE id=?", 
                                       (res["title"], res["show_title"], res["poster_path"], res["fanart_path"], res["id"]))
                    
                    res.pop("m_type", None)
                    batch_updates.append(res)
                    
                conn.commit()
                _notify_frontend(batch_updates)
                print(f"[Import TMDB] Processed chunk {i//chunk_size + 1}, items {i+1} to {min(i+chunk_size, len(rows))}/{len(rows)}")
            
        conn.close()
        # Notify frontend that TMDB is fully done.
        # We use await here (we are in async context) so this is guaranteed to
        # fire AFTER every download inside this coroutine has completed.
        done_msg = json.dumps({"type": "import_done", "total": total})
        await manager.broadcast(done_msg)
        print("[rescan] Full library rescan completed successfully.")
    except Exception as e:
        print(f"[rescan] Error during rescan: {e}")
        traceback.print_exc()
        # Even on error, signal the frontend so it doesn't hang
        try:
            err_msg = json.dumps({"type": "import_done", "total": total, "error": True})
            await manager.broadcast(err_msg)
        except: pass

async def bulk_download_tmdb_images():
    print("Starting bulk TMDB image download (full rescan logic)...")
    settings = load_settings()
    lang = os.getenv("SYNC_LANGUAGE", "en")
    tmdb_key = os.getenv("TMDB_API_KEY", "")
    p_pref = os.getenv("POSTER_PREF", "show")
    f_pref = os.getenv("FANART_PREF", "episode")
    is_debug = os.getenv("DEBUG", "false") == "true"
    
    loop = asyncio.get_running_loop()
    await execute_full_rescan(lang, tmdb_key, p_pref, f_pref, is_debug, loop)
    
    settings = load_settings()
    if settings.get("sync_state") in [1, 3]:
        settings["sync_state"] = 2
        save_settings(settings)
    print("Bulk TMDB image download completed!")

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
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())
            metadata = data.get("MediaContainer", {}).get("Metadata", [])
            if metadata:
                guids = metadata[0].get("Guid", [])
                return extract_ids(guids)
    except Exception as e:
        print(f"Error sacando IDs de la serie: {e}")
    return None, None, None

_my_plex_account_id = None

def get_my_plex_account_id() -> str:
    global _my_plex_account_id
    if _my_plex_account_id:
        return _my_plex_account_id
    try:
        r = requests.get(f"{PLEX_URL}/", headers=plex_headers, timeout=5)
        if r.status_code == 200:
            _my_plex_account_id = str(r.json().get("MediaContainer", {}).get("myPlexUserId", ""))
    except Exception as e:
        print(f"[account] No se pudo obtener accountID: {e}")
    return _my_plex_account_id or ""

def process_plex_payload(payload, cursor, is_bulk=False):
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] process_plex_payload: Processing webhook event '{payload.get('event')}'")
        
    account = payload.get("Account", {})
    if account:
        my_id = get_my_plex_account_id()
        if my_id and str(account.get("id", "")) != my_id:
            print(f"⏭️ Webhook ignorado: pertenece a otra cuenta ({account.get('title')})")
            return False
            
    if payload.get("event") != "media.scrobble": return False
        
    metadata = payload.get("Metadata", {})
    media_type = metadata.get("type") 
    if media_type not in ["movie", "episode"]: return False
    title = metadata.get("title")
    plex_guid = metadata.get("guid") 
    
    watched_at_payload = metadata.get("watched_at")
    is_live_event = not watched_at_payload and not is_bulk
    
    watched_at = watched_at_payload
    if not watched_at:
        watched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    duration = int(metadata.get("duration", 0)) // 60000
    year = metadata.get("year")
    show_year = metadata.get("show_year")
    
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

    if is_live_event:
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        if media_type == "episode" and tmdb_id:
            cursor.execute("""
                SELECT id FROM watch_history
                WHERE media_type='episode' AND tmdb_id=?
                AND substr(watched_at, 1, 10) = ?
            """, (tmdb_id, today))
        elif media_type == "episode":
            cursor.execute("""
                SELECT id FROM watch_history
                WHERE media_type='episode' AND show_title=? AND season=? AND episode=?
                AND substr(watched_at, 1, 10) = ?
            """, (show_title, season, episode, today))
        elif tmdb_id:
            cursor.execute("""
                SELECT id FROM watch_history
                WHERE media_type='movie' AND tmdb_id=?
                AND substr(watched_at, 1, 10) = ?
            """, (tmdb_id, today))
        else:
            cursor.execute("""
                SELECT id FROM watch_history
                WHERE media_type='movie' AND title=?
                AND substr(watched_at, 1, 10) = ?
            """, (title, today))
            
        if cursor.fetchone():
            print(f"🔁 Ignorando duplicado del mismo día (Plex): '{title}'")
            return False

    existing_id = None
    if media_type == "episode":
        if tmdb_id:
            cursor.execute("SELECT id, watched_at FROM watch_history WHERE media_type='episode' AND tmdb_id=?", (tmdb_id,))
        else:
            cursor.execute("SELECT id, watched_at FROM watch_history WHERE media_type='episode' AND show_title=? AND season=? AND episode=?", (show_title, season, episode))
    else:
        if tmdb_id:
            cursor.execute("SELECT id, watched_at FROM watch_history WHERE media_type='movie' AND tmdb_id=?", (tmdb_id,))
        else:
            cursor.execute("SELECT id, watched_at FROM watch_history WHERE media_type='movie' AND title=?", (title,))
            
    row = cursor.fetchone()
    if row and not is_live_event:
        existing_id = row[0]
        cursor.execute("""
            UPDATE watch_history SET
                plex_guid=?, plex_show_guid=?, duration=?,
                year=COALESCE(?, year), show_year=COALESCE(?, show_year)
            WHERE id=?
        """, (plex_guid, plex_show_guid, duration, year, show_year, existing_id))
        action = "Bulk Update (Solo IDs)"
        
        if media_type == "episode":
            prefix = "Bulk Import" if is_bulk else "Plex PUSH"
            print(f"🔄 {prefix} ({action}) [{watched_at}]: Serie '{show_title}' T{season}E{episode} - {title}")
        else:
            prefix = "Bulk Import" if is_bulk else "Plex PUSH"
            print(f"🔄 {prefix} ({action}) [{watched_at}]: Película '{title}'")
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
                watched_at, origin, created_at, duration, year, show_year
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            media_type, title, show_title, season, episode, 
            plex_guid, plex_show_guid,
            imdb_id, tmdb_id, tvdb_id, show_imdb_id, show_tmdb_id, show_tvdb_id,
            watched_at, 'plex', now_utc, duration, year, show_year
        ))
        
        if media_type == "episode":
            prefix = "Bulk Import" if is_bulk else "Plex PUSH"
            print(f"✅ {prefix} ({action}) [{watched_at}]: Serie '{show_title}' T{season}E{episode} - {title}")
        else:
            prefix = "Bulk Import" if is_bulk else "Plex PUSH"
            print(f"✅ {prefix} ({action}) [{watched_at}]: Película '{title}'")
            
    try:
        db_id = existing_id if existing_id else cursor.lastrowid
        target_tmdb = tmdb_id if media_type == "movie" else show_tmdb_id
        if not target_tmdb and media_type == "episode": target_tmdb = tmdb_id # Fallback
        
        # Download show poster/fanart or movie poster/fanart
        if not is_bulk:
            target_movie = tmdb_id if media_type == "movie" else None
            target_show = target_tmdb if media_type == "episode" else None
            
            # Use unified logic!
            updated_info = download_artwork_sync(
                media_type, target_movie, target_show, season, episode, title, show_title
            )
            p_path = updated_info.get("poster_path")
            f_path = updated_info.get("fanart_path")
            
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
    
    def _db_task():
        conn = get_db_connection()
        cursor = conn.cursor()
        if process_plex_payload(payload, cursor):
            conn.commit()
        conn.close()
    await run_in_threadpool(_db_task)
    
    return {"status": "success"}

@app.post("/webhook/kodi", dependencies=[Depends(verify_webhook_token)])
async def kodi_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}
        
    def _db_task():
        conn = get_db_connection()
        cursor = conn.cursor()
        if process_kodi_payload(payload, cursor):
            conn.commit()
        conn.close()
    await run_in_threadpool(_db_task)
    
    return {"status": "success"}

def process_kodi_payload(payload, cursor, is_bulk=False):
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] process_kodi_payload: Processing webhook event '{payload.get('event')}'")
    if payload.get("event") != "media.scrobble": return False
        
    metadata = payload.get("Metadata", {})
    media_type = metadata.get("type") 
    if media_type not in ["movie", "episode"]: return False
    title = metadata.get("title")
    kodi_id = metadata.get("kodi_id")
    
    watched_at_payload = metadata.get("watched_at")
    is_live_event = not watched_at_payload and not is_bulk
    
    watched_at = watched_at_payload
    if not watched_at:
        watched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    duration = int(metadata.get("duration", 0)) // 60
    year = metadata.get("year")
    show_year = metadata.get("show_year") or metadata.get("year")
    
    unique_ids = metadata.get("unique_ids", {})
    imdb_id = unique_ids.get("imdb") or metadata.get("imdbnumber")
    tmdb_id = unique_ids.get("tmdb")
    tvdb_id = unique_ids.get("tvdb")
    
    if imdb_id and not tmdb_id and not tvdb_id and not imdb_id.startswith("tt"): 
        print(f"⚠️ [Kodi] imdbnumber '{imdb_id}' sin prefijo 'tt', asumiendo TMDB ID. Verificar configuración Kodi.")
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

    plex_guid = None
    plex_rating_key = None
    
    plex_movies, plex_shows = get_plex_items_map()
    
    if media_type == "movie":
        matched = match_movie({"movie": {"title": title}}, plex_movies, tmdb_id, year=year)
        if matched:
            plex_guid = matched.get("guid")
            plex_rating_key = matched.get("ratingKey")
    elif media_type == "episode":
        s_season = int(season) if season is not None else None
        s_episode = int(episode) if episode is not None else None
        show_key = match_show({"show": {"title": show_title}}, plex_shows, show_tmdb_id, year=show_year)
        if show_key:
            try:
                r_eps = requests.get(f"{PLEX_URL}/library/metadata/{show_key}/allLeaves", headers=plex_headers, timeout=10)
                if r_eps.status_code == 200:
                    for ep in r_eps.json().get("MediaContainer", {}).get("Metadata", []):
                        if ep.get("parentIndex") == s_season and ep.get("index") == s_episode:
                            plex_guid = ep.get("guid")
                            plex_rating_key = ep.get("ratingKey")
                            break
            except Exception as e:
                pass

    if plex_guid is None:
        print(f"⚠️ [Kodi] No encontrado en Plex local: '{title}'. Guardando sin plex_guid.")

    if is_live_event:
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        if media_type == "episode" and tmdb_id:
            cursor.execute("""
                SELECT id FROM watch_history
                WHERE media_type='episode' AND tmdb_id=?
                AND substr(watched_at, 1, 10) = ?
            """, (tmdb_id, today))
        elif media_type == "episode":
            cursor.execute("""
                SELECT id FROM watch_history
                WHERE media_type='episode' AND show_title=? AND season=? AND episode=?
                AND substr(watched_at, 1, 10) = ?
            """, (show_title, season, episode, today))
        elif tmdb_id:
            cursor.execute("""
                SELECT id FROM watch_history
                WHERE media_type='movie' AND tmdb_id=?
                AND substr(watched_at, 1, 10) = ?
            """, (tmdb_id, today))
        else:
            cursor.execute("""
                SELECT id FROM watch_history
                WHERE media_type='movie' AND title=?
                AND substr(watched_at, 1, 10) = ?
            """, (title, today))
            
        if cursor.fetchone():
            print(f"🔁 Ignorando duplicado del mismo día: '{title}'")
            return False

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
                kodi_id=?, kodi_show_id=?, duration=?,
                year=COALESCE(?, year), show_year=COALESCE(?, show_year)
            WHERE id=?
        """, (kodi_id, kodi_show_id, duration, year, show_year, existing_id))
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
                watched_at, origin, created_at, duration, year, show_year, plex_guid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            media_type, title, show_title, season, episode, 
            kodi_id, kodi_show_id,
            imdb_id, tmdb_id, tvdb_id, show_imdb_id, show_tmdb_id, show_tvdb_id,
            watched_at, 'kodi', now_utc, duration, year, show_year, plex_guid
        ))
        
        if media_type == "episode":
            print(f"✅ Kodi PUSH ({action}): Serie '{show_title}' T{season}E{episode} - {title}")
        else:
            print(f"✅ Kodi PUSH (Nuevo): Película '{title}'")
            
    try:
        db_id = existing_id if existing_id else cursor.lastrowid
        target_tmdb = tmdb_id if media_type == "movie" else show_tmdb_id
        if not target_tmdb and media_type == "episode": target_tmdb = tmdb_id # Fallback
        
        target_movie = tmdb_id if media_type == "movie" else None
        target_show = target_tmdb if media_type == "episode" else None
        
        updated_info = download_artwork_sync(
            media_type, target_movie, target_show, season, episode, title, show_title
        )
        p_path = updated_info.get("poster_path")
        f_path = updated_info.get("fanart_path")
        
        if p_path or f_path:
            cursor.execute("UPDATE watch_history SET poster_path=COALESCE(?, poster_path), fanart_path=COALESCE(?, fanart_path) WHERE id=?", (p_path, f_path, db_id))
    except Exception as e:
        print(f"Error asignando carátulas Kodi: {e}")
            
    # Scrobble directo — ya tenemos el ratingKey, sin hilo separado
    if plex_rating_key and not existing_id:
        try:
            requests.get(
                f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={plex_rating_key}",
                headers=plex_headers, timeout=5
            )
            print(f"✅ [Kodi→Plex] Marcado como visto: '{title}'")
        except Exception as e:
            print(f"⚠️ [Kodi→Plex] Scrobble fallido para '{title}': {e}")
            
    return True


@app.post("/webhook/kodi/bulk", dependencies=[Depends(verify_webhook_token)])
async def kodi_webhook_bulk(request: Request):
    try:
        payloads = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}
        
    if not isinstance(payloads, list):
        return {"status": "error", "message": "Expected a list"}
        
    conn = get_db_connection()
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

@app.post("/sync/confirm-kodi", dependencies=[Depends(verify_webhook_token)])
def confirm_kodi_sync(req: ConfirmSyncRequest):
    if not req.ids:
        return {"status": "success", "deleted": 0}
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in req.ids)
    cursor.execute(f"DELETE FROM deleted_history WHERE id IN ({placeholders})", req.ids)
    conn.commit()
    conn.close()
    return {"status": "success", "deleted": len(req.ids)}

@app.get("/sync/all-items", dependencies=[Depends(verify_webhook_token)])
def get_all_items(client: Optional[str] = Query("kodi"), date_from: Optional[str] = Query(None)):
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT * FROM watch_history WHERE 1=1"
    params = []
    
    if date_from:
        query += " AND created_at >= ?"
        params.append(date_from)
        # Eliminamos la regla origin != client para que si hay mï¿½ltiples TVs con Kodi, todas reciban todo.
        
    cursor.execute(query, params)
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

    # Use scrypt (slow KDF) to verify the password against WEB_SALT+WEB_HASH.
    # Falls back to SHA-256 if WEB_SALT is missing (legacy installs).
    if WEB_SALT:
        pwd_hash = hashlib.scrypt(
            req.password.encode(),
            salt=WEB_SALT.encode(),
            n=16384, r=8, p=1
        ).hex()
    else:
        pwd_hash = hashlib.sha256((req.password + SALT).encode()).hexdigest()

    if hmac.compare_digest(pwd_hash, WEB_HASH):
        res = JSONResponse(content={"success": True})
        res.set_cookie(key="syncpk_session", value=WEB_HASH, httponly=True, samesite="lax", max_age=86400 * 30)
        return res
    raise HTTPException(status_code=401, detail="Invalid password")

@app.get("/api/history")
def get_history(limit: int = 20, offset: int = 0, type: str = "all", year: str = "all", month: str = "all", search: str = "", authorization: str = Depends(verify_api_key)):
    limit = min(limit, 100)
    conn = get_db_connection()
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
    conn = get_db_connection()
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


@app.get("/api/logs")
def get_logs(authorization: str = Depends(verify_api_key)):
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                lines = deque(f, maxlen=200)
            return {"logs": "".join(lines)}
        return {"logs": "No logs available."}
    except Exception as e:
        return {"logs": f"Error leyendo logs: {e}"}


@app.get("/api/download_logs", dependencies=[Depends(verify_api_key)])
def download_logs():
    try:
        if os.path.exists(LOG_FILE):
            # FileResponse se encarga automáticamente de los headers de descarga (Content-Disposition)
            return FileResponse(path=LOG_FILE, filename="syncpk_logs.txt", media_type="text/plain")
        
        return Response(content="No logs available.", media_type="text/plain")
    except Exception as e:
        error_str = str(e)
        error_str = re.sub(r'X-Plex-Token=[a-zA-Z0-9_-]+', 'X-Plex-Token=***', error_str)
        return {"error": f"Error descargando logs: {error_str}"}

@app.delete("/api/history/{item_id}")
def delete_history_item(item_id: int, scope: str = "episode", sync_remote: bool = False, authorization: str = Depends(verify_api_key)):
    if os.getenv("DEBUG") == "true":
        print(f"[DEBUG] delete_history_item: Deleting item_id {item_id}, scope={scope}, sync_remote={sync_remote}")
    conn = get_db_connection()
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
                
            # Clean up images if no longer used by any other row
            p_path = d_item.get("poster_path")
            f_path = d_item.get("fanart_path")
            if p_path:
                cursor.execute("SELECT COUNT(*) FROM watch_history WHERE poster_path = ?", (p_path,))
                if cursor.fetchone()[0] == 0:
                    try:
                        os.remove(os.path.join(CACHE_PATH, "posters", p_path))
                    except: pass
            if f_path:
                cursor.execute("SELECT COUNT(*) FROM watch_history WHERE fanart_path = ?", (f_path,))
                if cursor.fetchone()[0] == 0:
                    try:
                        os.remove(os.path.join(CACHE_PATH, "fanarts", f_path))
                    except: pass
                
    conn.commit()
    conn.close()
    
    if success_count == total_items:
        return {"success": True, "status": "success"}
    elif success_count > 0:
        return {"success": True, "status": "partial", "errors": errors}
    else:
        return {"success": False, "status": "error", "errors": errors}

@app.post("/api/cache/clean")
def clean_image_cache(authorization: str = Depends(verify_api_key)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT poster_path FROM watch_history WHERE poster_path IS NOT NULL")
    used_posters = {row[0] for row in cursor.fetchall()}
    cursor.execute("SELECT DISTINCT fanart_path FROM watch_history WHERE fanart_path IS NOT NULL")
    used_fanarts = {row[0] for row in cursor.fetchall()}
    conn.close()

    deleted_count = 0
    posters_dir = os.path.join(CACHE_PATH, "posters")
    if os.path.exists(posters_dir):
        for filename in os.listdir(posters_dir):
            if filename.endswith(".jpg") and filename not in used_posters:
                try:
                    os.remove(os.path.join(posters_dir, filename))
                    deleted_count += 1
                except: pass

    fanarts_dir = os.path.join(CACHE_PATH, "fanarts")
    if os.path.exists(fanarts_dir):
        for filename in os.listdir(fanarts_dir):
            if filename.endswith(".jpg") and filename not in used_fanarts:
                try:
                    os.remove(os.path.join(fanarts_dir, filename))
                    deleted_count += 1
                except: pass

    return {"status": "success", "deleted": deleted_count}

# --- UNIFIED PLEX ACTIVITY FEED FUNCTION ---
def get_plex_activity_nodes(metadata_id, types=None, max_timeout=600):
    if types is None:
        types = ["WATCH_HISTORY", "WATCH_SESSION"]
        
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
                if (time.time() - start_time) >= max_timeout:
                    return []
                retry_after = int(r.headers.get("Retry-After", 5))
                time.sleep(retry_after)
                continue
        except Exception:
            pass

        if (time.time() - start_time) >= max_timeout:
            return []
        
        time.sleep(5)

def mutate_plex_activity(node_id, action, watched_at_graphql=None, title="", max_timeout=600, main_loop=None):
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
                if os.getenv("DEBUG") == "true":
                    print(f"[DEBUG] mutate_plex_activity response for '{title}': {resp_json}", flush=True)
                    
                if "errors" in resp_json:
                    FATAL_CODES = {"NOT_FOUND", "FORBIDDEN", "UNAUTHORIZED", "BAD_USER_INPUT", "INVALID_ARGUMENT"}
                    for e in resp_json["errors"]:
                        code = e.get("extensions", {}).get("code", "")
                        if code in FATAL_CODES:
                            print(f"❌ [{action}] Error definitivo GraphQL ({code}) para '{title}'. Abortando.")
                            return False
                        if code == "RATE_LIMITED":
                            retry_s = int(e.get("extensions", {}).get("retryAfter", 10))
                            print(f"⚠️ [{action}] Rate Limit GraphQL. Esperando {retry_s}s...")
                            if main_loop:
                                try: asyncio.run_coroutine_threadsafe(manager.broadcast(json.dumps({"type": "bulk_update_wait", "seconds": retry_s})), main_loop)
                                except: pass
                            time.sleep(retry_s)
                            if main_loop:
                                try: asyncio.run_coroutine_threadsafe(manager.broadcast(json.dumps({"type": "bulk_update_resume"})), main_loop)
                                except: pass
                    
                    print(f"⚠️ [{action}] Error GraphQL transitorio, reintentando: {resp_json['errors']}", flush=True)
                    time.sleep(2) # Default delay if other transient error
                else:
                    print(f"{msg} para '{title}': HTTP 200", flush=True)
                    return True
            elif r.status_code in (400, 403, 404):
                print(f"❌ [{action}] HTTP {r.status_code} para '{title}'. No reintentable. Abortando.")
                return False
            elif r.status_code == 429:
                if (time.time() - start_time) >= max_timeout:
                    return False
                retry_after = int(r.headers.get("Retry-After", 5))
                if main_loop:
                    try: asyncio.run_coroutine_threadsafe(manager.broadcast(json.dumps({"type": "bulk_update_wait", "seconds": retry_after})), main_loop)
                    except: pass
                time.sleep(retry_after)
                if main_loop:
                    try: asyncio.run_coroutine_threadsafe(manager.broadcast(json.dumps({"type": "bulk_update_resume"})), main_loop)
                    except: pass
                continue
        except Exception:
            pass
            
        if (time.time() - start_time) >= max_timeout:
            print(f"❌ Error in mutation '{action}' for '{title}': Timeout", flush=True)
            return False
            
        time.sleep(5)
# -------------------------------------------

def unscrobble_plex(item):
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
                print(f"⚠️ [unscrobble] No se encontró coincidencia de fecha para '{title}'. ABORTANDO para evitar borrado masivo.")
                return False
            else:
                # Find the single closest node instead of all
                try:
                    target_date_dt = datetime.datetime.strptime(target_date_str.replace("Z", "").split(".")[0], "%Y-%m-%dT%H:%M:%S")
                    closest_node = min(
                        nodes_to_delete, 
                        key=lambda x: abs((datetime.datetime.strptime(x.get("date").replace("Z", "").split(".")[0], "%Y-%m-%dT%H:%M:%S") - target_date_dt).total_seconds())
                    )
                    nodes_to_delete = [closest_node]
                except Exception as e:
                    pass

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
                parent_key = matched  # match_show returns ratingKey string directly
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

def perform_plex_surgery(item: dict, watched_at_local: str, main_loop=None):
    plex_guid = item.get("plex_guid")
    if not plex_guid: return False
    metadata_id = plex_guid.split("/")[-1]
    watched_at_graphql = watched_at_local.replace("Z", ".000Z")
    
    # --- NUEVO UNIFICADO ---
    node_id = None
    nodes = get_plex_activity_nodes(metadata_id)
    
    if nodes:
        node_id = nodes[0].get("id")
        try:
            target_date = datetime.datetime.strptime(watched_at_local.replace("Z", "").split(".")[0], "%Y-%m-%dT%H:%M:%S")
            closest_node = min(
                [n for n in nodes if n.get("date")], 
                key=lambda x: abs((datetime.datetime.strptime(x.get("date").replace("Z", "").split(".")[0], "%Y-%m-%dT%H:%M:%S") - target_date).total_seconds()),
                default=None
            )
            if closest_node:
                node_id = closest_node.get("id")
        except Exception as e:
            print("Error finding closest node in surgery:", e)
    else:
        print(f"No node for {item.get('title')}, triggering CLOUD scrobble...")
        cloud_headers = {
            "Accept": "application/json", "Content-Type": "application/json",
            "x-plex-client-identifier": PLEX_CLIENT_ID, "x-plex-token": PLEX_TOKEN
        }
        scrobble_url = f"https://metadata.provider.plex.tv/actions/scrobble?key={metadata_id}&identifier=tv.plex.provider.metadata"
        scrobble_sent = False
        start_time = time.time()
        max_wait = 600
        
        while (time.time() - start_time) < max_wait:
            if not scrobble_sent:
                try:
                    s_res = requests.get(scrobble_url, headers=cloud_headers, timeout=10)
                    if s_res.status_code == 200:
                        print(f"  ✅ Cloud Scrobble executed for {item.get('title')}")
                        scrobble_sent = True
                    elif s_res.status_code == 429:
                        print(f"  ⚠️ Rate limited (429) for {item.get('title')}. Response: {s_res.text}. Retrying in 15s...")
                        if main_loop:
                            try: asyncio.run_coroutine_threadsafe(manager.broadcast(json.dumps({"type": "bulk_update_wait", "seconds": 15})), main_loop)
                            except: pass
                        time.sleep(15)
                        if main_loop:
                            try: asyncio.run_coroutine_threadsafe(manager.broadcast(json.dumps({"type": "bulk_update_resume"})), main_loop)
                            except: pass
                        continue
                    else:
                        print(f"  ❌ Failed with HTTP {s_res.status_code} for {item.get('title')}. Response: {s_res.text}. Retrying in 15s...")
                        time.sleep(15)
                        continue
                except Exception as e:
                    print(f"  ❌ Exception: {e}. Retrying in 15s...")
                    time.sleep(15)
                    continue
            
            # If scrobble was sent successfully, just poll for the node
            nodes = get_plex_activity_nodes(metadata_id)
            if nodes:
                node_id = nodes[0].get("id")
                break
            time.sleep(5)
    # -------------------------------------------------------------
            
    if node_id:
        success = mutate_plex_activity(node_id, "update_date", watched_at_graphql=watched_at_graphql, title=item.get("title"), main_loop=main_loop)
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

def _update_in_background(item_id: int, req: UpdateHistoryRequest, main_loop=None):
    conn = get_db_connection()
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
    
    if main_loop:
        try: asyncio.run_coroutine_threadsafe(manager.broadcast(json.dumps({"type": "bulk_update_start", "total": total_items, "scope": scope, "season": item.get('season', ''), "show_title": item.get('show_title', item.get('title', ''))})), main_loop)
        except: pass
    
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
    
    days_counts = []
    
    if dist_mode == "same":
        days_counts = [total_items]
    elif dist_mode == "fixed":
        eps = max(1, getattr(req, "eps_per_day", 1))
        days_counts = [eps] * (total_items // eps)
        if total_items % eps > 0: days_counts.append(total_items % eps)
    elif dist_mode == "random":
        eps_min = max(1, getattr(req, "eps_min", 1))
        eps_max = max(eps_min, getattr(req, "eps_max", 3))
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
    
    if len(sorted_items) != len(assigned_dates):
        print(f"⚠️ [dist] {len(sorted_items)} ítems pero {len(assigned_dates)} fechas generadas.")
        
    for item, assign_dt in zip(sorted_items, assigned_dates):
        watched_str = assign_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        should_update_db = True
        if req.sync_remote:
            success = perform_plex_surgery(item, watched_str, main_loop=main_loop)
            if not success:
                should_update_db = False
                error_msg = f"Fallo en Plex Cloud para: {item.get('title')}"
                errors.append(error_msg)
                print(f"Skipping DB update for {item.get('title')} because Plex surgery failed.")
                
        if should_update_db:
            success_count += 1
            conn_update = get_db_connection()
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
            
        # Short 0.75s delay to prevent rate-limiting the Plex Cloud GraphQL API
        time.sleep(0.75)
        
        if main_loop:
            try: asyncio.run_coroutine_threadsafe(manager.broadcast(json.dumps({"type": "bulk_update_progress", "current": success_count, "total": total_items, "item_title": item.get("title"), "season": item.get("season", ""), "episode": item.get("episode", "")})), main_loop)
            except: pass
        
    if success_count == total_items:
        print("[background] Update success")
    elif success_count > 0:
        print(f"[background] Update partial errors: {errors}")
    else:
        print(f"[background] Update error: {errors}")
        
    if main_loop:
        try:
            msg = json.dumps({"type": "reload_history"})
            asyncio.run_coroutine_threadsafe(manager.broadcast(msg), main_loop)
        except Exception as e:
            print(f"[background] Error broadcasting reload: {e}")

@app.put("/api/history/{item_id}")
async def update_history_item(item_id: int, req: UpdateHistoryRequest, background_tasks: BackgroundTasks, authorization: str = Depends(verify_api_key)):
    loop = asyncio.get_running_loop()
    background_tasks.add_task(_update_in_background, item_id, req, loop)
    return {"success": True, "status": "success", "message": "Procesando en segundo plano..."}




# --- PLEX SYNC BACKGROUND LOGIC ---

SYNC_INTERVAL = 900

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {"last_sync_date": ""}

def save_settings(settings):
    tmp_file = SETTINGS_FILE + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump(settings, f)
    os.replace(tmp_file, SETTINGS_FILE)

def get_plex_libraries():
    try:
        r = requests.get(f"{PLEX_URL}/library/sections", headers=plex_headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            sections = data.get("MediaContainer", {}).get("Directory", [])
            return [{"key": s["key"], "type": s.get("type")} for s in sections if s.get("type") in ["movie", "show"]]
    except Exception as e:
        error_str = str(e)
        error_str = re.sub(r'X-Plex-Token=[a-zA-Z0-9_-]+', 'X-Plex-Token=***', error_str)
        print(f"Error getting Plex libraries: {error_str}")
    return []

def get_real_plex_history_map():
    print("Fetching real playback history from Plex...")
    history_map = {}
    try:
        url = f"{PLEX_URL}/status/sessions/history/all"
        r = requests.get(url, headers=plex_headers, timeout=10)
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
        err_str = re.sub(r'X-Plex-Token=[a-zA-Z0-9_-]+', 'X-Plex-Token=***', str(req_err))
        print(f"❌ Connection error to local Plex server ({PLEX_URL}): {err_str}", flush=True)
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
            "duration": item.get("duration", 0),
            "year": item.get("year")
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
            payload["Metadata"]["show_year"] = show_data.get("year")
        else:
            if "grandparentGuid" in item:
                payload["Metadata"]["grandparentGuid"] = item.get("grandparentGuid")
            if "grandparentGuids" in item:
                payload["Metadata"]["grandparentGuids"] = item.get("grandparentGuids")
        
    return payload

def push_all_to_db(main_loop=None):
    print("Starting FULL PUSH from Plex to local DB (Library Scan)...")
    
    payloads = []
    sections = get_plex_libraries()
    
    # Phase 0: Pre-flight to get total items for WebSocket progress
    total_items_to_process = 0
    lib_names = {}
    for sec in sections:
        headers = plex_headers.copy()
        headers["X-Plex-Container-Start"] = "0"
        headers["X-Plex-Container-Size"] = "0"
        try:
            if sec["type"] == "movie":
                r = requests.get(f"{PLEX_URL}/library/sections/{sec['key']}/all", headers=headers, timeout=10)
            elif sec["type"] == "show":
                r = requests.get(f"{PLEX_URL}/library/sections/{sec['key']}/all?type=4", headers=headers, timeout=10)
            else:
                continue
            if r.status_code == 200:
                mc = r.json().get("MediaContainer", {})
                total_items_to_process += mc.get("totalSize", 0)
                lib_names[sec['key']] = mc.get("title1", sec.get("title", f"Library {sec['key']}"))
        except Exception as e:
            print(f"Error pre-flighting section {sec['key']}: {e}")
    
    def _notify_step1(current_count, lib_name):
        if not main_loop: return
        try:
            msg = json.dumps({
                "type": "import_progress",
                "step": 0,
                "library": lib_name,
                "current": current_count,
                "total": total_items_to_process
            })
            asyncio.run_coroutine_threadsafe(manager.broadcast(msg), main_loop)
        except: pass
    
    processed_items_count = 0
    
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
                                "Guid": item.get("Guid", []),
                                "year": item.get("year")
                            }
                        start += size
                    else:
                        break
                except Exception as e:
                    print(f"Error mapping shows in section {sec['key']}: {e}")
                    break

    # Phase B: Immediate Extraction and Insertion
    conn = get_db_connection()
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
                                dedup_key = ("movie", item.get("title"), item.get("year"))
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
                        
                        processed_items_count += 1
                        if processed_items_count % 20 == 0 or processed_items_count == total_items_to_process:
                            lib_title = lib_names.get(sec['key'], f"Library {sec['key']}")
                            _notify_step1(processed_items_count, lib_title)
                            print(f"[Import Local] Processed {processed_items_count}/{total_items_to_process} ({lib_title})")
                            
                    start += size
                else:
                    break
            except Exception as e:
                print(f"Error scanning section {sec['key']}: {e}")
                break
            
    conn.close()
    _notify_step1(total_items_to_process, "Finished")
    print(f"✅ Initial Sync completed! {count} items processed.")
    
def push_recent_to_db(last_sync_utc_str):
    print("Starting INCREMENTAL PUSH from Plex to local DB...")
    try:
        last_sync_dt = datetime.datetime.strptime(last_sync_utc_str, '%Y-%m-%dT%H:%M:%SZ')
        last_sync_ts = int(last_sync_dt.replace(tzinfo=datetime.timezone.utc).timestamp())
    except Exception:
        last_sync_ts = 0
        
    my_id = get_my_plex_account_id()
    recent_sessions = []
    
    offset = 0
    limit = 50
    
    while True:
        url = f"{PLEX_URL}/status/sessions/history/all?sort=viewedAt:desc&X-Plex-Container-Start={offset}&X-Plex-Container-Size={limit}"
        try:
            r = requests.get(url, headers=plex_headers, timeout=15)
        except Exception as e:
            print(f"Error fetching history: {e}")
            break
            
        if r.status_code != 200: 
            break
            
        raw_sessions = r.json().get("MediaContainer", {}).get("Metadata", [])
        if not raw_sessions:
            break
            
        should_break = False
        for s in raw_sessions:
            if s.get("viewedAt", 0) < last_sync_ts:
                should_break = True
                break
            if not my_id or str(s.get("accountID", "")) == my_id:
                recent_sessions.append(s)
                
        if should_break or len(raw_sessions) < limit:
            break
            
        offset += limit
    
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
            det_r = requests.get(f"{PLEX_URL}/library/metadata/{r_key}", headers=plex_headers, timeout=10)
            if det_r.status_code == 200:
                item_data = det_r.json().get("MediaContainer", {}).get("Metadata", [])[0]
                m_type = item_data.get("type")
                if m_type in ["movie", "episode"]:
                    history_map = {r_key: viewed_at}
                    payload = build_payload_from_plex(item_data, m_type)
                    if payload:
                        payloads.append(payload)
        except Exception as e:
            print(f"[push_recent_to_db] Error processing ratingKey {r_key}: {e}")
            
    if payloads:
        conn = get_db_connection()
        cursor = conn.cursor()
        for p in payloads:
            process_plex_payload(p, cursor, is_bulk=False)
        conn.commit()
        conn.close()
        print(f"🚀 Incremental items processed.")
    return True

_plex_items_cache = {"data": None, "ts": 0}
_CACHE_TTL = 300  # 5 minutos

def get_plex_items_map(force_refresh=False):
    now = time.time()
    if not force_refresh and _plex_items_cache["data"] and (now - _plex_items_cache["ts"]) < _CACHE_TTL:
        return _plex_items_cache["data"]

    plex_movies = []
    plex_shows = []
    sections = get_plex_libraries()
    
    for sec in sections:
        try:
            sec_key = sec["key"]
            r = requests.get(f"{PLEX_URL}/library/sections/{sec_key}/all?includeGuids=1", headers=plex_headers, timeout=10)
            if r.status_code == 200:
                items = r.json().get("MediaContainer", {}).get("Metadata", [])
                for item in items:
                    if item.get("type") == "movie":
                        plex_movies.append(item)
                    elif item.get("type") == "show":
                        plex_shows.append(item)
        except Exception as e:
            print(f"[get_plex_items_map] Error on section {sec}: {e}")
            
    _plex_items_cache["data"] = (plex_movies, plex_shows)
    _plex_items_cache["ts"] = now
    return plex_movies, plex_shows

def match_movie(movie_data, plex_movies, tmdb_id=None, year=None):
    # 1. Match por GUID (prioritario)
    if tmdb_id:
        for pm in plex_movies:
            _, pm_tmdb, _ = extract_ids(pm.get("Guid", []))
            if pm_tmdb == str(tmdb_id):
                return pm
    # 2. Match por título + año exacto
    title = movie_data.get("movie", {}).get("title", "").lower()
    candidates = []
    for pm in plex_movies:
        if pm.get("title", "").lower() == title:
            if year and pm.get("year") and abs(int(pm.get("year")) - int(year)) <= 1:
                return pm  # match exacto título+año → retornar inmediatamente
            candidates.append(pm)
    # 3. Fallback: título exacto sin año (si solo hay un candidato)
    if len(candidates) == 1:
        return candidates[0]
    return None

def match_show(show_data, plex_shows, show_tmdb_id=None, year=None):
    # 1. GUID
    if show_tmdb_id:
        for ps in plex_shows:
            _, ps_tmdb, _ = extract_ids(ps.get("Guid", []))
            if ps_tmdb == str(show_tmdb_id):
                return ps.get("ratingKey")
    # 2. Título exacto + año
    title = show_data.get("show", {}).get("title", "").lower()
    candidates = []
    for ps in plex_shows:
        ps_title = ps.get("title", "").lower()
        if ps_title == title:
            if year and ps.get("year") and abs(int(ps.get("year")) - int(year)) <= 1:
                return ps.get("ratingKey")
            candidates.append(ps)
        elif difflib.SequenceMatcher(None, ps_title, title).ratio() > 0.92:
            candidates.append(ps)
    if len(candidates) == 1:
        return candidates[0].get("ratingKey")
    return None



def push_cloud_orphans_to_db(main_loop=None):
    print("Starting SMART EXTRACTOR V2 from Plex Cloud to local DB...")
    is_debug = os.getenv("DEBUG") == "true"
    
    conn = get_db_connection()
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
    items_notified = 0
    
    processed_shows = set()
    show_titles_cache = {}
    
    task_queue = queue.Queue()
    result_queue = queue.Queue()
    rate_limit_event = threading.Event()
    rate_limit_event.set()
    
    request_lock = threading.Lock()
    last_request_time = [0.0]
    
    def wait_for_rate_limit():
        rate_limit_event.wait()
        with request_lock:
            now = time.time()
            time_since_last = now - last_request_time[0]
            if time_since_last < 0.25:
                time.sleep(0.25 - time_since_last)
            last_request_time[0] = time.time()
            
    def _notify_step2(count, orphans, updates):
        if not main_loop: return
        try:
            msg = json.dumps({
                "type": "import_progress",
                "step": 1,
                "count": count,
                "orphans": orphans,
                "updates": updates,
                "orphans": orphans,
                "updates": updates
            })
            asyncio.run_coroutine_threadsafe(manager.broadcast(msg), main_loop)
        except: pass

    def worker_loop():
        while True:
            task = task_queue.get()
            if task is None:
                task_queue.task_done()
                break
                
            task_type = task.get("type")
            try:
                if task_type == "orphan":
                    resolve_orphan(task["node"], task["meta"], task["guid"], task["cloud_date"])
                elif task_type == "show_history":
                    resolve_show_history(task["show_id"], task["show_title"])
            except Exception as e:
                print(f"❌ [Worker] Error en tarea {task_type}: {e}")
            task_queue.task_done()

    def resolve_orphan(node, meta, guid, cloud_date):
        try:
            plex_metadata_id = guid.split("/")[-1]
            meta_url = f"https://metadata.provider.plex.tv/library/metadata/{plex_metadata_id}?X-Plex-Token={PLEX_TOKEN}&X-Plex-Language={lang}"
            
            wait_for_rate_limit()
            m_resp = requests.get(meta_url, headers={"Accept": "application/json"}, timeout=10)
            
            if m_resp.status_code == 429:
                retry = int(m_resp.headers.get("Retry-After", "60"))
                print(f"⏳ [Worker-429] Huérfano {meta.get('title')}: Esperando {retry}s...")
                rate_limit_event.clear()
                time.sleep(retry)
                rate_limit_event.set()
                task_queue.put({"type": "orphan", "node": node, "meta": meta, "guid": guid, "cloud_date": cloud_date})
                return
                
            if m_resp.status_code == 200:
                m_data = m_resp.json().get("MediaContainer", {}).get("Metadata", [])
                if m_data:
                    item = m_data[0]
                    m_type = item.get("type")
                    if m_type not in ["movie", "episode"]: return
                    actual_media_type = m_type
                    p = build_payload_from_plex(item, actual_media_type)
                    if not p: return
                    p["Metadata"]["watched_at"] = cloud_date
                    
                    if actual_media_type == "episode" and "grandparentGuid" in item:
                        gp_guid = item["grandparentGuid"]
                        cloud_guids = []
                        cloud_title = None
                        gp_id = gp_guid.split("/")[-1]
                        
                        try:
                            gp_url = f"https://metadata.provider.plex.tv/library/metadata/{gp_id}?X-Plex-Token={PLEX_TOKEN}&X-Plex-Language={lang}"
                            wait_for_rate_limit()
                            gp_resp = requests.get(gp_url, headers={"Accept": "application/json"}, timeout=5)
                            if gp_resp.status_code == 200:
                                gp_data = gp_resp.json().get("MediaContainer", {}).get("Metadata", [])
                                if gp_data:
                                    cloud_title = gp_data[0].get("title")
                                    cloud_guids = gp_data[0].get("Guid", [])
                        except: pass
                        
                        p["_cloud_gp_title"] = cloud_title
                        p["_cloud_gp_guids"] = cloud_guids
                        p["_gp_guid"] = gp_guid
                    
                    result_queue.put({"type": "insert_orphan", "payload": p, "guid": guid, "cloud_date": cloud_date, "meta": meta})
        except Exception as e:
            print(f"❌ [Worker] Error rescuing orphan {guid}: {e}")

    def resolve_show_history(show_id, show_title):
        h_next = True
        p_cursor = None
        page_num = 1
        
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
            start_time = time.time()
            success = False
            
            while True:
                try:
                    wait_for_rate_limit()
                    resp_sec = requests.post(url_graphql, headers=headers_fetch, json=payload_sec, timeout=30)
                    if resp_sec.status_code == 429:
                        retry = int(resp_sec.headers.get("Retry-After", "60"))
                        rate_limit_event.clear()
                        time.sleep(retry)
                        rate_limit_event.set()
                        continue
                    if resp_sec.status_code != 200:
                        if time.time() - start_time > 600: break
                        time.sleep(3)
                        continue
                        
                    data_sec = resp_sec.json().get("data", {}).get("activityFeed", {})
                    nodes_sec = data_sec.get("nodes", [])
                    
                    for n_sec in nodes_sec:
                        m_sec = n_sec.get("metadataItem")
                        if m_sec and m_sec.get("type") == "EPISODE":
                            result_queue.put({"type": "process_node", "node": n_sec})
                            
                    p_info = data_sec.get("pageInfo", {})
                    h_next = p_info.get("hasNextPage", False)
                    p_cursor = p_info.get("endCursor")
                    page_num += 1
                    success = True
                    break
                except Exception as e:
                    if time.time() - start_time > 600: break
                    time.sleep(3)
            if not success: break
                
    def writer_loop():
        nonlocal count_orphans, count_updates, items_notified
        while True:
            msg = result_queue.get()
            if msg is None:
                result_queue.task_done()
                break
                
            mtype = msg["type"]
            try:
                if mtype == "process_node":
                    node = msg["node"]
                    items_notified += 1
                    
                    cloud_date = node.get("date")
                    meta = node.get("metadataItem")
                    if not meta or not meta.get("guid"): 
                        continue
                        
                    guid = meta.get("guid")
                    if guid in local_items_by_guid:
                        local_date = local_items_by_guid[guid]["watched_at"]
                        if cloud_date and local_date:
                            def parse_iso_to_dt(s: str):
                                clean = s.replace("Z", "").split(".")[0]
                                return datetime.datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")
                            try:
                                cloud_dt = parse_iso_to_dt(cloud_date)
                                local_dt = parse_iso_to_dt(local_date)
                                if cloud_dt < local_dt:
                                    print(f"🔄 Updating date from {local_date} to {cloud_date} for {meta.get('title')}")
                                    cursor.execute("UPDATE watch_history SET watched_at = ?, created_at = ? WHERE id = ?", (cloud_date, now_utc, local_items_by_guid[guid]["id"]))
                                    local_items_by_guid[guid]["watched_at"] = cloud_date
                                    count_updates += 1
                            except: pass
                    else:
                        task_queue.put({"type": "orphan", "node": node, "meta": meta, "guid": guid, "cloud_date": cloud_date})
                        
                elif mtype == "insert_orphan":
                    p = msg["payload"]
                    guid = msg["guid"]
                    cloud_date = msg["cloud_date"]
                    meta = msg.get("meta", {})
                    
                    if "_gp_guid" in p:
                        gp_guid = p["_gp_guid"]
                        if gp_guid not in show_titles_cache:
                            cursor.execute("SELECT show_title FROM watch_history WHERE plex_show_guid = ? LIMIT 1", (gp_guid,))
                            row_show = cursor.fetchone()
                            local_title = row_show["show_title"] if (row_show and row_show["show_title"]) else None
                            show_titles_cache[gp_guid] = {
                                "title": local_title or p.get("_cloud_gp_title"),
                                "guids": p.get("_cloud_gp_guids", [])
                            }
                        cached_data = show_titles_cache[gp_guid]
                        if cached_data["title"]: p["Metadata"]["grandparentTitle"] = cached_data["title"]
                        if cached_data["guids"]: p["Metadata"]["grandparentGuids"] = cached_data["guids"]
                        for k in ["_cloud_gp_title", "_cloud_gp_guids", "_gp_guid"]: p.pop(k, None)

                    if process_plex_payload(p, cursor, is_bulk=True):
                        count_orphans += 1
                        cursor.execute("SELECT id FROM watch_history WHERE plex_guid=?", (guid,))
                        new_r = cursor.fetchone()
                        if new_r:
                            local_items_by_guid[guid] = {"id": new_r["id"], "watched_at": cloud_date}
                            print(f"✅ Orphan inserted: {meta.get('title')}")
                            
                elif mtype == "commit":
                    conn.commit()
            except Exception as e:
                print(f"❌ [Writer] Error en {mtype}: {e}")
            finally:
                _notify_step2(items_notified, count_orphans, count_updates)
                result_queue.task_done()

    workers = []
    for i in range(4):
        t = threading.Thread(target=worker_loop, daemon=True)
        t.start()
        workers.append(t)
        
    writer = threading.Thread(target=writer_loop, daemon=True)
    writer.start()

    retries_primary = 0
    total_nodes_processed = 0
    while has_next:
        payload = {
            "query": query_primary,
            "variables": {"first": 100, "after": page_cursor, "types": ["WATCH_HISTORY", "WATCH_SESSION"]},
            "operationName": "GetActivityFeed"
        }
        start_time = time.time()
        success = False
        
        while True:
            try:
                wait_for_rate_limit()
                resp = requests.post(url_graphql, headers=headers_fetch, json=payload, timeout=20)
                
                if resp.status_code == 429:
                    retry = int(resp.headers.get("Retry-After", "5"))
                    print(f"⚠️ [Plex Rate Limit] Plex temporarily blocked us. Waiting {retry} seconds...")
                    rate_limit_event.clear()
                    time.sleep(retry)
                    rate_limit_event.set()
                    continue
                if resp.status_code != 200:
                    print(f"⚠️ [Error API] HTTP {resp.status_code}. Retrying in 3s...")
                    if time.time() - start_time > 600: break
                    time.sleep(3)
                    continue
                    
                resp_json = resp.json()
                if "errors" in resp_json:
                    FATAL_CODES = {"NOT_FOUND", "FORBIDDEN", "UNAUTHORIZED", "BAD_USER_INPUT"}
                    is_fatal = any(e.get("extensions", {}).get("code", "") in FATAL_CODES for e in resp_json["errors"])
                    if is_fatal or time.time() - start_time > 600: break
                    time.sleep(3)
                    continue
                success = True
                break
            except Exception as e:
                if time.time() - start_time > 600: break
                time.sleep(3)
                
        if not success: break
            
        data = resp_json.get("data")
        if not data: break
        
        data = data.get("activityFeed", {})
        nodes = data.get("nodes", [])
        page_info = data.get("pageInfo", {})
        
        if not nodes: break
        
        for node in nodes:
            total_nodes_processed += 1
            meta = node.get("metadataItem")
            if not meta: continue
            m_type = meta.get("type")
            
            if m_type == "MOVIE":
                result_queue.put({"type": "process_node", "node": node})
            elif m_type == "EPISODE":
                gp = meta.get("grandparent")
                if not gp:
                    result_queue.put({"type": "process_node", "node": node})
                    continue
                show_guid = gp.get("guid")
                if not show_guid:
                    result_queue.put({"type": "process_node", "node": node})
                    continue
                show_id = show_guid.split("/")[-1]
                if show_id not in processed_shows:
                    processed_shows.add(show_id)
                    task_queue.put({"type": "show_history", "show_id": show_id, "show_title": gp.get("title")})
            elif m_type == "SHOW":
                show_guid = meta.get("guid")
                if not show_guid: continue
                show_id = show_guid.split("/")[-1]
                if show_id not in processed_shows:
                    processed_shows.add(show_id)
                    task_queue.put({"type": "show_history", "show_id": show_id, "show_title": meta.get("title")})
                    
        has_next = page_info.get("hasNextPage", False)
        page_cursor = page_info.get("endCursor")
        result_queue.put({"type": "commit"})
        print(f"[Import Cloud] Processed {total_nodes_processed} items from cloud, found {count_orphans} new orphans so far...")

    # Wait for the cyclic queues to completely settle
    while True:
        if task_queue.unfinished_tasks == 0 and result_queue.unfinished_tasks == 0:
            time.sleep(0.5)
            if task_queue.unfinished_tasks == 0 and result_queue.unfinished_tasks == 0:
                break
        time.sleep(0.5)

    for _ in range(4): task_queue.put(None)
    result_queue.put({"type": "commit"})
    result_queue.put(None)
    
    for w in workers: w.join()
    writer.join()
    
    conn.close()
    print(f"✅ Smart Extractor V2 completed! Inserted {count_orphans} orphans and updated {count_updates} dates.")


def run_sync(main_loop=None):
    settings = load_settings()
    last_sync = settings.get("last_sync_date")
    is_first_sync = not last_sync
    
    if is_first_sync:
        push_all_to_db(main_loop)
        push_cloud_orphans_to_db(main_loop)

    else:
        push_recent_to_db(last_sync)
        
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    settings["last_sync_date"] = now_utc
    save_settings(settings)
    print(f"Sync completed. Date updated: {now_utc}")

is_initial_syncing = False

async def sync_loop():
    while True:
        try:
            if not is_initial_syncing:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: run_sync(loop))
        except Exception as e:
            print(f"Error in sync loop: {e}")
        
        sleep_time = 86400 if HAS_PLEX_PASS else SYNC_INTERVAL
        print(f"Sleeping {sleep_time} seconds...")
        await asyncio.sleep(sleep_time)

async def background_initial_task():
    global rescan_status, is_initial_syncing
    is_initial_syncing = True
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: run_sync(loop))
    finally:
        is_initial_syncing = False
    
    # Notificamos que la BD ha terminado de llenarse, pasamos a descargar imágenes
    settings = load_settings()
    settings["sync_state"] = 3
    save_settings(settings)
    
    # Once DB loading is finished, download images asynchronously
    # Mark rescan as running so the frontend polling doesn't close step 3 prematurely
    rescan_status = {"running": True, "done": False}
    try:
        await bulk_download_tmdb_images()
    finally:
        rescan_status = {"running": False, "done": True}

async def docker_update_checker():
    while True:
        try:
            if os.getenv("SYNC_IS_DOCKER", "false").lower() == "true":
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.get("https://api.github.com/repos/lechtung/SyncPK/releases/latest")
                    if resp.status_code == 200:
                        data = resp.json()
                        remote_ver = data.get("tag_name", "").lstrip("vV")
                        
                        local_ver = "0.0.0"
                        if os.path.exists(os.path.join(APP_DIR, ".ver")):
                            with open(os.path.join(APP_DIR, ".ver")) as f:
                                local_ver = f.read().strip()
                        
                        ignored_version = os.getenv("IGNORED_UPDATE_VERSION", "")
                        notify_updates = os.getenv("NOTIFY_UPDATES", "true").lower() == "true"
                        
                        # Compare versions properly? For simplicity, if they are different and notify_updates, we flag it.
                        if remote_ver and remote_ver != local_ver:
                            if remote_ver != ignored_version and notify_updates:
                                os.environ["UPDATE_AVAILABLE"] = remote_ver
                                env_path = ENV_PATH
                                env_lines = []
                                if os.path.exists(env_path):
                                    with open(env_path, "r", encoding="utf-8") as f:
                                        env_lines = [line for line in f.readlines() if not line.startswith("UPDATE_AVAILABLE=")]
                                env_lines.append(f'UPDATE_AVAILABLE={remote_ver}\n')
                                with open(env_path, "w", encoding="utf-8") as f:
                                    f.writelines(env_lines)
        except Exception as e:
            print(f"Error checking for Docker updates: {e}")
            
        await asyncio.sleep(86400) # Check once a day

def start_background_tasks():
    settings = load_settings()
    last_sync = settings.get("last_sync_date")
    
    if not last_sync:
        print("First time setup: Triggering initial sync in background...")
        settings["sync_state"] = 1
        save_settings(settings)
        asyncio.create_task(background_initial_task())
        
    if os.getenv("SYNC_IS_DOCKER", "false").lower() == "true":
        asyncio.create_task(docker_update_checker())
        
    if not HAS_PLEX_PASS:
        print("Plex Pass NOT detected: Starting incremental sync loop...")
        asyncio.create_task(sync_loop())
    else:
        print("Plex Pass detected: Incremental sync loop disabled. Relying purely on webhooks.")

@app.on_event("startup")
async def startup_event():
    init_db()
    
    # We only start the loops if the application has already been configured
    if os.path.exists(ENV_PATH):
        start_background_tasks()
    else:
        print("Setup pending: Synchronization tasks paused until installation is complete.")

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
        conn_check = get_db_connection()
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
                ep_title = f"Episode {req.episode}" if req.media_type == "episode" else req.title
                s_season = int(req.season) if req.season is not None else None
                s_episode = int(req.episode) if req.episode is not None else None
                movie_tmdb = req.tmdb_id if req.media_type == "movie" else None
                show_tmdb = req.tmdb_id if req.media_type == "episode" else None
                
                plex_movies, plex_shows = get_plex_items_map()
                
                if req.media_type == "movie":
                    matched = match_movie({"movie": {"title": ep_title}}, plex_movies, movie_tmdb, year=target_year)
                    if matched:
                        r_key = matched.get("ratingKey")
                        requests.get(f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={r_key}", headers=plex_headers, timeout=10)
                        print(f"✅ [Local Scrobble] Marked movie in Plex: {ep_title}")
                elif req.media_type == "episode":
                    s_key = match_show({"show": {"title": req.title}}, plex_shows, show_tmdb, year=target_year)
                    if s_key:
                        r_eps = requests.get(f"{PLEX_URL}/library/metadata/{s_key}/allLeaves", headers=plex_headers, timeout=10)
                        if r_eps.status_code == 200:
                            for pep in r_eps.json().get("MediaContainer", {}).get("Metadata", []):
                                if pep.get("parentIndex") == s_season and pep.get("index") == s_episode:
                                    requests.get(f"{PLEX_URL}/:/scrobble?identifier=com.plexapp.plugins.library&key={pep['ratingKey']}", headers=plex_headers, timeout=10)
                                    print(f"✅ [Local Scrobble] Marked episode in Plex: {req.title} T{s_season}E{s_episode}")
                                    break
            except Exception as e:
                print(f"[manual_add] Error triggering local scrobble: {e}")

        # --- PHASE 2: Download TMDB images ---
        poster_path = None
        fanart_path = None
        try:
            target_tmdb = None if req.media_type == "episode" else req.tmdb_id
            show_tmdb = req.tmdb_id if req.media_type == "episode" else None
            
            # Use unified logic!
            updated_info = download_artwork_sync(
                req.media_type, target_tmdb, show_tmdb, req.season, req.episode, req.title, req.title
            )
            poster_path = updated_info.get("poster_path")
            fanart_path = updated_info.get("fanart_path")
                    
        except Exception as e:
            print(f"[manual_add] Error downloading images for '{req.title}': {e}")

        conn = get_db_connection()
        cursor = conn.cursor()
        d_obj = datetime.datetime.fromisoformat(req.watched_at.replace('Z', '+00:00'))
        final_watched_at = d_obj.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        final_origin = "manual" if req.sync_remote else "kodi"

        if req.media_type == "movie":
            cursor.execute("""
                INSERT INTO watch_history (origin, title, media_type, tmdb_id, watched_at, poster_path, fanart_path, plex_guid, duration, year)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (final_origin, req.title, req.media_type, req.tmdb_id, final_watched_at, poster_path, fanart_path, plex_guid, found_duration, target_year))
        else:
            ep_title = f"Episode {req.episode}"
            cursor.execute("""
                INSERT INTO watch_history (origin, title, show_title, media_type, show_tmdb_id, season, episode, watched_at, poster_path, fanart_path, plex_guid, plex_show_guid, duration, show_year)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (final_origin, ep_title, req.title, req.media_type, req.tmdb_id, req.season, req.episode, final_watched_at, poster_path, fanart_path, plex_guid, found_show_guid, found_duration, target_year))

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
        "fanart_mask_opacity": os.getenv("FANART_MASK_OPACITY", "0.3"),
        "dashboard_language": os.getenv("DASHBOARD_LANGUAGE", "auto"),
        "ui_poster_w": os.getenv("UI_POSTER_W", "108"),
        "ui_poster_h": os.getenv("UI_POSTER_H", "225"),
        "ui_fanart_w": os.getenv("UI_FANART_W", "288"),
        "ui_fanart_h": os.getenv("UI_FANART_H", "168"),
        "ui_grid_gap": os.getenv("UI_GRID_GAP", "15"),
        "ui_card_radius": os.getenv("UI_CARD_RADIUS", "8"),
        "ui_bg_color": os.getenv("UI_BG_COLOR", "#0d1117"),
        "ui_glass_bg": os.getenv("UI_GLASS_BG", "rgba(22,27,34,0.7)"),
        "ui_glass_border": os.getenv("UI_GLASS_BORDER", "rgba(255,255,255,0.1)"),
        "ui_edit_bg": os.getenv("UI_EDIT_BG", "rgba(22,27,34,0.85)"),
        "ui_combo_bg": os.getenv("UI_COMBO_BG", "rgba(13,17,23,0.95)"),
        "ui_panel_bg": os.getenv("UI_PANEL_BG", "rgba(255,255,255,0.03)"),
        "ui_accent_primary": os.getenv("UI_ACCENT_PRIMARY", "#58a6ff"),
        "ui_accent_hover": os.getenv("UI_ACCENT_HOVER", "#3182ce"),
        "ui_accent_dark": os.getenv("UI_ACCENT_DARK", "#2568a8"),
        "ui_danger": os.getenv("UI_DANGER", "#f85149"),
        "ui_glass_blur": os.getenv("UI_GLASS_BLUR", "10"),
        "ui_font": os.getenv("UI_FONT", "inter"),
        "ui_text_primary": os.getenv("UI_TEXT_PRIMARY", "#c9d1d9"),
        "ui_text_secondary": os.getenv("UI_TEXT_SECONDARY", "#8b949e"),
        "ui_show_duration": os.getenv("UI_SHOW_DURATION", "true") == "true",
        "ui_show_watch_time": os.getenv("UI_SHOW_WATCH_TIME", "true") == "true",
        "ui_show_title": os.getenv("UI_SHOW_TITLE", "true") == "true",
    }

# --- CONFIGURATION ---
@app.get("/api/config", dependencies=[Depends(verify_api_key)])
def get_config():
    def _mask(value: str) -> str:
        """Return a partially-masked version of a secret string."""
        if not value or len(value) < 8:
            return "" if not value else "***"
        return value[:6] + "..." + value[-4:]

    return {
        "plex_url": PLEX_URL,
        "plex_token": _mask(PLEX_TOKEN),
        "tmdb_api_key": _mask(TMDB_API_KEY),
        "sync_language": os.getenv("SYNC_LANGUAGE", "es"),
        "dashboard_language": os.getenv("DASHBOARD_LANGUAGE", "auto"),
        "plex_client_id": PLEX_CLIENT_ID,
        "debug_mode": os.getenv("DEBUG", "false") == "true",
        "auto_update": os.getenv("AUTO_UPDATE", "true") == "true",
        "notify_updates": os.getenv("NOTIFY_UPDATES", "true").lower() == "true",
        "api_token_raw": os.getenv("API_TOKEN_RAW", ""),
        "has_plex_pass": os.getenv("HAS_PLEX_PASS", "false") == "true",
        "poster_pref": os.getenv("POSTER_PREF", "show"),
        "fanart_pref": os.getenv("FANART_PREF", "episode"),
        "poster_quality": os.getenv("POSTER_QUALITY", "w185"),
        "fanart_quality": os.getenv("FANART_QUALITY", "w300"),
        "ui_poster_w": os.getenv("UI_POSTER_W", "108"),
        "ui_poster_h": os.getenv("UI_POSTER_H", "225"),
        "ui_fanart_w": os.getenv("UI_FANART_W", "288"),
        "ui_fanart_h": os.getenv("UI_FANART_H", "168"),
        "ui_grid_gap": os.getenv("UI_GRID_GAP", "15"),
        "ui_card_radius": os.getenv("UI_CARD_RADIUS", "8"),
        "ui_bg_color": os.getenv("UI_BG_COLOR", "#0d1117"),
        "ui_glass_bg": os.getenv("UI_GLASS_BG", "rgba(22,27,34,0.7)"),
        "ui_glass_border": os.getenv("UI_GLASS_BORDER", "rgba(255,255,255,0.1)"),
        "ui_edit_bg": os.getenv("UI_EDIT_BG", "rgba(22,27,34,0.85)"),
        "ui_combo_bg": os.getenv("UI_COMBO_BG", "rgba(13,17,23,0.95)"),
        "ui_panel_bg": os.getenv("UI_PANEL_BG", "rgba(255,255,255,0.03)"),
        "ui_accent_primary": os.getenv("UI_ACCENT_PRIMARY", "#58a6ff"),
        "ui_accent_hover": os.getenv("UI_ACCENT_HOVER", "#3182ce"),
        "ui_accent_dark": os.getenv("UI_ACCENT_DARK", "#2568a8"),
        "ui_danger": os.getenv("UI_DANGER", "#f85149"),
        "ui_glass_blur": os.getenv("UI_GLASS_BLUR", "10"),
        "ui_font": os.getenv("UI_FONT", "inter"),
        "ui_text_primary": os.getenv("UI_TEXT_PRIMARY", "#c9d1d9"),
        "ui_text_secondary": os.getenv("UI_TEXT_SECONDARY", "#8b949e"),
        "ui_show_duration": os.getenv("UI_SHOW_DURATION", "true") == "true",
        "ui_show_watch_time": os.getenv("UI_SHOW_WATCH_TIME", "true") == "true",
        "ui_show_title": os.getenv("UI_SHOW_TITLE", "true") == "true",
        "fanart_mask_opacity": os.getenv("FANART_MASK_OPACITY", "0.3")
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
    clear_cache: Optional[bool] = False
    poster_pref: Optional[str] = "show"
    fanart_pref: Optional[str] = "episode"
    poster_quality: Optional[str] = "w185"
    fanart_quality: Optional[str] = "w300"
    ui_poster_w: Optional[str] = "108"
    ui_poster_h: Optional[str] = "225"
    ui_fanart_w: Optional[str] = "288"
    ui_fanart_h: Optional[str] = "168"
    ui_grid_gap: Optional[str] = "15"
    ui_card_radius: Optional[str] = "8"
    ui_bg_color: Optional[str] = "#0d1117"
    ui_glass_bg: Optional[str] = "rgba(22,27,34,0.7)"
    ui_glass_border: Optional[str] = "rgba(255,255,255,0.1)"
    ui_edit_bg: Optional[str] = "rgba(22,27,34,0.85)"
    ui_combo_bg: Optional[str] = "rgba(13,17,23,0.95)"
    ui_panel_bg: Optional[str] = "rgba(255,255,255,0.03)"
    ui_accent_primary: Optional[str] = "#58a6ff"
    ui_accent_hover: Optional[str] = "#3182ce"
    ui_accent_dark: Optional[str] = "#2568a8"
    ui_danger: Optional[str] = "#f85149"
    ui_glass_blur: Optional[str] = "10"
    ui_font: Optional[str] = "inter"
    ui_text_primary: Optional[str] = "#c9d1d9"
    ui_text_secondary: Optional[str] = "#8b949e"
    ui_show_duration: Optional[bool] = True
    ui_show_watch_time: Optional[bool] = True
    ui_show_title: Optional[bool] = True
    fanart_mask_opacity: Optional[str] = "0.3"

@app.post("/api/config", dependencies=[Depends(verify_api_key)])
def save_config(payload: ConfigPayload):
    try:
        env_path = ENV_PATH
        env_vars = {}
        
        # Read current environment
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")
        
        # Update values - skip masked sentinel values so real secrets are not overwritten
        def _is_masked(v: str) -> bool:
            return bool(v) and "..." in v and v.endswith(v[-4:]) and len(v) < 20
        old_plex_token = env_vars.get("PLEX_TOKEN")
        env_vars["PLEX_URL"] = payload.plex_url
        if not _is_masked(payload.plex_token):   env_vars["PLEX_TOKEN"]     = payload.plex_token
        if not _is_masked(payload.tmdb_api_key): env_vars["TMDB_API_KEY"]   = payload.tmdb_api_key
        env_vars["SYNC_LANGUAGE"] = payload.sync_language
        env_vars["DASHBOARD_LANGUAGE"] = payload.dashboard_language
        env_vars["PLEX_CLIENT_ID"] = payload.plex_client_id
        env_vars["DEBUG"] = "true" if payload.debug_mode else "false"
        env_vars["AUTO_UPDATE"] = "true" if payload.auto_update else "false"
        env_vars["NOTIFY_UPDATES"] = "true" if payload.notify_updates else "false"
        
        has_plex_pass = env_vars.get("HAS_PLEX_PASS", "false").lower() == "true"
        
        # Only check Plex Pass if the user actually provided a new real (unmasked) token
        if payload.plex_token and payload.plex_client_id and not _is_masked(payload.plex_token) and payload.plex_token != old_plex_token:
            try:
                req = urllib.request.Request("https://plex.tv/api/v2/user")
                req.add_header("Accept", "application/json")
                req.add_header("User-Agent", "curl/7.68.0") # Pretend to be curl to avoid Plex API throttling
                req.add_header("X-Plex-Client-Identifier", payload.plex_client_id)
                req.add_header("X-Plex-Token", payload.plex_token)
                with urllib.request.urlopen(req, timeout=5) as response:
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
            # Generate a fresh dedicated salt and hash with scrypt (slow KDF, not SHA-256)
            new_web_salt = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
            new_hash = hashlib.scrypt(
                payload.master_password.encode(),
                salt=new_web_salt.encode(),
                n=16384, r=8, p=1
            ).hex()
            env_vars["WEB_SALT"] = new_web_salt
            env_vars["WEB_HASH"] = new_hash

        # Update .env while preserving comments
        env_lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                env_lines = f.readlines()

        # Create a dict of variables we need to update in the file
        updates = {
            "PLEX_URL": payload.plex_url,
            "PLEX_TOKEN": env_vars.get("PLEX_TOKEN", payload.plex_token),
            "TMDB_API_KEY": env_vars.get("TMDB_API_KEY", payload.tmdb_api_key),
            "SYNC_LANGUAGE": payload.sync_language,
            "DASHBOARD_LANGUAGE": payload.dashboard_language,
            "PLEX_CLIENT_ID": payload.plex_client_id,
            "DEBUG": "true" if payload.debug_mode else "false",
            "AUTO_UPDATE": "true" if payload.auto_update else "false",
            "NOTIFY_UPDATES": "true" if payload.notify_updates else "false",
            "POSTER_PREF": payload.poster_pref,
            "FANART_PREF": payload.fanart_pref,
            "POSTER_QUALITY": payload.poster_quality,
            "FANART_QUALITY": payload.fanart_quality,
            "UI_POSTER_W": payload.ui_poster_w,
            "UI_POSTER_H": payload.ui_poster_h,
            "UI_FANART_W": payload.ui_fanart_w,
            "UI_FANART_H": payload.ui_fanart_h,
            "UI_GRID_GAP": payload.ui_grid_gap,
            "UI_CARD_RADIUS": payload.ui_card_radius,
            "UI_BG_COLOR": payload.ui_bg_color,
            "UI_GLASS_BG": payload.ui_glass_bg,
            "UI_GLASS_BORDER": payload.ui_glass_border,
            "UI_EDIT_BG": payload.ui_edit_bg,
            "UI_COMBO_BG": payload.ui_combo_bg,
            "UI_PANEL_BG": payload.ui_panel_bg,
            "UI_ACCENT_PRIMARY": payload.ui_accent_primary,
            "UI_ACCENT_HOVER": payload.ui_accent_hover,
            "UI_ACCENT_DARK": payload.ui_accent_dark,
            "UI_DANGER": payload.ui_danger,
            "UI_GLASS_BLUR": payload.ui_glass_blur,
            "UI_FONT": payload.ui_font,
            "UI_TEXT_PRIMARY": payload.ui_text_primary,
            "UI_TEXT_SECONDARY": payload.ui_text_secondary,
            "UI_SHOW_DURATION": "true" if payload.ui_show_duration else "false",
            "UI_SHOW_WATCH_TIME": "true" if payload.ui_show_watch_time else "false",
            "UI_SHOW_TITLE": "true" if payload.ui_show_title else "false",
            "FANART_MASK_OPACITY": payload.fanart_mask_opacity
        }
        if payload.master_password:
            updates["WEB_SALT"] = env_vars.get("WEB_SALT", "")
            updates["WEB_HASH"] = env_vars.get("WEB_HASH", "")
            
        new_env_lines = []
        updated_keys = set()
        
        for line in env_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                new_env_lines.append(line)
                continue
                
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                safe_val = str(updates[k]).replace('\n', '').replace('\r', '')
                new_env_lines.append(f'{k}="{safe_val}"\n')
                updated_keys.add(k)
                # update memory
                os.environ[k] = safe_val
            else:
                new_env_lines.append(line)
                
        # Append any new keys that weren't in the file
        for k, v in updates.items():
            if k not in updated_keys:
                safe_val = str(v).replace('\n', '').replace('\r', '')
                new_env_lines.append(f'{k}="{safe_val}"\n')
                os.environ[k] = safe_val
                
        # Unhide file on Windows before writing
        if os.name == 'nt' and os.path.exists(env_path):
            # FILE_ATTRIBUTE_NORMAL = 128
            ctypes.windll.kernel32.SetFileAttributesW(env_path, 128)
            
        # Save to file
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_env_lines)
                
        # Hide file again on Windows
        if os.name == 'nt':
            try:
                # FILE_ATTRIBUTE_HIDDEN = 2
                ctypes.windll.kernel32.SetFileAttributesW(env_path, 2)
            except:
                pass
                
        # Reload all globals from the freshly written .env
        try:
            if os.path.exists(ENV_PATH + '.bak') and os.name == 'nt':
                ctypes.windll.kernel32.SetFileAttributesW(ENV_PATH + '.bak', 128)
            shutil.copyfile(ENV_PATH, ENV_PATH + '.bak')
        except Exception:
            pass
        reload_settings()

        if payload.force_rescan:
            if payload.clear_cache:
                print("[rescan] Vaciar caché solicitado. Limpiando directorios de imágenes...", flush=True)
                for folder in ["posters", "fanarts"]:
                    folder_path = os.path.join(CACHE_PATH, folder)
                    if os.path.exists(folder_path):
                        shutil.rmtree(folder_path, ignore_errors=True)
            
            global rescan_status
            rescan_status = {"running": True, "done": False}
            _sync_lang = payload.sync_language
            _tmdb_key = TMDB_API_KEY
            _is_debug = os.getenv("DEBUG") == "true"
            _poster_pref = payload.poster_pref or "show"
            _fanart_pref = payload.fanart_pref or "episode"

            def rescan_task_wrapper():
                global rescan_status
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                loop.run_until_complete(execute_full_rescan(_sync_lang, _tmdb_key, _poster_pref, _fanart_pref, _is_debug, loop))
                rescan_status = {"running": False, "done": True}

            threading.Thread(target=rescan_task_wrapper, daemon=True).start()
            return {"status": "success", "has_plex_pass": has_plex_pass, "rescan_started": True}

        return {"status": "success", "has_plex_pass": has_plex_pass}
    except Exception as e:
        print(f"Save config failed: {e}")
        return {"status": "error", "message": "Server error while saving configuration."}

@app.post("/api/config/restore", dependencies=[Depends(verify_api_key)])
def restore_config():
    if not os.path.exists(ENV_PATH + ".bak"):
        return {"status": "error", "message": "No backup found (.env.bak)"}
    
    if os.name == 'nt' and os.path.exists(ENV_PATH):
        ctypes.windll.kernel32.SetFileAttributesW(ENV_PATH, 128)
        
    shutil.copy(ENV_PATH + ".bak", ENV_PATH)
    
    if os.name == 'nt':
        ctypes.windll.kernel32.SetFileAttributesW(ENV_PATH, 2)
    
    # Reload config into memory
    env_vars = {}
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                val = v.strip().strip('"').strip("'")
                env_vars[k.strip()] = val
                os.environ[k.strip()] = val
                
                try:
                    if os.path.exists(ENV_PATH + '.bak') and os.name == 'nt':
                        ctypes.windll.kernel32.SetFileAttributesW(ENV_PATH + '.bak', 128)
                    shutil.copyfile(ENV_PATH, ENV_PATH + '.bak')
                except Exception:
                    pass
    reload_settings()
    return {"status": "success"}

@app.get("/api/rescan/status", dependencies=[Depends(verify_api_key)])
def get_rescan_status():
    return rescan_status

class UpdateIgnoreRequest(BaseModel):
    ignore_version: str = ""
    never_notify: bool = False

class SetupData(BaseModel):
    plex_url: str
    plex_token: str
    plex_client_id: str
    has_plex_pass: bool
    password: str
    tmdb_api_key: str
    dashboard_language: str = "auto"
    sync_language: str = "en"
    poster_pref: str = "show"
    fanart_pref: str = "episode"
    poster_quality: str = "w185"
    fanart_quality: str = "w300"
    auto_update: bool = True
    debug: bool = False

class TestPlexRequest(BaseModel):
    url: str

@app.post("/api/setup/test-plex")
async def test_plex(req: TestPlexRequest, request: Request):
    if os.path.exists(ENV_PATH):
        try:
            await verify_api_key(request)
        except Exception:
            raise HTTPException(status_code=403, detail="Not authorized")
    try:
        clean_url = req.url.rstrip("/")
        async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
            res = await client.get(f"{clean_url}/identity")
            if res.status_code == 200:
                return {"success": True}
            return {"success": False, "status": res.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}

class TestTmdbRequest(BaseModel):
    api_key: str

@app.post("/api/setup/test-tmdb")
async def test_tmdb(req: TestTmdbRequest, request: Request):
    if os.path.exists(ENV_PATH):
        try:
            await verify_api_key(request)
        except Exception:
            raise HTTPException(status_code=403, detail="Not authorized")
    try:
        url = "https://api.themoviedb.org/3/authentication"
        headers = {"accept": "application/json"}
        if len(req.api_key) > 40:
            headers["Authorization"] = f"Bearer {req.api_key}"
        else:
            url += f"?api_key={req.api_key}"
            
        async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return {"success": True}
            return {"success": False, "status": res.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/setup")
async def process_setup(data: SetupData):
    # For security, if the .env file already exists, we block any attempt to overwrite it.
    if os.path.exists(ENV_PATH):
        return {"error": "La instalación ya ha sido completada previamente."}

    # 1. Generate tokens and hashes
    alphabet = string.ascii_letters + string.digits
    # SALT: for API token verification (SHA-256 is fine for high-entropy random tokens)
    salt = ''.join(secrets.choice(alphabet) for _ in range(16))
    # WEB_SALT: dedicated salt for the web password, used with scrypt (slow KDF)
    web_salt = ''.join(secrets.choice(alphabet) for _ in range(16))

    # Web password hash: scrypt instead of SHA-256 (resistant to brute-force/dictionary attacks)
    
    def _hash_pwd():
        return hashlib.scrypt(
            data.password.encode('utf-8'),
            salt=web_salt.encode('utf-8'),
            n=16384, r=8, p=1
        ).hex()
        
    web_hash = await run_in_threadpool(_hash_pwd)

    api_token_raw = ''.join(secrets.choice(alphabet) for _ in range(32))
    api_token = f"sk_{api_token_raw}"
    api_hash = hashlib.sha256((api_token + salt).encode('utf-8')).hexdigest()

    # 2. Read the immutable template (always from the codebase folder)
    example_path = ".env.example"
    if not os.path.exists(example_path):
        return {"error": "Plantilla .env.example no encontrada en el directorio base."}

    with open(example_path, "r", encoding="utf-8") as f:
        env_content = f.read()

    # 3. Prepare the replacement dictionary
    replacements = {
        "PLEX_URL=": f"PLEX_URL={data.plex_url}",
        "PLEX_TOKEN=": f"PLEX_TOKEN={data.plex_token}",
        "HAS_PLEX_PASS=": f"HAS_PLEX_PASS={str(data.has_plex_pass).lower()}",
        "SALT=": f"SALT={salt}",
        "WEB_SALT=": f"WEB_SALT={web_salt}",
        "WEB_HASH=": f"WEB_HASH={web_hash}",
        "API_HASH=": f"API_HASH={api_hash}",
        "TMDB_API_KEY=": f"TMDB_API_KEY={data.tmdb_api_key}",
        "SYNC_LANGUAGE=": f'SYNC_LANGUAGE="{data.sync_language}"',
        "DASHBOARD_LANGUAGE=": f'DASHBOARD_LANGUAGE="{data.dashboard_language}"',
        "POSTER_PREF=": f'POSTER_PREF="{data.poster_pref}"',
        "FANART_PREF=": f'FANART_PREF="{data.fanart_pref}"',
        "POSTER_QUALITY=": f'POSTER_QUALITY="{data.poster_quality}"',
        "FANART_QUALITY=": f'FANART_QUALITY="{data.fanart_quality}"',
        "AUTO_UPDATE=": f"AUTO_UPDATE={str(data.auto_update).lower()}",
        "DEBUG=": f"DEBUG={str(data.debug).lower()}",
        "API_TOKEN_RAW=": f"API_TOKEN_RAW={api_token}",
        "PLEX_CLIENT_ID=": f"PLEX_CLIENT_ID={data.plex_client_id}"
    }

    # Replace each line (acts exactly the same as the 'sed' command in your Bash)
    for key, value in replacements.items():
        env_content = re.sub(rf"^{key}.*", value, env_content, flags=re.MULTILINE)

    print(f"[DEBUG SETUP] ---------------------------------------------", flush=True)
    print(f"[DEBUG SETUP] VALORES EXTRAIDOS DEL FRONTEND PARA .ENV:", flush=True)
    print(f"[DEBUG SETUP] POSTER_PREF = {data.poster_pref}", flush=True)
    print(f"[DEBUG SETUP] FANART_PREF = {data.fanart_pref}", flush=True)
    print(f"[DEBUG SETUP] ---------------------------------------------", flush=True)

    # 4. Write the final file to the persistent data folder (ESCRITURA ATÓMICA)
    try:
        tmp_path = ENV_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(env_content)
        
        # Atomic replacement
        os.replace(tmp_path, ENV_PATH)
        
        # Restrictive permissions if we are on Linux/Mac
        if os.name != 'nt':
            os.chmod(ENV_PATH, 0o600)
    except Exception as e:
        return {"error": f"Error al escribir en disco: {str(e)}"}

    # Hot reload configuration
    try:
        if os.path.exists(ENV_PATH + '.bak') and os.name == 'nt':
            ctypes.windll.kernel32.SetFileAttributesW(ENV_PATH + '.bak', 128)
        shutil.copyfile(ENV_PATH, ENV_PATH + '.bak')
    except Exception:
        pass
    reload_settings()        

    # Now that we have Plex data and the configuration is in memory, we start the tasks.
    start_background_tasks()    

    # Creating the response with HTTPONLY cookie
    
    response_data = {
        "status": "success", 
        "message": "Configuración guardada correctamente.",
        "api_token": api_token
    }
    
    response = JSONResponse(content=response_data)
    
    # We assign the HttpOnly cookie using the web_hash generated above
    response.set_cookie(
        key="syncpk_session", 
        value=web_hash,
        httponly=True,       # The JS frontend will not be able to read it (XSS protection)
        samesite="lax",      # Basic CSRF Protection
        max_age=60*60*24*30  # Expires in 30 days (adjust it to your liking)
    )
    
    return response

@app.get("/api/update/status", dependencies=[Depends(verify_api_key)])
def update_status():
    # Read directly from .env so check_update.sh changes are visible without restart
    update_available = ""
    ignored_version = os.getenv("IGNORED_UPDATE_VERSION", "")
    notify_updates = os.getenv("NOTIFY_UPDATES", "true").lower() == "true"
    try:
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("UPDATE_AVAILABLE="):
                        update_available = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
    except Exception:
        update_available = os.getenv("UPDATE_AVAILABLE", "")
    
    is_docker = os.getenv("SYNC_IS_DOCKER", "false").lower() == "true"
    if update_available and update_available != ignored_version and notify_updates:
        return {"has_update": True, "version": update_available, "is_docker": is_docker}
    return {"has_update": False, "is_docker": is_docker}

@app.post("/api/update/ignore", dependencies=[Depends(verify_api_key)])
def update_ignore(req: UpdateIgnoreRequest):
    # Update .env
    env_path = ENV_PATH
    env_lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            env_lines = f.readlines()
            
    # Set IGNORED_UPDATE_VERSION and NOTIFY_UPDATES in .env
    new_env_lines = []
    for line in env_lines:
        if line.startswith("IGNORED_UPDATE_VERSION="): continue
        if line.startswith("NOTIFY_UPDATES="): continue
        if line.startswith("UPDATE_AVAILABLE="): continue
        new_env_lines.append(line)
        
    if req.ignore_version:
        new_env_lines.append(f"IGNORED_UPDATE_VERSION={req.ignore_version}\n")
        os.environ["IGNORED_UPDATE_VERSION"] = req.ignore_version
        
    if req.never_notify:
        new_env_lines.append("NOTIFY_UPDATES=false\n")
        os.environ["NOTIFY_UPDATES"] = "false"
    else:
        new_env_lines.append("NOTIFY_UPDATES=true\n")
        os.environ["NOTIFY_UPDATES"] = "true"
        
    # Clear UPDATE_AVAILABLE
    new_env_lines.append("UPDATE_AVAILABLE=\n")
    os.environ["UPDATE_AVAILABLE"] = ""
        
    if os.name == 'nt' and os.path.exists(env_path):
        ctypes.windll.kernel32.SetFileAttributesW(env_path, 128)
        
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_env_lines)
        
    if os.name == 'nt':
        try:
            ctypes.windll.kernel32.SetFileAttributesW(env_path, 2)
        except:
            pass
        
    return {"status": "success"}

@app.post("/api/update/trigger", dependencies=[Depends(verify_api_key)])
def update_trigger():
    try:
        # We start the systemd service. We don't wait for it because it will restart our service!
        subprocess.Popen(["sudo", "systemctl", "start", "syncpk-updater.service"])
        return {"status": "success", "message": "Update triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/update/trigger", dependencies=[Depends(verify_api_key)])
def update_trigger():
    
    # Check if we are in Docker (where we don't use systemd)
    if os.path.exists("/.dockerenv"):
        return {
            "status": "info", 
            "message": "In Docker, updates are performed by downloading the new image (e.g., Watchtower or manual pull)."
        }

    # In Proxmox/Baremetal, we simply create the "snitch" file
    trigger_file = os.path.join(DATA_DIR, ".trigger_update")
    try:
        with open(trigger_file, 'w') as f:
            f.write("update_requested")
        return {"status": "success", "message": "Update process started in the background."}
    except Exception as e:
        return {"status": "error", "message": f"Error requesting update: {e}"}

# --- SERVE FRONTEND ---
# Mount the static folder at the end to avoid overwriting routes /api/
os.makedirs(os.path.join(DATA_DIR, "static"), exist_ok=True)
@app.get("/")
def serve_index():
    # We select the file based on the setup state
    if not os.path.exists(ENV_PATH):
        target_html = "setup.html"
    else:
        settings = load_settings()
        if not settings.get("last_sync_date"):
            target_html = "import.html"
        else:
            target_html = "dashboard.html"
            
    file_path = os.path.join("static", target_html)
    
    if not os.path.exists(file_path):
        return HTMLResponse(f"{target_html} not found", status_code=404)
        
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # We inject the language into the chosen file.
    dashboard_lang = os.getenv("DASHBOARD_LANGUAGE", "auto")
    inject_script = f"<script>window.DASHBOARD_LANG = '{dashboard_lang}';</script>"
    content = content.replace("<head>", f"<head>\n    {inject_script}", 1)
    
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

# Assemble the static files last for the rest of the resources (CSS, JS, images...)
os.makedirs(CACHE_PATH, exist_ok=True)
app.mount("/cache", StaticFiles(directory=CACHE_PATH), name="cache")
app.mount("/", StaticFiles(directory="static"), name="static")

@app.post("/api/logout")
def logout_endpoint(response: Response):
    response.delete_cookie("syncpk_session")
    return {"status": "success"}
