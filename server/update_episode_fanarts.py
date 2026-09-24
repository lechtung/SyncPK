import sqlite3
import os
import requests
import hashlib
from dotenv import load_dotenv

# Cargar variables de entorno para coger el TMDB_API_KEY
if os.path.exists(".env"):
    load_dotenv(".env")
elif os.path.exists("../.env"):
    load_dotenv("../.env")
    
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

if not TMDB_API_KEY:
    print("Error: TMDB_API_KEY no encontrada en .env")
    exit(1)

CACHE_DIR = "static/cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def download_tmdb_image(url: str, filename: str) -> str:
    """Descarga una imagen y la guarda en la caché, devolviendo la ruta relativa."""
    filepath = os.path.join(CACHE_DIR, filename)
    if os.path.exists(filepath):
        return f"/cache/{filename}"
    
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open(filepath, "wb") as f:
                f.write(r.content)
            return f"/cache/{filename}"
    except Exception as e:
        print(f"Error descargando {url}: {e}")
    return ""

def main():
    conn = sqlite3.connect("sync.db")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Seleccionamos episodios que no tengan ya un fanart descargado por este script
    c.execute("SELECT id, show_tmdb_id, season, episode, title, fanart_path FROM watch_history WHERE media_type = 'episode' AND show_tmdb_id IS NOT NULL AND show_tmdb_id != '' AND (fanart_path IS NULL OR fanart_path NOT LIKE '/cache/tmdb_ep_%')")
    rows = c.fetchall()
    
    print(f"Encontrados {len(rows)} episodios para comprobar...")
    
    updated_count = 0
    for row in rows:
        ep_id = row["id"]
        show_tmdb_id = row["show_tmdb_id"]
        season = row["season"]
        episode = row["episode"]
        title = row["title"]
        
        # Obtener información del episodio desde TMDB
        url = f"https://api.themoviedb.org/3/tv/{show_tmdb_id}/season/{season}/episode/{episode}?api_key={TMDB_API_KEY}"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                still_path = data.get("still_path")
                if still_path:
                    # El still_path viene con un / inicial, ej: /kqjL17yufvn9OVLyK90VDWTc61K.jpg
                    image_url = f"https://image.tmdb.org/t/p/w300{still_path}"
                    
                    # Generamos un nombre único para el archivo de caché
                    filename = f"tmdb_ep_{show_tmdb_id}_s{season}e{episode}.jpg"
                    
                    local_path = download_tmdb_image(image_url, filename)
                    if local_path:
                        c_upd = conn.cursor()
                        c_upd.execute("UPDATE watch_history SET fanart_path = ? WHERE id = ?", (local_path, ep_id))
                        updated_count += 1
                        print(f"[{updated_count}] Actualizado fanart para: {title} (S{season:02d}E{episode:02d})")
                        
                        # Guardar cada 50 registros por si acaso falla
                        if updated_count % 50 == 0:
                            conn.commit()
                            print(f"💾 Guardados {updated_count} cambios en disco...")
                else:
                    print(f"Sin imagen (still_path) en TMDB para: {title}")
            else:
                print(f"Error {r.status_code} en TMDB para S{season}E{episode} de show {show_tmdb_id}")
        except Exception as e:
            print(f"Fallo de conexión en {title}: {e}")
            
    conn.commit()
    conn.close()
    print(f"\n¡Proceso finalizado! Se actualizaron {updated_count} episodios.")

if __name__ == "__main__":
    main()
