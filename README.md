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

SyncPK requires Linux with `systemd` (for baremetal/LXC) and Python 3.9+. We support `amd64` and `arm64` architectures.
We provide multiple installation options. The initial installation is very quick, and once it finishes, you will complete the configuration (Plex Auth, TMDB, passwords) through a beautiful Web UI.

### Option A: Proxmox Automatic LXC
If you are running a Proxmox server, you can use our interactive script to automatically create a brand-new LXC container, install all dependencies, and set up the services. Run this command directly in your **Proxmox Host Shell**:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lechtung/SyncPK/main/install_proxmox.sh)"
```

**Next step:** Open your browser and go to `http://<YOUR_LXC_IP>:8000` to start the Web Setup.

### Option B: Baremetal / Existing Linux Machine
If you want to install SyncPK on a Raspberry Pi, an existing Debian/Ubuntu server, or inside a container you already created, use this script. Run this command as **root** inside your Linux machine:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lechtung/SyncPK/main/install.sh)"
```

**Next step:** Open your browser and go to `http://<YOUR_SERVER_IP>:8000` to start the Web Setup.

### Option C: Docker Compose (r/selfhosted)
For the standard self-hosted stack, SyncPK can be deployed seamlessly using Docker. We provide a pre-built image hosted on GitHub Container Registry (GHCR).

1. Create a `data` directory to store your persistent files and download the `docker-compose.yml`:
   ```bash
   mkdir data
   curl -O https://raw.githubusercontent.com/lechtung/SyncPK/main/docker-compose.yml
   ```
2. Start the container:
   ```bash
   docker compose up -d
   ```
3. **Next step:** Open your browser and go to `http://<YOUR_SERVER_IP>:8000` to start the Web Setup.

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

1. Download the `kodi_addon.zip` from the latest GitHub Release and install it in Kodi via "Install from zip file".
2. Go to the Addon Settings.
3. In the **SyncPK Server** tab, configure:
   - **Webhook URL**: Copy the Kodi Webhook URL provided at the end of the Web Setup wizard or in the SyncPK Dashboard settings (it includes your secure API token).
4. Restart Kodi. It will automatically perform a full sync!

---

## 🔄 Updates & Maintenance

- **UI Auto-Update**: If you enabled auto-updates in the Web Setup (Baremetal/LXC), the server updates itself automatically.
- **Docker Updates**: Run `docker compose pull && docker compose up -d`.
- **View Logs**: To troubleshoot, run `journalctl -u syncpk-server -f` (Baremetal/LXC) or `docker compose logs -f` (Docker).

---

## 🔒 Security & Firewall

**Important:** SyncPK runs entirely over HTTP and tokens are passed in the URL. **DO NOT** expose port 8000 directly to the Internet. If you need external access, use a secure Reverse Proxy (like NGINX, Traefik, or Cloudflare Tunnels) with SSL/TLS termination.

If your server has an active firewall (like `ufw`), you must open port 8000 to allow connections from your local network:

```bash
sudo ufw allow 8000/tcp
```

---

## 💾 Backup & Restore

All your configuration, hashed passwords, and the synchronized watch history are securely stored in a single folder. 

**Backup:**
To avoid database corruption, stop the service before copying the data, or use SQLite's backup feature.
```bash
sudo systemctl stop syncpk-server
sudo cp -r /var/lib/syncpk /var/lib/syncpk_backup
sudo systemctl start syncpk-server
```
*(For Docker, backup your mapped `./data` folder).*

**Restore:**
Simply place the contents back into `/var/lib/syncpk` (or `./data` for Docker) before starting a fresh installation, and the server will pick up where it left off.

---

## 🗑️ Uninstallation

**Warning:** Uninstalling will permanently delete your entire watch history database and configuration. Make a backup first!

**For Proxmox LXC**: Destroy the container from your Proxmox web interface.

**For Baremetal**: 
```bash
sudo systemctl disable --now syncpk-server syncpk-checker.timer syncpk-updater.path
sudo rm -rf /opt/syncpk /var/lib/syncpk /etc/systemd/system/syncpk*
sudo systemctl daemon-reload
sudo userdel syncpk
```

**For Docker**: Run `docker compose down -v` and delete the folder.

---

## 🙏 Credits & Third-Party Libraries

SyncPK uses the following open-source libraries:

- **[Pickr](https://github.com/Simonwep/pickr)** by Simonwep: A flat, simple, and elegant color-picker used in the dashboard's appearance configuration.

**Disclaimer:** This product uses the TMDB API but is not endorsed or certified by TMDB. SyncPK is not affiliated with, endorsed by, or sponsored by Plex Inc. or the XBMC Foundation (Kodi).

---

## 📄 License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0). See the [LICENSE](LICENSE) file for details.


### 🪟 Windows Users (Docker / Podman)
Running SyncPK natively as a service on Windows is not officially supported. However, you can run it perfectly using **Docker Desktop** (Free for personal use) or **Podman Desktop** (Open Source).
Make sure you use absolute Windows paths for your volumes in your docker-compose.yml or docker run command:
`yaml
volumes:
  - C:\path	o\your\data:/app/data
`

⚠️ **Important Note about UFW / Firewall in Docker:**
If you expose the port 8000:8000 in Docker, it will bypass Linux firewalls like UFW. To strictly bind the server to localhost for a reverse proxy, use 127.0.0.1:8000:8000 in your compose file.

⚠️ **Important Note about LXC DHCP:**
If you installed via LXC (Proxmox) and rely on DHCP, ensure you create a DHCP reservation in your router so the IP doesn't change and break your Kodi/Plex webhook URLs.
