# SyncPK Initial Sync Architecture

This document details the step-by-step technical process of the initial synchronization in `SyncPK`. When the application is set up for the first time (i.e. no `plex_settings.json` or `last_sync_date` exists), the backend triggers a massive, full-scale initial load.

The initial synchronization is orchestrated by the `background_initial_task()` which internally calls `run_sync()` (with `is_first_sync=True`) followed by `bulk_download_tmdb_images()`.

---

## 1. Local Plex Data & Activity (`push_all_to_db`)
This function processes local Plex libraries and extracts the watch history in a single, highly efficient pass.

### Initial Step: Library Discovery
The process begins by calling `get_plex_libraries()` to fetch the complete list of available libraries (sections) from the Plex server. 
* **API Call:** `GET /library/sections`
* **Headers:** Requires standard `plex_headers` (which includes `Accept: application/json` and `X-Plex-Token`).
* **Result:** Returns the total number of libraries and filters them to keep only those of type `movie` or `show`.

### Phase A: In-Memory Show Mapping
Before downloading episodes, the system maps the "Shows" metadata to memory.
* **Process:** It iterates through the previously retrieved list of libraries. If a library is specifically of type `show`, it hits the Plex API: `/library/sections/{key}/all?includeGuids=1`.
* **Result:** It stores the Plex `ratingKey` of every TV Show in a dictionary (`show_map`), linking it to its `guid` (IMDB/TMDB/TVDB) and `year`. This is necessary because when we later query for episodes, the episodes themselves might not contain the full Show ID metadata, so we use this map to attach the correct parent IDs to the episodes.

### Phase B: Extraction and DB Insertion
This is the core loop where the actual data and activity are extracted.
* **Process:** The system iterates over the list of libraries a second time to process the actual media items.
* **API Calls:** It paginates through the library (500 items at a time) using the headers `X-Plex-Container-Start` and `X-Plex-Container-Size`.
  * For Movie libraries: `GET /library/sections/{key}/all?includeGuids=1`
  * For Show libraries: `GET /library/sections/{key}/all?type=4&includeGuids=1` (Notice `type=4` forces Plex to return **flat episodes**, not shows).
* **Activity Detection:** The JSON response from Plex includes a field called `viewCount`. For every single item (movie or episode), the code checks `if item.get("viewCount", 0) > 0:`.
* **Deep History Check (`get_oldest_date`):** If the item has been watched, the backend does *not* just trust the `lastViewedAt` from the XML (which is only the most recent watch date). Instead, it makes a specific per-item API call to `/status/sessions/history/all?metadataItemID={rating_key}` to retrieve the complete playback history for that exact item and extracts the oldest possible watch date.
* **Insertion:** After extracting the true oldest date, it builds the payload and immediately inserts it into the `watch_history` SQLite table. It commits (`conn.commit()`) instantly so the data is visible in real-time.

> **Important Detail:** We do *not* fetch all movies/episodes and then do a secondary query to see if they were watched. The `/all` endpoint provides the `viewCount` directly in the payload. Therefore, "Library parsing" and "Activity fetching" happen concurrently.

---

## 2. Cloud Activity Sync (`push_cloud_orphans_to_db`)
This step recovers cross-server watch activity directly from Plex Cloud (the central Plex servers), capturing items watched on servers you don't host locally.

### Collision Prevention
First, it runs a `SELECT id, plex_guid, watched_at FROM watch_history` and loads it into memory. This prevents the cloud sync from duplicating items that were already fetched locally in the previous phase.

### GraphQL API Pagination
It connects to `https://community.plex.tv/api` using your `X-Plex-Token`.
* **Process:** It sends a GraphQL query (`GetActivityFeed`) to fetch all watch activities across your entire Plex account.
* **Query details:** It asks for `ActivityType` (watch events) and paginates using cursors (`hasNextPage`, `endCursor`).
* **Filtering:** For every activity node returned, it checks if it's a Movie or an Episode, extracts the IDs, and verifies against the local DB dictionary to avoid inserting duplicates. If the item is new (orphan cloud activity), it inserts it into `watch_history`.

### Orphan Episode Metadata Reconstruction (`get_cloud_episodes_for_scope`)
A critical edge case occurs when the GraphQL feed returns watch activity for an episode that has been **deleted from your local server**. Since the episode is gone, the local XML no longer provides its metadata.
To solve this, if an episode is identified as an orphan, the system connects directly to Plex's central provider (`metadata.provider.plex.tv`). It queries the full Show structure, requests the specific Season (`/children`), and retrieves the metadata for **every single episode in that season**. This guarantees that even if you only have episode 3 on your hard drive, the system can perfectly reconstruct the metadata (titles, durations, external IDs) for the deleted episodes 1 and 2 that you watched in the past.

---

## 3. TMDB Covers & Metadata (`bulk_download_tmdb_images`)
At this point, `watch_history` contains all your watched movies and episodes, but it lacks high-quality posters, fanarts, and proper localized titles. The system enters `sync_state = 3`.

### The Rescan Loop (`execute_full_rescan`)
* **Process:** It queries all items from the `watch_history` table.
* **API Calls:** For each item, it connects to the TMDB API using your provided API key and localized language preference (`SYNC_LANGUAGE`).
  * Movies: `GET /3/movie/{tmdb_id}`
  * Episodes: `GET /3/tv/{show_tmdb_id}/season/{season}/episode/{episode}` and optionally the Show/Season endpoints to get the correct poster based on user preferences.
* **Downloading (Concurrency):** It downloads the images (Posters and Fanarts) into the `/static/cache/posters/` and `/static/cache/fanarts/` directories. This process is **highly concurrent**, using an `asyncio.Semaphore(15)` and `asyncio.gather()` to fetch up to 15 items simultaneously, maximizing bandwidth and drastically reducing total download time.
* **WebSocket Broadcasting:** Every 20 items processed, the backend groups them and fires a `{"type": "batch_update", "items": [...]}` WebSocket message to `manager.broadcast()`. This is what allows the frontend to show real-time progress of the artwork downloads.
