/**
 * Furry Con Archives web API client.
 * Loads public content from the API host /v1/ and renders DOM fragments.
 */
(function (global) {
    'use strict';

    const API_ORIGIN = (
        document.documentElement.getAttribute('data-api-origin')
        || global.FCA_API_ORIGIN
        || window.location.origin
    ).replace(/\/$/, '');
    const API_KEY = (
        document.documentElement.getAttribute('data-api-key')
        || global.FCA_API_KEY
        || ''
    ).trim();
    const API_BASE = `${API_ORIGIN}/v1`;
    const PLACEHOLDER = '/static/archive/placeholder.jpg';

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function truncate(text, maxLen) {
        const value = String(text || '');
        if (value.length <= maxLen) return value;
        return value.slice(0, maxLen - 1) + '…';
    }

    function skeletonBar(className, style) {
        const cls = ['skeleton-shimmer', className].filter(Boolean).join(' ');
        return `<div class="${cls}" style="${style || ''}" aria-hidden="true"></div>`;
    }

    function skeletonRepeat(count, renderFn) {
        return Array.from({ length: count }, (_, index) => renderFn(index)).join('');
    }

    function renderHeroStatsSkeleton() {
        return skeletonRepeat(3, () => `
<div class="stat-item skeleton-stat" aria-hidden="true">
    ${skeletonBar('skeleton-line lg', 'width:4rem;margin:0 auto 0.55rem;')}
    ${skeletonBar('skeleton-line sm', 'width:5.5rem;margin:0 auto;')}
</div>`);
    }

    function renderConbookRowSkeleton(count = 6) {
        return skeletonRepeat(count, () => `
<div class="conbook-card conbook-link-card skeleton-card" aria-hidden="true">
    ${skeletonBar('skeleton-line title', 'margin-bottom:0.75rem;')}
    ${skeletonBar('skeleton-block', 'width:100%;height:400px;border-radius:8px;')}
</div>`);
    }

    function renderDocumentCardsSkeleton(count = 8) {
        return skeletonRepeat(count, () => `
<div class="document-card skeleton-card" aria-hidden="true">
    ${skeletonBar('skeleton-block', 'width:100%;aspect-ratio:3/4;margin-bottom:1rem;border-radius:8px;')}
    ${skeletonBar('skeleton-line title', 'margin-bottom:0.75rem;')}
    ${skeletonBar('skeleton-line medium', 'margin-bottom:0.4rem;')}
    ${skeletonBar('skeleton-line short', 'margin-bottom:1rem;')}
    ${skeletonBar('skeleton-line sm', 'width:35%;')}
</div>`);
    }

    function renderCategoryCardsSkeleton(count = 6) {
        return skeletonRepeat(count, () => `
<div class="category-card skeleton-card" aria-hidden="true">
    <div class="category-logo-container" style="display:flex;justify-content:center;margin-bottom:1rem;">
        ${skeletonBar('skeleton-circle', 'width:80px;height:80px;')}
    </div>
    ${skeletonBar('skeleton-line title', 'margin-bottom:0.75rem;')}
    ${skeletonBar('skeleton-line medium', 'margin-bottom:0.4rem;')}
    ${skeletonBar('skeleton-line short', 'margin-bottom:1rem;')}
    ${skeletonBar('skeleton-line sm', 'width:45%;')}
</div>`);
    }

    function renderScheduleCardsSkeleton(count = 6) {
        return renderCategoryCardsSkeleton(count);
    }

    function renderVaultCardsSkeleton(count = 6) {
        return skeletonRepeat(count, () => `
<div class="inventory-card skeleton-card" aria-hidden="true">
    ${skeletonBar('skeleton-line lg', 'width:72%;margin-bottom:1rem;')}
    ${skeletonBar('skeleton-line medium', 'margin-bottom:0.55rem;')}
    ${skeletonBar('skeleton-line medium', 'width:88%;margin-bottom:0.55rem;')}
    ${skeletonBar('skeleton-line short', 'width:50%;')}
</div>`);
    }

    function renderResultsCountSkeleton() {
        return skeletonBar('skeleton-line sm', 'width:11rem;height:0.9rem;margin:0;');
    }

    async function fetchJson(path, params, options) {
        const url = new URL(path, `${API_ORIGIN}/`);
        if (params) {
            Object.entries(params).forEach(([key, val]) => {
                if (val !== undefined && val !== null && val !== '') {
                    url.searchParams.set(key, val);
                }
            });
        }
        const headers = { Accept: 'application/json' };
        if (API_KEY) {
            headers['X-App-Key'] = API_KEY;
        }
        const response = await fetch(url.toString(), {
            headers,
            credentials: 'omit',
            signal: options?.signal,
        });
        if (!response.ok) {
            throw new Error(`API request failed (${response.status})`);
        }
        return response.json();
    }

    function sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function getThumbWrapper(img) {
        return img.closest('.document-thumbnail, .conbook-image-wrapper, .preview-box') || img.parentElement;
    }

    const thumbQueue = [];
    let thumbQueuePump = null;

    function enqueueDocThumb(img) {
        if (!img || img.dataset.thumbInit === '1' || img.dataset.thumbQueued === '1') {
            return;
        }
        img.dataset.thumbQueued = '1';
        thumbQueue.push(img);
        if (!thumbQueuePump) {
            thumbQueuePump = pumpDocThumbQueue().finally(() => {
                thumbQueuePump = null;
            });
        }
    }

    async function pumpDocThumbQueue() {
        while (thumbQueue.length) {
            const img = thumbQueue.shift();
            if (!img) continue;
            delete img.dataset.thumbQueued;
            if (!img.isConnected || img.dataset.thumbInit === '1') continue;
            await loadDocThumb(img);
        }
    }

    async function loadDocThumb(img) {
        if (!img || img.dataset.thumbInit === '1') return;
        img.dataset.thumbInit = '1';

        const originalSrc = img.dataset.originalSrc || img.src;
        const baseUrl = new URL(originalSrc, window.location.origin);
        baseUrl.search = '';

        const wrapper = getThumbWrapper(img);
        if (wrapper) {
            wrapper.classList.add('doc-thumb-loading');
        }

        const maxAttempts = 20;
        for (let attempt = 0; attempt < maxAttempts; attempt++) {
            const url = new URL(baseUrl);
            if (attempt > 0) {
                url.searchParams.set('regenerate', '1');
            }

            try {
                const response = await fetch(url.toString(), {
                    credentials: 'same-origin',
                    cache: 'no-store',
                });
                if (!response.ok) {
                    await sleep(1500);
                    continue;
                }
                if (response.headers.get('X-FCA-Placeholder') === '1') {
                    await sleep(Math.min(1500 + attempt * 400, 4000));
                    continue;
                }

                const blob = await response.blob();
                const objectUrl = URL.createObjectURL(blob);
                if (img.dataset.thumbObjectUrl) {
                    URL.revokeObjectURL(img.dataset.thumbObjectUrl);
                }
                img.dataset.thumbObjectUrl = objectUrl;
                img.src = objectUrl;
                if (wrapper) {
                    wrapper.classList.remove('doc-thumb-loading');
                }
                return;
            } catch (_) {
                await sleep(2000);
            }
        }

        img.src = PLACEHOLDER;
        if (wrapper) {
            wrapper.classList.remove('doc-thumb-loading');
        }
    }

    function initDocThumb(img) {
        enqueueDocThumb(img);
    }

    function initDocThumbs(root) {
        (root || document).querySelectorAll('img[data-doc-thumb="1"]').forEach((img) => {
            enqueueDocThumb(img);
        });
    }

    async function appendCardsSequentially(container, docs, renderCard, options = {}) {
        if (!container) return;
        const { append = false } = options;
        if (!append) {
            container.innerHTML = '';
        }
        for (const doc of docs || []) {
            const html = renderCard(doc);
            if (!html) continue;
            container.insertAdjacentHTML('beforeend', html);
            const img = container.lastElementChild?.querySelector('img[data-doc-thumb="1"]');
            if (img) {
                await loadDocThumb(img);
            }
        }
    }

    function renderDocumentCard(doc) {
        if (doc.takedown) {
            return '';
        }

        const cover = doc.cover_url || PLACEHOLDER;
        const webUrl = doc.web_url || `/documents/${encodeURIComponent(doc.slug)}`;
        const downloadUrl = doc.download_url || `/documents/${encodeURIComponent(doc.slug)}/download`;
        const badge = doc.is_scanned ? 'Scanned File' : 'Source File';
        const meta = [];

        if (doc.year) {
            meta.push(`<div class="meta-item"><i class="bi bi-calendar"></i><span>${escapeHtml(doc.year)}</span></div>`);
        }
        if (doc.convention_name) {
            meta.push(`<div class="meta-item"><i class="bi bi-building"></i><span>${escapeHtml(doc.convention_name)}</span></div>`);
        }
        if (doc.page_count) {
            meta.push(`<div class="meta-item"><i class="bi bi-file-earmark-text"></i><span>${escapeHtml(doc.page_count)} pages</span></div>`);
        }
        if (doc.file_size_mb) {
            meta.push(`<div class="meta-item"><i class="bi bi-file-pdf"></i><span>${escapeHtml(doc.file_size_mb)} MB</span></div>`);
        }
        meta.push(`<div class="meta-item"><i class="bi bi-eye"></i><span>${escapeHtml(doc.view_count || 0)} views</span></div>`);

        const description = doc.description
            ? `<p class="document-description">${escapeHtml(truncate(doc.description, 500))}</p>`
            : '';

        const category = doc.category
            ? `<div class="document-tags"><a href="/conventions/${encodeURIComponent(doc.category.slug)}" class="tag"><i class="bi bi-building"></i>${escapeHtml(doc.category.name)}</a></div>`
            : '';

        return `
<div class="document-card">
    <a href="${escapeHtml(webUrl)}" class="document-thumbnail">
        <img data-doc-thumb="1" data-original-src="${escapeHtml(cover)}" src="${PLACEHOLDER}" alt="${escapeHtml(doc.title)} - Page 1" loading="lazy">
        <div class="document-type-badge">${badge}</div>
    </a>
    <div class="document-content">
        <h3 class="document-title"><a href="${escapeHtml(webUrl)}">${escapeHtml(doc.title || 'Untitled')}</a></h3>
        <div class="document-meta">${meta.join('')}</div>
        ${description}
        ${category}
        <div class="document-actions">
            <a href="${escapeHtml(webUrl)}" class="btn btn-small"><i class="bi bi-eye"></i> View</a>
            <a href="${escapeHtml(downloadUrl)}" class="btn btn-small btn-secondary"><i class="bi bi-download"></i> Download</a>
        </div>
    </div>
</div>`;
    }

    function renderConbookCard(doc) {
        if (doc.takedown) return '';
        const cover = doc.cover_url || PLACEHOLDER;
        const webUrl = doc.web_url || `/documents/${encodeURIComponent(doc.slug)}`;
        return `
<a class="conbook-card conbook-link-card" href="${escapeHtml(webUrl)}">
    <div class="conbook-title">${escapeHtml(doc.title || 'Untitled')}</div>
    <div class="conbook-image-wrapper">
        <img class="conbook-image" data-doc-thumb="1" data-original-src="${escapeHtml(cover)}" src="${PLACEHOLDER}" alt="Cover for ${escapeHtml(doc.title || 'Untitled')}" loading="lazy">
    </div>
</a>`;
    }

    function renderCategoryLogo(category) {
        if (category.logo) {
            return `
<img src="${escapeHtml(category.logo)}" alt="${escapeHtml(category.name)} logo" class="category-logo" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
<div class="category-logo-placeholder" style="display: none;"><i class="bi bi-folder-fill"></i></div>`;
        }
        return `<div class="category-logo-placeholder"><i class="bi bi-folder-fill"></i></div>`;
    }

    function renderCategoryCard(category, variant) {
        const docCount = category.document_count ?? category.doc_count ?? 0;
        const scheduleCount = category.schedule_count ?? 0;
        const conventionUrl = category.url || `/conventions/${encodeURIComponent(category.slug)}`;
        const docsUrl = category.documents_url || `/documents?category=${encodeURIComponent(category.slug)}`;
        const schedulesUrl = category.schedules_url || `/schedules/list?category=${encodeURIComponent(category.slug)}`;

        const location = category.location
            ? `<div class="info-item"><i class="bi bi-geo-alt"></i><span><strong>Location:</strong> ${escapeHtml(category.location)}</span></div>`
            : '';

        const yearRange = category.year_range
            ? `<div class="info-item"><i class="bi bi-calendar-event"></i><span><strong>Active Years:</strong> ${escapeHtml(category.year_range)}</span></div>`
            : '';

        if (variant === 'schedule') {
            return `
<a href="${escapeHtml(schedulesUrl)}" class="category-card">
    <div class="category-logo-container">${renderCategoryLogo(category)}</div>
    <div class="category-content">
        <h2 class="category-name">${escapeHtml(category.name)}</h2>
        <div class="category-info">${location}${yearRange}</div>
        <div class="category-meta">
            <div class="meta-badge"><i class="bi bi-calendar-check"></i><span>${escapeHtml(scheduleCount)} schedule${scheduleCount === 1 ? '' : 's'}</span></div>
        </div>
    </div>
</a>`;
        }

        return `
<a href="${escapeHtml(conventionUrl)}" class="category-card category-card-link">
    <div class="category-logo-container">${renderCategoryLogo(category)}</div>
    <div class="category-content">
        <h2 class="category-name">${escapeHtml(category.name)}</h2>
        <div class="category-info">${location}${yearRange}</div>
        <div class="category-meta">
            <div class="meta-badge"><i class="bi bi-file-pdf"></i><span>${escapeHtml(docCount)} document${docCount === 1 ? '' : 's'}</span></div>
        </div>
    </div>
</a>`;
    }

    function renderVaultDonor(donor) {
        const name = escapeHtml(donor.display_name || donor.username || '');
        const avatarStyle = 'width: 28px; height: 28px; border-radius: 50%; object-fit: cover; border: 1px solid #00d4ff; background: #eee;';
        const placeholder = '<span style="display: inline-block; width: 28px; height: 28px; border-radius: 50%; background: #3d6652; border: 1px solid #00d4ff; text-align: center; line-height: 28px; font-size: 0.9em; color: #888;">?</span>';

        let avatarHtml;
        if (donor.avatar_url) {
            const img = `<img src="${escapeHtml(donor.avatar_url)}" alt="${name}" title="${name}" style="${avatarStyle}">`;
            avatarHtml = donor.profile_url
                ? `<a href="${escapeHtml(donor.profile_url)}" target="_blank" rel="noopener noreferrer">${img}</a>`
                : img;
        } else if (donor.type === 'discord' && donor.profile_url) {
            avatarHtml = `<a href="${escapeHtml(donor.profile_url)}" target="_blank" rel="noopener noreferrer"><img src="https://cdn.discordapp.com/embed/avatars/0.png" alt="${name}" title="${name}" style="${avatarStyle}"></a>`;
        } else {
            avatarHtml = placeholder;
        }

        return `<span style="display: inline-flex; align-items: center; gap: 0.4em;">${avatarHtml}<span>${name}</span></span>`;
    }

    function renderVaultDonors(item) {
        const donors = item.donor_entries || [];
        if (!donors.length) {
            return item.donators ? escapeHtml(item.donators) : '—';
        }
        return `<div style="display: flex; flex-direction: column; gap: 0.35em; align-items: flex-start;">${donors.map(renderVaultDonor).join('')}</div>`;
    }

    function renderVaultItem(item) {
        const header = `${escapeHtml(item.con)}${item.year ? ' ' + escapeHtml(item.year) : ''} - ${escapeHtml(item.item_type)}`;
        const donators = renderVaultDonors(item);
        const notes = item.notes ? `<div><strong>Notes:</strong> ${escapeHtml(item.notes)}</div>` : '';
        const copies = item.copies ?? item.quantity ?? 0;
        return `
<div class="inventory-card" data-convention="${escapeHtml(String(item.con || '').toLowerCase())}" data-year="${escapeHtml(item.year || '')}" data-type="${escapeHtml(String(item.item_type || '').toLowerCase())}" data-donors="${escapeHtml(String(item.donators || '').toLowerCase())}" data-notes="${escapeHtml(String(item.notes || '').toLowerCase())}">
    <div class="inventory-header">${header}</div>
    <div class="inventory-body">
        <div><strong>Copies:</strong> ${escapeHtml(copies)}</div>
        <div style="display: flex; align-items: flex-start; gap: 0.5em; flex-wrap: wrap;">
            <strong>Donator(s):</strong>
            ${donators}
        </div>
        ${notes}
    </div>
</div>`;
    }

    function paginationFromParams(page, pageSize, total) {
        const totalPages = pageSize ? Math.ceil(total / pageSize) : 1;
        return {
            page,
            page_size: pageSize,
            total,
            total_pages: totalPages,
            has_next: page < totalPages,
            has_previous: page > 1,
        };
    }

    function startIndex(pagination) {
        if (!pagination.total) return 0;
        return (pagination.page - 1) * pagination.page_size + 1;
    }

    function endIndex(pagination) {
        if (!pagination.total) return 0;
        return Math.min(pagination.page * pagination.page_size, pagination.total);
    }

    async function loadStats() {
        return fetchJson(`${API_BASE}/stats/`);
    }

    async function loadFeaturedDocuments(pageSize) {
        return fetchJson(`${API_BASE}/documents/`, {
            shuffle: '1',
            page_size: pageSize || 20,
            page: 1,
        });
    }

    async function loadDocuments(params, options) {
        return fetchJson(`${API_BASE}/documents/`, params, options);
    }

    async function loadCategories(params) {
        return fetchJson(`${API_BASE}/categories/`, params);
    }

    async function loadSchedules(params) {
        return fetchJson(`${API_BASE}/schedules/`, params);
    }

    async function loadVault(params) {
        return fetchJson(`${API_BASE}/vault/`, params);
    }

    function animateStatNumber(el, end, duration) {
        let start = 0;
        let startTime = null;
        function step(timestamp) {
            if (!startTime) startTime = timestamp;
            const progress = Math.min((timestamp - startTime) / duration, 1);
            const value = Math.floor(progress * (end - start) + start);
            el.textContent = value.toLocaleString();
            if (progress < 1) {
                requestAnimationFrame(step);
            } else {
                el.textContent = end.toLocaleString();
            }
        }
        requestAnimationFrame(step);
    }

    function applyStatsToHero(stats) {
        const container = document.getElementById('hero-stats');
        if (!container) return;

        const documents = stats.total_documents ?? stats.documents ?? 0;
        const views = stats.total_pdf_views ?? stats.document_views ?? 0;
        const conventions = stats.total_conventions ?? stats.conventions ?? 0;

        container.innerHTML = `
            <div class="stat-item"><span class="stat-number" data-stat="documents">${documents.toLocaleString()}</span><span class="stat-label">Documents</span></div>
            <div class="stat-item"><span class="stat-number" data-stat="views">${views.toLocaleString()}</span><span class="stat-label">Document Views</span></div>
            <div class="stat-item"><span class="stat-number" data-stat="conventions">${conventions.toLocaleString()}</span><span class="stat-label">Conventions</span></div>`;

        container.querySelectorAll('.stat-number').forEach((el) => {
            const end = parseInt(el.textContent.replace(/,/g, ''), 10) || 0;
            animateStatNumber(el, end, 1000);
        });
        container.style.display = '';
    }

    function initHomepageCarousel(row) {
        if (!row || !row.children.length) return;
        const cards = Array.from(row.children);
        cards.forEach((card) => {
            const clone = card.cloneNode(true);
            clone.classList.add('conbook-card-clone');
            row.appendChild(clone);
        });

        const cardWidth = cards[0]?.offsetWidth || 340;
        const gap = 24;
        const scrollStep = cardWidth + gap;
        let scrollPos = 0;

        function doScroll() {
            scrollPos += scrollStep;
            if (scrollPos >= cardWidth * cards.length) {
                row.scrollTo({ left: 0, behavior: 'auto' });
                scrollPos = 0;
            } else {
                row.scrollTo({ left: scrollPos, behavior: 'smooth' });
            }
        }

        row.addEventListener('scroll', () => {
            scrollPos = row.scrollLeft;
        });
        setInterval(doScroll, 3000);
    }

    async function initHomepage() {
        const row = document.getElementById('conbook-row');
        const statsContainer = document.getElementById('hero-stats');

        if (statsContainer) {
            statsContainer.innerHTML = renderHeroStatsSkeleton();
            statsContainer.style.display = '';
        }
        if (row) {
            row.innerHTML = renderConbookRowSkeleton(6);
        }

        try {
            const [stats, docs] = await Promise.all([
                loadStats(),
                loadFeaturedDocuments(20),
            ]);
            applyStatsToHero(stats);
            if (row) {
                const results = docs.results || [];
                row.innerHTML = '';
                row.removeAttribute('aria-busy');
                if (!results.length) {
                    row.innerHTML = '<div style="color:var(--text-secondary);padding:2rem;font-size:1.1rem;">No conbooks available to display.</div>';
                } else {
                    await appendCardsSequentially(row, results, renderConbookCard);
                    initHomepageCarousel(row);
                }
            }
        } catch (error) {
            console.error('Homepage API load failed', error);
            if (statsContainer) {
                statsContainer.innerHTML = '<div class="stat-item"><span class="stat-label">Stats unavailable</span></div>';
            }
            if (row) {
                row.innerHTML = '<div style="color:var(--text-secondary);padding:2rem;font-size:1.1rem;">Unable to load featured conbooks.</div>';
            }
        }
    }

    global.FCA = {
        API_ORIGIN,
        API_BASE,
        API_KEY,
        PLACEHOLDER,
        escapeHtml,
        fetchJson,
        initDocThumb,
        initDocThumbs,
        appendCardsSequentially,
        loadDocThumb,
        renderDocumentCard,
        renderConbookCard,
        renderCategoryCard,
        renderVaultItem,
        renderHeroStatsSkeleton,
        renderConbookRowSkeleton,
        renderDocumentCardsSkeleton,
        renderCategoryCardsSkeleton,
        renderScheduleCardsSkeleton,
        renderVaultCardsSkeleton,
        renderResultsCountSkeleton,
        paginationFromParams,
        startIndex,
        endIndex,
        loadStats,
        loadFeaturedDocuments,
        loadDocuments,
        loadCategories,
        loadSchedules,
        loadVault,
        initHomepage,
        sleep,
    };

    document.addEventListener('DOMContentLoaded', function () {
        initDocThumbs();
    });
})(window);
