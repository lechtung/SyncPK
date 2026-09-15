<p align="center">
  <img src="server/static/logo.png" width="128" height="128" alt="SyncPK Logo">
</p>

# SyncPK: Two-Way Kodi & Plex Sync Server

SyncPK is a self-hosted, lightweight two-way synchronization tool designed to keep your **Kodi** and **Plex** watch history perfectly aligned. 

Whether you watch a movie on your TV using Kodi or catch up on a series on your phone using Plex, SyncPK ensures both platforms instantly reflect the watched status without infinite synchronization loops.

## 🚀 How it works

The project is divided into three main components:
1. **Central Server (`main.py`)**: A lightweight FastAPI server that acts as a broker. It receives webhooks from Kodi and Plex when you watch something and stores a unified watch history in a local SQLite database.
2. **Plex Syncer (`plex_syncer.py`)**: A background service that runs alongside the central server. It periodically asks Plex for its latest watch history and sends it to the central server.
3. **Kodi Addon**: A Kodi plugin that tracks your local playback and pushes it to the server via Webhooks, while also pulling the latest changes from the server on startup or periodically.

Everything is secured by an API Key (Base64) to ensure no unauthorized access to your server.

---

## 🛠️ Installation

## 🔑 Prerequisites (Tokens & API Keys)

Before installing, you will need two things:

### 1. Plex Token
You need your Plex Token so the syncer can communicate with your Plex server.
1. Log in to Plex Web and enter any library.
2. Click on a movie or episode, click the three dots (...), and select **Get Info**.
3. At the bottom of the popup, click **View XML**.
4. Look at the URL in your browser's address bar. At the very end, you will see `&X-Plex-Token=xxxxxxxxxxxx`. Those characters are your token.

### 2. TMDB API Key
This is required to fetch movie/show posters and durations for your dashboard.
1. Create a free account at [The Movie Database (TMDB)](https://www.themoviedb.org/).
2. Go to your Account Settings -> API.
3. Request an API Key (Developer). It's instant and free. You'll get a 32-character string.

We provide two automated installation scripts. You don't need to manually download or configure the files.

### Option A: Proxmox Automatic LXC (Recommended)
If you are running a Proxmox server, you can use our interactive script to automatically create a brand-new LXC container, install all dependencies, and set up the services.

Run this command directly in your **Proxmox Host Shell**:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lechtung/SyncPK/main/install_proxmox.sh)"
```

### Option B: Baremetal / Existing Linux Machine
If you want to install SyncPK on a Raspberry Pi, an existing Debian/Ubuntu server, or inside a container you already created, use this script.

Run this command as **root** inside your Linux machine:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/lechtung/SyncPK/main/install.sh)"
```

*During the installation, you will be prompted (via an interactive blue UI) to enter your Plex IP, Plex Token, and a Master Password to secure the API.*

---

## 🎬 Plex Webhook Setup

For SyncPK to know immediately when you watch something on Plex (from your phone, TV, or browser), you need to configure a webhook in your Plex server:

1. Open your Plex Web interface and go to **Settings**.
2. Scroll down the left menu and click on **Webhooks** (under your server or account settings).
3. Click **Add Webhook**.
4. Enter your SyncPK server webhook URL: `http://<YOUR_SERVER_IP>:8000/webhook/plex`
5. Click **Save Changes**.

*(Note: Plex Webhooks require an active Plex Pass subscription. If you do not have Plex Pass, the background `plex_syncer.py` script will still poll Plex periodically to get your watches, but the webhook allows for instant sync without delays).*

---

## 📺 Kodi Addon Setup

Once the server is running, you need to install the Kodi addon on your media players.

1. Zip the `kodi_addon` folder and install it in Kodi via "Install from zip file".
2. Go to the Addon Settings.
3. In the **Servidor SyncPK** tab, configure:
   - **URL del Webhook**: `http://<YOUR_SERVER_IP>:8000/webhook/kodi`
   - **Contraseña del Servidor**: The Master Password you chose during the installation script.
4. Restart Kodi. It will automatically perform a full sync!
