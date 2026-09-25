// v10

let currentLangData = {};
let historyData = [];
let offset = 0;
let limit = 20;
let hasMore = true;
let isLoading = false;
let currentFilters = { type: 'all', year: 'all', month: 'all', search: '' };
let statsCache = { movies: 0, moviesHours: 0, episodes: 0, episodesHours: 0 };
let logInterval = null;

// --- TOAST NOTIFICATION SYSTEM ---
function showToast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const icons = { error: '<i class="fas fa-times-circle"></i>', success: '<i class="fas fa-check-circle"></i>', info: '<i class="fas fa-info-circle"></i>' };
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span class="toast-icon">${icons[type] || icons.info}</span><span>${message}</span>`;
    container.appendChild(toast);
    const remove = () => {
        toast.classList.add('toast-out');
        toast.addEventListener('animationend', () => toast.remove(), { once: true });
    };
    setTimeout(remove, duration);
    toast.addEventListener('click', remove);
}

// --- FULLSCREEN OVERLAY SYSTEM ---
function showProcessingOverlay(title, subtitle) {
    const overlay = document.getElementById('loading-overlay');
    if (!overlay) return;
    document.getElementById('overlay-title').textContent = title || currentLangData.overlay_processing || 'Processing request';
    document.getElementById('overlay-subtitle').textContent = subtitle || currentLangData.overlay_wait || 'Please wait...';

    const icon = document.getElementById('overlay-icon');
    // Restaurar spinner inicial
    const iconContainer = document.getElementById('overlay-icon-container');
    if (iconContainer) {
        iconContainer.innerHTML = '<div class="spinner-premium"></div>';
    }

    overlay.classList.remove('hidden');
}

function updateOverlayResult(type, title, subtitle) {
    const overlay = document.getElementById('loading-overlay');
    if (!overlay) return;

    document.getElementById('overlay-title').textContent = title;

    const subtitleEl = document.getElementById('overlay-subtitle');
    if (subtitle !== undefined) {
        subtitleEl.textContent = subtitle;
        subtitleEl.style.display = subtitle ? 'block' : 'none';
    } else {
        subtitleEl.textContent = '';
        subtitleEl.style.display = 'none';
    }

    const iconContainer = document.getElementById('overlay-icon-container');
    if (iconContainer) {
        if (type === 'success') {
            iconContainer.innerHTML = `
                <svg class="svg-anim" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52">
                    <circle class="circle" cx="26" cy="26" r="25" stroke="#3fb950"/>
                    <path class="check" stroke="#3fb950" d="M14.1 27.2l7.1 7.2 16.7-16.8"/>
                </svg>
            `;
        } else {
            iconContainer.innerHTML = `
                <svg class="svg-anim" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52">
                    <circle class="circle" cx="26" cy="26" r="25" stroke="#f85149"/>
                    <path class="cross" stroke="#f85149" d="M16 16 36 36 M36 16 16 36"/>
                </svg>
            `;
        }
    }
}

function hideOverlay(delay = 2000) {
    const overlay = document.getElementById('loading-overlay');
    if (!overlay) return;
    setTimeout(() => {
        overlay.classList.add('hidden');
    }, delay);
}

// Helper to get token value
function getAuthToken() {
    return localStorage.getItem('syncpk_token') || "";
}

// Wrapper for fetch to ensure auth is always sent
async function apiFetch(url, options = {}) {
    if (!options.headers) options.headers = {};
    let token = getAuthToken();
    if (token) {
        // Backend returns "Basic <password>", don't duplicate "Basic "
        if (token.startsWith("Basic ")) {
            options.headers['Authorization'] = token;
        } else {
            options.headers['Authorization'] = `Basic ${token}`;
        }
    }
    return fetch(url, options);
}

document.addEventListener('DOMContentLoaded', init);

async function init() {
    await loadTranslations();
    await loadUIConfig();

    // Check if token exists
    if (getAuthToken()) {
        showDashboard();
    } else {
        document.getElementById('login-overlay').classList.remove('hidden');
    }

    setupEventListeners();
    initDropdowns();
    populateYearFilter();
    populateMonthFilter();
}

async function loadUIConfig() {
    try {
        let res = await fetch('/api/ui_config');
        if (res.ok) {
            let data = await res.json();
            if (data.fanart_mask_opacity) {
                document.documentElement.style.setProperty('--fanart-mask-opacity', data.fanart_mask_opacity);
            }
        }
    } catch (e) {
        console.warn("Could not load UI config", e);
    }
}

async function loadTranslations() {
    let lang = navigator.language.split('-')[0];
    if (window.DASHBOARD_LANG && window.DASHBOARD_LANG !== "auto") {
        lang = window.DASHBOARD_LANG;
    }
    try {
        let res = await fetch(`locales/${lang}.json`);
        if (!res.ok) throw new Error("Not found");
        currentLangData = await res.json();
    } catch (e) {
        let res = await fetch(`locales/en.json`);
        currentLangData = await res.json();
    }

    document.querySelectorAll('[data-i18n]').forEach(el => {
        let key = el.getAttribute('data-i18n');
        if (currentLangData[key]) el.textContent = currentLangData[key];
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        let key = el.getAttribute('data-i18n-placeholder');
        if (currentLangData[key]) el.placeholder = currentLangData[key];
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
        let key = el.getAttribute('data-i18n-title');
        if (currentLangData[key]) el.title = currentLangData[key];
    });
}



function setupEventListeners() {
    document.getElementById('login-btn').addEventListener('click', doLogin);
    document.getElementById('password-input').addEventListener('keypress', e => { if (e.key === 'Enter') doLogin(); });

    let searchTimeout;
    document.getElementById('search-input').addEventListener('input', e => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            currentFilters.search = e.target.value;
            reloadHistory();
        }, 500);
    });

    // Infinite scroll
    window.addEventListener('scroll', () => {
        if (window.innerHeight + window.scrollY >= document.body.offsetHeight - 200) {
            loadMoreHistory();
        }
    });

    // Close dropdowns on outside click
    document.addEventListener('click', e => {
        if (!e.target.closest('.c-dropdown')) {
            document.querySelectorAll('.c-dropdown').forEach(d => d.classList.remove('open'));
        }
        if (!e.target.closest('.kebab-menu-btn')) {
            document.querySelectorAll('.kebab-dropdown').forEach(d => d.classList.remove('show'));
        }
        if (!e.target.closest('#manual-search-results') && !e.target.closest('#manual-search-input')) {
            let resBox = document.getElementById('manual-search-results');
            if (resBox) resBox.classList.add('hidden');
        }
    });

    let closeLogBtn = document.getElementById('close-log-btn');
    if (closeLogBtn) closeLogBtn.addEventListener('click', closeLogViewer);

    document.getElementById('edit-cancel-btn').addEventListener('click', () => {
        document.getElementById('edit-modal').classList.add('hidden');
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            ['edit-modal', 'manual-add-modal', 'config-modal', 'log-modal'].forEach(id => {
                const modal = document.getElementById(id);
                if (modal && !modal.classList.contains('hidden')) {
                    modal.classList.add('hidden');
                }
            });
        }
    });
}

function openLogViewer() {
    document.getElementById('log-modal').classList.remove('hidden');
    fetchLogs();
    logInterval = setInterval(fetchLogs, 5000); // 5 seconds auto-refresh
}

function closeLogViewer() {
    document.getElementById('log-modal').classList.add('hidden');
    if (logInterval) {
        clearInterval(logInterval);
        logInterval = null;
    }
}

async function fetchLogs() {
    try {
        let res = await apiFetch('/api/logs');
        if (res.ok) {
            let data = await res.json();
            let logEl = document.getElementById('log-content');
            let isAtBottom = (logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 50);
            logEl.textContent = data.logs;
            if (isAtBottom || logEl.scrollTop === 0) {
                logEl.scrollTop = logEl.scrollHeight;
            }
        }
    } catch (e) { }
}

function initDropdowns() {
    document.querySelectorAll('.c-dropdown').forEach(dd => {
        let trigger = dd.querySelector('.c-dropdown-trigger');
        let closeTimer;

        // Hover to open (excepto para editScope-dd, editDistMode-dd y configLang-dd)
        if (dd.id !== 'editScope-dd' && dd.id !== 'editDistMode-dd' && dd.id !== 'configLang-dd') {
            dd.addEventListener('mouseenter', () => {
                clearTimeout(closeTimer);
                dd.classList.add('open');
            });
            dd.addEventListener('mouseleave', () => {
                closeTimer = setTimeout(() => dd.classList.remove('open'), 180);
            });
        }

        // Click trigger also toggles
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            dd.classList.toggle('open');
        });

        // Click items
        dd.querySelectorAll('.c-dropdown-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                let value = item.dataset.value;
                let filterId = dd.dataset.filter;
                let valEl = dd.querySelector('.c-dropdown-value');

                // Update selected style
                dd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
                item.classList.add('selected');
                if (valEl) valEl.textContent = item.textContent;
                dd.classList.remove('open');

                // Update filter and reload if applicable
                if (filterId) {
                    currentFilters[filterId] = value;
                    reloadHistory();
                } else {
                    dd.dataset.currentValue = value;
                    if (dd.id === 'editScope-dd' || dd.id === 'editDistMode-dd') {
                        if (window.updateBulkOptionsUI) window.updateBulkOptionsUI();
                    }
                }
            });
        });
    });
}

async function doLogin() {
    const pwd = document.getElementById('password-input').value;
    try {
        const res = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pwd })
        });
        if (res.ok) {
            const data = await res.json();
            localStorage.setItem('syncpk_token', data.token);
            document.getElementById('login-overlay').classList.add('hidden');
            showDashboard();
        } else {
            document.getElementById('login-error').classList.remove('hidden');
        }
    } catch (e) {
        document.getElementById('login-error').classList.remove('hidden');
    }
}

function showDashboard() {
    document.getElementById('login-overlay').classList.add('hidden');
    document.getElementById('dashboard').classList.remove('hidden');
    loadStats();
    reloadHistory();
    generateTimeline();
}

function populateYearFilter(years = []) {
    const menu = document.getElementById('dd-year-menu');
    if (!menu) return;

    // Preserve the "All years" option using current language
    let allText = currentLangData['filter_year_all'] || 'All';
    menu.innerHTML = `<div class="c-dropdown-item selected" data-value="all" data-i18n="filter_year_all">${allText}</div>`;

    if (!years || years.length === 0) {
        const currentYear = new Date().getFullYear();
        years = [currentYear.toString()];
    }

    years.forEach(y => {
        let item = document.createElement('div');
        item.className = 'c-dropdown-item';
        item.dataset.value = y;
        item.textContent = y;
        menu.appendChild(item);
    });

    // Re-init the year dropdown to wire up the new items
    let dd = document.getElementById('dd-year');
    if (dd) {
        dd.querySelectorAll('.c-dropdown-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                let value = item.dataset.value;
                dd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
                item.classList.add('selected');
                let valEl = dd.querySelector('.c-dropdown-value');
                if (valEl) valEl.textContent = item.textContent;
                dd.classList.remove('open');
                currentFilters.year = value;
                reloadHistory();
            });
        });
    }
}

function populateMonthFilter() {
    const menu = document.getElementById('dd-month-menu');
    const dd = menu?.closest('.c-dropdown');
    if (!menu || !dd) return;

    const locale = navigator.language || navigator.languages?.[0] || 'es';
    console.log('[SyncPK] Locale detected for months:', locale);

    for (let i = 0; i < 12; i++) {
        let monthName = new Date(2000, i, 1).toLocaleDateString(navigator.language, { month: 'long' });
        monthName = monthName.charAt(0).toUpperCase() + monthName.slice(1);

        let item = document.createElement('div');
        item.className = 'c-dropdown-item';
        item.dataset.value = i + 1; // months are 1-12 in our API
        item.textContent = monthName;
        menu.appendChild(item);
    }

    // Wire up click events for all items including "All"
    dd.querySelectorAll('.c-dropdown-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            dd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
            item.classList.add('selected');
            let valEl = dd.querySelector('.c-dropdown-value');
            if (valEl) valEl.textContent = item.textContent;
            dd.classList.remove('open');
            currentFilters.month = item.dataset.value;
            reloadHistory();
        });
    });
}

async function reloadHistory() {
    offset = 0;
    hasMore = true;
    historyData = [];
    document.getElementById('history-feed').innerHTML = '';
    statsCache.moviesHours = 0;
    statsCache.episodesHours = 0;
    loadStats(); // Refrescar las tarjetas de estadísticas globales
    await loadMoreHistory();
}

async function loadMoreHistory() {
    if (isLoading || !hasMore) return;
    isLoading = true;
    document.getElementById('loading-spinner').classList.remove('hidden');

    try {
        let url = `/api/history?limit=${limit}&offset=${offset}&type=${currentFilters.type}&year=${currentFilters.year}&month=${currentFilters.month}&search=${encodeURIComponent(currentFilters.search)}`;
        let res = await apiFetch(url);
        if (res.status === 401) { logout(); return; }
        if (!res.ok) throw new Error("API returned status " + res.status);

        let data = await res.json();
        let newItems = data.items || [];

        if (newItems.length < limit) hasMore = false;

        historyData = historyData.concat(newItems);
        offset += limit;

        if (historyData.length === 0) {
            let emptyMsg = currentLangData.empty_state_msg || 'Run to the TV and put on a good movie!';
            document.getElementById('history-feed').innerHTML = `<div style="text-align:center; padding:50px; color:#fff; font-size:2rem; font-weight:bold;">${emptyMsg}</div>`;
        } else {
            await renderHistory(newItems);
            generateTimeline(); // Refresh dots after new data
        }
    } catch (e) {
        console.error(e);
        if (historyData.length === 0) {
            let errMsg = currentLangData.error_state_msg || 'Error connecting to the database or scan in progress...';
            document.getElementById('history-feed').innerHTML = `<div style="text-align:center; padding:50px; color:#f55; font-size:1.2rem;">${errMsg}</div>`;
        }
    }

    isLoading = false;
    document.getElementById('loading-spinner').classList.add('hidden');
}

async function loadStats() {
    try {
        let url = `/api/stats?type=${currentFilters.type}&year=${currentFilters.year}&month=${currentFilters.month}&search=${encodeURIComponent(currentFilters.search)}`;
        let res = await apiFetch(url);
        if (res.ok) {
            let data = await res.json();
            document.getElementById('stat-movies').textContent = data.movies_count;
            document.getElementById('stat-episodes').textContent = data.episodes_count;
            if (data.available_years) {
                populateYearFilter(data.available_years);
            }

            // Use exact duration directly from the server!
            let estMoviesHours = Math.round(data.movies_hours || 0);
            let estEpisodesHours = Math.round(data.episodes_hours || 0);

            document.getElementById('stat-movies-hours').textContent = estMoviesHours + 'h';
            document.getElementById('stat-episodes-hours').textContent = estEpisodesHours + 'h';
            document.getElementById('stat-total-hours').textContent = (estMoviesHours + estEpisodesHours) + 'h';

            // Handle sync banner
            let banner = document.getElementById('sync-banner');
            if (data.sync_state === 1 || data.sync_state === 2) {
                if (!banner) {
                    banner = document.createElement('div');
                    banner.id = 'sync-banner';
                    let feed = document.getElementById('history-feed');
                    feed.parentNode.insertBefore(banner, feed);
                }

                if (data.sync_state === 1) {
                    banner.style.cssText = "background-color: rgba(255, 152, 0, 0.2); color: #ffb74d; border: 1px solid #ffb74d; padding: 15px 20px; text-align: center; font-weight: bold; margin-bottom: 20px; border-radius: 8px; display: flex; justify-content: center; align-items: center; gap: 20px; flex-wrap: wrap;";
                    let msg = currentLangData.sync_in_progress_msg || 'The server is still performing the initial load. Some images or metadata might not be available.';
                    banner.innerHTML = `<span>${msg}</span><button id="btn-view-logs" style="padding: 6px 12px; background: #ffb74d; color: #000; border: none; cursor: pointer; border-radius: 4px; font-weight: bold; font-family: 'Inter', sans-serif;">Ver Terminal</button>`;

                    document.getElementById('btn-view-logs').addEventListener('click', openLogViewer);
                } else if (data.sync_state === 2) {
                    banner.style.cssText = "background-color: rgba(76, 175, 80, 0.2); color: #81c784; border: 1px solid #81c784; padding: 15px 20px; text-align: center; font-weight: bold; margin-bottom: 20px; border-radius: 8px;";
                    banner.textContent = currentLangData.sync_done_msg || 'Initial load complete! You can now configure webhooks.';

                    // Mark as seen on server so it doesn't show on next refresh
                    apiFetch('/api/dismiss-sync', { method: 'POST' }).catch(e => console.error(e));
                }
            } else {
                if (banner) banner.remove();
            }
        }
    } catch (e) { console.error(e); }
}

function logout() {
    localStorage.removeItem('syncpk_token');
    document.getElementById('dashboard').classList.add('hidden');
    document.getElementById('login-overlay').classList.remove('hidden');
}


// --- RENDER ---
async function renderHistory(items) {
    const feed = document.getElementById('history-feed');

    // Group by day string
    let grouped = {};
    items.forEach(item => {
        let dateObj = new Date(item.watched_at);
        let dayString = dateObj.toLocaleDateString(navigator.language, { day: 'numeric', month: 'long', year: 'numeric' });
        if (!grouped[dayString]) grouped[dayString] = [];
        grouped[dayString].push(item);
    });

    for (const [dayString, dayItems] of Object.entries(grouped)) {
        // Find or create group container
        let groupId = 'group-' + dayString.replace(/\s+/g, '-');
        let groupDiv = document.getElementById(groupId);
        let cardsGrid = null;

        if (!groupDiv) {
            groupDiv = document.createElement('div');
            groupDiv.className = 'history-group';
            groupDiv.id = groupId;
            // Store raw date for scroll tracking
            if (dayItems.length > 0) {
                groupDiv.dataset.date = new Date(dayItems[0].watched_at).toDateString();
            }
            groupDiv.innerHTML = `<div class="history-group-title">${dayString}</div><div class="cards-grid"></div>`;
            feed.appendChild(groupDiv);
        }
        cardsGrid = groupDiv.querySelector('.cards-grid');

        for (const item of dayItems) {
            // Check if card already exists
            if (document.getElementById(`card-${item.id}`)) continue;

            let card = document.createElement('div');
            card.className = 'media-card c-card';
            card.id = `card-${item.id}`;
            card.style.cursor = 'pointer';

            card.onclick = (e) => {
                if (e.target.closest('.kebab-menu-btn') || e.target.closest('.kebab-dropdown')) return;

                let tmdbUrl = '';
                if (item.media_type === 'movie' && item.tmdb_id) {
                    tmdbUrl = `https://www.themoviedb.org/movie/${item.tmdb_id}`;
                } else if (item.media_type === 'episode' && item.show_tmdb_id) {
                    // Usamos el ID de la serie de la base de datos
                    tmdbUrl = `https://www.themoviedb.org/tv/${item.show_tmdb_id}/season/${item.season}/episode/${item.episode}`;
                } else if (item.media_type === 'movie' && item.title) {
                    tmdbUrl = `https://www.themoviedb.org/search/movie?query=${encodeURIComponent(item.title)}`;
                } else if (item.media_type === 'episode' && item.show_title) {
                    tmdbUrl = `https://www.themoviedb.org/search/tv?query=${encodeURIComponent(item.show_title)}`;
                }

                if (tmdbUrl) {
                    window.open(tmdbUrl, '_blank');
                }
            };

            let posterStyle = item.poster_path ? `background-image: url('${item.poster_path}')` : 'background-color: #333';
            let bgStyle = item.fanart_path ? `background-image: url('${item.fanart_path}')` : 'background-color: #111';

            let timeStr = new Date(item.watched_at).toLocaleTimeString(navigator.language, { hour: '2-digit', minute: '2-digit' });
            let title = item.media_type === 'movie' ? item.title : item.show_title;
            let subtitle = item.media_type === 'movie' ? '' : `T${item.season} · E${item.episode} - ${item.title}`;

            card.innerHTML = `
                <div class="c-poster" style="${posterStyle}"></div>
                <div class="c-fanart-content" style="${bgStyle}">
                    <div class="c-fanart-mask"></div>
                    <div class="c-fanart-overlay"></div>
                    <div class="card-content">
                        <div class="card-title">${title}</div>
                        <div class="card-subtitle">${subtitle}</div>
                        <div class="card-time">${timeStr}</div>
                    </div>
                    <button class="kebab-menu-btn" onclick="toggleDropdown(${item.id}, event)">⋮</button>
                    <div class="kebab-dropdown glass-panel" id="dropdown-${item.id}">
                        <div class="dropdown-item danger" onclick="deleteItem(${item.id})" data-i18n="action_delete">${currentLangData.action_delete || 'Delete'}</div>
                        <div class="dropdown-item" onclick="openEditModal(${item.id}, '${item.watched_at}', '${item.media_type}')" data-i18n="action_edit">${currentLangData.action_edit || 'Edit'}</div>
                    </div>
                </div>
            `;
            cardsGrid.appendChild(card);
        }
    }
}

// --- ACTIONS ---
window.toggleDropdown = function (id, e) {
    e.stopPropagation();
    document.querySelectorAll('.kebab-dropdown').forEach(d => {
        if (d.id !== `dropdown-${id}`) d.classList.remove('show');
    });
    document.getElementById(`dropdown-${id}`).classList.toggle('show');
}

window.deleteItem = function (id) {
    let modal = document.getElementById('confirm-modal');
    modal.classList.remove('hidden');
    let yesBtn = document.getElementById('confirm-yes-btn');
    let noBtn = document.getElementById('confirm-no-btn');

    // Create new listeners to avoid duplicate events from previous calls
    let newYes = yesBtn.cloneNode(true);
    let newNo = noBtn.cloneNode(true);
    yesBtn.parentNode.replaceChild(newYes, yesBtn);
    noBtn.parentNode.replaceChild(newNo, noBtn);

    newNo.addEventListener('click', () => modal.classList.add('hidden'));
    newYes.addEventListener('click', async () => {
        modal.classList.add('hidden');
        try {
            let syncRemote = document.getElementById('deleteSyncRemote') ? document.getElementById('deleteSyncRemote').checked : false;

            showProcessingOverlay(
                currentLangData.overlay_processing || 'Processing request',
                currentLangData.deleting_msg || 'Deleting, please wait...'
            );

            let res = await apiFetch(`/api/history/${id}?sync_remote=${syncRemote}`, { method: 'DELETE' });
            if (res.ok) {
                let card = document.getElementById(`card-${id}`);
                if (card) {
                    let group = card.closest('.history-group');
                    card.remove();
                    if (group) {
                        let grid = group.querySelector('.cards-grid');
                        if (grid && grid.children.length === 0) {
                            group.remove();
                            if (typeof generateTimeline === 'function') generateTimeline();
                        }
                    }
                }
                updateOverlayResult('success', currentLangData.delete_success || 'Entry deleted successfully.', '');
                hideOverlay(2000);
            } else {
                updateOverlayResult('error', currentLangData.manual_save_error || 'Error deleting.', '');
                hideOverlay(3000);
            }
        } catch (e) {
            console.error(e);
            updateOverlayResult('error', currentLangData.network_error || 'Network error.', '');
            hideOverlay(3000);
        }
    });
}

let currentEditId = null;
window.openEditModal = function (id, dateStr, mediaType) {
    currentEditId = id;
    let modal = document.getElementById('edit-modal');
    // Format to datetime-local expected format YYYY-MM-DDThh:mm
    let d = new Date(dateStr);
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    document.getElementById('edit-date-input').value = d.toISOString().slice(0, 16);

    // Show scope selector only for episodes
    let scopeSelect = document.getElementById('editScope-dd');
    let scopeValEl = document.getElementById('editScope-val');

    // Reset selection to default (episode)
    scopeSelect.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
    let defaultItem = scopeSelect.querySelector('.c-dropdown-item[data-value="episode"]');
    if (defaultItem) defaultItem.classList.add('selected');

    if (mediaType === 'episode') {
        scopeSelect.classList.remove('hidden');
        scopeSelect.dataset.currentValue = 'episode';
        if (currentLangData.edit_scope_episode) scopeValEl.textContent = currentLangData.edit_scope_episode;
    } else {
        scopeSelect.classList.add('hidden');
        scopeSelect.dataset.currentValue = 'episode';
    }

    // Reset Bulk Options Mode to 'same'
    let distModeSelect = document.getElementById('editDistMode-dd');
    if (distModeSelect) {
        distModeSelect.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
        let dmItem = distModeSelect.querySelector('.c-dropdown-item[data-value="same"]');
        if (dmItem) dmItem.classList.add('selected');
        distModeSelect.dataset.currentValue = 'same';
        let dmValEl = document.getElementById('editDistMode-val');
        if (dmValEl && currentLangData.dist_mode_same) dmValEl.textContent = currentLangData.dist_mode_same;

        // Setup initial radio listener if not already there
        if (!window.distRadioInit) {
            document.querySelectorAll('input[name="distBetweenType"]').forEach(r => r.addEventListener('change', window.updateBulkOptionsUI));
            document.querySelectorAll('input[name="distOrder"]').forEach(r => {
                r.addEventListener('change', (e) => {
                    let help = document.getElementById('order-help-text');
                    if (e.target.value === 'asc') help.textContent = currentLangData.help_order_asc || "First episode will be assigned to selected date.";
                    else help.textContent = currentLangData.help_order_desc || "Last episode will be assigned to selected date.";
                });
            });
            window.distRadioInit = true;
        }

        // Trigger initial UI update
        if (window.updateBulkOptionsUI) window.updateBulkOptionsUI();
    }

    modal.classList.remove('hidden');
}

window.updateBulkOptionsUI = function () {
    let scopeVal = document.getElementById('editScope-dd').dataset.currentValue || 'episode';
    let bulkContainer = document.getElementById('bulkOptions-container');
    if (!bulkContainer) return;

    if (scopeVal === 'episode') {
        bulkContainer.classList.add('hidden');
        return;
    }

    bulkContainer.classList.remove('hidden');
    let mode = document.getElementById('editDistMode-dd').dataset.currentValue || 'same';

    let fEnd = document.getElementById('field-end-date');
    let fMin = document.getElementById('field-eps-min');
    let fMax = document.getElementById('field-eps-max');
    let fOrder = document.getElementById('field-order');
    let fBetweenType = document.getElementById('field-between-type');
    let labelMin = document.getElementById('label-eps-min');

    fEnd.classList.add('hidden');
    fMin.classList.add('hidden');
    fMax.classList.add('hidden');
    fOrder.classList.add('hidden');
    fBetweenType.classList.add('hidden');

    if (mode === 'fixed') {
        fMin.classList.remove('hidden');
        fOrder.classList.remove('hidden');
        labelMin.textContent = currentLangData.label_eps_per_day || "Episodes per day";
    } else if (mode === 'random') {
        fMin.classList.remove('hidden');
        fMax.classList.remove('hidden');
        fOrder.classList.remove('hidden');
        labelMin.textContent = currentLangData.label_eps_min || "Min episodes per day";
    } else if (mode === 'between') {
        fEnd.classList.remove('hidden');
        fBetweenType.classList.remove('hidden');
        let isRandom = document.querySelector('input[name="distBetweenType"]:checked').value === 'random';
        if (isRandom) fMax.classList.remove('hidden');
    }
}

document.getElementById('edit-save-btn').addEventListener('click', async () => {
    let newVal = document.getElementById('edit-date-input').value; // YYYY-MM-DDThh:mm
    if (!newVal) {
        showToast(currentLangData.manual_missing_fields || 'Please fill all required fields.', 'info');
        return;
    }

    let scopeSelect = document.getElementById('editScope-dd');
    let scopeVal = scopeSelect.dataset.currentValue || 'episode';

    // Convert back to UTC string format used by DB (or local if prefered, backend saves as string)
    let finalDateStr = new Date(newVal).toISOString();

    let payload = {
        watched_at: finalDateStr,
        scope: scopeVal,
        sync_remote: document.getElementById('editSyncRemote').checked
    };

    if (scopeVal !== 'episode') {
        let distMode = document.getElementById('editDistMode-dd').dataset.currentValue || 'same';
        payload.dist_mode = distMode;
        if (distMode === 'fixed') {
            let valMin = document.getElementById('input-eps-min').value;
            if (!valMin) { showToast(currentLangData.manual_missing_fields || 'Please fill all required fields.', 'info'); return; }
            payload.eps_per_day = parseInt(valMin) || 1;
            payload.dist_order = document.querySelector('input[name="distOrder"]:checked').value;
        } else if (distMode === 'random') {
            let valMin = document.getElementById('input-eps-min').value;
            let valMax = document.getElementById('input-eps-max').value;
            if (!valMin || !valMax) { showToast(currentLangData.manual_missing_fields || 'Please fill all required fields.', 'info'); return; }
            payload.eps_min = parseInt(valMin) || 1;
            payload.eps_max = parseInt(valMax) || 3;
            payload.dist_order = document.querySelector('input[name="distOrder"]:checked').value;
        } else if (distMode === 'between') {
            let endDateStr = document.getElementById('edit-end-date-input').value;
            if (!endDateStr) { showToast(currentLangData.manual_missing_fields || 'Please fill all required fields.', 'info'); return; }
            payload.end_date = new Date(endDateStr).toISOString();

            let betweenType = document.querySelector('input[name="distBetweenType"]:checked').value;
            payload.dist_between_type = betweenType;
            if (betweenType === 'random') {
                let valMax = document.getElementById('input-eps-max').value;
                if (!valMax) { showToast(currentLangData.manual_missing_fields || 'Please fill all required fields.', 'info'); return; }
                payload.eps_max = parseInt(valMax) || 3;
            }
        }
    }

    // Use new load overlay
    document.getElementById('edit-modal').classList.add('hidden');
    showProcessingOverlay(
        currentLangData.overlay_processing || 'Processing request',
        currentLangData.updating_msg || 'Updating, this may take a few minutes...'
    );

    try {
        let res = await apiFetch(`/api/history/${currentEditId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        let data = null;
        try { data = await res.json(); } catch (e) { }

        if (res.ok && data && data.status === 'success') {
            reloadHistory(); // Reload to sort properly
            updateOverlayResult('success', currentLangData.config_saved || 'Saved successfully', '');
            hideOverlay(2000);
        } else if (res.ok && data && data.status === 'partial') {
            reloadHistory();
            let errText = (data.errors || []).join(', ');
            updateOverlayResult('error', currentLangData.manual_save_error || 'Error (Parcial)', errText);
            hideOverlay(4000);
            setTimeout(() => document.getElementById('edit-modal').classList.remove('hidden'), 4000);
        } else {
            let errText = (data && data.errors) ? data.errors.join(', ') : '';
            updateOverlayResult('error', currentLangData.manual_save_error || 'Error saving.', errText);
            hideOverlay(4000);
            setTimeout(() => document.getElementById('edit-modal').classList.remove('hidden'), 4000);
        }
    } catch (e) {
        console.error(e);
        updateOverlayResult('error', currentLangData.network_error || 'Network error.', '');
        hideOverlay(3000);
        setTimeout(() => document.getElementById('edit-modal').classList.remove('hidden'), 3000);
    }
});

// --- TIMELINE NAVIGATOR ---
let currentWeekStart = null;

function getWeekStart(date) {
    let d = new Date(date);
    let day = d.getDay();
    let diff = (day === 0) ? -6 : 1 - day; // Monday = start
    d.setDate(d.getDate() + diff);
    d.setHours(0, 0, 0, 0);
    return d;
}

function generateTimeline(anchorDate) {
    const selector = document.getElementById('day-selector');
    selector.innerHTML = '';

    let anchor = anchorDate || new Date();
    let weekStart = getWeekStart(anchor);
    currentWeekStart = weekStart;

    let today = new Date();
    today.setHours(0, 0, 0, 0);

    // Build a set of dates that have data in historyData
    let datesWithData = new Set();
    historyData.forEach(item => {
        let d = new Date(item.watched_at);
        datesWithData.add(d.toDateString());
    });

    // Generate 7 days starting from Monday - use Intl for locale-aware day/month names
    for (let i = 0; i < 7; i++) {
        let d = new Date(weekStart);
        d.setDate(weekStart.getDate() + i);

        let isToday = d.toDateString() === today.toDateString();

        // Browser locale day and month names
        let weekdayStr = d.toLocaleDateString(navigator.language, { weekday: 'short' }).toUpperCase();
        let monthStr = d.toLocaleDateString(navigator.language, { month: 'short' }).toUpperCase();
        let dateNum = d.getDate();

        let btn = document.createElement('button');
        btn.className = 'day-btn';
        if (isToday) btn.classList.add('active');
        if (datesWithData.has(d.toDateString())) btn.classList.add('has-data');
        btn.dataset.date = d.toDateString();

        btn.innerHTML = `<span class="d-weekday">${weekdayStr}</span><span class="d-month">${monthStr}</span><span class="d-num">${dateNum}</span><div class="d-dot"></div>`;

        btn.addEventListener('click', () => {
            document.querySelectorAll('.day-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            let dayString = d.toLocaleDateString(navigator.language, { day: 'numeric', month: 'long', year: 'numeric' });
            let groupId = 'group-' + dayString.replace(/\s+/g, '-');
            let group = document.getElementById(groupId);
            if (group) {
                group.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });

        selector.appendChild(btn);
    }
}

// Update week shown in timeline as user scrolls
window.addEventListener('scroll', () => {
    // Find which group is currently at the top of the viewport
    let groups = document.querySelectorAll('.history-group');
    let visibleGroup = null;
    groups.forEach(g => {
        let rect = g.getBoundingClientRect();
        if (rect.top <= 150 && rect.bottom > 0) visibleGroup = g;
    });

    if (visibleGroup) {
        // Parse the date from the group title element
        let titleEl = visibleGroup.querySelector('.history-group-title');
        if (titleEl) {
            let rawDate = visibleGroup.dataset.date;
            if (rawDate) {
                let d = new Date(rawDate);
                let weekStart = getWeekStart(d);
                if (!currentWeekStart || weekStart.toDateString() !== currentWeekStart.toDateString()) {
                    generateTimeline(d);
                }
                document.querySelectorAll('.day-btn').forEach(b => {
                    b.classList.toggle('active', b.dataset.date === d.toDateString());
                });
            }
        }
    }
});

// --- CONFIG MODAL & PLEX PIN ---
let plexPinPollingInterval = null;

function setupConfigModal() {
    const fabConfig = document.getElementById('fab-config');
    const configModal = document.getElementById('config-modal');
    const cancelBtn = document.getElementById('config-cancel-btn');
    const saveBtn = document.getElementById('config-save-btn');

    // Dynamic Repeat Password Field
    const pwdInput = document.getElementById('config-password');
    const repeatContainer = document.getElementById('config-repeat-password-container');
    const repeatInput = document.getElementById('config-repeat-password');
    const pwdError = document.getElementById('config-password-error');

    const checkPasswords = () => {
        if (pwdInput.value) {
            repeatContainer.classList.remove('hidden');
            if (repeatInput.value && pwdInput.value !== repeatInput.value) {
                pwdError.classList.remove('hidden');
                saveBtn.disabled = true;
            } else {
                pwdError.classList.add('hidden');
                saveBtn.disabled = false;
            }
        } else {
            repeatContainer.classList.add('hidden');
            pwdError.classList.add('hidden');
            saveBtn.disabled = false;
        }
    };

    pwdInput.addEventListener('input', checkPasswords);
    repeatInput.addEventListener('input', checkPasswords);

    // Open Config Modal
    if (fabConfig) {
        fabConfig.addEventListener('click', async () => {
            document.querySelector('.fab-container').classList.remove('active');
            configModal.classList.remove('hidden');

            try {
                let res = await apiFetch('/api/config');
                if (res.ok) {
                    let data = await res.json();
                    document.getElementById('config-plex-url').value = data.plex_url || '';
                    document.getElementById('config-plex-token').value = data.plex_token || '';
                    document.getElementById('config-tmdb-api').value = data.tmdb_api_key || '';
                    document.getElementById('config-plex-client-id').value = data.plex_client_id || '';
                    document.getElementById('config-debug').checked = !!data.debug_mode;

                    let langValue = data.sync_language || 'es';
                    let langDd = document.getElementById('configLang-dd');
                    langDd.dataset.currentValue = langValue;
                    langDd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
                    let selectedItem = langDd.querySelector(`.c-dropdown-item[data-value="${langValue}"]`);
                    if (selectedItem) {
                        selectedItem.classList.add('selected');
                        let valEl = langDd.querySelector('.c-dropdown-value');
                        valEl.textContent = selectedItem.textContent;
                        valEl.setAttribute('data-i18n', selectedItem.getAttribute('data-i18n'));
                    }
                    
                    let dashLangValue = data.dashboard_language || 'auto';
                    let dashLangDd = document.getElementById('configDashboardLang-dd');
                    dashLangDd.dataset.currentValue = dashLangValue;
                    dashLangDd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
                    let dashSelectedItem = dashLangDd.querySelector(`.c-dropdown-item[data-value="${dashLangValue}"]`);
                    if (dashSelectedItem) {
                        dashSelectedItem.classList.add('selected');
                        let dashValEl = dashLangDd.querySelector('.c-dropdown-value');
                        dashValEl.textContent = dashSelectedItem.textContent;
                        dashValEl.setAttribute('data-i18n', dashSelectedItem.getAttribute('data-i18n'));
                    }
                    configModal.dataset.originalLang = langValue;

                    // Resetear estado del formulario
                    pwdInput.value = '';
                    repeatInput.value = '';
                    repeatContainer.classList.add('hidden');
                    pwdError.classList.add('hidden');
                    saveBtn.disabled = false;
                }
            } catch (e) { console.error(e); }
        });
    }

    // Cancel Config
    cancelBtn.addEventListener('click', () => {
        configModal.classList.add('hidden');
        if (plexPinPollingInterval) clearInterval(plexPinPollingInterval);
        document.getElementById('config-pin-status').classList.add('hidden');
    });

    // Restore Config
    const restoreBtn = document.getElementById('config-restore-btn');
    if (restoreBtn) {
        restoreBtn.addEventListener('click', async () => {
            if (!confirm(currentLangData.config_restore_confirm || 'Are you sure you want to restore the original configuration? Unsaved changes will be lost.')) return;

            showProcessingOverlay(currentLangData.config_restoring || 'Restoring', currentLangData.config_restoring_sub || 'Loading backup...');
            try {
                let res = await apiFetch('/api/config/restore', { method: 'POST' });
                if (res.ok) {
                    let data = await res.json();
                    if (data.status === 'success') {
                        updateOverlayResult('success', currentLangData.config_restore_success || 'Restored successfully.');
                        setTimeout(() => window.location.reload(), 1500);
                    } else {
                        updateOverlayResult('error', data.message || currentLangData.config_restore_error || 'Error restoring');
                        hideOverlay(3000);
                    }
                } else {
                    updateOverlayResult('error', currentLangData.network_error || 'Network error');
                    hideOverlay(3000);
                }
            } catch (e) {
                console.error(e);
                updateOverlayResult('error', currentLangData.critical_error || 'Critical error');
                hideOverlay(3000);
            }
        });
    }

    // Plex Auth Flow
    document.getElementById('config-get-token-btn').addEventListener('click', async () => {
        const statusEl = document.getElementById('config-pin-status');
        statusEl.classList.remove('hidden');
        statusEl.textContent = currentLangData.config_pin_getting || 'Getting PIN...';

        try {
            const formData = new URLSearchParams();
            formData.append("strong", "true");
            formData.append("X-Plex-Product", "SyncPK");
            formData.append("X-Plex-Client-Identifier", "SyncPK-Server-App");

            const pinRes = await fetch("https://plex.tv/api/v2/pins", {
                method: "POST",
                headers: { "Accept": "application/json" },
                body: formData
            });
            const pinData = await pinRes.json();
            const pinId = pinData.id;
            const pinCode = pinData.code;

            const authAppUrl = `https://app.plex.tv/auth#?clientID=SyncPK-Server-App&code=${pinCode}&context%5Bdevice%5D%5Bproduct%5D=SyncPK`;
            window.open(authAppUrl, '_blank');

            statusEl.textContent = currentLangData.config_pin_login || 'Please log in on the new Plex tab...';

            if (plexPinPollingInterval) clearInterval(plexPinPollingInterval);
            plexPinPollingInterval = setInterval(async () => {
                const checkRes = await fetch(`https://plex.tv/api/v2/pins/${pinId}?X-Plex-Client-Identifier=SyncPK-Server-App`, {
                    headers: { "Accept": "application/json" }
                });
                const checkData = await checkRes.json();
                if (checkData.authToken) {
                    clearInterval(plexPinPollingInterval);
                    document.getElementById('config-plex-token').value = checkData.authToken;
                    statusEl.textContent = currentLangData.config_pin_success || 'Token obtained successfully!';
                    setTimeout(() => statusEl.classList.add('hidden'), 3000);
                }
            }, 2000);

        } catch (e) {
            statusEl.textContent = currentLangData.config_pin_error || 'Error getting Plex PIN.';
            console.error(e);
        }
    });

    // Save Config
    saveBtn.addEventListener('click', async () => {
        const origLang = configModal.dataset.originalLang;
        const newLang = document.getElementById('configLang-dd').dataset.currentValue || 'es';
        const newDashLang = document.getElementById('configDashboardLang-dd').dataset.currentValue || 'auto';
        const pwd = pwdInput.value;

        const payload = {
            plex_url: document.getElementById('config-plex-url').value,
            plex_token: document.getElementById('config-plex-token').value,
            tmdb_api_key: document.getElementById('config-tmdb-api').value,
            plex_client_id: document.getElementById('config-plex-client-id').value,
            debug_mode: document.getElementById('config-debug').checked,
            sync_language: newLang,
            dashboard_language: newDashLang
        };

        if (pwd) payload.master_password = pwd;

        if (newLang !== origLang) {
            let rescanConfirmMsg = currentLangData.config_rescan_confirm || 'You have changed the language. Do you want to rescan your ENTIRE library (Titles and Posters) to apply the new language? (This may take a few minutes)';
            if (!confirm(rescanConfirmMsg)) {
                return; // Wait or just save without rescan? Let's just save. Actually, if they say NO, maybe just save. Let's do a custom modal or just native confirm.
            } else {
                payload.force_rescan = true;
            }
        }

        configModal.classList.add('hidden');
        showProcessingOverlay(currentLangData.overlay_processing || 'Processing request', currentLangData.overlay_wait || 'Please wait...');

        try {
            let res = await apiFetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (res.ok) {
                const data = await res.json();
                if (data.status === 'success') {
                    let successMsg = currentLangData.config_saved || 'Settings saved.';
                    if (data.has_plex_pass === true) {
                        successMsg += '\\n' + (currentLangData.config_pass_found || 'Plex Pass detected! Webhooks enabled.');
                    } else if (data.has_plex_pass === false) {
                        successMsg += '\\n' + (currentLangData.config_pass_not_found || 'No Plex Pass detected.');
                    }
                    
                    if (data.rescan_started) {
                        updateOverlayResult('success', currentLangData.config_saved_rescan || 'Settings saved. Rescan started in background.');
                    } else {
                        updateOverlayResult('success', successMsg);
                    }
                    hideOverlay(3000);
                } else {
                    updateOverlayResult('error', currentLangData.config_save_error || 'Error saving settings.');
                    hideOverlay(3000);
                    setTimeout(() => configModal.classList.remove('hidden'), 3000);
                }
            } else {
                updateOverlayResult('error', currentLangData.network_error || 'Network error.');
                hideOverlay(3000);
                setTimeout(() => configModal.classList.remove('hidden'), 3000);
            }
        } catch (e) {
            console.error(e);
            updateOverlayResult('error', currentLangData.network_error || 'Network error.');
            hideOverlay(3000);
            setTimeout(() => configModal.classList.remove('hidden'), 3000);
        }
    });
}
document.addEventListener('DOMContentLoaded', setupConfigModal);

// --- FLOATING ACTION BUTTON & MANUAL ADD ---
document.addEventListener('DOMContentLoaded', () => {
    const fabContainer = document.querySelector('.fab-container');
    const fabMain = document.querySelector('.fab-main');
    const fabAddManual = document.getElementById('fab-add-manual');
    const fabLogs = document.getElementById('fab-logs');
    const manualModal = document.getElementById('manual-modal');
    const cancelManualBtn = document.getElementById('manual-cancel-btn');
    const saveManualBtn = document.getElementById('manual-save-btn');

    // Toggle FAB Speed Dial
    if (fabMain) {
        fabMain.addEventListener('click', () => {
            fabContainer.classList.toggle('active');
        });
    }

    if (fabLogs) {
        fabLogs.addEventListener('click', () => {
            fabContainer.classList.remove('active');
            openLogViewer();
        });
    }

    // Open Manual Modal
    if (fabAddManual) {
        fabAddManual.addEventListener('click', () => {
            fabContainer.classList.remove('active');
            manualModal.classList.remove('hidden');
            resetManualForm();
        });
    }

    // Close Modal
    if (cancelManualBtn) {
        cancelManualBtn.addEventListener('click', () => {
            manualModal.classList.add('hidden');
        });
    }

    // TMDB Search Logic
    const searchInput = document.getElementById('manual-search-input');
    const searchResults = document.getElementById('manual-search-results');
    const selectedItem = document.getElementById('manual-selected-item');
    const episodeFields = document.getElementById('manual-episode-fields');
    let searchTimeout;

    if (searchInput) {
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                searchResults.classList.add('hidden');
            }
        });

        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            const query = e.target.value.trim();

            if (query.length < 3) {
                searchResults.classList.add('hidden');
                return;
            }

            searchTimeout = setTimeout(async () => {
                try {
                    const lang = navigator.language.split('-')[0] || 'en';
                    const res = await apiFetch(`/api/tmdb/search?q=${encodeURIComponent(query)}&lang=${lang}`);
                    if (res.ok) {
                        const data = await res.json();
                        renderTMDBResults(data.results || []);
                    }
                } catch (err) {
                    console.error("Error searching TMDB", err);
                }
            }, 500);
        });
    }

    function renderTMDBResults(results) {
        searchResults.innerHTML = '';
        if (results.length === 0) {
            searchResults.innerHTML = `<div style="padding:10px; color:#aaa;">${currentLangData.no_results || 'No results found'}</div>`;
        } else {
            results.forEach(item => {
                const div = document.createElement('div');
                div.className = 'tmdb-search-item';

                const title = item.title || item.name;
                const year = (item.release_date || item.first_air_date || "").split('-')[0];
                const poster = item.poster_path ? `https://image.tmdb.org/t/p/w92${item.poster_path}` : '';
                const type = item.media_type === 'tv' ? (currentLangData.type_series || 'Series') : (currentLangData.type_movie || 'Movie');

                div.innerHTML = `
                    <img src="${poster}" alt="poster">
                    <div>
                        <div style="font-weight:bold; color:#fff;">${title} <span style="color:#aaa; font-size:0.8rem; font-weight:normal;">(${year})</span></div>
                        <div style="color:var(--accent); font-size:0.8rem;">${type}</div>
                    </div>
                `;

                div.addEventListener('click', () => selectTMDBItem(item, title, year, poster));
                searchResults.appendChild(div);
            });
        }
        searchResults.classList.remove('hidden');
    }

    function selectTMDBItem(item, title, year, poster) {
        searchResults.classList.add('hidden');
        searchInput.value = '';

        document.getElementById('manual-title').textContent = title;
        document.getElementById('manual-year').textContent = year;
        document.getElementById('manual-poster').src = poster;
        document.getElementById('manual-tmdb-id').value = item.id;
        document.getElementById('manual-media-type').value = item.media_type === 'tv' ? 'episode' : 'movie';

        selectedItem.classList.remove('hidden');

        if (item.media_type === 'tv') {
            episodeFields.classList.remove('hidden');
        } else {
            episodeFields.classList.add('hidden');
        }

        checkManualForm();
    }

    document.getElementById('manual-date').addEventListener('change', checkManualForm);

    function checkManualForm() {
        const tmdbId = document.getElementById('manual-tmdb-id').value;
        const date = document.getElementById('manual-date').value;
        if (tmdbId && date) {
            saveManualBtn.classList.remove('btn-disabled');
        } else {
            saveManualBtn.classList.add('btn-disabled');
        }
    }

    function resetManualForm() {
        searchInput.value = '';
        searchResults.classList.add('hidden');
        selectedItem.classList.add('hidden');
        episodeFields.classList.add('hidden');
        document.getElementById('manual-tmdb-id').value = '';
        document.getElementById('manual-date').value = '';
        saveManualBtn.classList.add('btn-disabled');
        saveManualBtn.disabled = false; // allow click for error message
    }

    // Submit Manual Form
    if (saveManualBtn) {
        saveManualBtn.addEventListener('click', async () => {
            // Explicit validation before sending
            const tmdbIdCheck = document.getElementById('manual-tmdb-id').value;
            const dateCheck = document.getElementById('manual-date').value;
            if (!tmdbIdCheck || !dateCheck) {
                showToast(currentLangData.manual_missing_fields || 'Please select a title and a date before saving.', 'info');
                return;
            }

            manualModal.classList.add('hidden');
            showProcessingOverlay(currentLangData.overlay_processing || 'Processing request', currentLangData.overlay_wait || 'Please wait...');

            const payload = {
                tmdb_id: document.getElementById('manual-tmdb-id').value,
                media_type: document.getElementById('manual-media-type').value,
                title: document.getElementById('manual-title').textContent,
                watched_at: new Date(document.getElementById('manual-date').value).toISOString(),
                sync_remote: document.getElementById('manual-sync-plex').checked
            };

            if (payload.media_type === 'episode') {
                payload.season = parseInt(document.getElementById('manual-season').value) || 1;
                payload.episode = parseInt(document.getElementById('manual-episode').value) || 1;
            }

            const sendManualPayload = async (payload) => {
                try {
                    const res = await apiFetch('/api/manual_add', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });

                    if (res.ok) {
                        const data = await res.json();
                        if (data.status === 'confirm_rewatch') {
                            hideOverlay(0);
                            let rewMsg = currentLangData.manual_rewatch_confirm || 'You have watched this before (last time: {date}). Do you want to log a NEW watch (re-watch)?';
                            rewMsg = rewMsg.replace('{date}', new Date(data.last_watched).toLocaleDateString());
                            const confirmed = window.confirm(rewMsg);
                            if (confirmed) {
                                payload.force_rewatch = true;
                                showProcessingOverlay(currentLangData.overlay_processing || 'Processing request', currentLangData.overlay_wait || 'Please wait...');
                                await sendManualPayload(payload);
                            } else {
                                manualModal.classList.remove('hidden');
                            }
                        } else if (data.status === 'duplicate') {
                            updateOverlayResult('error', currentLangData.manual_duplicate || 'This title is already in your watch history.', '');
                            hideOverlay(3000);
                            setTimeout(() => manualModal.classList.remove('hidden'), 3000);
                        } else if (data.status === 'success') {
                            reloadHistory();
                            updateOverlayResult('success', currentLangData.manual_saved || 'Entry saved successfully.', '');
                            hideOverlay(2000);
                        } else {
                            updateOverlayResult('error', currentLangData.manual_save_error || 'Error saving the manual record.', '');
                            hideOverlay(3000);
                            setTimeout(() => manualModal.classList.remove('hidden'), 3000);
                        }
                    } else {
                        updateOverlayResult('error', currentLangData.manual_save_error || 'Error saving the manual record.', '');
                        hideOverlay(3000);
                        setTimeout(() => manualModal.classList.remove('hidden'), 3000);
                    }
                } catch (err) {
                    console.error(err);
                    updateOverlayResult('error', currentLangData.network_error || 'Network error. Please try again.', '');
                    hideOverlay(3000);
                    setTimeout(() => manualModal.classList.remove('hidden'), 3000);
                }
            };
            
            await sendManualPayload(payload);
            checkManualForm();
        });
    }
});
