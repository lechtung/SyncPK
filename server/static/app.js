// v10

let currentLangData = {};
let historyData = [];
let offset = 0;
let limit = 20;
let hasMore = true;
let isSyncing = false;
let isLoading = false;
let currentFilters = { type: 'all', year: 'all', month: 'all', search: '' };
let statsCache = { movies: 0, moviesHours: 0, episodes: 0, episodesHours: 0 };
let logInterval = null;
let activeLang = navigator.language;


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

// --- GENERIC CONFIRM MODAL ---
window.customConfirm = function (message) {
    return new Promise((resolve) => {
        const modal = document.getElementById('generic-confirm-modal');
        const msgEl = document.getElementById('generic-confirm-message');
        const btnYes = document.getElementById('generic-confirm-yes');
        const btnNo = document.getElementById('generic-confirm-no');

        msgEl.textContent = message;
        modal.classList.remove('hidden');

        const handleYes = () => { cleanup(); resolve(true); };
        const handleNo = () => { cleanup(); resolve(false); };

        const cleanup = () => {
            modal.classList.add('hidden');
            btnYes.removeEventListener('click', handleYes);
            btnNo.removeEventListener('click', handleNo);
        };

        btnYes.addEventListener('click', handleYes);
        btnNo.addEventListener('click', handleNo);
    });
}

// Alert-style dialog: reuses the confirm modal but hides the No button
window.customAlert = function (message) {
    return new Promise((resolve) => {
        const modal = document.getElementById('generic-confirm-modal');
        const msgEl = document.getElementById('generic-confirm-message');
        const btnYes = document.getElementById('generic-confirm-yes');
        const btnNo = document.getElementById('generic-confirm-no');

        msgEl.textContent = message;
        btnNo.style.display = 'none';
        modal.classList.remove('hidden');

        const handleOk = () => {
            modal.classList.add('hidden');
            btnNo.style.display = '';
            btnYes.removeEventListener('click', handleOk);
            resolve();
        };

        btnYes.addEventListener('click', handleOk);
    });
}

// --- FULLSCREEN OVERLAY SYSTEM ---
function showProcessingOverlay(title, subtitle) {
    const overlay = document.getElementById('loading-overlay');
    if (!overlay) return;
    document.getElementById('overlay-title').textContent = title || currentLangData.overlay_processing || 'Processing request';
    document.getElementById('overlay-subtitle').textContent = subtitle || currentLangData.overlay_wait || 'Please wait...';

    const icon = document.getElementById('overlay-icon');
    // Restore initial spinner
    const iconContainer = document.getElementById('loading-overlay-icon');
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

    // Allow closing on click only if it's an error
    overlay.onclick = function () {
        if (type === 'error') {
            overlay.classList.add('hidden');
        }
    };
    if (type !== 'error') {
        overlay.onclick = null;
    }

    const iconContainer = document.getElementById('loading-overlay-icon');
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
    return ""; // Token is now securely handled via HttpOnly cookies
}

function escapeHTML(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
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

    // Check for updates
    // Check if session is valid via HttpOnly cookie
    try {
        const res = await apiFetch('/api/config');
        if (res.ok) {
            showDashboard();
        } else {
            document.getElementById('login-overlay').classList.remove('hidden');
        }
    } catch (e) {
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
            if (data.dashboard_language) {
                window.DASHBOARD_LANG = data.dashboard_language;
            }
            if (data.ui_poster_w) document.documentElement.style.setProperty('--poster-w', data.ui_poster_w + 'px');
            if (data.ui_poster_h) document.documentElement.style.setProperty('--poster-h', data.ui_poster_h + 'px');
            if (data.ui_fanart_w) document.documentElement.style.setProperty('--fanart-w', data.ui_fanart_w + 'px');
            if (data.ui_fanart_h) document.documentElement.style.setProperty('--fanart-h', data.ui_fanart_h + 'px');
            if (data.ui_grid_gap) document.documentElement.style.setProperty('--grid-gap', data.ui_grid_gap + 'px');
            if (data.ui_card_radius) document.documentElement.style.setProperty('--card-radius', data.ui_card_radius + 'px');
            if (data.ui_bg_color) document.documentElement.style.setProperty('--bg-color', data.ui_bg_color);
            if (data.ui_glass_bg) document.documentElement.style.setProperty('--glass-bg', data.ui_glass_bg);
            if (data.ui_glass_border) document.documentElement.style.setProperty('--glass-border', data.ui_glass_border);
            if (data.ui_edit_bg) document.documentElement.style.setProperty('--edit-bg', data.ui_edit_bg);
            if (data.ui_combo_bg) document.documentElement.style.setProperty('--combo-bg', data.ui_combo_bg);
            if (data.ui_panel_bg) document.documentElement.style.setProperty('--panel-bg', data.ui_panel_bg);
            if (data.ui_accent_primary) document.documentElement.style.setProperty('--accent', data.ui_accent_primary);
            if (data.ui_accent_hover) document.documentElement.style.setProperty('--accent-hover', data.ui_accent_hover);
            if (data.ui_accent_dark) document.documentElement.style.setProperty('--accent-dark', data.ui_accent_dark);
            if (data.ui_danger) document.documentElement.style.setProperty('--danger', data.ui_danger);
            if (data.ui_text_primary) document.documentElement.style.setProperty('--text-primary', data.ui_text_primary);
            if (data.ui_text_secondary) document.documentElement.style.setProperty('--text-secondary', data.ui_text_secondary);
            if (data.ui_glass_blur) document.documentElement.style.setProperty('--glass-blur', `blur(${data.ui_glass_blur}px)`);
            if (data.ui_show_duration !== undefined) window.UI_SHOW_DURATION = data.ui_show_duration;
            if (data.ui_show_watch_time !== undefined) window.UI_SHOW_WATCH_TIME = data.ui_show_watch_time;
            if (data.ui_show_title !== undefined) window.UI_SHOW_TITLE = data.ui_show_title;
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
    activeLang = lang;
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
            ['edit-modal', 'manual-modal', 'confirm-modal', 'config-modal', 'log-modal', 'update-modal', 'about-modal'].forEach(id => {
                const modal = document.getElementById(id);
                if (modal && !modal.classList.contains('hidden')) {
                    if (id === 'update-modal') {
                        dismissUpdate();
                    } else {
                        modal.classList.add('hidden');
                    }
                }
            });
        }
    });

    let updateDismissBtn = document.getElementById('update-dismiss-btn');
    if (updateDismissBtn) updateDismissBtn.addEventListener('click', dismissUpdate);

    let updateNowBtn = document.getElementById('update-now-btn');
    if (updateNowBtn) updateNowBtn.addEventListener('click', triggerUpdate);
}

function openLogViewer() {
    document.getElementById('log-modal').classList.remove('hidden');
    if (logInterval) clearInterval(logInterval);
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

async function checkUpdates() {
    try {
        let res = await apiFetch('/api/update/status');
        if (res.ok) {
            let data = await res.json();
            if (data.has_update) {
                let subtitle = currentLangData.update_subtitle || 'Version [VERSION] is available. Do you want to install it now?';
                subtitle = subtitle.replace('[VERSION]', data.version);

                if (data.is_docker) {
                    let dockerMsg = currentLangData.update_docker_instructions || 'To update this Docker instance, open your terminal and run: docker compose pull && docker compose up -d';
                    subtitle = `(v${data.version}) ` + dockerMsg;
                    document.getElementById('update-now-btn').style.display = 'none';
                } else {
                    document.getElementById('update-now-btn').style.display = 'inline-block';
                }

                document.getElementById('update-subtitle').textContent = subtitle;

                // Store version globally for ignoring
                window.currentUpdateVersion = data.version;
                document.getElementById('update-modal').classList.remove('hidden');
            }
        }
    } catch (e) {
        console.warn("Could not check updates", e);
    }
}

async function dismissUpdate() {
    document.getElementById('update-modal').classList.add('hidden');

    let ignoreVersion = document.getElementById('updateIgnoreVersion').checked;
    let neverNotify = document.getElementById('updateNeverNotify').checked;

    try {
        await apiFetch('/api/update/ignore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ignore_version: ignoreVersion ? window.currentUpdateVersion : "",
                never_notify: neverNotify
            })
        });
    } catch (e) {
        console.warn("Could not save ignore update preferences", e);
    }
}

async function triggerUpdate() {
    document.getElementById('update-modal').classList.add('hidden');
    showProcessingOverlay(
        currentLangData.updating_title || 'Updating System',
        currentLangData.updating_subtitle || 'Please wait a few minutes while the system updates and restarts...'
    );

    try {
        await apiFetch('/api/update/trigger', { method: 'POST' });
        // The server will restart, we can just reload after a few seconds
        setTimeout(() => {
            window.location.reload();
        }, 8000);
    } catch (e) {
        updateOverlayResult('error', currentLangData.error_state_msg || 'Error', '');
        hideOverlay();
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
        // Click trigger also toggles
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            let wasOpen = dd.classList.contains('open');
            document.querySelectorAll('.c-dropdown').forEach(other => {
                other.classList.remove('open');
                let modal = other.closest('.login-box');
                if (modal) modal.style.paddingBottom = '';
            });
            if (!wasOpen) {
                dd.classList.add('open');
                let menu = dd.querySelector('.c-dropdown-menu');
                let modal = dd.closest('.login-box');
                if (modal && menu) {
                    // Small delay to allow DOM to render the menu and get height
                    setTimeout(() => {
                        let rect = menu.getBoundingClientRect();
                        let modalRect = modal.getBoundingClientRect();
                        if (rect.bottom > modalRect.bottom) {
                            let diff = rect.bottom - modalRect.bottom;
                            // Add diff + some margin to the base padding (32px)
                            modal.style.paddingBottom = (32 + diff + 10) + 'px';
                        }
                    }, 10);
                }
            }
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
                let modal = dd.closest('.login-box');
                if (modal) modal.style.paddingBottom = '';

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

    // Close dropdowns when clicking anywhere outside
    document.addEventListener('click', (e) => {
        document.querySelectorAll('.c-dropdown.open').forEach(dd => {
            dd.classList.remove('open');
            let modal = dd.closest('.login-box');
            if (modal) modal.style.paddingBottom = '';
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
        let monthName = new Date(2000, i, 1).toLocaleDateString(activeLang, { month: 'long' });
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
    await loadStats(); // Await so that isSyncing updates before loading the history
    await loadMoreHistory();
}

async function loadMoreHistory() {
    if (isLoading || !hasMore) return;
    isLoading = true;
    document.getElementById('loading-spinner').classList.remove('hidden');

    try {
        let url = `/api/history?limit=${limit}&offset=${offset}&type=${currentFilters.type}&year=${currentFilters.year}&month=${currentFilters.month}&search=${encodeURIComponent(currentFilters.search)}&_cb=${Date.now()}`;
        let res = await apiFetch(url);
        if (res.status === 401) { logout(); return; }
        if (!res.ok) throw new Error("API returned status " + res.status);

        let data = await res.json();
        let newItems = data.items || [];

        // We block scrolling temporarily or permanently if we reach the current end.
        if (newItems.length < limit) {
            if (!isSyncing) {
                // Permanently blocked (until a websocket releases it if something new comes in)
                hasMore = false;
            } else {
                // During database import (state 1), the database grows by the second.
                // If we permanently lock it, we'll be stuck halfway through.
                // But if we don't lock it, the scroll bar vibrates at 50Hz against the background.
                // Solution: Lock it for 2 seconds and then allow scrolling again.
                hasMore = false;
                setTimeout(() => { hasMore = true; }, 2000);
            }
        }

        historyData = historyData.concat(newItems);
        offset += newItems.length;

        if (historyData.length === 0) {
            let emptyMsg = currentLangData.empty_state_msg || 'Run to the TV and put on a good movie!';
            document.getElementById('history-feed').innerHTML = `<div style="text-align:center; padding:50px; color:#fff; font-size:2rem; font-weight:bold;">${emptyMsg}</div>`;
        } else {
            await renderHistory(newItems);
            generateTimeline(); // Refresh dots after new data

            // Dynamically update the year combo box during the initial import
            if (isSyncing) {
                let menu = document.getElementById('dd-year-menu');
                let dd = document.getElementById('dd-year');
                if (menu && dd) {
                    let existingYears = Array.from(menu.querySelectorAll('.c-dropdown-item')).map(el => el.dataset.value);
                    newItems.forEach(item => {
                        if (item.watched_at) {
                            let y = new Date(item.watched_at).getFullYear().toString();
                            if (!existingYears.includes(y)) {
                                existingYears.push(y);
                                let div = document.createElement('div');
                                div.className = 'c-dropdown-item';
                                div.dataset.value = y;
                                div.textContent = y;
                                div.addEventListener('click', (e) => {
                                    e.stopPropagation();
                                    dd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
                                    div.classList.add('selected');
                                    let valEl = dd.querySelector('.c-dropdown-value');
                                    if (valEl) valEl.textContent = div.textContent;
                                    dd.classList.remove('open');
                                    currentFilters.year = y;
                                    reloadHistory();
                                });
                                menu.appendChild(div);
                            }
                        }
                    });
                }
            }
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


        }
    } catch (e) { console.error(e); }
}

function logout() {
    // Session is handled via HttpOnly cookie. True logout would require a backend call.
    // For now, we clear the UI.
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
        let dayString = dateObj.toLocaleDateString(activeLang, { day: 'numeric', month: 'long', year: 'numeric' });
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

            let pUrl = item.poster_path ? (item.poster_path.startsWith('http') || item.poster_path.startsWith('/') ? item.poster_path : `/cache/posters/${item.poster_path}`) : null;
            let fUrl = item.fanart_path ? (item.fanart_path.startsWith('http') || item.fanart_path.startsWith('/') ? item.fanart_path : `/cache/fanarts/${item.fanart_path}`) : null;
            let posterStyle = pUrl ? `background-image: url('${pUrl}')` : 'background-color: #333';
            let bgStyle = fUrl ? `background-image: url('${fUrl}')` : 'background-color: #111';

            let timeStr = new Date(item.watched_at).toLocaleTimeString(navigator.language, { hour: '2-digit', minute: '2-digit' });
            let timeHtml = "";
            if (window.UI_SHOW_WATCH_TIME !== false) {
                timeHtml = `<div class="card-time">${timeStr}</div>`;
            }

            let title = item.media_type === 'movie' ? item.title : item.show_title;
            let subtitle = "";
            if (item.media_type !== 'movie') {
                subtitle = `T${item.season} · E${item.episode}`;
                if (window.UI_SHOW_TITLE !== false) {
                    subtitle += ` - ${item.title}`;
                }
            }

            let durationHtml = "";
            if (window.UI_SHOW_DURATION !== false && item.duration) {
                let h = Math.floor(item.duration / 60);
                let m = item.duration % 60;
                let dStr = (h > 0 ? `${h}h ` : "") + `${m}m`;
                durationHtml = `<div class="card-duration">${dStr}</div>`;
            }

            card.innerHTML = `
                <div class="c-poster" style="${posterStyle}"></div>
                <div class="c-fanart-content" style="${bgStyle}">
                    <div class="c-fanart-mask"></div>
                    <div class="c-fanart-overlay"></div>
                    <div class="card-content">
                        <div class="card-title">${escapeHTML(title)}</div>
                        <div class="card-subtitle">${escapeHTML(subtitle)}</div>
                    </div>
                    ${timeHtml}
                    ${durationHtml}
                    <button class="kebab-menu-btn">⋮</button>
                    <div class="kebab-dropdown glass-panel" id="dropdown-${item.id}">
                        <div class="dropdown-item danger btn-del" data-i18n="action_delete">${currentLangData.action_delete || 'Delete'}</div>
                        <div class="dropdown-item btn-edit" data-i18n="action_edit">${currentLangData.action_edit || 'Edit'}</div>
                    </div>
                </div>
            `;

            // Event Listeners instead of inline onclicks
            card.querySelector('.kebab-menu-btn').addEventListener('click', (e) => toggleDropdown(item.id, e));
            card.querySelector('.btn-del').addEventListener('click', () => deleteItem(item.id, item.media_type));
            card.querySelector('.btn-edit').addEventListener('click', () => openEditModal(item.id, item.watched_at, item.media_type));

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

window.deleteItem = function (id, mediaType) {
    let modal = document.getElementById('confirm-modal');
    modal.classList.remove('hidden');

    let scopeSelect = document.getElementById('deleteScope-dd');
    let scopeValEl = document.getElementById('deleteScope-val');
    let scopeTitle = document.getElementById('deleteScope-title');

    // Reset selection to default (episode)
    if (scopeSelect) {
        scopeSelect.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
        let defaultItem = scopeSelect.querySelector('.c-dropdown-item[data-value="episode"]');
        if (defaultItem) defaultItem.classList.add('selected');
        scopeSelect.dataset.currentValue = 'episode';

        if (mediaType === 'episode') {
            scopeSelect.classList.remove('hidden');
            if (scopeTitle) scopeTitle.classList.remove('hidden');
            if (currentLangData.edit_scope_episode) scopeValEl.textContent = currentLangData.edit_scope_episode;
        } else {
            scopeSelect.classList.add('hidden');
            if (scopeTitle) scopeTitle.classList.add('hidden');
        }
    }

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
            let scopeVal = scopeSelect ? (scopeSelect.dataset.currentValue || 'episode') : 'episode';

            showProcessingOverlay(
                currentLangData.overlay_processing || 'Processing request',
                currentLangData.overlay_wait || 'Please wait...'
            );

            let res = await apiFetch(`/api/history/${id}?sync_remote=${syncRemote}&scope=${scopeVal}`, { method: 'DELETE' });
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

    // Use new Bulk Progress modal
    document.getElementById('edit-modal').classList.add('hidden');
    document.getElementById('bulk-progress-modal').classList.remove('hidden');
    document.getElementById('bulk-progress-title').textContent = currentLangData.overlay_processing || 'Calculating...';
    document.getElementById('bulk-progress-bar').style.width = "0%";
    document.getElementById('bulk-progress-bar').classList.add('indeterminate');
    document.getElementById('bulk-progress-warning').classList.add('hidden');
    document.getElementById('bulk-progress-current').textContent = "0";
    document.getElementById('bulk-progress-total').textContent = "0";

    try {
        let res = await apiFetch(`/api/history/${currentEditId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        let data = null;
        try { data = await res.json(); } catch (e) { }

        if (res.ok && data && data.status === 'success') {
            // Background task started, WebSockets will take over
        } else {
            document.getElementById('bulk-progress-modal').classList.add('hidden');
            let errText = (data && data.errors) ? data.errors.join(', ') : '';
            showToast(errText || currentLangData.manual_save_error || 'Error saving.', 'error');
            document.getElementById('edit-modal').classList.remove('hidden');
        }
    } catch (e) {
        console.error(e);
        document.getElementById('bulk-progress-modal').classList.add('hidden');
        showToast(currentLangData.network_error || 'Network error.', 'error');
        document.getElementById('edit-modal').classList.remove('hidden');
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
        let weekdayStr = d.toLocaleDateString(activeLang, { weekday: 'short' }).toUpperCase();
        let monthStr = d.toLocaleDateString(activeLang, { month: 'short' }).toUpperCase();
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

            let dayString = d.toLocaleDateString(activeLang, { day: 'numeric', month: 'long', year: 'numeric' });
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
    const mobileSettingsBtn = document.getElementById('settings-menu-btn');

    const openConfigHandler = async () => {
        document.querySelector('.fab-container').classList.remove('active');
        document.getElementById('header-right').classList.remove('open');
        configModal.classList.remove('hidden');

        try {
            let res = await apiFetch('/api/config');
            if (res.ok) {
                let data = await res.json();
                document.getElementById('config-plex-url').value = data.plex_url || '';
                document.getElementById('config-plex-token').value = data.plex_token || '';
                document.getElementById('config-tmdb-api').value = data.tmdb_api_key || '';
                document.getElementById('config-plex-client-id').value = data.plex_client_id || '';

                if (data.api_token_raw) {
                    document.getElementById('webhooks-toggle-container').style.display = 'block';
                    const host = window.location.host;
                    document.getElementById('webhook-url-plex').value = `http://${host}/webhook/plex?token=${data.api_token_raw}`;
                    document.getElementById('webhook-url-kodi').value = `http://${host}/webhook/kodi?token=${data.api_token_raw}`;
                } else {
                    document.getElementById('webhooks-toggle-container').style.display = 'none';
                }

                document.getElementById('config-debug').checked = !!data.debug_mode;
                document.getElementById('config-auto-update').checked = !!data.auto_update;
                document.getElementById('config-notify-updates').checked = !!data.notify_updates;

                let pqDd = document.getElementById('configPosterQuality-dd'); if (pqDd) { let val = data.poster_quality || 'w185'; pqDd.dataset.currentValue = val; pqDd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected')); let match = Array.from(pqDd.querySelectorAll('.c-dropdown-item')).find(i => i.dataset.value === val); if (match) { match.classList.add('selected'); document.getElementById('configPosterQuality-val').innerText = match.innerText; } }
                let fqDd = document.getElementById('configFanartQuality-dd'); if (fqDd) { let val = data.fanart_quality || 'w300'; fqDd.dataset.currentValue = val; fqDd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected')); let match = Array.from(fqDd.querySelectorAll('.c-dropdown-item')).find(i => i.dataset.value === val); if (match) { match.classList.add('selected'); document.getElementById('configFanartQuality-val').innerText = match.innerText; } }
                let posterDd = document.getElementById('configPosterPref-dd');
                if (posterDd) {
                    let val = data.poster_pref || 'show';
                    posterDd.dataset.currentValue = val;
                    posterDd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
                    let item = posterDd.querySelector(`.c-dropdown-item[data-value="${val}"]`);
                    if (item) {
                        item.classList.add('selected');
                        posterDd.querySelector('.c-dropdown-value').textContent = item.textContent;
                    }
                }

                let fanartDd = document.getElementById('configFanartPref-dd');
                if (fanartDd) {
                    let val = data.fanart_pref || 'episode';
                    fanartDd.dataset.currentValue = val;
                    fanartDd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
                    let item = fanartDd.querySelector(`.c-dropdown-item[data-value="${val}"]`);
                    if (item) {
                        item.classList.add('selected');
                        fanartDd.querySelector('.c-dropdown-value').textContent = item.textContent;
                    }
                }

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
                configModal.dataset.originalDashLang = dashLangValue;
                configModal.dataset.originalPoster = data.poster_pref || 'show';
                configModal.dataset.originalFanart = data.fanart_pref || 'episode';
                configModal.dataset.originalPosterQ = data.poster_quality || 'w185';
                configModal.dataset.originalFanartQ = data.fanart_quality || 'w300';
                configModal.dataset.originalPosterW = data.ui_poster_w || 108;
                configModal.dataset.originalFanartW = data.ui_fanart_w || 288;

                document.getElementById('config-poster-w').value = data.ui_poster_w || 108;
                document.getElementById('val-poster-w').value = data.ui_poster_w || 108;
                document.getElementById('config-poster-h').value = data.ui_poster_h || 225;
                document.getElementById('val-poster-h').value = data.ui_poster_h || 225;
                document.getElementById('config-fanart-w').value = data.ui_fanart_w || 288;
                document.getElementById('val-fanart-w').value = data.ui_fanart_w || 288;
                document.getElementById('config-fanart-h').value = data.ui_fanart_h || 168;
                document.getElementById('val-fanart-h').value = data.ui_fanart_h || 168;
                document.getElementById('config-grid-gap').value = data.ui_grid_gap !== undefined ? data.ui_grid_gap : 15;
                document.getElementById('val-gap').value = data.ui_grid_gap !== undefined ? data.ui_grid_gap : 15;
                document.getElementById('config-card-radius').value = data.ui_card_radius !== undefined ? data.ui_card_radius : 8;
                document.getElementById('val-radius').value = data.ui_card_radius !== undefined ? data.ui_card_radius : 8;

                /*
                --bg-color: #0d1117;
                --glass-bg: rgba(22, 27, 34, 0.7);
                --glass-border: rgba(255, 255, 255, 0.1);
                --text-primary: #c9d1d9;
                --text-secondary: #8b949e;
                --accent: #58a6ff;
                --accent-hover: #3182ce;
                --accent-dark: #2568a8;
                --danger: #f85149;                    
                */

                document.getElementById('config-bg-color').value = data.ui_bg_color || '#0d1117';
                if (window.pickrBgColor) { window.pickrBgColor.setColor(data.ui_bg_color || '#0d1117'); window.pickrBgColor.applyColor(); }
                document.getElementById('config-glass-bg').value = data.ui_glass_bg || 'rgba(22,27,34,0.7)';
                if (window.pickrGlassBg) { window.pickrGlassBg.setColor(data.ui_glass_bg || 'rgba(22,27,34,0.7)'); window.pickrGlassBg.applyColor(); }
                document.getElementById('config-glass-border').value = data.ui_glass_border || 'rgba(255,255,255,0.1)';
                if (window.pickrGlassBorder) { window.pickrGlassBorder.setColor(data.ui_glass_border || 'rgba(255,255,255,0.1)'); window.pickrGlassBorder.applyColor(); }
                document.getElementById('config-edit-bg').value = data.ui_edit_bg || 'rgba(22,27,34,0.85)';
                if (window.pickrEditBg) { window.pickrEditBg.setColor(data.ui_edit_bg || 'rgba(22,27,34,0.85)'); window.pickrEditBg.applyColor(); }
                document.getElementById('config-combo-bg').value = data.ui_combo_bg || 'rgba(13,17,23,0.95)';
                if (window.pickrComboBg) { window.pickrComboBg.setColor(data.ui_combo_bg || 'rgba(13,17,23,0.95)'); window.pickrComboBg.applyColor(); }
                document.getElementById('config-panel-bg').value = data.ui_panel_bg || 'rgba(255,255,255,0.03)';
                if (window.pickrPanelBg) { window.pickrPanelBg.setColor(data.ui_panel_bg || 'rgba(255,255,255,0.03)'); window.pickrPanelBg.applyColor(); }
                document.getElementById('config-accent-primary').value = data.ui_accent_primary || '#58a6ff';
                if (window.pickrAccentPrimary) { window.pickrAccentPrimary.setColor(data.ui_accent_primary || '#58a6ff'); window.pickrAccentPrimary.applyColor(); }
                document.getElementById('config-accent-hover').value = data.ui_accent_hover || '#3182ce';
                if (window.pickrAccentHover) { window.pickrAccentHover.setColor(data.ui_accent_hover || '#3182ce'); window.pickrAccentHover.applyColor(); }
                document.getElementById('config-accent-dark').value = data.ui_accent_dark || '#2568a8';
                if (window.pickrAccentDark) { window.pickrAccentDark.setColor(data.ui_accent_dark || '#2568a8'); window.pickrAccentDark.applyColor(); }
                document.getElementById('config-danger').value = data.ui_danger || '#f85149';
                if (window.pickrDanger) { window.pickrDanger.setColor(data.ui_danger || '#f85149'); window.pickrDanger.applyColor(); }
                document.getElementById('config-text-primary').value = data.ui_text_primary || '#c9d1d9';
                if (window.pickrTextPrimary) { window.pickrTextPrimary.setColor(data.ui_text_primary || '#c9d1d9'); window.pickrTextPrimary.applyColor(); }
                document.getElementById('config-text-secondary').value = data.ui_text_secondary || '#8b949e';
                if (window.pickrTextSecondary) { window.pickrTextSecondary.setColor(data.ui_text_secondary || '#8b949e'); window.pickrTextSecondary.applyColor(); }


                document.getElementById('config-glass-blur').value = data.ui_glass_blur !== undefined ? data.ui_glass_blur : 10;
                document.getElementById('val-blur').value = data.ui_glass_blur !== undefined ? data.ui_glass_blur : 10;
                document.getElementById('config-fanart-opacity').value = data.fanart_mask_opacity !== undefined ? data.fanart_mask_opacity : 0.3;
                document.getElementById('val-mask-opacity').innerText = data.fanart_mask_opacity !== undefined ? data.fanart_mask_opacity : 0.3;
                document.getElementById('config-show-duration').checked = data.ui_show_duration !== false;
                document.getElementById('config-show-watch-time').checked = data.ui_show_watch_time !== false;
                document.getElementById('config-show-title').checked = data.ui_show_title !== false;

                let fontDd = document.getElementById('configFont-dd');
                if (fontDd) {
                    let fontVal = data.ui_font || 'inter';
                    fontDd.dataset.currentValue = fontVal;
                    fontDd.querySelectorAll('.c-dropdown-item').forEach(i => i.classList.remove('selected'));
                    let item = fontDd.querySelector(`.c-dropdown-item[data-value="${fontVal}"]`);
                    if (item) {
                        item.classList.add('selected');
                        fontDd.querySelector('.c-dropdown-value').textContent = item.textContent;
                    }
                }

                // Resetear estado del formulario
                pwdInput.value = '';
                repeatInput.value = '';
                repeatContainer.classList.add('hidden');
                pwdError.classList.add('hidden');
                saveBtn.disabled = false;
            }
        } catch (e) { console.error(e); }
    };
    if (fabConfig) {
        fabConfig.addEventListener('click', openConfigHandler);
    }
    if (mobileSettingsBtn) {
        mobileSettingsBtn.addEventListener('click', openConfigHandler);
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
            let confirmed = await window.customConfirm(currentLangData.config_restore_confirm || 'Are you sure you want to restore the original configuration? Unsaved changes will be lost.');
            if (!confirmed) return;

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
            plexPinPollingInterval = window.plexPinInterval = setInterval(async () => {
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
        const origDashLang = configModal.dataset.originalDashLang || 'auto';
        const origPoster = configModal.dataset.originalPoster;
        const origFanart = configModal.dataset.originalFanart;
        const origPosterQ = configModal.dataset.originalPosterQ;
        const origFanartQ = configModal.dataset.originalFanartQ;
        const origPosterW = parseInt(configModal.dataset.originalPosterW) || 108;
        const origFanartW = parseInt(configModal.dataset.originalFanartW) || 288;

        const newLang = document.getElementById('configLang-dd').dataset.currentValue || 'es';
        const newDashLang = document.getElementById('configDashboardLang-dd').dataset.currentValue || 'auto';
        const newPoster = document.getElementById('configPosterPref-dd').dataset.currentValue || 'show';
        const newFanart = document.getElementById('configFanartPref-dd').dataset.currentValue || 'episode';
        const newPosterQ = document.getElementById('configPosterQuality-dd').dataset.currentValue || 'w185';
        const newFanartQ = document.getElementById('configFanartQuality-dd').dataset.currentValue || 'w300';

        const newPosterW = parseInt(document.getElementById('config-poster-w').value) || 108;
        const newFanartW = parseInt(document.getElementById('config-fanart-w').value) || 288;

        const pwd = pwdInput.value;

        const payload = {
            plex_url: document.getElementById('config-plex-url').value,
            plex_token: document.getElementById('config-plex-token').value,
            tmdb_api_key: document.getElementById('config-tmdb-api').value,
            plex_client_id: document.getElementById('config-plex-client-id').value,
            debug_mode: document.getElementById('config-debug').checked,
            auto_update: document.getElementById('config-auto-update').checked,
            notify_updates: document.getElementById('config-notify-updates').checked,
            sync_language: newLang,
            dashboard_language: newDashLang,
            poster_pref: newPoster,
            fanart_pref: newFanart, poster_quality: newPosterQ, fanart_quality: newFanartQ,
            ui_poster_w: String(newPosterW),
            ui_poster_h: document.getElementById('config-poster-h').value,
            ui_fanart_w: String(newFanartW),
            ui_fanart_h: document.getElementById('config-fanart-h').value,
            ui_grid_gap: document.getElementById('config-grid-gap').value,
            ui_card_radius: document.getElementById('config-card-radius').value,
            ui_bg_color: document.getElementById('config-bg-color').value,
            ui_glass_bg: document.getElementById('config-glass-bg').value,
            ui_glass_border: document.getElementById('config-glass-border').value,
            ui_edit_bg: document.getElementById('config-edit-bg').value,
            ui_combo_bg: document.getElementById('config-combo-bg').value,
            ui_panel_bg: document.getElementById('config-panel-bg').value,
            ui_accent_primary: document.getElementById('config-accent-primary').value,
            ui_accent_hover: document.getElementById('config-accent-hover').value,
            ui_accent_dark: document.getElementById('config-accent-dark').value,
            ui_danger: document.getElementById('config-danger').value,
            ui_glass_blur: document.getElementById('config-glass-blur').value,
            ui_font: document.getElementById('configFont-dd').dataset.currentValue || 'inter',
            ui_text_primary: document.getElementById('config-text-primary').value,
            ui_text_secondary: document.getElementById('config-text-secondary').value,
            ui_show_duration: document.getElementById('config-show-duration').checked,
            ui_show_watch_time: document.getElementById('config-show-watch-time').checked,
            ui_show_title: document.getElementById('config-show-title').checked,
            fanart_mask_opacity: document.getElementById('config-fanart-opacity').value
        };

        if (pwd) payload.master_password = pwd;

        configModal.classList.add('hidden');

        let needsPoster = (newLang !== origLang) || (newPoster !== origPoster) || (newPosterQ !== origPosterQ);
        let needsFanart = (newLang !== origLang) || (newFanart !== origFanart) || (newFanartQ !== origFanartQ);

        if (newPosterQ === 'dynamic') {
            const getPBucket = (w) => w > 500 ? 780 : w > 342 ? 500 : w > 185 ? 342 : w > 154 ? 185 : 154;
            if (getPBucket(newPosterW) > getPBucket(origPosterW)) needsPoster = true;
        }
        if (newFanartQ === 'dynamic') {
            const getFBucket = (w) => w > 780 ? 1280 : w > 300 ? 780 : 300;
            if (getFBucket(newFanartW) > getFBucket(origFanartW)) needsFanart = true;
        }

        if (needsPoster || needsFanart) {
            let rescanConfirmMsg = currentLangData.config_rescan_smart_confirm || 'You have modified options that require higher resolution or a different language. Do you want to download the new art with these settings?';
            let confirmed = await window.customConfirm(rescanConfirmMsg);
            if (confirmed) {
                payload.force_rescan = true;
            }
        }

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

                    if (data.rescan_started) {
                        hideOverlay(0);
                        document.getElementById('bulk-progress-modal').classList.remove('hidden');
                        document.getElementById('bulk-progress-title').textContent = currentLangData.bulk_progress_scraping || 'Downloading images...';
                        document.getElementById('bulk-progress-bar').style.width = "0%";
                        document.getElementById('bulk-progress-bar').classList.add('indeterminate');
                        document.getElementById('bulk-progress-warning').classList.add('hidden');
                        document.getElementById('bulk-progress-current').textContent = "0";
                        document.getElementById('bulk-progress-total').textContent = "0";

                    } else {
                        let successTitle = currentLangData.config_saved || 'Settings saved.';
                        updateOverlayResult('success', successTitle);
                        if (newDashLang !== origDashLang) {
                            setTimeout(() => window.location.reload(true), 3000);
                        } else {
                            hideOverlay(3000);

                            let visualChanged = (
                                window.UI_SHOW_DURATION !== payload.ui_show_duration ||
                                window.UI_SHOW_WATCH_TIME !== payload.ui_show_watch_time ||
                                window.UI_SHOW_TITLE !== payload.ui_show_title
                            );

                            loadUIConfig().then(() => {
                                if (visualChanged) reloadHistory();
                            });
                        }
                    }

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

    const btnCleanCache = document.getElementById('btn-clean-cache');
    if (btnCleanCache) {
        btnCleanCache.addEventListener('click', async () => {
            let msg = currentLangData.config_cache_confirm || "¿Estás seguro de que deseas eliminar todas las imágenes que ya no están asociadas a ninguna tarjeta de la base de datos?";
            let confirmed = await window.customConfirm(msg);
            if (!confirmed) return;

            showProcessingOverlay(currentLangData.overlay_processing || 'Processing request', currentLangData.overlay_wait || 'Please wait...');
            try {
                let res = await apiFetch('/api/cache/clean', { method: 'POST' });
                if (res.ok) {
                    let data = await res.json();
                    let successTitle = (currentLangData.config_cache_success || "Caché limpiada con éxito. Archivos eliminados: ") + data.deleted;
                    updateOverlayResult('success', successTitle);
                    hideOverlay(3000);
                } else {
                    updateOverlayResult('error', 'Error clearing cache.');
                    hideOverlay(3000);
                }
            } catch (e) {
                console.error(e);
                updateOverlayResult('error', currentLangData.network_error || 'Network error.');
                hideOverlay(3000);
            }
        });
    }
}
document.addEventListener('DOMContentLoaded', setupConfigModal);

// --- FLOATING ACTION BUTTON & MANUAL ADD ---
document.addEventListener('DOMContentLoaded', () => {
    initPickers();
    checkUpdates();

    // --- Auto close mobile menu ---
    document.addEventListener("click", (e) => {
        const headerRight = document.getElementById("header-right");
        const mobileMenuBtn = document.getElementById("mobile-menu-btn");
        if (headerRight && headerRight.classList.contains("open") && mobileMenuBtn) {
            // Check if click was outside both the menu and the hamburger button
            if (!headerRight.contains(e.target) && !mobileMenuBtn.contains(e.target)) {
                headerRight.classList.remove("open");
            }
        }
    });


    // --- Mobile Menu Toggle ---
    const mobileMenuBtn = document.getElementById("mobile-menu-btn");
    if (mobileMenuBtn) {
        mobileMenuBtn.addEventListener("click", () => {
            const headerRight = document.getElementById("header-right");
            if (headerRight) {
                headerRight.classList.toggle("open");
            }
        });
    }

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

    const fabAbout = document.getElementById('fab-about');
    const aboutModal = document.getElementById('about-modal');
    const aboutCloseBtn = document.getElementById('about-close-btn');

    if (fabAbout) {
        fabAbout.addEventListener('click', () => {
            fabContainer.classList.remove('active');
            aboutModal.classList.remove('hidden');
        });
    }

    const logoTrigger = document.getElementById('logo-trigger');
    if (logoTrigger) {
        logoTrigger.addEventListener('click', () => {
            aboutModal.classList.remove('hidden');
        });
    }

    if (aboutCloseBtn) {
        aboutCloseBtn.addEventListener('click', () => {
            aboutModal.classList.add('hidden');
        });
    }

    // Open Manual Modal
    const mobileAddManualBtn = document.getElementById('add-manual-menu-btn');

    const openManualHandler = () => {
        fabContainer.classList.remove('active');
        document.getElementById('header-right').classList.remove('open');
        manualModal.classList.remove('hidden');
        resetManualForm();
    };

    if (fabAddManual) {
        fabAddManual.addEventListener('click', openManualHandler);
    }

    if (mobileAddManualBtn) {
        mobileAddManualBtn.addEventListener('click', openManualHandler);
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
                            rewMsg = rewMsg.replace('{date}', new Date(data.last_watched).toLocaleDateString(activeLang));
                            const confirmed = await window.customConfirm(rewMsg);
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

function switchTab(prefix, tab) {
    document.querySelectorAll('#' + prefix + '-sys, #' + prefix + '-scrap, #' + prefix + '-ui').forEach(el => el.classList.remove('active'));
    document.getElementById(prefix + '-' + tab).classList.add('active');

    const btns = document.getElementById(prefix + '-' + tab).parentNode.querySelectorAll('.segmented-btn');
    btns.forEach(b => b.classList.remove('active'));
    event.currentTarget.classList.add('active');
}

function toggleWebhooks() {
    const content = document.getElementById('webhooks-content');
    const btnSpan = document.querySelector('#webhooks-toggle-btn span');
    content.classList.toggle('hidden');
    if (content.classList.contains('hidden')) {
        btnSpan.textContent = 'Mostrar Webhooks';
    } else {
        btnSpan.textContent = 'Ocultar Webhooks';
    }
}

function toggleAcc(accElement) {
    const wasOpen = accElement.classList.contains('open');
    document.querySelectorAll('.accordion').forEach(a => a.classList.remove('open'));
    if (!wasOpen) {
        accElement.classList.add('open');
    }
}

function initPickers() {
    if (typeof Pickr === 'undefined') return;

    const createPickr = (elId, inputId, defaultColor) => {
        const el = document.getElementById(elId);
        if (!el) return null;

        const pickr = Pickr.create({
            el: el,
            theme: 'nano',
            default: defaultColor,
            swatches: [
                '#0d1117',
                'rgba(22, 27, 34, 0.7)',
                '#3584e4',
                '#1a5fb4',
                '#242424'
            ],
            components: {
                preview: true,
                opacity: true,
                hue: true,
                interaction: {
                    hex: true,
                    rgba: true,
                    input: true,
                    save: true,
                    cancel: true
                }
            },
            i18n: {
                'btn:save': 'Guardar',
                'btn:cancel': 'Cancelar',
                'btn:clear': 'Limpiar'
            }
        });

        pickr.on('init', instance => {
            instance.setColor(defaultColor);
        });

        pickr.on('init', instance => {
            let val = document.getElementById(inputId).value || defaultColor;
            instance.setColor(val);
        });

        pickr.on('save', (color, instance) => {
            if (color) {
                document.getElementById(inputId).value = color.toRGBA().toString(0);
                instance.hide();
            }
        });

        return pickr;
    };

    window.pickrBgColor = createPickr('pickr-bg-color', 'config-bg-color', '#0d1117');
    window.pickrGlassBg = createPickr('pickr-glass-bg', 'config-glass-bg', 'rgba(22,27,34,0.7)');
    window.pickrGlassBorder = createPickr('pickr-glass-border', 'config-glass-border', 'rgba(255,255,255,0.1)');
    window.pickrEditBg = createPickr('pickr-edit-bg', 'config-edit-bg', 'rgba(22,27,34,0.85)');
    window.pickrComboBg = createPickr('pickr-combo-bg', 'config-combo-bg', 'rgba(13,17,23,0.95)');
    window.pickrPanelBg = createPickr('pickr-panel-bg', 'config-panel-bg', 'rgba(255,255,255,0.03)');
    window.pickrAccentPrimary = createPickr('pickr-accent-primary', 'config-accent-primary', '#58a6ff');
    window.pickrAccentHover = createPickr('pickr-accent-hover', 'config-accent-hover', '#3182ce');
    window.pickrAccentDark = createPickr('pickr-accent-dark', 'config-accent-dark', '#2568a8');
    window.pickrDanger = createPickr('pickr-danger', 'config-danger', '#f85149');
    window.pickrTextPrimary = createPickr('pickr-text-primary', 'config-text-primary', '#c9d1d9');
    window.pickrTextSecondary = createPickr('pickr-text-secondary', 'config-text-secondary', '#8b949e');
}




// WebSocket for live updates
let ws;
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws/updates`);

    ws.onmessage = function (event) {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'bulk_update_start') {
                let title = currentLangData.bulk_progress_default || 'Updating...';
                if (data.scope === 'season') {
                    let tmpl = currentLangData.bulk_progress_season || 'Updating Season {season}...';
                    title = tmpl.replace('{season}', data.season);
                } else if (data.scope === 'show') {
                    title = currentLangData.bulk_progress_show || 'Updating Show...';
                } else if (data.scope === 'onwards' || data.scope === 'backwards') {
                    title = currentLangData.bulk_progress_episodes || 'Updating episodes...';
                }

                document.getElementById('bulk-progress-show-title').textContent = data.show_title || "";
                document.getElementById('bulk-progress-title').textContent = title;
                document.getElementById('bulk-progress-total').textContent = data.total;
                document.getElementById('bulk-progress-current').textContent = "0";
                document.getElementById('bulk-progress-item-name').textContent = "";
                document.getElementById('bulk-progress-show-title').style.display = data.show_title ? 'block' : 'none';

                document.getElementById('bulk-progress-bar').classList.remove('indeterminate');
                document.getElementById('bulk-progress-bar').style.width = "0%";
                return;
            }
            if (data.type === 'bulk_update_progress') {
                document.getElementById('bulk-progress-current').textContent = data.current;
                let percent = (data.total > 0) ? (data.current / data.total) * 100 : 0;
                document.getElementById('bulk-progress-bar').style.width = `${percent}%`;

                let itemName = data.item_title || "";
                if (data.season && data.episode) {
                    itemName = `S${String(data.season).padStart(2, '0')}-E${String(data.episode).padStart(2, '0')}: ${itemName}`;
                }
                document.getElementById('bulk-progress-item-name').textContent = itemName;
                return;
            }
            if (data.type === 'bulk_update_wait') {
                const warningEl = document.getElementById('bulk-progress-warning');
                const warningText = document.getElementById('bulk-progress-warning-text');
                const updateWarn = (s) => warningText.textContent = (currentLangData.bulk_progress_rate_limit || '⚠️ Waiting {seconds} seconds (Plex Rate Limit)').replace('{seconds}', s);
                updateWarn(data.seconds);
                warningEl.classList.remove('hidden');
                if (window.bulkWaitInterval) clearInterval(window.bulkWaitInterval);
                let timeLeft = data.seconds;
                window.bulkWaitInterval = setInterval(() => {
                    timeLeft--;
                    if (timeLeft <= 0) {
                        clearInterval(window.bulkWaitInterval);
                        warningEl.classList.add('hidden');
                    } else updateWarn(timeLeft);
                }, 1000);
                return;
            }
            if (data.type === 'bulk_update_resume') {
                if (window.bulkWaitInterval) clearInterval(window.bulkWaitInterval);
                document.getElementById('bulk-progress-warning').classList.add('hidden');
                return;
            }
            if (data.type === 'reload_history') {
                document.getElementById('bulk-progress-modal').classList.add('hidden');
                reloadHistory();
                return;
            }
            if (data.type === 'step3_init') {
                window.rescrapeTotal = data.total;
                window.rescrapeCurrent = 0;
                document.getElementById('bulk-progress-modal').classList.remove('hidden');
                document.getElementById('bulk-progress-show-title').textContent = currentLangData.config_tab_scraping || 'Scraping';
                document.getElementById('bulk-progress-show-title').style.display = 'block';
                document.getElementById('bulk-progress-title').textContent = currentLangData.bulk_progress_scraping || 'Downloading images...';
                document.getElementById('bulk-progress-item-name').textContent = "";
                document.getElementById('bulk-progress-total').textContent = data.total || "0";
                document.getElementById('bulk-progress-current').textContent = "0";
                document.getElementById('bulk-progress-bar').classList.remove('indeterminate');
                document.getElementById('bulk-progress-bar').style.width = "0%";
                return;
            }
            if (data.type === 'import_done') {
                document.getElementById('bulk-progress-modal').classList.add('hidden');
                updateOverlayResult('success', currentLangData.rescan_done || 'Rescan completed!', currentLangData.rescan_reload_hint || 'Click anywhere to reload.');
                const overlay = document.getElementById('loading-overlay');
                overlay.onclick = function () { window.location.reload(true); };
                overlay.classList.remove('hidden');
                return;
            }
            if (data.type === 'batch_update' && data.items) {
                if (window.rescrapeTotal) {
                    window.rescrapeCurrent = (window.rescrapeCurrent || 0) + (data.items ? data.items.length : 0);
                    document.getElementById('bulk-progress-current').textContent = window.rescrapeCurrent;
                    let pct = window.rescrapeTotal > 0 ? (window.rescrapeCurrent / window.rescrapeTotal) * 100 : 0;
                    document.getElementById('bulk-progress-bar').style.width = `${pct}%`;
                    if (data.items && data.items.length > 0) {
                        let last = data.items[data.items.length - 1];
                        let displayText = last.title || "";
                        if (last.show_title && last.season && last.episode) {
                            displayText = `S${String(last.season).padStart(2, '0')}-E${String(last.episode).padStart(2, '0')}: ${last.show_title}`;
                        }
                        document.getElementById('bulk-progress-item-name').textContent = displayText;
                    }
                }
                let hasNewItems = false;
                data.items.forEach(item => {
                    // Actualizar las tarjetas si existen en el DOM
                    const card = document.getElementById(`card-${item.id}`);
                    if (!card) { hasNewItems = true; }
                    if (card) {
                        const img = card.querySelector('.c-poster');
                        if (img && item.poster_path) {
                            let pUrl = (item.poster_path.startsWith('http') || item.poster_path.startsWith('/')) ? item.poster_path : `/cache/posters/${item.poster_path}`;
                            img.style.backgroundImage = `url('${pUrl}?t=${new Date().getTime()}')`;
                        }

                        const fanart = card.querySelector('.c-fanart-content');
                        if (fanart && item.fanart_path) {
                            let fUrl = (item.fanart_path.startsWith('http') || item.fanart_path.startsWith('/')) ? item.fanart_path : `/cache/fanarts/${item.fanart_path}`;
                            fanart.style.backgroundImage = `url('${fUrl}?t=${new Date().getTime()}')`;
                        }

                        const title = card.querySelector('.card-title');
                        if (title && item.title) {
                            title.textContent = item.title;
                        }
                        // Actualizar data attributes para el panel derecho si está abierto
                        card.dataset.title = item.title;
                        card.dataset.showTitle = item.show_title;
                        card.dataset.posterPath = item.poster_path;
                        card.dataset.fanartPath = item.fanart_path;

                        // Si es la tarjeta seleccionada, actualizar el panel derecho
                        if (card.classList.contains('active')) {
                            updateDetailView(card);
                        }
                    }
                });

                // We reactivated infinite scrolling in case it had been blocked.
                if (hasNewItems) {
                    hasMore = true;
                    // If the user is at the bottom of the page (or there is no scrollbar because there are few items), 
                    // we auto-load them so they appear on the screen
                    if (window.innerHeight + window.scrollY >= document.body.offsetHeight - 200) {
                        loadMoreHistory();
                    }
                }
            }
        } catch (e) {
            console.error("Error processing websocket message", e);
        }
    };

    ws.onclose = function () {
        // Reconnect after 3 seconds
        setTimeout(connectWebSocket, 3000);
    };
}

// Call on load
document.addEventListener('DOMContentLoaded', () => {
    initPickers();
    checkUpdates();

    // --- Auto close mobile menu ---
    document.addEventListener("click", (e) => {
        const headerRight = document.getElementById("header-right");
        const mobileMenuBtn = document.getElementById("mobile-menu-btn");
        if (headerRight && headerRight.classList.contains("open") && mobileMenuBtn) {
            // Check if click was outside both the menu and the hamburger button
            if (!headerRight.contains(e.target) && !mobileMenuBtn.contains(e.target)) {
                headerRight.classList.remove("open");
            }
        }
    });


    // --- Mobile Menu Toggle ---
    const mobileMenuBtn = document.getElementById("mobile-menu-btn");
    if (mobileMenuBtn) {
        mobileMenuBtn.addEventListener("click", () => {
            const headerRight = document.getElementById("header-right");
            if (headerRight) {
                headerRight.classList.toggle("open");
            }
        });
    }

    connectWebSocket();
});
window.copyWebhookUrl = function (inputId) {
    let el = document.getElementById(inputId);
    if (!el || !el.value) return;
    let url = el.value;
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(url).then(() => {
            showToast(currentLangData.config_copied || "Copied to clipboard", "success");
        }).catch(err => {
            console.error("Clipboard API error:", err);
        });
    } else {
        el.select();
        try {
            document.execCommand('copy');
            showToast(currentLangData.config_copied || "Copied to clipboard", "success");
        } catch (err) {
            console.error("Fallback copy error:", err);
        }
    }
};

