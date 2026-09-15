let authToken = localStorage.getItem('syncpk_token') || '';
let tmdbApiKey = '';
let currentLangData = {};
let historyData = [];
let offset = 0;
let limit = 20;
let hasMore = true;
let isLoading = false;
let currentFilters = { type: 'all', year: 'all', month: 'all', search: '' };
let tmdbCache = {}; // Cache to avoid duplicate API calls
let statsCache = { movies: 0, moviesHours: 0, episodes: 0, episodesHours: 0 };

document.addEventListener('DOMContentLoaded', init);

async function init() {
    await loadTranslations();
    await loadConfig();
    
    if (authToken) {
        showDashboard();
    } else {
        document.getElementById('login-overlay').classList.remove('hidden');
    }
    
    setupEventListeners();
    initDropdowns();
    populateYearFilter();
    populateMonthFilter();
}

async function loadTranslations() {
    let lang = navigator.language.split('-')[0];
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
}

async function loadConfig() {
    try {
        let res = await fetch('/api/config');
        let data = await res.json();
        tmdbApiKey = data.tmdb_api_key;
    } catch (e) { console.error("Error loading config", e); }
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
    });

    document.getElementById('edit-cancel-btn').addEventListener('click', () => {
        document.getElementById('edit-modal').classList.add('hidden');
    });
}

function initDropdowns() {
    document.querySelectorAll('.c-dropdown').forEach(dd => {
        let trigger = dd.querySelector('.c-dropdown-trigger');
        let closeTimer;
        
        // Hover to open
        dd.addEventListener('mouseenter', () => {
            clearTimeout(closeTimer);
            dd.classList.add('open');
        });
        dd.addEventListener('mouseleave', () => {
            closeTimer = setTimeout(() => dd.classList.remove('open'), 180);
        });
        
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
            authToken = data.token;
            localStorage.setItem('syncpk_token', authToken);
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
    document.getElementById('dashboard').classList.remove('hidden');
    reloadHistory();
    loadStats();
    generateTimeline();
}

function populateYearFilter() {
    const menu = document.getElementById('dd-year-menu');
    if (!menu) return;
    const currentYear = new Date().getFullYear();
    for (let i = currentYear; i >= currentYear - 10; i--) {
        let item = document.createElement('div');
        item.className = 'c-dropdown-item';
        item.dataset.value = i;
        item.textContent = i;
        menu.appendChild(item);
    }
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
    await loadMoreHistory();
}

async function loadMoreHistory() {
    if (isLoading || !hasMore) return;
    isLoading = true;
    document.getElementById('loading-spinner').classList.remove('hidden');
    
    try {
        let url = `/api/history?limit=${limit}&offset=${offset}&type=${currentFilters.type}&year=${currentFilters.year}&month=${currentFilters.month}&search=${encodeURIComponent(currentFilters.search)}`;
        let res = await fetch(url, { headers: { 'Authorization': `Basic ${authToken}` } });
        if (res.status === 401) { logout(); return; }
        
        let data = await res.json();
        if (data.items.length < limit) hasMore = false;
        
        historyData = historyData.concat(data.items);
        offset += limit;
        
        await renderHistory(data.items);
        generateTimeline(); // Refresh dots after new data
    } catch(e) {
        console.error(e);
    }
    
    isLoading = false;
    document.getElementById('loading-spinner').classList.add('hidden');
}

async function loadStats() {
    try {
        let res = await fetch('/api/stats', { headers: { 'Authorization': `Basic ${authToken}` } });
        if (res.ok) {
            let data = await res.json();
            document.getElementById('stat-movies').textContent = data.movies_count;
            document.getElementById('stat-episodes').textContent = data.episodes_count;
        }
    } catch(e) { console.error(e); }
}

function updateStatsUI() {
    document.getElementById('stat-movies-hours').textContent = Math.round(statsCache.moviesHours / 60) + 'h';
    document.getElementById('stat-episodes-hours').textContent = Math.round(statsCache.episodesHours / 60) + 'h';
    document.getElementById('stat-total-hours').textContent = Math.round((statsCache.moviesHours + statsCache.episodesHours) / 60) + 'h';
}

function logout() {
    localStorage.removeItem('syncpk_token');
    authToken = '';
    document.getElementById('dashboard').classList.add('hidden');
    document.getElementById('login-overlay').classList.remove('hidden');
}

// --- TMDB INTEGRATION ---
async function fetchTMDBData(item) {
    if (!tmdbApiKey) return null;
    
    let cacheKey = `${item.media_type}_${item.id}`;
    if (tmdbCache[cacheKey]) return tmdbCache[cacheKey];
    
    let result = null;
    
    try {
        // Try via external IDs first
        let externalId = item.imdb_id || item.show_imdb_id;
        let externalSource = 'imdb_id';
        if (!externalId) { externalId = item.tvdb_id || item.show_tvdb_id; externalSource = 'tvdb_id'; }
        if (!externalId) { externalId = item.tmdb_id || item.show_tmdb_id; externalSource = 'tmdb_id'; }
        
        let typePath = item.media_type === 'movie' ? 'movie' : 'tv';
        
        if (externalId) {
            let url = externalSource === 'tmdb_id'
                ? `https://api.themoviedb.org/3/${typePath}/${externalId}?api_key=${tmdbApiKey}`
                : `https://api.themoviedb.org/3/find/${externalId}?api_key=${tmdbApiKey}&external_source=${externalSource}`;
            
            let res = await fetch(url);
            let data = await res.json();
            
            if (externalSource === 'tmdb_id') {
                result = data;
            } else {
                let arr = item.media_type === 'movie' ? data.movie_results : data.tv_results;
                if (arr?.length > 0) {
                    let det = await fetch(`https://api.themoviedb.org/3/${typePath}/${arr[0].id}?api_key=${tmdbApiKey}`);
                    result = await det.json();
                }
            }
        }
        
        // Fallback for movies: search by title (handles movies without IDs)
        if (!result && item.media_type === 'movie' && item.title) {
            // Remove colons and normalize accents just in case TMDB search fails with them
            let cleanTitle = item.title.replace(/[:]/g, '').normalize("NFD").replace(/[\u0300-\u036f]/g, "");
            let lang = navigator.language || 'es-ES';
            let searchRes = await fetch(`https://api.themoviedb.org/3/search/movie?api_key=${tmdbApiKey}&query=${encodeURIComponent(cleanTitle)}&language=${lang}`);
            let searchData = await searchRes.json();
            if (searchData.results?.length > 0) {
                let det = await fetch(`https://api.themoviedb.org/3/movie/${(searchData.results.find(r => r.poster_path) || searchData.results[0]).id}?api_key=${tmdbApiKey}&language=${lang}`);
                result = await det.json();
            } else {
                // If clean title fails, try exact title
                let searchRes2 = await fetch(`https://api.themoviedb.org/3/search/movie?api_key=${tmdbApiKey}&query=${encodeURIComponent(item.title)}&language=${lang}`);
                let searchData2 = await searchRes2.json();
                if (searchData2.results?.length > 0) {
                    let det2 = await fetch(`https://api.themoviedb.org/3/movie/${(searchData2.results.find(r => r.poster_path) || searchData2.results[0]).id}?api_key=${tmdbApiKey}&language=${lang}`);
                    result = await det2.json();
                }
            }
        }

        if (result) {
            let finalData = {
                poster: result.poster_path ? `https://image.tmdb.org/t/p/w500${result.poster_path}` : '',
                backdrop: result.backdrop_path ? `https://image.tmdb.org/t/p/w1280${result.backdrop_path}` : '',
                runtime: result.runtime || (result.episode_run_time ? result.episode_run_time[0] : 0) || 45
            };
            tmdbCache[cacheKey] = finalData;
            return finalData;
        }
    } catch(e) { console.error("TMDB error", e); }
    return null;
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
            
            let tmdbData = await fetchTMDBData(item);
            
            if (tmdbData) {
                if (item.media_type === 'movie') statsCache.moviesHours += tmdbData.runtime;
                else statsCache.episodesHours += tmdbData.runtime;
            }
            updateStatsUI();
            
            let card = document.createElement('div');
            card.className = 'media-card';
            card.id = `card-${item.id}`;
            
            let posterStyle = tmdbData && tmdbData.poster ? `background-image: url('${tmdbData.poster}')` : 'background-color: #333';
            let bgStyle = tmdbData && tmdbData.backdrop ? `background-image: url('${tmdbData.backdrop}')` : 'background-color: #111';
            
            let timeStr = new Date(item.watched_at).toLocaleTimeString(navigator.language, { hour: '2-digit', minute: '2-digit' });
            let title = item.media_type === 'movie' ? item.title : item.show_title;
            let subtitle = item.media_type === 'movie' ? '' : `T${item.season} · E${item.episode} - ${item.title}`;
            
            card.innerHTML = `
                <div class="card-bg" style="${bgStyle}"></div>
                <div class="card-poster" style="${posterStyle}"></div>
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
            `;
            cardsGrid.appendChild(card);
        }
    }
}

// --- ACTIONS ---
window.toggleDropdown = function(id, e) {
    e.stopPropagation();
    document.querySelectorAll('.kebab-dropdown').forEach(d => {
        if (d.id !== `dropdown-${id}`) d.classList.remove('show');
    });
    document.getElementById(`dropdown-${id}`).classList.toggle('show');
}

window.deleteItem = function(id) {
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
            let res = await fetch(`/api/history/${id}`, { method: 'DELETE', headers: { 'Authorization': `Basic ${authToken}` } });
            if (res.ok) {
                document.getElementById(`card-${id}`).remove();
            }
        } catch(e) { console.error(e); }
    });
}

let currentEditId = null;
window.openEditModal = function(id, dateStr, mediaType) {
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
        if(currentLangData.edit_scope_episode) scopeValEl.textContent = currentLangData.edit_scope_episode;
    } else {
        scopeSelect.classList.add('hidden');
        scopeSelect.dataset.currentValue = 'episode';
    }
    
    modal.classList.remove('hidden');
}

document.getElementById('edit-save-btn').addEventListener('click', async () => {
    let newVal = document.getElementById('edit-date-input').value; // YYYY-MM-DDThh:mm
    if (!newVal) return;
    
    let scopeSelect = document.getElementById('editScope-dd');
    let scopeVal = scopeSelect.dataset.currentValue || 'episode';
    
    // Convert back to UTC string format used by DB (or local if prefered, backend saves as string)
    let finalDateStr = new Date(newVal).toISOString();
    
    try {
        let res = await fetch(`/api/history/${currentEditId}`, { 
            method: 'PUT', 
            headers: { 'Authorization': `Basic ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ watched_at: finalDateStr, scope: scopeVal })
        });
        if (res.ok) {
            document.getElementById('edit-modal').classList.add('hidden');
            reloadHistory(); // Reload to sort properly
        }
    } catch(e) { console.error(e); }
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
            // Try to parse the date from the title (which is a localized string)
            // We store a data-date on the group for reliable parsing
            let rawDate = visibleGroup.dataset.date;
            if (rawDate) {
                let d = new Date(rawDate);
                let weekStart = getWeekStart(d);
                if (!currentWeekStart || weekStart.toDateString() !== currentWeekStart.toDateString()) {
                    generateTimeline(d);
                }
                // Highlight the correct day button
                document.querySelectorAll('.day-btn').forEach(b => {
                    b.classList.toggle('active', b.dataset.date === d.toDateString());
                });
            }
        }
    }
});
