import xbmc
import xbmcaddon
import xbmcvfs
import json
import urllib.request
import os
import datetime
import time
import threading
import sqlite3
import re

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
QUEUE_DB = os.path.join(ADDON_PATH, 'queue.db')

def get_db_connection():
    os.makedirs(ADDON_PATH, exist_ok=True)
    conn = sqlite3.connect(QUEUE_DB, timeout=5)
    conn.execute("CREATE TABLE IF NOT EXISTS webhook_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT, created_at TEXT)")
    return conn

def log(msg, level=xbmc.LOGINFO):
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
        except:
            pass

    if level == xbmc.LOGDEBUG and ADDON.getSetting('debug_log') != 'true':
        return
    xbmc.log(f"[{ADDON_ID}] {msg_str}", level)

def notify(msg):
    if ADDON.getSetting('show_notifications') == 'true':
        xbmc.executebuiltin(f"Notification(SyncPK, {msg}, 3000, '')")

def enqueue_webhook(payload):
    try:
        with get_db_connection() as conn:
            conn.execute("INSERT INTO webhook_queue (payload, created_at) VALUES (?, ?)", 
                         (json.dumps(payload), datetime.datetime.now(datetime.timezone.utc).isoformat()))
        log("Webhook encolado localmente.", xbmc.LOGDEBUG)
    except Exception as e:
        log(f"Error al encolar webhook: {e}", xbmc.LOGERROR)

def process_queue():
    webhook_url = ADDON.getSetting('server_url')
    if not webhook_url or "/webhook/kodi" not in webhook_url:
        return

    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT id, payload FROM webhook_queue ORDER BY id ASC LIMIT 50")
            rows = c.fetchall()
            if not rows: return
            
            log(f"Procesando {len(rows)} webhooks encolados...", xbmc.LOGDEBUG)
            for row_id, payload_str in rows:
                try:
                    req = urllib.request.Request(webhook_url, data=payload_str.encode('utf-8'))
                    req.add_header('Content-Type', 'application/json')
                    with urllib.request.urlopen(req, timeout=5) as response:
                        if response.status in [200, 201]:
                            conn.execute("DELETE FROM webhook_queue WHERE id = ?", (row_id,))
                except Exception as e:
                    log(f"Error reenviando webhook ID {row_id}: {e}", xbmc.LOGERROR)
                    break 
    except Exception as e:
        log(f"Error procesando cola: {e}", xbmc.LOGERROR)

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
        self.playing_file = self.getPlayingFile()
        self.total_time = self.getTotalTime()
        self.start_time = self.getTime()
        self.last_known_time = self.start_time
        
        try:
            rpc_query = {
                "jsonrpc": "2.0", 
                "method": "Player.GetItem",
                "params": {"playerid": 1, "properties": ["showtitle", "title", "season", "episode", "tvshowid", "imdbnumber", "uniqueid"]},
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
                    "params": {"tvshowid": int(tvshowid), "properties": ["uniqueid", "imdbnumber"]}, "id": 1
                }
                show_res = json.loads(xbmc.executeJSONRPC(json.dumps(show_query)))
                show_details = show_res.get("result", {}).get("tvshowdetails", {})
                show_unique_ids = show_details.get("uniqueid", {})
                
                if not show_unique_ids and show_details.get("imdbnumber"):
                    f_id = show_details.get("imdbnumber")
                    if f_id.startswith("tt"): show_unique_ids["imdb"] = f_id
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
                'show_unique_ids': show_unique_ids
            }
        except Exception as e:
            log(f"Error leyendo metadatos: {e}", xbmc.LOGERROR)
            self.media_info = {}
        
        log(f"Reproducción iniciada: {self.playing_file} (Total: {self.total_time}s) [Tipo: {self.media_info.get('media_type')}]", xbmc.LOGDEBUG)

    def onPlayBackEnded(self):
        self.process_stop(True)

    def onPlayBackStopped(self):
        self.process_stop(False)

    def process_stop(self, ended):
        if not self.playing_file:
            return
            
        current_time = self.total_time if ended else self.last_known_time

        if self.total_time > 0:
            percent_watched = (current_time / self.total_time) * 100
            start_percent = (self.start_time / self.total_time) * 100
        else:
            percent_watched = 100
            start_percent = 0

        threshold_str = ADDON.getSetting('watched_percent')
        try:
            threshold = float(threshold_str)
        except:
            threshold = 80.0

        log(f"Reproducción detenida. Inicio: {start_percent:.1f}%, Actual: {percent_watched:.1f}% (Umbral: {threshold}%)", xbmc.LOGDEBUG)

        if percent_watched >= threshold and start_percent < threshold:
            self.send_webhook()
        else:
            log(f"Webhook denegado. No cumple criterios de visionado real.", xbmc.LOGDEBUG)
            
        self.reset()

    def send_webhook(self):
        try:
            if self.playing_file:
                path_lower = self.playing_file.lower()
                if 'plugin.video.youtube' in path_lower or 'plugin.video.tubed' in path_lower:
                    log(f"Ignorando envío porque es un vídeo de YouTube/Tubed: {self.playing_file}", xbmc.LOGDEBUG)
                    return
                
            kodi_id_str = self.media_info.get('kodi_id', '')
            try: kodi_id_int = int(kodi_id_str)
            except: kodi_id_int = -1
            
            if kodi_id_int <= 0:
                log(f"Ignorando envío porque el archivo no está catalogado en la base de datos de Kodi (kodi_id={kodi_id_str})", xbmc.LOGDEBUG)
                return
                
            media_type = self.media_info.get('media_type')
            if not media_type or media_type not in ['movie', 'episode']:
                log(f"Ignorando envio porque DBTYPE no es pelicula o episodio, es: '{media_type}'", xbmc.LOGDEBUG)
                return
            
            if media_type == 'movie' and ADDON.getSetting('sync_movies') != 'true':
                log("Ignorando película porque está desactivado en ajustes", xbmc.LOGDEBUG)
                return
            if media_type == 'episode' and ADDON.getSetting('sync_shows') != 'true':
                log("Ignorando serie porque está desactivado en ajustes", xbmc.LOGDEBUG)
                return
            
            payload = {
                "event": "media.scrobble",
                "Metadata": {
                    "type": media_type,
                    "title": self.media_info.get('title'),
                    "kodi_id": self.media_info.get('kodi_id'),
                    "imdbnumber": self.media_info.get('imdbnumber'),
                    "unique_ids": self.media_info.get('unique_ids', {}),
                    "watched_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                }
            }
            
            if media_type == 'episode':
                payload["Metadata"]["grandparentTitle"] = self.media_info.get('show_title')
                payload["Metadata"]["parentIndex"] = self.media_info.get('season')
                payload["Metadata"]["index"] = self.media_info.get('episode')
                payload["Metadata"]["kodi_show_id"] = self.media_info.get('show_id')
                payload["Metadata"]["show_unique_ids"] = self.media_info.get('show_unique_ids', {})

            webhook_url = ADDON.getSetting('server_url')
            if not webhook_url or "/webhook/kodi" not in webhook_url:
                log("Servidor URL no configurado o invalido. Encolando webhook.", xbmc.LOGWARNING)
                enqueue_webhook(payload)
                return
            
            log(f"Enviando Webhook a {webhook_url} -> {json.dumps(payload)}", xbmc.LOGDEBUG)
            
            req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode('utf-8'))
            req.add_header('Content-Type', 'application/json')
            
            with urllib.request.urlopen(req, timeout=5) as response:
                res = response.read()
                log(f"Respuesta del servidor: {res}", xbmc.LOGDEBUG)
                notify("Sincronizado con éxito")
                
        except Exception as e:
            log(f"Error procesando el envio del webhook: {e}. Encolando...", xbmc.LOGERROR)
            enqueue_webhook(payload)

def json_rpc(method, params=None):
    request = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params: request["params"] = params
    response = xbmc.executeJSONRPC(json.dumps(request))
    return json.loads(response)

class SyncPuller:
    def __init__(self):
        self.kodi_movies = []
        self.kodi_shows = []
        
    def _cargar_catalogo_kodi(self):
        resp_m = json_rpc("VideoLibrary.GetMovies", {"properties": ["uniqueid", "imdbnumber", "title"]})
        self.kodi_movies = resp_m.get("result", {}).get("movies", [])
        resp_t = json_rpc("VideoLibrary.GetTVShows", {"properties": ["uniqueid", "imdbnumber", "title"]})
        self.kodi_shows = resp_t.get("result", {}).get("tvshows", [])

    def _buscar_pelicula(self, server_ids, titulo):
        for m in self.kodi_movies:
            u_id = m.get("uniqueid", {})
            if server_ids.get("imdb") and (str(u_id.get("imdb")) == str(server_ids["imdb"]) or str(m.get("imdbnumber")) == str(server_ids["imdb"])): return m["movieid"]
            if server_ids.get("tmdb") and (str(u_id.get("tmdb")) == str(server_ids["tmdb"]) or str(m.get("imdbnumber")) == str(server_ids["tmdb"])): return m["movieid"]
        if titulo:
            for m in self.kodi_movies:
                if m.get("title", "").lower() == titulo.lower(): return m["movieid"]
        return None

    def _buscar_serie(self, server_ids, titulo):
        for show in self.kodi_shows:
            u_id = show.get("uniqueid", {})
            if server_ids.get("imdb") and (str(u_id.get("imdb")) == str(server_ids["imdb"]) or str(show.get("imdbnumber")) == str(server_ids["imdb"])): return show["tvshowid"]
            if server_ids.get("tvdb") and (str(u_id.get("tvdb")) == str(server_ids["tvdb"]) or str(show.get("imdbnumber")) == str(server_ids["tvdb"])): return show["tvshowid"]
            if server_ids.get("tmdb") and (str(u_id.get("tmdb")) == str(server_ids["tmdb"]) or str(show.get("imdbnumber")) == str(server_ids["tmdb"])): return show["tvshowid"]
        if titulo:
            for show in self.kodi_shows:
                if show.get("title", "").lower() == titulo.lower(): return show["tvshowid"]
        return None
        
    def _full_push_sync(self, host_url):
        log("Iniciando FULL PUSH SYNC. Construyendo paquete masivo...", xbmc.LOGINFO)
        payloads = []
        
        resp_m = json_rpc("VideoLibrary.GetMovies", {"properties": ["uniqueid", "imdbnumber", "title", "playcount", "lastplayed"]})
        for m in resp_m.get("result", {}).get("movies", []):
            if m.get("playcount", 0) > 0:
                watched_at = ""
                if m.get("lastplayed"):
                    try:
                        local_dt = datetime.datetime.strptime(m["lastplayed"], '%Y-%m-%d %H:%M:%S')
                        timestamp = time.mktime(local_dt.timetuple())
                        utc_dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
                        watched_at = utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                    except: pass
                
                payload = {
                    "event": "media.scrobble",
                    "Metadata": {
                        "type": "movie", "title": m.get("title"), "kodi_id": str(m.get("movieid")),
                        "imdbnumber": m.get("imdbnumber"), "unique_ids": m.get("uniqueid", {})
                    }
                }
                if watched_at: payload["Metadata"]["watched_at"] = watched_at
                payloads.append(payload)

        resp_e = json_rpc("VideoLibrary.GetEpisodes", {"properties": ["uniqueid", "title", "showtitle", "season", "episode", "tvshowid", "playcount", "lastplayed"]})
        resp_s = json_rpc("VideoLibrary.GetTVShows", {"properties": ["uniqueid", "imdbnumber", "title"]})
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
                    except: pass
                
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
                    if f_id.startswith("tt"): payload["Metadata"]["show_unique_ids"]["imdb"] = f_id
                    else: payload["Metadata"]["show_unique_ids"]["tmdb"] = f_id
                    
                payloads.append(payload)
                
        if not payloads:
            log("Full Push completado: No había nada visto en Kodi localmente.", xbmc.LOGINFO)
            return

        bulk_url = f"{host_url}/webhook/kodi/bulk"
        chunk_size = 100
        for i in range(0, len(payloads), chunk_size):
            chunk = payloads[i:i + chunk_size]
            try:
                req = urllib.request.Request(bulk_url, data=json.dumps(chunk).encode('utf-8'))
                req.add_header('Content-Type', 'application/json')
                with urllib.request.urlopen(req, timeout=30) as response:
                    res = json.loads(response.read())
                    log(f"Full Push chunk {i//chunk_size + 1} enviado. Procesados: {res.get('processed')}", xbmc.LOGINFO)
            except Exception as e:
                log(f"Error en Full Push Bulk chunk {i//chunk_size + 1}: {e}", xbmc.LOGERROR)

    def run(self):
        try:
            self._run_internal()
        except Exception as e:
            log(f"Error fatal en el ciclo SyncPuller: {e}", xbmc.LOGERROR)

    def _run_internal(self):
        utc_sync = ADDON.getSetting('last_sync_date')
                
        base_url = ADDON.getSetting('server_url')
        if not base_url or "/webhook/kodi" not in base_url:
            log("Configuracion de red invalida. Configura la URL correcta del servidor en los ajustes del Addon.", xbmc.LOGWARNING)
            notify("Configura la URL de SyncPK")
            return
            
        host_url = base_url.split("/webhook/kodi")[0]
            
        url = f"{host_url}/sync/all-items?client=kodi"
        if utc_sync: url += f"&date_from={utc_sync}"
        
        is_first_sync = not utc_sync
            
        log(f"Iniciando Pull Sync. URL: {url}", xbmc.LOGDEBUG)
        
        server_time = None
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
                server_time = data.get("server_time")
        except Exception as e:
            log(f"Error conectando al servidor para Pull Sync: {e}", xbmc.LOGERROR)
            return

        self._cargar_catalogo_kodi()
        confirm_ids = []
        
        for m in data.get("movies", []):
            m_obj = m.get("movie", {})
            movieid = self._buscar_pelicula(m_obj.get("ids", {}), m_obj.get("title"))
            if movieid:
                resp = json_rpc("VideoLibrary.SetMovieDetails", {"movieid": movieid, "playcount": 1})
                if resp.get("result") == "OK":
                    confirm_ids.append(m.get("id"))
                    log(f"Película marcada como vista (Pull): {m_obj.get('title')}")
                    
        for s in data.get("shows", []):
            s_obj = s.get("show", {})
            titulo = s_obj.get("title")
            cached_tvshowid = None
            for season in s.get("seasons", []):
                s_num = season.get("number")
                for ep in season.get("episodes", []):
                    if not cached_tvshowid:
                        cached_tvshowid = self._buscar_serie(s_obj.get("ids", {}), titulo)
                    if cached_tvshowid:
                        episodes = json_rpc("VideoLibrary.GetEpisodes", {"tvshowid": cached_tvshowid, "season": s_num, "properties": ["season", "episode"]}).get("result", {}).get("episodes", [])
                        for kodi_ep in episodes:
                            if kodi_ep["season"] == s_num and kodi_ep["episode"] == ep.get("number"):
                                if json_rpc("VideoLibrary.SetEpisodeDetails", {"episodeid": kodi_ep["episodeid"], "playcount": 1}).get("result") == "OK":
                                    confirm_ids.append(ep.get("id"))
                                    log(f"Episodio marcado como visto (Pull): {titulo} T{s_num}E{ep.get('number')}")
                                break

        for m in data.get("deleted_movies", []):
            movieid = self._buscar_pelicula({"tmdb": m.get("tmdb_id")}, m.get("title"))
            if movieid:
                resp = json_rpc("VideoLibrary.SetMovieDetails", {"movieid": movieid, "playcount": 0})
                if resp.get("result") == "OK":
                    confirm_ids.append(m.get("id"))
                    log(f"Película desmarcada (Pull - Deleted): {m.get('title')}")

        for s in data.get("deleted_shows", []):
            titulo = s.get("show_title")
            s_num = s.get("season")
            ep_num = s.get("episode")
            tvshowid = self._buscar_serie({"tmdb": s.get("show_tmdb_id")}, titulo)
            if tvshowid:
                episodes = json_rpc("VideoLibrary.GetEpisodes", {"tvshowid": tvshowid, "season": s_num, "properties": ["season", "episode"]}).get("result", {}).get("episodes", [])
                for kodi_ep in episodes:
                    if kodi_ep["season"] == s_num and kodi_ep["episode"] == ep_num:
                        if json_rpc("VideoLibrary.SetEpisodeDetails", {"episodeid": kodi_ep["episodeid"], "playcount": 0}).get("result") == "OK":
                            confirm_ids.append(s.get("id"))
                            log(f"Episodio desmarcado (Pull - Deleted): {titulo} T{s_num}E{ep_num}")
                        break

        if confirm_ids:
            try:
                confirm_url = f"{host_url}/sync/confirm-kodi"
                req = urllib.request.Request(confirm_url, data=json.dumps({"ids": confirm_ids}).encode('utf-8'))
                req.add_header('Content-Type', 'application/json')
                with urllib.request.urlopen(req, timeout=5) as response: pass
            except Exception as e:
                log(f"Error confirmando sincronización: {e}", xbmc.LOGERROR)
                
        if is_first_sync:
            threading.Thread(target=self._full_push_sync, args=(host_url,), daemon=True).start()
                
        if server_time:
            ADDON.setSetting('last_sync_date', server_time)
            log(f"Sincronización terminada. Nueva fecha de servidor guardada: {server_time}")
        else:
            log("Sincronización terminada pero no se pudo obtener server_time. Manteniendo la fecha anterior.", xbmc.LOGWARNING)

if __name__ == '__main__':
    log("Servicio iniciado")
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
        except:
            sync_interval = 15
            
        ticks += 1
        
        if player.isPlayingVideo():
            try:
                player.last_known_time = player.getTime()
            except: pass

        if ticks % 60 == 0:
            threading.Thread(target=process_queue, daemon=True).start()
            
        if sync_interval > 0 and ticks >= (sync_interval * 60):
            threading.Thread(target=puller.run, daemon=True).start()
            ticks = 0
            
    log("Servicio detenido")
