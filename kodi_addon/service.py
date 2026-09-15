import xbmc
import xbmcaddon
import xbmcvfs
import json
import urllib.request
import os
import datetime
import time
import base64

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))

def get_server_headers():
    headers = {'Content-Type': 'application/json'}
    password = ADDON.getSetting('server_password')
    if password:
        b64_pass = base64.b64encode(password.encode('utf-8')).decode('utf-8')
        headers['Authorization'] = f"Basic {b64_pass}"
    return headers

def log(msg, level=xbmc.LOGINFO):
    # Always write to custom log to facilitate debugging
    try:
        log_file = os.path.join(ADDON_PATH, 'syncpk.log')
        with open(log_file, 'a', encoding='utf-8') as f:
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            f.write(f"[{now}] {msg}\n")
    except:
        pass

    if level == xbmc.LOGDEBUG and ADDON.getSetting('debug_log') != 'true':
        return
    xbmc.log(f"[{ADDON_ID}] {msg}", level)

def notify(msg):
    if ADDON.getSetting('show_notifications') == 'true':
        xbmc.executebuiltin(f"Notification(SyncPK, {msg}, 3000, '')")

class PlayerMonitor(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.reset()
        
    def reset(self):
        self.playing_file = None
        self.start_time = 0
        self.Total_time = 0
        self.media_info = {}

    def onPlayBackStarted(self):
        self.playing_file = self.getPlayingFile()
        self.Total_time = self.getTotalTime()
        self.start_time = self.getTime()
        
        # Kodi takes a few milliseconds to fill xbmc.getInfoLabel and returns garbage. We use JSON-RPC:
        try:
            rpc_query = {
                "jsonrpc": "2.0", 
                "method": "Player.GetItem",
                "params": {
                   "playerid": 1,
                   "properties": ["showtitle", "title", "season", "episode", "tvshowid", "imdbnumber", "uniqueid"]
                },
                "id": 1
            }
            res = json.loads(xbmc.executeJSONRPC(json.dumps(rpc_query)))
            item = res.get("result", {}).get("item", {})
            media_type = item.get("type", "")
            
            # Si Kodi no sabe qué es, adivinamos por la temporada
            if media_type == 'unknown' or not media_type:
                if int(item.get("season", -1)) > -1:
                    media_type = "episode"
                else:
                    media_type = "movie"
                    
            unique_ids = item.get("uniqueid", {})
            show_unique_ids = {}
            
            tvshowid = item.get('tvshowid', -1)
            if media_type == "episode" and tvshowid != -1:
                # Get the IDs of the complete show
                show_query = {
                    "jsonrpc": "2.0", "method": "VideoLibrary.GetTVShowDetails",
                    "params": {"tvshowid": int(tvshowid), "properties": ["uniqueid", "imdbnumber"]}, "id": 1
                }
                show_res = json.loads(xbmc.executeJSONRPC(json.dumps(show_query)))
                show_details = show_res.get("result", {}).get("tvshowdetails", {})
                show_unique_ids = show_details.get("uniqueid", {})
                
                # Fallback por si la librería es vieja y solo tiene imdbnumber
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
            log(f"Error reading metadata: {e}", xbmc.LOGERROR)
            self.media_info = {}
        
        log(f"Reproducción iniciada: {self.playing_file} (Total: {self.Total_time}s) [Type: {self.media_info.get('media_type')}]", xbmc.LOGDEBUG)

    def onPlayBackEnded(self):
        self.process_stop(True)

    def onPlayBackStopped(self):
        self.process_stop(False)

    def process_stop(self, ended):
        if not self.playing_file:
            return
            
        try:
            current_time = self.getTime() if not ended else self.Total_time
        except:
            current_time = self.Total_time

        if self.Total_time > 0:
            percent_watched = (current_time / self.Total_time) * 100
        else:
            percent_watched = 100

        threshold_str = ADDON.getSetting('watched_percent')
        try:
            threshold = float(threshold_str)
        except:
            threshold = 80.0

        log(f"Reproducción detenida. Visto: {percent_watched:.1f}% (Threshold: {threshold}%)", xbmc.LOGDEBUG)

        if percent_watched >= threshold:
            self.send_webhook()
            
        self.reset()

    def send_webhook(self):
        try:
            # Filter YouTube/Tubed immediately
            if self.playing_file:
                path_lower = self.playing_file.lower()
                if 'plugin.video.youtube' in path_lower or 'plugin.video.tubed' in path_lower:
                    log(f"Ignorando envío porque es un vídeo de YouTube/Tubed: {self.playing_file}", xbmc.LOGDEBUG)
                    return
                
            # Filtrado inteligente: Si no tiene un ID válido en la base de datos de Kodi, es un trailer o un stream externo
            kodi_id_str = self.media_info.get('kodi_id', '')
            try: kodi_id_int = int(kodi_id_str)
            except: kodi_id_int = -1
            
            if kodi_id_int <= 0:
                log(f"Ignorando envío porque el archivo no está catalogado en la base de datos de Kodi (kodi_id={kodi_id_str})", xbmc.LOGDEBUG)
                return
                
            media_type = self.media_info.get('media_type')
            if not media_type or media_type not in ['movie', 'episode']:
                log(f"Ignoring push because DBTYPE is not movie or episode, it is: '{media_type}'", xbmc.LOGDEBUG)
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
            
            log(f"Sending Webhook to {webhook_url} -> {json.dumps(payload)}", xbmc.LOGDEBUG)
            
            req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode('utf-8'), headers=get_server_headers())
            
            with urllib.request.urlopen(req, timeout=5) as response:
                res = response.read()
                log(f"Server response: {res}", xbmc.LOGDEBUG)
                notify("Sincronizado con éxito")
                
        except Exception as e:
            log(f"Error processing webhook push: {e}", xbmc.LOGERROR)

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

    def _buscar_pelicula(self, simkl_ids, titulo):
        for m in self.kodi_movies:
            u_id = m.get("uniqueid", {})
            if simkl_ids.get("imdb") and (u_id.get("imdb") == simkl_ids["imdb"] or m.get("imdbnumber") == simkl_ids["imdb"]): return m["movieid"]
            if simkl_ids.get("tmdb") and (u_id.get("tmdb") == str(simkl_ids["tmdb"]) or m.get("imdbnumber") == str(simkl_ids["tmdb"])): return m["movieid"]
        if titulo:
            for m in self.kodi_movies:
                if m.get("title", "").lower() == titulo.lower(): return m["movieid"]
        return None

    def _buscar_serie(self, simkl_ids, titulo):
        for show in self.kodi_shows:
            u_id = show.get("uniqueid", {})
            if simkl_ids.get("imdb") and (u_id.get("imdb") == simkl_ids["imdb"] or show.get("imdbnumber") == simkl_ids["imdb"]): return show["tvshowid"]
            if simkl_ids.get("tvdb") and (u_id.get("tvdb") == str(simkl_ids["tvdb"]) or show.get("imdbnumber") == str(simkl_ids["tvdb"])): return show["tvshowid"]
            if simkl_ids.get("tmdb") and (u_id.get("tmdb") == str(simkl_ids["tmdb"]) or show.get("imdbnumber") == str(simkl_ids["tmdb"])): return show["tvshowid"]
        if titulo:
            for show in self.kodi_shows:
                if show.get("title", "").lower() == titulo.lower(): return show["tvshowid"]
        return None
        
    def _full_push_sync(self, host_url):
        log("Starting FULL PUSH SYNC. Building massive package...", xbmc.LOGINFO)
        payloads = []
        
        # 1. Películas vistas
        resp_m = json_rpc("VideoLibrary.GetMovies", {"properties": ["uniqueid", "imdbnumber", "title", "playcount", "lastplayed"]})
        for m in resp_m.get("result", {}).get("movies", []):
            if m.get("playcount", 0) > 0:
                watched_at = ""
                if m.get("lastplayed"):
                    try:
                        local_dt = datetime.datetime.strptime(m["lastplayed"], '%Y-%m-%d %H:%M:%S')
                        timestamp = time.mktime(local_dt.timetuple())
                        utc_dt = datetime.datetime.utcfromtimestamp(timestamp)
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

        # 2. Watched episodes
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
                        utc_dt = datetime.datetime.utcfromtimestamp(timestamp)
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
                
                # IMDB Fallback for the show
                if not payload["Metadata"]["show_unique_ids"] and show_info.get("imdbnumber"):
                    f_id = show_info.get("imdbnumber")
                    if f_id.startswith("tt"): payload["Metadata"]["show_unique_ids"]["imdb"] = f_id
                    else: payload["Metadata"]["show_unique_ids"]["tmdb"] = f_id
                    
                payloads.append(payload)
                
        if not payloads:
            log("Full Push completado: No había nada visto en Kodi localmente.", xbmc.LOGINFO)
            return

        try:
            bulk_url = f"{host_url}/webhook/kodi/bulk"
            req = urllib.request.Request(bulk_url, data=json.dumps(payloads).encode('utf-8'), headers=get_server_headers())
            with urllib.request.urlopen(req, timeout=30) as response:
                res = json.loads(response.read())
                log(f"Full Push completado con éxito. Enviados {len(payloads)} items. Processed: {res.get('processed')}", xbmc.LOGINFO)
        except Exception as e:
            log(f"Error in Full Push Bulk: {e}", xbmc.LOGERROR)

    def run(self):
        last_sync_local = ADDON.getSetting('last_sync_date')
        
        # Convert local time (saved in settings) to UTC for the server
        utc_sync = ""
        if last_sync_local:
            try:
                local_dt = datetime.datetime.strptime(last_sync_local, '%Y-%m-%d %H:%M:%S')
                timestamp = time.mktime(local_dt.timetuple())
                utc_dt = datetime.datetime.utcfromtimestamp(timestamp)
                utc_sync = utc_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            except Exception as e:
                log(f"Error parsing local date '{last_sync_local}': {e}", xbmc.LOGERROR)
                utc_sync = last_sync_local # Fallback just in case
                
        base_url = ADDON.getSetting('server_url')
        host_url = base_url.split("/webhook/kodi")[0] if "/webhook/kodi" in base_url else "http://192.168.178.206:8000"
            
        url = f"{host_url}/sync/all-items?client=kodi"
        if utc_sync: url += f"&date_from={utc_sync}"
        
        is_first_sync = not utc_sync
            
        log(f"Starting Pull Sync. URL: {url}", xbmc.LOGDEBUG)
        
        try:
            req = urllib.request.Request(url, headers=get_server_headers())
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read())
        except Exception as e:
            log(f"Error connecting to server for Pull Sync: {e}", xbmc.LOGERROR)
            return

        self._cargar_catalogo_kodi()
        
        for m in data.get("movies", []):
            m_obj = m.get("movie", {})
            movieid = self._buscar_pelicula(m_obj.get("ids", {}), m_obj.get("title"))
            if movieid:
                resp = json_rpc("VideoLibrary.SetMovieDetails", {"movieid": movieid, "playcount": 1})
                if resp.get("result") == "OK":
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
                                    log(f"Episode marked as watched (Pull): {titulo} T{s_num}E{ep.get('number')}")
                                break
                
        # --- PULL FINALIZADO. SI ES LA PRIMERA VEZ, HACEMOS FULL PUSH DESPUÉS ---
        if is_first_sync:
            self._full_push_sync(host_url)
                
        now_local = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ADDON.setSetting('last_sync_date', now_local)
        log(f"Sincronización terminada. Nueva fecha local guardada: {now_local}")

if __name__ == '__main__':
    log("Service started")
    player = PlayerMonitor()
    monitor = xbmc.Monitor()
    
    sync_on_startup = ADDON.getSetting('sync_on_startup') == 'true'
        
    puller = SyncPuller()
    if sync_on_startup:
        puller.run()
        
    ticks = 0
    # Keep the service alive until Kodi closes
    while not monitor.abortRequested():
        if monitor.waitForAbort(1):
            break
            
        try:
            sync_interval = int(ADDON.getSetting('sync_interval'))
        except:
            sync_interval = 15
            
        ticks += 1
        if sync_interval > 0 and ticks >= (sync_interval * 60):
            puller.run()
            ticks = 0
            
    log("Service stopped")

