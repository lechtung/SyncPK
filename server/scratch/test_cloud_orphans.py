import os
import sys

print("======================================================")
print("   ORPHAN HISTORY RESCUE TEST (CLOUD)                 ")
print("======================================================")

# 1. Load the environment to ensure PLEX_TOKEN and SYNC_LANGUAGE are picked up
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

# If no token in the environment, ask for it to facilitate testing
if not os.getenv("PLEX_TOKEN"):
    token = input("\n[?] PLEX_TOKEN not found in .env.\nPaste your PLEX_TOKEN here: ").strip()
    os.environ["PLEX_TOKEN"] = token
    
if not os.getenv("SYNC_LANGUAGE"):
    os.environ["SYNC_LANGUAGE"] = "es"

# 2. Import main (FastAPI and dependencies must be installed)
try:
    import main
except ImportError as e:
    print(f"\n[❌] Error importing main.py: {e}")
    print("Make sure you are running this script inside the server's virtual environment or have dependencies installed (pip install fastapi pydantic requests).")
    sys.exit(1)

# 3. Force module variable update just in case
main.PLEX_TOKEN = os.getenv("PLEX_TOKEN")
main.plex_headers["X-Plex-Token"] = main.PLEX_TOKEN

print("\n[+] Environment loaded. Starting Cloud extraction...\n")

# 4. Launch the function
try:
    main.push_cloud_orphans_to_db()
    print("\n[✅] Test finished successfully.")
except Exception as e:
    print(f"\n[❌] An error occurred during execution: {e}")
