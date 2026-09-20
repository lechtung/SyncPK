import os
import sys

print("======================================================")
print("   TEST DE RESCATE DE HISTORIAL HUÉRFANO (CLOUD)      ")
print("======================================================")

# 1. Cargamos el entorno para asegurar que pilla el PLEX_TOKEN y el idioma
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

# Si no hay token en el entorno, lo pedimos por pantalla para facilitar la prueba
if not os.getenv("PLEX_TOKEN"):
    token = input("\n[?] No se ha detectado PLEX_TOKEN en el .env.\nPega tu PLEX_TOKEN aquí: ").strip()
    os.environ["PLEX_TOKEN"] = token
    
if not os.getenv("SYNC_LANGUAGE"):
    os.environ["SYNC_LANGUAGE"] = "es"

# 2. Importamos main (FastAPI y dependencias deben estar instaladas)
try:
    import main
except ImportError as e:
    print(f"\n[❌] Error al importar main.py: {e}")
    print("Asegúrate de estar ejecutando este script dentro del entorno virtual del servidor o de tener instaladas las dependencias (pip install fastapi pydantic requests).")
    sys.exit(1)

# 3. Forzamos la actualización de la variable en el módulo por si acaso
main.PLEX_TOKEN = os.getenv("PLEX_TOKEN")
main.plex_headers["X-Plex-Token"] = main.PLEX_TOKEN

print("\n[+] Entorno cargado. Iniciando extracción del Cloud...\n")

# 4. Lanzamos la función
try:
    main.push_cloud_orphans_to_db()
    print("\n[✅] Prueba finalizada correctamente.")
except Exception as e:
    print(f"\n[❌] Ocurrió un error durante la ejecución: {e}")
