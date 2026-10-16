<p align="center">
  <img src="server/static/logo.png" width="128" height="128" alt="SyncPK Logo">
</p>

# SyncPK: Two-Way Kodi & Plex Sync Server

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

SyncPK is a self-hosted, lightweight two-way synchronization tool designed to keep your **Kodi** and **Plex** watch history perfectly aligned. 

Whether you watch a movie on your TV using Kodi or catch up on a series on your phone using Plex, SyncPK ensures both platforms instantly reflect the watched status without infinite synchronization loops.

## 🚀 How it works

The project is divided into two main components:
1. **Central Server (`main.py`)**: A lightweight, robust FastAPI server that acts as a broker and background worker. It receives webhooks from Kodi and Plex when you watch something, storing a unified watch history in a local SQLite database. It also features a built-in **Plex background syncer** that periodically asks Plex for its latest watch history, a **Full Library historical importer**, and an asynchronous **TMDB Image Cacher** that downloads highly optimized posters to your local drive for zero-latency dashboard rendering.
2. **Kodi Addon**: A Kodi plugin that tracks your local playback and pushes it to the server via Webhooks, while also pulling the latest changes from the server on startup or periodically.

Everything is secured by **scrypt** and **Plex PIN OAuth** for a seamless and secure login experience.

---

## 🔑 Prerequisites (Tokens & API Keys)

Before installing, you will need two things:

### 1. Plex Account (OAuth)
The setup uses a secure OAuth flow to connect with your Plex server. During the web setup, you will be prompted to log in to Plex via a secure PIN link. You simply visit that link, authorize SyncPK, and the setup will securely configure your server automatically.

### 2. TMDB API Key
This is required to fetch movie/show posters and durations for your dashboard.
1. Create a free account at [The Movie Database (TMDB)](https://www.themoviedb.org/).
2. Go to your Account Settings -> API.
3. Request an API Key (Developer). It's instant and free. You'll get a 32-character string.

---

## 🛠️ Installation

SyncPK requires Linux with `systemd` (for baremetal/LXC) and Python 3.10+. We support `amd64` and `arm64` architectures.
Supported Linux distributions: **Debian 12+**, **Ubuntu 22.04+**, **Raspberry Pi OS (64-bit, Bookworm recommended)**, **Fedora 38+**, **AlmaLinux / Rocky / RHEL 9+**, and **Arch Linux**.

We provide multiple installation options. The initial installation is very quick, and once it finishes, you will complete the configuration (Plex Auth, TMDB, passwords) through a beautiful Web UI.

### Option A: Proxmox Automatic LXC
If you are running a Proxmox server, you can use our interactive script to automatically create a brand-new LXC container, install all dependencies, and set up the services. Run this command directly in your **Proxmox Host Shell**:

```bash
bash -c "$(curl -fsSL https://github.com/lechtung/SyncPK/releases/latest/download/install_proxmox.sh)"
```

**Next step:** Open your browser and go to `http://<YOUR_LXC_IP>:8000` to start the Web Setup.

> ⚠️ **Note about DHCP:** If you rely on DHCP for the LXC container's IP, create a **DHCP reservation** in your router so the IP doesn't change and break your Kodi/Plex webhook URLs.

### Option B: Baremetal / Existing Linux Machine
If you want to install SyncPK on a Raspberry Pi, an existing Linux server, or inside a container you already created, use this script. Run this command as **root** inside your Linux machine:

```bash
bash -c "$(curl -fsSL https://github.com/lechtung/SyncPK/releases/latest/download/install.sh)"
```

**To reinstall** (overwriting an existing installation safely):
```bash
bash -c "$(curl -fsSL https://github.com/lechtung/SyncPK/releases/latest/download/install.sh)" _ --force
```

*(Optional)* To manually verify download integrity prior to running, releases provide a `checksums.sha256` file:
```bash
curl -fsSLO https://github.com/lechtung/SyncPK/releases/latest/download/checksums.sha256 \
  && curl -fsSLO https://github.com/lechtung/SyncPK/releases/latest/download/install.sh \
  && sha256sum -c checksums.sha256 --ignore-missing \
  && sudo bash install.sh
```

**Next step:** Open your browser and go to `http://<YOUR_SERVER_IP>:8000` to start the Web Setup.

### Option C: Docker Compose (r/selfhosted)
For the standard self-hosted stack, SyncPK can be deployed seamlessly using Docker. We provide a pre-built image hosted on GitHub Container Registry (GHCR).

1. Create a `docker-compose.yml` file (or paste this into Portainer / Dockge):
   ```yaml
   services:
     syncpk-server:
       image: ghcr.io/lechtung/syncpk:latest
       container_name: syncpk-server
       restart: unless-stopped
       environment:
         # Set your local timezone (e.g. Europe/Madrid, America/New_York, UTC)
         - TZ=Etc/UTC
         # - PUID=1000 # Uncomment if running on a NAS (e.g. Synology, Unraid)
         # - PGID=1000
       ports:
         - "8000:8000"
       volumes:
         - ./data:/app/data
   ```

   > 💡 **Portainer / Dockge Note:** When using web managers like Portainer, relative paths like `./data` are stored in the engine's internal stack folder. If you prefer to store data in a specific host path, replace `./data` with an absolute path (e.g. `/opt/syncpk-data:/app/data`).

   *(Alternatively, download the compose file directly via terminal)*:
   ```bash
   curl -fsSL -O https://raw.githubusercontent.com/lechtung/SyncPK/main/docker-compose.yml
   ```

2. Start the container:
   ```bash
   docker compose up -d
   ```
3. **Next step:** Open your browser and go to `http://<YOUR_SERVER_IP>:8000` to start the Web Setup.

#### 🪟 Windows Users (Docker Desktop / Podman)
Running SyncPK natively as a Windows service is not officially supported. However, you can run it seamlessly using **Docker Desktop** (with WSL2 backend).

1. **Download compose file in PowerShell**:
   In PowerShell, invoke `curl.exe` directly (as `curl` is a PowerShell alias):
   ```powershell
   curl.exe -fsSL -O https://raw.githubusercontent.com/lechtung/SyncPK/main/docker-compose.yml
   ```

2. **Recommended Storage (Named Volume)**:
   SyncPK uses SQLite in WAL mode. Due to filesystem locking limitations between Windows NTFS and WSL2 bind mounts, we **strongly recommend** using a Docker Named Volume instead of mapping a local Windows folder. This guarantees maximum performance and eliminates database lock errors:

   ```yaml
   services:
     syncpk-server:
       image: ghcr.io/lechtung/syncpk:latest
       container_name: syncpk-server
       restart: unless-stopped
       environment:
         - TZ=UTC # Change to your local timezone (e.g. America/New_York, Europe/Madrid)
       ports:
         - "8000:8000"
       volumes:
         - syncpk-data:/app/data

   volumes:
     syncpk-data:
   ```

---

## 🎬 Plex Webhook Setup

For SyncPK to know immediately when you watch something on Plex, you need to configure a webhook in your Plex server:

1. Open your Plex Web interface and go to **Account Settings**.
2. Scroll down the left menu and click on **Webhooks**.
3. Click **Add Webhook**.
4. Enter your SyncPK server webhook URL. **You can find your exact URL with your secure token at the end of the Web Setup wizard or in the SyncPK Dashboard settings.**
5. Click **Save Changes**.

*(Note: Plex Webhooks require an active Plex Pass subscription. If you do not have Plex Pass, the integrated background task in the server will still poll Plex periodically to get your watches, but the webhook allows for instant sync without delays).*

---

## 📺 Kodi Addon Setup

Once the server is running, you need to install the Kodi addon on your media players.

1. Download [`kodi_addon.zip`](https://github.com/lechtung/SyncPK/releases/latest/download/kodi_addon.zip) from the latest GitHub Release and install it in Kodi via "Install from zip file".
2. Go to the Addon Settings.
3. In the **SyncPK Server** tab, configure:
   - **Webhook URL**: Copy the Kodi Webhook URL provided at the end of the Web Setup wizard or in the SyncPK Dashboard settings (it includes your secure API token).
4. Restart Kodi. It will automatically perform a full sync!

---

## 🔄 Updates & Maintenance

- **UI Auto-Update**: If you enabled auto-updates in the Web Setup (Baremetal/LXC), the server updates itself automatically via systemd timer.
- **Manual Baremetal Update**: If auto-update is disabled, run `sudo /opt/syncpk/update.sh`.
- **Docker Updates**: Run `docker compose pull && docker compose up -d`.
- **View Logs**: To troubleshoot, run `journalctl -u syncpk-server -f` (Baremetal/LXC) or `docker compose logs -f` (Docker).
- **Custom Port/Host (Baremetal)**: To change the listening port or IP, create a systemd drop-in override:
  ```bash
  sudo systemctl edit syncpk-server
  ```
  Add the following lines (replacing 8080 with your desired port):
  ```ini
  [Service]
  ExecStart=
  ExecStart=/opt/syncpk/venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8080 --no-access-log
  ```
  Then reload and restart: `sudo systemctl restart syncpk-server`.

  > ⚠️ **Note on systemd overrides:** The drop-in replaces the entire `ExecStart=` directive. SyncPK's updater automatically detects your overridden port to perform health checks and rollbacks reliably.

---

## 🔒 Security & Firewall

**Important:** SyncPK runs entirely over HTTP and tokens are passed in the URL. **DO NOT** expose port 8000 directly to the Internet. If you need external access, use a secure Reverse Proxy (like NGINX, Traefik, or Cloudflare Tunnels) with SSL/TLS termination.

If your server has an active firewall, allow connections to port 8000 from your local network:

```bash
# For UFW (Ubuntu / Debian / Raspberry Pi OS):
sudo ufw allow from 192.168.0.0/16 to any port 8000 proto tcp
# Or to allow unconditionally on a trusted local network:
# sudo ufw allow 8000/tcp

# For firewalld (AlmaLinux / Rocky / Fedora / RHEL):
# sudo firewall-cmd --add-port=8000/tcp --permanent && sudo firewall-cmd --reload
```

> ⚠️ **Docker & UFW:** If you expose port `8000:8000` in Docker, it will **bypass UFW**. To strictly bind the server to localhost for a reverse proxy, use `127.0.0.1:8000:8000` in your compose file.

---

## 💾 Backup & Restore

All your configuration, hashed passwords, and the synchronized watch history are securely stored in `/var/lib/syncpk`.

> ⚠️ **Filesystem Compatibility:** SyncPK uses SQLite in WAL mode. **Do not** locate `/var/lib/syncpk` or your Docker data directory on network storage like NFS or SMB/CIFS, as network filesystem locking limitations can cause database corruption.

**Backup:**
To guarantee database consistency without locking errors, stop the service before creating a compressed archive:
```bash
sudo systemctl stop syncpk-server
sudo install -d -m 700 /var/backups/syncpk
(umask 077 && sudo tar -czf /var/backups/syncpk/manual_backup_$(date +%Y%m%d_%H%M%S).tar.gz --exclude="./cache" -C /var/lib/syncpk .)
sudo systemctl start syncpk-server
```
*(Note: In Proxmox LXC or when running directly as root, omit 'sudo')*  
*(For Docker, simply backup your mapped `./data` directory or named volume).*

> 💡 **Automated Backups:** The updater (`update.sh`) automatically takes a verified compressed backup before every update in `/var/backups/syncpk/syncpk_data_backup_*.tar.gz`, retaining the latest 3 copies.

**Restore:**
Restore the archive into `/var/lib/syncpk` and ensure ownership before starting the service:
```bash
sudo systemctl stop syncpk-server
sudo find /var/lib/syncpk -mindepth 1 -maxdepth 1 ! -name 'cache' -exec rm -rf {} +
sudo tar -xzf /var/backups/syncpk/YOUR_BACKUP_FILE.tar.gz -C /var/lib/syncpk
sudo chown -R syncpk:syncpk /var/lib/syncpk
sudo systemctl start syncpk-server
```

---

## 🗑️ Uninstallation

**Warning:** Uninstalling will permanently delete your application files.

**For Proxmox LXC**: Destroy the container from your Proxmox web interface.

**For Baremetal**:
```bash
sudo /opt/syncpk/uninstall.sh
```
To also purge all data and backups non-interactively:
```bash
sudo /opt/syncpk/uninstall.sh --purge -y
```

**For Docker**: Run `docker compose down` and delete the persistent folder or volume:
```bash
docker compose down -v
rm -rf data
```

---

## 🙏 Credits & Third-Party Libraries

SyncPK uses the following open-source libraries:

- **[Pickr](https://github.com/Simonwep/pickr)** by Simonwep: A flat, simple, and elegant color-picker used in the dashboard's appearance configuration.

**Disclaimer:** This product uses the TMDB API but is not endorsed or certified by TMDB. SyncPK is not affiliated with, endorsed by, or sponsored by Plex Inc. or the XBMC Foundation (Kodi).

---

## 📄 License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0). See the [LICENSE](LICENSE) file for details.
