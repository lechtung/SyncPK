import xbmc
import xbmcaddon
import xbmcvfs
import json
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs, urlencode
import os
import datetime
import time
import threading
import sqlite3
import re
import shutil
import uuid

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
PROFILE_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
QUEUE_DB = os.path.join(PROFILE_PATH, 'queue.db')

_queue_lock = threading.Lock()
_sync_lock = threading.Lock()

def get_client_id():
    """Returns or generates a unique, persistent client UID for this Kodi instance."""
    cid = ADDON.getSetting('client_id')
    if not cid:
        cid = f"kodi_{uuid.uuid4().hex[:16]}"
        ADDON.setSetting('client_id', cid)
        log(f"Generated new persistent client UID: {cid}", xbmc.LOGINFO)
    return cid

def get_db_connection():
    """Initializes local SQLite database for webhook queue in the addon profile directory."""
    os.makedirs(PROFILE_PATH, exist_ok=True)
    
    # Migrate legacy queue.db from installation directory to addon profile directory if it exists
    old_queue_db = os.path.join(ADDON_PATH, 'queue.db')
    if os.path.exists(old_queue_db) and not os.path.exists(QUEUE_DB):
        try:
            shutil.copy2(old_queue_db, QUEUE_DB)
            os.remove(old_queue_db)
            log("Successfully migrated queue.db from ADDON_PATH to PROFILE_PATH.", xbmc.LOGINFO)
        except Exception as e:
            log(f"Error migrating queue.db to profile: {e}", xbmc.LOGWARNING)

    conn = sqlite3.connect(QUEUE_DB, timeout=5)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS webhook_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT,
            created_at TEXT,
            attempts INTEGER DEFAULT 0,
            last_error TEXT
        )
    """)
    try:
        conn.execute("ALTER TABLE webhook_queue ADD COLUMN attempts INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE webhook_queue ADD COLUMN last_error TEXT")
    except Exception:
        pass
    return conn

def _split_server_url(base_url):
    """Splits server base URL and authentication token (extracted from query parameter or settings)."""
    if not base_url:
        return "", ""
    host_url = base_url.split("/webhook/kodi")[0]
    parsed = urlparse(base_url)
    token = parse_qs(parsed.query).get("token", [""])[0]
    if not token:
        token = ADDON.getSetting('server_password') or ""
    return host_url, token

def _api_url(host_url, token, path, **params):
    """Constructs API endpoint URL retaining query parameters and authentication token."""
    clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
    if token:
        clean_params["token"] = token
    query_str = urlencode(clean_params)
    return f"{host_url}{path}" + (f"?{query_str}" if query_str else "")

def log(msg, level=xbmc.LOGINFO):
    """Logs message to Kodi log and optional debug log file, masking sensitive tokens."""
    msg_str = str(msg)
    msg_str = re.sub(r'(\?|&)token=[^&]+', r'\1token=***', msg_str)
    
    if ADDON.getSetting('debug_log') == 'true':
        try:
            log_file = os.path.join(xbmcvfs.translatePath('special://temp'), 'syncpk.log')
            if os.path.exists(log_file) and os.path.getsize(log_file) > 5 * 1024 * 1024:
                os.rename(log_file, log_file + '.old')
            with open(log_file, 'a', encoding='utf-8') as f:
                now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"[{now}] {msg_str}\n")
        except Exception:
            pass

    if level == xbmc.LOGDEBUG and ADDON.getSetting('debug_log') != 'true':
        return
    xbmc.log(f"[{ADDON_ID}] {msg_str}", level)

def notify(msg):
    """Displays on-screen toast notification if enabled in addon settings."""
    if ADDON.getSetting('show_notifications') == 'true':
        xbmc.executebuiltin(f"Notification(SyncPK, {msg}, 3000, '')")

def enqueue_webhook(payload):
    """Enqueues failed or offline webhook payload into local SQLite queue for subsequent retries."""
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO webhook_queue (payload, created_at, attempts) VALUES (?, ?, 0)", 
                (json.dumps(payload), datetime.datetime.now(datetime.timezone.utc).isoformat())
            )
        log("Webhook enqueued locally.", xbmc.LOGDEBUG)
    except Exception as e:
        log(f"Error enqueuing webhook: {e}", xbmc.LOGERROR)

def process_queue():
    """Processes enqueued webhooks, discarding permanent 4xx errors and handling retries."""
    if not _queue_lock.acquire(blocking=False):
        return
    try:
        base_url = ADDON.getSetting('server_url')
        if not base_url or "/webhook/kodi" not in base_url:
            return

        host_url, token = _split_server_url(base_url)
        webhook_url = _api_url(host_url, token, "/webhook/kodi")

        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT id, payload, attempts FROM webhook_queue ORDER BY id ASC LIMIT 50")
            rows = c.fetchall()
            if not rows:
                return
            
            log(f"Processing {len(rows)} enqueued webhooks...", xbmc.LOGDEBUG)
            for row_id, payload_str, attempts in rows:
                attempts = attempts or 0
                try:
                    req = urllib.request.Request(webhook_url, data=payload_str.encode('utf-8'))
                    req.add_header('Content-Type', 'application/json')
                    with urllib.request.urlopen(req, timeout=10) as response:
                        if response.status in [200, 201]:
                            conn.execute("DELETE FROM webhook_queue WHERE id = ?", (row_id,))
                except urllib.error.HTTPError as he:
                    if he.code in [400, 422]:
                        log(f"Permanent HTTP {he.code} error on webhook ID {row_id}. Discarding from queue: {he}", xbmc.LOGERROR)
                        conn.execute("DELETE FROM webhook_queue WHERE id = ?", (row_id,))
                    else:
                        attempts += 1
                        if attempts >= 5:
                            log(f"Webhook ID {row_id} exceeded retry limit ({attempts}). Discarding: {he}", xbmc.LOGERROR)
                            conn.execute("DELETE FROM webhook_queue WHERE id = ?", (row_id,))
                        else:
                            conn.execute("UPDATE webhook_queue SET attempts = ?, last_error = ? WHERE id = ?", (attempts, str(he), row_id))
                        break
                except Exception as e:
                    attempts += 1
                    if attempts >= 5:
                        log(f"Webhook ID {row_id} exceeded retry limit ({attempts}). Discarding: {e}", xbmc.LOGERROR)
                        conn.execute("DELETE FROM webhook_queue WHERE id = ?", (row_id,))
                    else:
                        conn.execute("UPDATE webhook_queue SET attempts = ?, last_error = ? WHERE id = ?", (attempts, str(e), row_id))
                    break
    except Exception as e:
        log(f"Error processing webhook queue: {e}", xbmc.LOGERROR)
    finally:
        _queue_lock.release()

def json_rpc(method, params=None):
    """Executes a JSON-RPC method call on the local Kodi interface."""
    request = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params: request["params"] = params
    response = xbmc.executeJSONRPC(json.dumps(request))
    return json.loads(response)

def _format_kodi_datetime(iso_str):
    """Converts UTC ISO-8601 string to Kodi local datetime format (YYYY-MM-DD HH:MM:SS)."""
    if not iso_str:
        return ""
    try:
        clean_str = iso_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean_str)
        local_dt = dt.astimezone()
        return local_dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return iso_str.replace("T", " ").replace("Z", "")[:19]

class PlayerMonitor(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.reset()
        
    def reset(self):
        self.playing_file = None
        self.start_time = 0
        self.total_time = 0
        self.media_info = {}
        self.last_known_time = 0

    def onPlayBackStarted(self):
        self._handle_playback_start()

    def onAVStarted(self):
        # Audio/video stream is fully initialized and total playback duration is guaranteed available
        self._handle_playback_start()

    def _get_active_video_player_id(self):
        try:
            active_players = json_rpc("Player.GetActivePlayers").get("result", [])
            for ap in active_players:
                if ap.get("type") == "video":
                    return ap.get("playerid", 1)
            if active_players:
                return active_players[0].get("playerid", 1)
        except Exception:
            pass
        return 1

    def _handle_playback_start(self):
        try:
            self.playing_file = self.getPlayingFile()
        except Exception:
            pass

        try:
            t = self.getTotalTime()
            if t > 0:
                self.total_time = t
        except Exception:
            pass

        try:
            cur_time = self.getTime()
            if self.start_time == 0:
                self.start_time = cur_time
            self.last_known_time = cur_time
        except Exception:
            pass
        
        # Avoid redundant JSON-RPC calls if metadata and duration are already properly captured
        if self.media_info and self.total_time > 0:
            return

        try:
            player_id = self._get_active_video_player_id()
            rpc_query = {
                "jsonrpc": "2.0", 
                "method": "Player.GetItem",
                "params": {"playerid": player_id, "properties": ["showtitle", "title", "season", "episode", "tvshowid", "imdbnumber", "uniqueid", "year"]},
                "id": 1
            }
            res = json.loads(xbmc.executeJSONRPC(json.dumps(rpc_query)))
            item = res.get("result", {}).get("item", {})
            media_type = item.get("type", "")
            
            if media_type == 'unknown' or not media_type:
                if int(item.get("season", -1)) > -1:
                    media_type = "episode"
                else:
                    media_type = "movie"
                    
            unique_ids = item.get("uniqueid", {})
            show_unique_ids = {}
            
            tvshowid = item.get('tvshowid', -1)
            if media_type == "episode" and tvshowid != -1:
                show_query = {
                    "jsonrpc": "2.0", "method": "VideoLibrary.GetTVShowDetails",
                    "params": {"tvshowid": int(tvshowid), "properties": ["uniqueid", "imdbnumber", "year"]}, "id": 1
                }
                show_res = json.loads(xbmc.executeJSONRPC(json.dumps(show_query)))
                show_details = show_res.get("result", {}).get("tvshowdetails", {})
                show_unique_ids = show_details.get("uniqueid", {})
                
                if not show_unique_ids and show_details.get("imdbnumber"):
                    f_id = show_details.get("imdbnumber")
                    if str(f_id).startswith("tt"): show_unique_ids["imdb"] = f_id
                    else: show_unique_ids["tmdb"] = f_id
                    
            self.media_info = {
                'media_type': media_type,
                'kodi_id': str(item.get('id', '')),
                'title': item.get('title', ''),
                'imdbnumber': item.get('imdbnumber', ''),
                'unique_ids': unique_ids,
                'show_title': item.get('showtitle', ''),
                'season': str(item.get('season', '')),
                'episode': str(item.get('episode', '')),
                'show_id': str(tvshowid),
                'show_unique_ids': show_unique_ids,
                'year': item.get('year')
            }
        except Exception as e:
            log(f"Error reading playback metadata: {e}", xbmc.LOGERROR)
            self.media_info = {}
            
        log(f"Playback started: {self.playing_file} (Duration: {self.total_time}s) [Type: {self.media_info.get('media_type')}]", xbmc.LOGDEBUG)

    def onPlayBackEnded(self):
        self.process_stop(True)

    def onPlayBackStopped(self):
        self.process_stop(False)

    def process_stop(self, ended):
        if not self.playing_file:
            return

        if self.total_time <= 0:
            try:
                self.total_time = self.getTotalTime()
            except Exception:
                pass
            
        # If total duration is unavailable, never assume 100% completion; safely discard
        if self.total_time <= 0:
            log(f"Total duration unavailable ({self.total_time}s). Discarding scrobble.", xbmc.LOGDEBUG)
            self.reset()
            return

        current_time = self.total_time if ended else self.last_known_time
        percent_watched = (current_time / self.total_time) * 100
        start_percent = (self.start_time / self.total_time) * 100

        threshold_str = ADDON.getSetting('watched_percent')
        try:
            threshold = float(threshold_str)
        except Exception:
            threshold = 80.0

        log(f"Playback stopped. Start: {start_percent:.1f}%, End: {percent_watched:.1f}% (Threshold: {threshold}%)", xbmc.LOGDEBUG)

        if percent_watched >= threshold and start_percent < threshold:
            # Dispatch webhook asynchronously in background thread to avoid blocking Kodi playback thread
            media_info_copy = dict(self.media_info)
            playing_file_copy = self.playing_file
            threading.Thread(target=self._send_webhook_async, args=(media_info_copy, playing_file_copy), daemon=True).start()
        else:
            log("Webhook denied. Does not fulfill watched threshold criteria.", xbmc.LOGDEBUG)
            
        self.reset()

    def _send_webhook_async(self, media_info, playing_file):
        payload = None
        try:
            if playing_file:
                path_lower = playing_file.lower()
                if 'plugin.video.youtube' in path_lower or 'plugin.video.tubed' in path_lower:
                    log(f"Ignoring scrobble for YouTube/Tubed stream: {playing_file}", xbmc.LOGDEBUG)
                    return
                
            kodi_id_str = media_info.get('kodi_id', '')
            try: kodi_id_int = int(kodi_id_str)
            except Exception: kodi_id_int = -1
            
            if kodi_id_int <= 0:
                log(f"Ignoring scrobble: file not cataloged in Kodi library (kodi_id={kodi_id_str})", xbmc.LOGDEBUG)
                return
                
            media_type = media_info.get('media_type')
            if not media_type or media_type not in ['movie', 'episode']:
                log(f"Ignoring scrobble: DBTYPE is not movie or episode ('{media_type}')", xbmc.LOGDEBUG)
                return
            
            if media_type == 'movie' and ADDON.getSetting('sync_movies') != 'true':
                log("Ignoring movie: synchronization disabled in settings", xbmc.LOGDEBUG)
                return
            if media_type == 'episode' and ADDON.getSetting('sync_shows') != 'true':
                log("Ignoring episode: synchronization disabled in settings", xbmc.LOGDEBUG)
                return
            
            payload = {
                "event": "media.scrobble",
                "Metadata": {
                    "type": media_type,
                    "title": media_info.get('title'),
                    "kodi_id": media_info.get('kodi_id'),
                    "imdbnumber": media_info.get('imdbnumber'),
                    "unique_ids": media_info.get('unique_ids', {}),
                    "watched_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                }
            }
            if media_info.get('year'):
                payload["Metadata"]["year"] = media_info.get('year')
            
            if media_type == 'episode':
                payload["Metadata"]["grandparentTitle"] = media_info.get('show_title')
                payload["Metadata"]["parentIndex"] = media_info.get('season')
                payload["Metadata"]["index"] = media_info.get('episode')
                payload["Metadata"]["kodi_show_id"] = media_info.get('show_id')
                payload["Metadata"]["show_unique_ids"] = media_info.get('show_unique_ids', {})

            base_url = ADDON.getSetting('server_url')
            if not base_url or "/webhook/kodi" not in base_url:
                log("Server URL not configured or invalid. Enqueuing webhook.", xbmc.LOGWARNING)
                enqueue_webhook(payload)
                return
            
            host_url, token = _split_server_url(base_url)
            webhook_url = _api_url(host_url, token, "/webhook/kodi")
            
            log(f"Sending webhook to {webhook_url} -> {json.dumps(payload)}", xbmc.LOGDEBUG)
            
            req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode('utf-8'))
            req.add_header('Content-Type', 'application/json')
            
            with urllib.request.urlopen(req, timeout=10) as response:
                res = response.read()
                log(f"Server response: {res}", xbmc.LOGDEBUG)
                notify("Sincronizado con éxito")
                
        except Exception as e:
            log(f"Error sending webhook: {e}. Enqueuing locally...", xbmc.LOGERROR)
            if payload:
                enqueue_webhook(payload)

class SyncPuller:
    def __init__(self):
        self.kodi_movies = []
        self.kodi_shows = []
        self.movies_by_id = {}
        self.shows_by_id = {}
        self.movies_by_imdb = {}
        self.movies_by_tmdb = {}
        self.movies_by_tvdb = {}
        self.movies_by_title_year = {}
        self.movies_by_title = {}
        self.shows_by_imdb = {}
        self.shows_by_tmdb = {}
        self.shows_by_tvdb = {}
        self.shows_by_title_year = {}
        self.shows_by_title = {}

    def _cargar_catalogo_kodi(self):
        """Pre-indexes local Kodi library into O(1) hash maps for high-performance matching."""
        resp_m = json_rpc("VideoLibrary.GetMovies", {"properties": ["uniqueid", "imdbnumber", "title", "year", "playcount"]})
        self.kodi_movies = resp_m.get("result", {}).get("movies", [])
        self.movies_by_id = {m["movieid"]: m for m in self.kodi_movies}
        self.movies_by_imdb = {}
        self.movies_by_tmdb = {}
        self.movies_by_tvdb = {}
        self.movies_by_title_year = {}
        self.movies_by_title = {}

        for m in self.kodi_movies:
            mid = m["movieid"]
            u_id = m.get("uniqueid", {})
            imdb = u_id.get("imdb")
            if not imdb and str(m.get("imdbnumber", "")).startswith("tt"):
                imdb = m.get("imdbnumber")
            if imdb:
                self.movies_by_imdb[str(imdb).lower()] = mid
            
            tmdb = u_id.get("tmdb")
            if tmdb:
                self.movies_by_tmdb[str(tmdb)] = mid
            tvdb = u_id.get("tvdb")
            if tvdb:
                self.movies_by_tvdb[str(tvdb)] = mid
            
            title = m.get("title", "").strip().lower()
            year = m.get("year")
            if title:
                if year:
                    try:
                        self.movies_by_title_year[(title, int(year))] = mid
                    except Exception:
                        pass
                if title not in self.movies_by_title:
                    self.movies_by_title[title] = mid

        resp_t = json_rpc("VideoLibrary.GetTVShows", {"properties": ["uniqueid", "imdbnumber", "title", "year"]})
        self.kodi_shows = resp_t.get("result", {}).get("tvshows", [])
        self.shows_by_id = {s["tvshowid"]: s for s in self.kodi_shows}
        self.shows_by_imdb = {}
        self.shows_by_tmdb = {}
        self.shows_by_tvdb = {}
        self.shows_by_title_year = {}
        self.shows_by_title = {}

        for s in self.kodi_shows:
            sid = s["tvshowid"]
            u_id = s.get("uniqueid", {})
            imdb = u_id.get("imdb")
            if not imdb and str(s.get("imdbnumber", "")).startswith("tt"):
                imdb = s.get("imdbnumber")
            if imdb:
                self.shows_by_imdb[str(imdb).lower()] = sid
            
            tmdb = u_id.get("tmdb")
            if tmdb:
                self.shows_by_tmdb[str(tmdb)] = sid
            tvdb = u_id.get("tvdb")
            if tvdb:
                self.shows_by_tvdb[str(tvdb)] = sid
            
            title = s.get("title", "").strip().lower()
            year = s.get("year")
            if title:
                if year:
                    try:
                        self.shows_by_title_year[(title, int(year))] = sid
                    except Exception:
                        pass
                if title not in self.shows_by_title:
                    self.shows_by_title[title] = sid

    def _buscar_pelicula(self, server_ids, titulo, year=None):
        """Resolves local Kodi movie ID by exact global IDs first, then by (title, year), then title."""
        if not server_ids:
            server_ids = {}
        if server_ids.get("imdb"):
            val = str(server_ids["imdb"]).lower()
            if val in self.movies_by_imdb:
                return self.movies_by_imdb[val]
        if server_ids.get("tmdb"):
            val = str(server_ids["tmdb"])
            if val in self.movies_by_tmdb:
                return self.movies_by_tmdb[val]
        if server_ids.get("tvdb"):
            val = str(server_ids["tvdb"])
            if val in self.movies_by_tvdb:
                return self.movies_by_tvdb[val]
        
        if titulo:
            t_norm = titulo.strip().lower()
            if year:
                try:
                    y_int = int(year)
                    for dy in (0, -1, 1):
                        if (t_norm, y_int + dy) in self.movies_by_title_year:
                            return self.movies_by_title_year[(t_norm, y_int + dy)]
                except Exception:
                    pass
            if t_norm in self.movies_by_title:
                return self.movies_by_title[t_norm]
        return None

    def _buscar_serie(self, server_ids, titulo, year=None):
        """Resolves local Kodi TV show ID by exact global IDs first, then by (title, year), then title."""
        if not server_ids:
            server_ids = {}
        if server_ids.get("imdb"):
            val = str(server_ids["imdb"]).lower()
            if val in self.shows_by_imdb:
                return self.shows_by_imdb[val]
        if server_ids.get("tmdb"):
            val = str(server_ids["tmdb"])
            if val in self.shows_by_tmdb:
                return self.shows_by_tmdb[val]
        if server_ids.get("tvdb"):
            val = str(server_ids["tvdb"])
            if val in self.shows_by_tvdb:
                return self.shows_by_tvdb[val]
        
        if titulo:
            t_norm = titulo.strip().lower()
            if year:
                try:
                    y_int = int(year)
                    for dy in (0, -1, 1):
                        if (t_norm, y_int + dy) in self.shows_by_title_year:
                            return self.shows_by_title_year[(t_norm, y_int + dy)]
                except Exception:
                    pass
            if t_norm in self.shows_by_title:
                return self.shows_by_title[t_norm]
        return None

    def _full_push_sync(self, host_url, token):
        """Pushes full local watched library to SyncPK server on initial addon setup."""
        log("Starting initial FULL PUSH SYNC. Building bulk payload...", xbmc.LOGINFO)
        payloads = []
        
        resp_m = json_rpc("VideoLibrary.GetMovies", {"properties": ["uniqueid", "imdbnumber", "title", "year", "playcount", "lastplayed"]})
        for m in resp_m.get("result", {}).get("movies", []):
            if m.get("playcount", 0) > 0:
                watched_at = ""
                if m.get("lastplayed"):
                    try:
                        local_dt = datetime.datetime.strptime(m["lastplayed"], '%Y-%m-%d %H:%M:%S')
                        timestamp = time.mktime(local_dt.timetuple())
                        utc_dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
                        watched_at = utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                    except Exception:
                        pass
                
                payload = {
                    "event": "media.scrobble",
                    "Metadata": {
                        "type": "movie", "title": m.get("title"), "kodi_id": str(m.get("movieid")),
                        "imdbnumber": m.get("imdbnumber"), "unique_ids": m.get("uniqueid", {})
                    }
                }
                if m.get("year"):
                    payload["Metadata"]["year"] = m.get("year")
                if watched_at: payload["Metadata"]["watched_at"] = watched_at
                payloads.append(payload)

        resp_e = json_rpc("VideoLibrary.GetEpisodes", {"properties": ["uniqueid", "title", "showtitle", "season", "episode", "tvshowid", "playcount", "lastplayed"]})
        resp_s = json_rpc("VideoLibrary.GetTVShows", {"properties": ["uniqueid", "imdbnumber", "title", "year"]})
        shows_cache = {s["tvshowid"]: s for s in resp_s.get("result", {}).get("tvshows", [])}
        
        for ep in resp_e.get("result", {}).get("episodes", []):
            if ep.get("playcount", 0) > 0:
                watched_at = ""
                if ep.get("lastplayed"):
                    try:
                        local_dt = datetime.datetime.strptime(ep["lastplayed"], '%Y-%m-%d %H:%M:%S')
                        timestamp = time.mktime(local_dt.timetuple())
                        utc_dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
                        watched_at = utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                    except Exception:
                        pass
                
                tvshowid = ep.get("tvshowid")
                show_info = shows_cache.get(tvshowid, {})
                
                payload = {
                    "event": "media.scrobble",
                    "Metadata": {
                        "type": "episode", "title": ep.get("title"), "kodi_id": str(ep.get("episodeid")),
                        "unique_ids": ep.get("uniqueid", {}), "grandparentTitle": ep.get("showtitle"),
                        "parentIndex": str(ep.get("season")), "index": str(ep.get("episode")),
                        "kodi_show_id": str(tvshowid), "show_unique_ids": show_info.get("uniqueid", {})
                    }
                }
                if watched_at: payload["Metadata"]["watched_at"] = watched_at
                
                if not payload["Metadata"]["show_unique_ids"] and show_info.get("imdbnumber"):
                    f_id = show_info.get("imdbnumber")
                    if str(f_id).startswith("tt"): payload["Metadata"]["show_unique_ids"]["imdb"] = f_id
                    else: payload["Metadata"]["show_unique_ids"]["tmdb"] = f_id
                    
                payloads.append(payload)
                
        if not payloads:
            log("Full Push completed: No local watched media found in Kodi.", xbmc.LOGINFO)
            return

        bulk_url = _api_url(host_url, token, "/webhook/kodi/bulk")
        chunk_size = 100
        for i in range(0, len(payloads), chunk_size):
            chunk = payloads[i:i + chunk_size]
            try:
                req = urllib.request.Request(bulk_url, data=json.dumps(chunk).encode('utf-8'))
                req.add_header('Content-Type', 'application/json')
                with urllib.request.urlopen(req, timeout=30) as response:
                    res = json.loads(response.read())
                    log(f"Full Push chunk {i//chunk_size + 1} dispatched. Processed: {res.get('processed')}", xbmc.LOGINFO)
            except Exception as e:
                log(f"Error in Full Push Bulk chunk {i//chunk_size + 1}: {e}", xbmc.LOGERROR)

    def run(self):
        """Runs the synchronization cycle, guarded by a non-blocking execution lock."""
        if not _sync_lock.acquire(blocking=False):
            log("SyncPuller cycle already running. Skipping this iteration.", xbmc.LOGDEBUG)
            return
        try:
            self._run_internal()
        except Exception as e:
            log(f"Fatal error in SyncPuller execution: {e}", xbmc.LOGERROR)
        finally:
            _sync_lock.release()

    def _run_internal(self):
        utc_sync = ADDON.getSetting('last_sync_date')
        base_url = ADDON.getSetting('server_url')
        if not base_url or "/webhook/kodi" not in base_url:
            log("Invalid network configuration. Please configure SyncPK Server URL in addon settings.", xbmc.LOGWARNING)
            notify("Configura la URL de SyncPK")
            return

        host_url, token = _split_server_url(base_url)
        is_first_sync = not utc_sync
        client_id = get_client_id()

        url = _api_url(
            host_url, token, "/sync/all-items", 
            client="kodi", client_id=client_id, 
            date_from=utc_sync if utc_sync else None
        )
        log(f"Starting Pull Sync (Initial={is_first_sync}, Client={client_id}). URL: {url}", xbmc.LOGDEBUG)
        
        server_time = None
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read())
                server_time = data.get("server_time")
        except Exception as e:
            log(f"Error connecting to server for Pull Sync: {e}", xbmc.LOGERROR)
            return

        self._cargar_catalogo_kodi()
        confirm_ids = []
        episodes_cache = {}
        
        for m in data.get("movies", []):
            m_obj = m.get("movie", {})
            movieid = self._buscar_pelicula(m_obj.get("ids", {}), m_obj.get("title"), m_obj.get("year"))
            if movieid:
                local_movie = self.movies_by_id.get(movieid, {})
                local_playcount = local_movie.get("playcount", 0)
                if local_playcount > 0:
                    confirm_ids.append(m.get("id"))
                else:
                    watched_at = m.get("watched_at")
                    details = {
                        "movieid": movieid,
                        "playcount": 1,
                        "resume": {"position": 0.0, "total": 0.0}
                    }
                    if watched_at:
                        details["lastplayed"] = _format_kodi_datetime(watched_at)
                    resp = json_rpc("VideoLibrary.SetMovieDetails", details)
                    if resp.get("result") == "OK":
                        confirm_ids.append(m.get("id"))
                        local_movie["playcount"] = 1
                        log(f"Movie marked as watched (Pull): {m_obj.get('title')}")
                    
        for s in data.get("shows", []):
            s_obj = s.get("show", {})
            titulo = s_obj.get("title")
            s_year = s_obj.get("year")
            s_ids = s_obj.get("ids", {})
            cached_tvshowid = None
            for season in s.get("seasons", []):
                s_num = season.get("number")
                for ep in season.get("episodes", []):
                    if not cached_tvshowid:
                        cached_tvshowid = self._buscar_serie(s_ids, titulo, s_year)
                    if cached_tvshowid:
                        cache_key = (cached_tvshowid, s_num)
                        if cache_key not in episodes_cache:
                            ep_resp = json_rpc("VideoLibrary.GetEpisodes", {
                                "tvshowid": cached_tvshowid,
                                "season": s_num,
                                "properties": ["season", "episode", "playcount"]
                            })
                            episodes_cache[cache_key] = ep_resp.get("result", {}).get("episodes", [])
                        
                        for kodi_ep in episodes_cache[cache_key]:
                            if kodi_ep.get("season") == s_num and kodi_ep.get("episode") == ep.get("number"):
                                if kodi_ep.get("playcount", 0) > 0:
                                    confirm_ids.append(ep.get("id"))
                                else:
                                    watched_at = ep.get("watched_at")
                                    details = {
                                        "episodeid": kodi_ep["episodeid"],
                                        "playcount": 1,
                                        "resume": {"position": 0.0, "total": 0.0}
                                    }
                                    if watched_at:
                                        details["lastplayed"] = _format_kodi_datetime(watched_at)
                                    if json_rpc("VideoLibrary.SetEpisodeDetails", details).get("result") == "OK":
                                        confirm_ids.append(ep.get("id"))
                                        kodi_ep["playcount"] = 1
                                        log(f"Episode marked as watched (Pull): {titulo} S{s_num:02d}E{ep.get('number', 0):02d}")
                                break

        for m in data.get("deleted_movies", []):
            movieid = self._buscar_pelicula({"tmdb": m.get("tmdb_id")}, m.get("title"), m.get("year"))
            if movieid:
                local_movie = self.movies_by_id.get(movieid, {})
                if local_movie.get("playcount", 0) > 0:
                    resp = json_rpc("VideoLibrary.SetMovieDetails", {"movieid": movieid, "playcount": 0})
                    if resp.get("result") == "OK":
                        local_movie["playcount"] = 0
                        confirm_ids.append(m.get("id"))
                        log(f"Movie unmarked as watched (Pull - Deleted): {m.get('title')}")
                else:
                    confirm_ids.append(m.get("id"))

        for s in data.get("deleted_shows", []):
            titulo = s.get("show_title")
            s_num = s.get("season")
            ep_num = s.get("episode")
            tvshowid = self._buscar_serie({"tmdb": s.get("show_tmdb_id")}, titulo, s.get("year"))
            if tvshowid:
                cache_key = (tvshowid, s_num)
                if cache_key not in episodes_cache:
                    ep_resp = json_rpc("VideoLibrary.GetEpisodes", {
                        "tvshowid": tvshowid,
                        "season": s_num,
                        "properties": ["season", "episode", "playcount"]
                    })
                    episodes_cache[cache_key] = ep_resp.get("result", {}).get("episodes", [])
                
                for kodi_ep in episodes_cache[cache_key]:
                    if kodi_ep.get("season") == s_num and kodi_ep.get("episode") == ep_num:
                        if kodi_ep.get("playcount", 0) > 0:
                            if json_rpc("VideoLibrary.SetEpisodeDetails", {"episodeid": kodi_ep["episodeid"], "playcount": 0}).get("result") == "OK":
                                kodi_ep["playcount"] = 0
                                confirm_ids.append(s.get("id"))
                                log(f"Episode unmarked as watched (Pull - Deleted): {titulo} S{s_num}E{ep_num}")
                        else:
                            confirm_ids.append(s.get("id"))
                        break

        if confirm_ids:
            try:
                confirm_url = _api_url(host_url, token, "/sync/confirm-kodi")
                confirm_payload = {
                    "client_id": client_id,
                    "ids": confirm_ids
                }
                req = urllib.request.Request(confirm_url, data=json.dumps(confirm_payload).encode('utf-8'))
                req.add_header('Content-Type', 'application/json')
                with urllib.request.urlopen(req, timeout=10) as response:
                    pass
            except Exception as e:
                log(f"Error confirming synchronization ACKs: {e}", xbmc.LOGERROR)
                
        if is_first_sync:
            self._full_push_sync(host_url, token)
                
        if server_time:
            try:
                clean_time = server_time.replace("Z", "+00:00")
                dt = datetime.datetime.fromisoformat(clean_time)
                # Apply 5-minute clock drift margin to avoid race conditions with server timestamp
                adjusted_dt = dt - datetime.timedelta(minutes=5)
                saved_time = adjusted_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            except Exception:
                saved_time = server_time
            ADDON.setSetting('last_sync_date', saved_time)
            log(f"Synchronization completed. Saved server timestamp: {saved_time}")
        else:
            log("Synchronization completed but server_time was not returned. Retaining previous timestamp.", xbmc.LOGWARNING)

if __name__ == '__main__':
    log("SyncPK service started")
    player = PlayerMonitor()
    monitor = xbmc.Monitor()
    
    sync_on_startup = ADDON.getSetting('sync_on_startup') == 'true'
        
    puller = SyncPuller()
    if sync_on_startup:
        threading.Thread(target=puller.run, daemon=True).start()
        
    ticks = 0
    while not monitor.abortRequested():
        if monitor.waitForAbort(1):
            break
            
        try:
            sync_interval = int(ADDON.getSetting('sync_interval'))
        except Exception:
            sync_interval = 15
            
        ticks += 1
        
        if player.isPlayingVideo():
            try:
                player.last_known_time = player.getTime()
            except Exception:
                pass

        if ticks % 60 == 0:
            threading.Thread(target=process_queue, daemon=True).start()
            
        if sync_interval > 0 and ticks >= (sync_interval * 60):
            threading.Thread(target=puller.run, daemon=True).start()
            ticks = 0
            
    log("SyncPK service stopped")
