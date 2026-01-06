(function() {
  var meta = window.__CODEX_TRANSCRIPTS_META__;
  if (!meta) return;

  // Populated by chunk scripts:
  // window.__CODEX_TRANSCRIPTS__.chunks[chunkIndex] = [messageHtml, ...]
  var CT = window.__CODEX_TRANSCRIPTS__ = window.__CODEX_TRANSCRIPTS__ || {};
  CT.meta = meta;
  CT.chunks = CT.chunks || {};
  CT._chunkCallbacks = CT._chunkCallbacks || [];
  CT.registerChunk = function(chunkIndex, items) {
    CT.chunks[chunkIndex] = items;
    CT._chunkCallbacks.forEach(function(cb) {
      try { cb(chunkIndex); } catch (e) {}
    });
  };
  CT.onChunkLoaded = function(cb) {
    CT._chunkCallbacks.push(cb);
  };

  function isTextInputFocused() {
    var el = document.activeElement;
    if (!el) return false;
    var tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function computeAssetPrefix() {
    var host = window.location.hostname;
    if (host !== 'gisthost.github.io' && host !== 'gistpreview.github.io') return '';

    var qs = window.location.search || '';
    var qm = qs.match(/^\?([a-f0-9]+)(?:\/|$)/i);
    if (qm) return '?' + qm[1] + '/';

    var parts = window.location.pathname.split('/').filter(Boolean);
    if (parts.length && /^[a-f0-9]+$/i.test(parts[0])) return '/' + parts[0] + '/';

    return '';
  }

  var assetPrefix = computeAssetPrefix();
  function assetUrl(rel) {
    rel = (rel || '').replace(/^\.\//, '');
    if (!assetPrefix) return rel;
    return assetPrefix + rel;
  }

  function chunkUrl(chunkIndex) {
    return assetUrl(meta.chunks[chunkIndex] || '');
  }

  function loadChunk(chunkIndex) {
    if (CT.chunks[chunkIndex]) return;
    CT._loadingChunks = CT._loadingChunks || {};
    if (CT._loadingChunks[chunkIndex]) return;
    CT._loadingChunks[chunkIndex] = true;

    var src = chunkUrl(chunkIndex);
    if (!src) return;
    var s = document.createElement('script');
    s.src = src;
    s.async = true;
    s.onload = function() { CT._loadingChunks[chunkIndex] = false; };
    s.onerror = function() { CT._loadingChunks[chunkIndex] = false; };
    document.head.appendChild(s);
  }

  function getChunkIndexForItem(index) {
    return Math.floor(index / meta.chunk_size);
  }

  function ensureChunksForRange(startIndex, endIndex) {
    if (meta.total <= 0) return [];
    if (startIndex < 0) startIndex = 0;
    if (endIndex >= meta.total) endIndex = meta.total - 1;
    if (endIndex < startIndex) return [];
    var startChunk = getChunkIndexForItem(startIndex);
    var endChunk = getChunkIndexForItem(endIndex);
    var needed = [];
    for (var c = startChunk; c <= endChunk; c++) {
      needed.push(c);
      loadChunk(c);
    }
    return needed;
  }

  function getItemHtml(index) {
    var chunkIndex = getChunkIndexForItem(index);
    var chunk = CT.chunks[chunkIndex];
    if (!chunk) return null;
    var offset = index - (chunkIndex * meta.chunk_size);
    return chunk[offset] || null;
  }

  function waitForChunks(chunks) {
    chunks = chunks || [];
    if (!chunks.length) return Promise.resolve();
    var pending = {};
    chunks.forEach(function(c) { pending[c] = true; });
    chunks.forEach(function(c) { if (CT.chunks[c]) delete pending[c]; });
    if (!Object.keys(pending).length) return Promise.resolve();
    return new Promise(function(resolve) {
      var done = false;
      function check() {
        if (done) return;
        Object.keys(pending).forEach(function(k) {
          var c = parseInt(k, 10);
          if (CT.chunks[c]) delete pending[c];
        });
        if (!Object.keys(pending).length) {
          done = true;
          resolve();
        }
      }
      CT.onChunkLoaded(function() { check(); });
      check();
      setTimeout(check, 50);
      setTimeout(check, 250);
      setTimeout(check, 1000);
    });
  }

  function enhance(root) {
    if (typeof window.__codexTranscriptsEnhance === 'function') {
      window.__codexTranscriptsEnhance(root || document);
    }
  }

  function kindCharAt(i) {
    if (!meta.kinds || i < 0 || i >= meta.kinds.length) return 's';
    return meta.kinds.charAt(i) || 's';
  }

  function kindLabel(ch) {
    if (ch === 'u') return 'user';
    if (ch === 'a') return 'assistant';
    if (ch === 't') return 'tool call';
    if (ch === 'r') return 'tool reply';
    if (ch === 's') return 'system';
    return 'system';
  }

  function findNextByKind(fromIndex, kindChar, direction, minIndex, maxIndex) {
    var i = fromIndex;
    while (true) {
      i += direction;
      if (i < minIndex || i > maxIndex) return null;
      if (kindCharAt(i) === kindChar) return i;
    }
  }

  function groupIndexForMessage(msgIndex) {
    var groups = meta.groups || [];
    var lo = 0;
    var hi = groups.length - 1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      var g = groups[mid];
      var start = g.start | 0;
      var end = g.end | 0;
      if (msgIndex < start) hi = mid - 1;
      else if (msgIndex > end) lo = mid + 1;
      else return mid;
    }
    return null;
  }

  function getConversationEl(groupIndex) {
    return document.querySelector('.conversation[data-group-index="' + groupIndex + '"]');
  }

  function renderMessagesIncremental(container, startIdx, endIdx) {
    var BATCH = 40;
    var i = startIdx;
    container.innerHTML = '';
    function step() {
      var parts = [];
      for (var n = 0; n < BATCH && i <= endIdx; n++, i++) {
        var html = getItemHtml(i);
        if (html) parts.push(html);
      }
      if (parts.length) container.insertAdjacentHTML('beforeend', parts.join(''));
      if (i <= endIdx) {
        window.requestAnimationFrame(step);
      } else {
        enhance(container);
      }
    }
    window.requestAnimationFrame(step);
  }

  function loadConversation(groupIndex) {
    var el = getConversationEl(groupIndex);
    if (!el) return Promise.resolve(false);
    if (el.dataset.loaded === '1') return Promise.resolve(true);

    var start = parseInt(el.getAttribute('data-start') || '0', 10);
    var end = parseInt(el.getAttribute('data-end') || '0', 10);
    var container = document.getElementById('group-' + groupIndex);
    if (!container) return Promise.resolve(false);

    container.innerHTML = '<div class="conversation-loading">Loading…</div>';
    var chunks = ensureChunksForRange(start, end);
    return waitForChunks(chunks).then(function() {
      el.dataset.loaded = '1';
      renderMessagesIncremental(container, start, end);
      return true;
    });
  }

  // Selection/filtering via minimap brush.
  var selStart = 0;
  var selEnd = Math.max(0, (meta.total || 1) - 1);

  function clampSelection() {
    if (selStart < 0) selStart = 0;
    if (selEnd < 0) selEnd = 0;
    if (selStart > selEnd) selStart = selEnd;
    var max = Math.max(0, (meta.total || 1) - 1);
    if (selEnd > max) selEnd = max;
    if (selStart > max) selStart = max;
    if (selStart > selEnd) selStart = selEnd;
  }

  function selectionActive() {
    return selStart > 0 || selEnd < Math.max(0, (meta.total || 1) - 1);
  }

  function applyGroupFilter() {
    var groups = document.querySelectorAll('.conversation');
    groups.forEach(function(d) {
      var start = parseInt(d.getAttribute('data-start') || '0', 10);
      var end = parseInt(d.getAttribute('data-end') || '0', 10);
      var overlaps = !(end < selStart || start > selEnd);
      if (!overlaps) {
        d.classList.add('filtered-out');
        if (d.open) d.open = false;
      } else {
        d.classList.remove('filtered-out');
      }
    });
  }

  function setSelection(startIdx, endIdx) {
    selStart = startIdx | 0;
    selEnd = endIdx | 0;
    clampSelection();
    updateBrushUI();
    applyGroupFilter();
  }

  function updateBrushUI() {
    var canvas = document.getElementById('minimap');
    var selEl = document.getElementById('minimap-selection');
    var hL = document.getElementById('minimap-handle-left');
    var hR = document.getElementById('minimap-handle-right');
    if (!canvas || !selEl || !hL || !hR) return;

    var total = Math.max(1, meta.total || 1);
    var startRatio = selStart / total;
    var endRatio = (selEnd + 1) / total;
    if (endRatio < startRatio) endRatio = startRatio;

    selEl.style.left = (startRatio * 100) + '%';
    selEl.style.width = ((endRatio - startRatio) * 100) + '%';
    hL.style.left = (startRatio * 100) + '%';
    hR.style.left = (endRatio * 100) + '%';
    selEl.classList.toggle('active', selectionActive());
  }

  function setupBrush() {
    var canvas = document.getElementById('minimap');
    var selEl = document.getElementById('minimap-selection');
    var hL = document.getElementById('minimap-handle-left');
    var hR = document.getElementById('minimap-handle-right');
    if (!canvas || !selEl || !hL || !hR) return;

    var drag = null;
    var startClientX = 0;
    var startSelStart = 0;
    var startSelEnd = 0;

    function pxToIndex(clientX) {
      var rect = canvas.getBoundingClientRect();
      var x = clientX - rect.left;
      var ratio = rect.width ? (x / rect.width) : 0;
      if (ratio < 0) ratio = 0;
      if (ratio > 1) ratio = 1;
      var idx = Math.floor(ratio * (meta.total || 0));
      if (idx < 0) idx = 0;
      if (idx > (meta.total - 1)) idx = meta.total - 1;
      return idx;
    }

    function onDown(which, e) {
      e.preventDefault();
      drag = which;
      startClientX = e.clientX;
      startSelStart = selStart;
      startSelEnd = selEnd;
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
    }

    function onMove(e) {
      if (!drag) return;
      if (drag === 'left') {
        setSelection(pxToIndex(e.clientX), selEnd);
        return;
      }
      if (drag === 'right') {
        setSelection(selStart, pxToIndex(e.clientX));
        return;
      }
      if (drag === 'range') {
        var rect = canvas.getBoundingClientRect();
        var dx = (e.clientX - startClientX);
        var total = Math.max(1, meta.total || 1);
        var deltaIdx = Math.round((dx / rect.width) * total);
        setSelection(startSelStart + deltaIdx, startSelEnd + deltaIdx);
        return;
      }
    }

    function onUp() {
      drag = null;
      window.removeEventListener('pointermove', onMove);
    }

    hL.addEventListener('pointerdown', function(e) { onDown('left', e); });
    hR.addEventListener('pointerdown', function(e) { onDown('right', e); });
    selEl.addEventListener('pointerdown', function(e) { onDown('range', e); });
    canvas.addEventListener('dblclick', function() { setSelection(0, Math.max(0, (meta.total || 1) - 1)); });
  }

  // Minimap drawing + hover tooltip.
  function minimapColors() {
    var styles = window.getComputedStyle(document.documentElement);
    return {
      u: styles.getPropertyValue('--user-border').trim() || '#1976d2',
      a: styles.getPropertyValue('--assistant-border').trim() || '#9e9e9e',
      t: styles.getPropertyValue('--tool-border').trim() || '#9c27b0',
      r: styles.getPropertyValue('--thinking-border').trim() || '#ffc107',
      s: styles.getPropertyValue('--system-border').trim() || '#f97316',
      cursor: styles.getPropertyValue('--text-muted').trim() || '#757575',
      bg: styles.getPropertyValue('--card-bg').trim() || '#ffffff'
    };
  }

  var minimapBins = null;
  function computeBins(binCount) {
    var bins = new Array(binCount);
    for (var i = 0; i < binCount; i++) bins[i] = { u: 0, a: 0, t: 0, r: 0, s: 0 };
    var total = meta.total || 0;
    for (var j = 0; j < total; j++) {
      var b = Math.floor((j / total) * binCount);
      if (b >= binCount) b = binCount - 1;
      var k = kindCharAt(j);
      if (k === 'u') bins[b].u++;
      else if (k === 'a') bins[b].a++;
      else if (k === 't') bins[b].t++;
      else if (k === 'r') bins[b].r++;
      else bins[b].s++;
    }
    return bins;
  }

  function resizeMinimap() {
    var minimap = document.getElementById('minimap');
    if (!minimap) return;
    var rect = minimap.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    minimap.width = Math.max(1, Math.floor(rect.width * dpr));
    minimap.height = Math.max(1, Math.floor((rect.height || 64) * dpr));
    minimapBins = null;
    drawMinimap();
    updateBrushUI();
  }

  function drawMinimap(activeIndex) {
    var minimap = document.getElementById('minimap');
    if (!minimap) return;
    var ctx = minimap.getContext('2d');
    if (!ctx) return;

    var w = minimap.width;
    var h = minimap.height;
    var colors = minimapColors();
    ctx.clearRect(0, 0, w, h);

    var binCount = Math.min(w, Math.max(1, Math.min(meta.total, 800)));
    if (!minimapBins || minimapBins.length !== binCount) minimapBins = computeBins(binCount);

    var scaleX = w / binCount;
    for (var i = 0; i < binCount; i++) {
      var b = minimapBins[i];
      var total = b.u + b.a + b.t + b.r + b.s;
      if (!total) continue;

      var x = Math.floor(i * scaleX);
      var barW = Math.max(1, Math.ceil(scaleX));
      var y = h;
      function drawPart(count, color) {
        if (!count) return;
        var ph = Math.max(1, Math.round((count / total) * h));
        y -= ph;
        ctx.fillStyle = color;
        ctx.fillRect(x, y, barW, ph);
      }

      drawPart(b.s, colors.s);
      drawPart(b.r, colors.r);
      drawPart(b.t, colors.t);
      drawPart(b.a, colors.a);
      drawPart(b.u, colors.u);
    }

    if (typeof activeIndex === 'number' && meta.total > 0) {
      var ratio = activeIndex / meta.total;
      var cx = Math.floor(ratio * w);
      ctx.fillStyle = colors.cursor;
      ctx.fillRect(cx, 0, Math.max(2, Math.round((window.devicePixelRatio || 1))), h);
    }
  }

  function setupTooltip() {
    var minimap = document.getElementById('minimap');
    var tip = document.getElementById('minimap-tooltip');
    if (!minimap || !tip) return;

    function hide() {
      tip.style.display = 'none';
    }

    function show(e) {
      var rect = minimap.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var ratio = rect.width ? (x / rect.width) : 0;
      if (ratio < 0) ratio = 0;
      if (ratio > 1) ratio = 1;
      var idx = Math.floor(ratio * (meta.total || 0));
      if (idx < 0) idx = 0;
      if (idx > meta.total - 1) idx = meta.total - 1;

      var ts = meta.ts && meta.ts[idx] ? meta.ts[idx] : '';
      var k = kindCharAt(idx);
      var gidx = groupIndexForMessage(idx);
      var g = (meta.groups && gidx != null) ? meta.groups[gidx] : null;
      var prompt = g && g.prompt ? String(g.prompt) : '(session start)';
      prompt = prompt.replace(/\s+/g, ' ').trim();
      if (prompt.length > 90) prompt = prompt.slice(0, 90) + '…';

      tip.innerHTML =
        '<div class="minimap-tip-title">' + (gidx != null ? ('Conversation #' + (gidx + 1)) : 'Conversation') + '</div>' +
        '<div class="minimap-tip-body">' +
          '<div><span class="minimap-tip-k">' + kindLabel(k) + '</span> · <code>' + ts + '</code></div>' +
          '<div class="minimap-tip-prompt">' + prompt + '</div>' +
        '</div>';

      tip.style.display = 'block';
      tip.style.left = Math.max(8, Math.min(rect.width - 8, x)) + 'px';
    }

    minimap.addEventListener('mousemove', show);
    minimap.addEventListener('mouseleave', hide);
  }

  // Message navigation + permalinks
  var activeIndex = 0;
  function setActiveIndex(idx) {
    activeIndex = idx;
  }

  function highlightActiveMessage(id) {
    document.querySelectorAll('.message.active').forEach(function(m) { m.classList.remove('active'); });
    if (!id) return;
    var el = document.getElementById(id);
    if (el) el.classList.add('active');
  }

  function scrollToIndex(idx) {
    if (meta.total <= 0) return;
    clampSelection();
    if (idx < selStart) idx = selStart;
    if (idx > selEnd) idx = selEnd;
    if (idx < 0) idx = 0;
    if (idx >= meta.total) idx = meta.total - 1;

    var id = meta.ids && meta.ids[idx] ? meta.ids[idx] : null;
    if (!id) return;

    var gidx = groupIndexForMessage(idx);
    if (gidx == null) return;
    var d = getConversationEl(gidx);
    if (d) d.open = true;

    loadConversation(gidx).then(function() {
      var target = document.getElementById(id);
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        highlightActiveMessage(id);
      }
      setActiveIndex(idx);
      try {
        history.replaceState(null, '', window.location.pathname + window.location.search + '#' + id);
      } catch (e) {}
      drawMinimap(idx);
    });
  }

  function indexForId(id) {
    if (!meta.ids || !meta.ids.length) return -1;
    for (var i = 0; i < meta.ids.length; i++) {
      if (meta.ids[i] === id) return i;
    }
    return -1;
  }

  function handleHash() {
    if (!window.location.hash) return false;
    var id = window.location.hash.slice(1);
    if (!id) return false;
    var idx = indexForId(id);
    if (idx >= 0) {
      scrollToIndex(idx);
      return true;
    }
    return false;
  }

  // Help + shortcuts
  var pendingBracket = null;
  var pendingTimer = null;
  function toggleHelp(open) {
    var helpDialog = document.getElementById('kb-help');
    if (!helpDialog) return;
    var isOpen = helpDialog.open;
    var next = typeof open === 'boolean' ? open : !isOpen;
    if (next && !isOpen) helpDialog.showModal();
    else if (!next && isOpen) helpDialog.close();
  }

  function setupKeyboard() {
    var helpBtn = document.getElementById('kb-help-btn');
    var helpClose = document.getElementById('kb-help-close');
    var helpDialog = document.getElementById('kb-help');
    if (helpBtn) helpBtn.addEventListener('click', function() { toggleHelp(); });
    if (helpClose) helpClose.addEventListener('click', function() { toggleHelp(false); });
    if (helpDialog) {
      helpDialog.addEventListener('click', function(e) {
        var rect = helpDialog.getBoundingClientRect();
        if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) {
          toggleHelp(false);
        }
      });
    }

    document.addEventListener('keydown', function(e) {
      if (e.defaultPrevented) return;
      if (isTextInputFocused()) return;

      if (e.key === 'Escape') {
        if (helpDialog && helpDialog.open) {
          e.preventDefault();
          toggleHelp(false);
        }
        var modal = document.getElementById('search-modal');
        if (modal && modal.open) {
          e.preventDefault();
          modal.close();
        }
        return;
      }

      if (e.key === '?') {
        e.preventDefault();
        toggleHelp();
        return;
      }

      if (pendingBracket) {
        e.preventDefault();
        var dir = pendingBracket === ']' ? 1 : -1;
        pendingBracket = null;
        if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null; }
        var k = (e.key || '').toLowerCase();
        if (k === 'a' || k === 'u' || k === 't' || k === 'r' || k === 's') {
          var idx = findNextByKind(activeIndex, k, dir, selStart, selEnd);
          if (idx != null) scrollToIndex(idx);
        }
        return;
      }

      if (e.key === '[' || e.key === ']') {
        e.preventDefault();
        pendingBracket = e.key;
        if (pendingTimer) clearTimeout(pendingTimer);
        pendingTimer = setTimeout(function() { pendingBracket = null; pendingTimer = null; }, 1000);
        return;
      }

      if (e.key === 'n' || e.key === 'j') {
        e.preventDefault();
        scrollToIndex(activeIndex + 1);
        return;
      }
      if (e.key === 'p' || e.key === 'k') {
        e.preventDefault();
        scrollToIndex(activeIndex - 1);
        return;
      }
      if (e.key === 'g') {
        e.preventDefault();
        scrollToIndex(selStart);
        return;
      }
      if (e.key === 'G') {
        e.preventDefault();
        scrollToIndex(selEnd);
        return;
      }
    });
  }

  // Search
  function setupSearch() {
    var searchInput = document.getElementById('search-input');
    var searchBtn = document.getElementById('search-btn');
    var modal = document.getElementById('search-modal');
    var modalInput = document.getElementById('modal-search-input');
    var modalSearchBtn = document.getElementById('modal-search-btn');
    var modalCloseBtn = document.getElementById('modal-close-btn');
    var searchStatus = document.getElementById('search-status');
    var searchResults = document.getElementById('search-results');
    if (!searchInput || !searchBtn || !modal || !modalInput || !modalSearchBtn || !modalCloseBtn) return;

    function openModal(query) {
      modalInput.value = query || '';
      searchResults.innerHTML = '';
      searchStatus.textContent = '';
      modal.showModal();
      modalInput.focus();
      if (query) performSearch(query);
    }

    function closeModal() {
      modal.close();
    }

    function escapeHtml(text) {
      var div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    function snippetFromHtml(html, q) {
      try {
        var tmp = document.createElement('div');
        tmp.innerHTML = html;
        var msg = tmp.querySelector('.message');
        var text = (msg ? msg.textContent : tmp.textContent) || '';
        text = text.replace(/\s+/g, ' ').trim();
        var lower = text.toLowerCase();
        var qi = lower.indexOf(q.toLowerCase());
        if (qi < 0) return text.slice(0, 180) + (text.length > 180 ? '…' : '');
        var start = Math.max(0, qi - 60);
        var end = Math.min(text.length, qi + q.length + 80);
        var pre = text.slice(start, qi);
        var hit = text.slice(qi, qi + q.length);
        var post = text.slice(qi + q.length, end);
        return (start > 0 ? '…' : '') + escapeHtml(pre) + '<mark>' + escapeHtml(hit) + '</mark>' + escapeHtml(post) + (end < text.length ? '…' : '');
      } catch (e) {
        return escapeHtml(String(html).slice(0, 200));
      }
    }

    function yieldToUI() {
      return new Promise(function(resolve) { window.requestAnimationFrame(function() { resolve(); }); });
    }

    async function performSearch(query) {
      var q = (query || '').trim();
      if (!q) {
        searchStatus.textContent = 'Enter a search term';
        return;
      }
      searchResults.innerHTML = '';
      searchStatus.textContent = 'Searching…';

      var qLower = q.toLowerCase();
      var found = 0;

      var totalChunks = (meta.chunks || []).length;
      for (var c = 0; c < totalChunks; c++) {
        loadChunk(c);
        await waitForChunks([c]);
        var items = CT.chunks[c] || [];
        for (var i = 0; i < items.length; i++) {
          var idx = c * meta.chunk_size + i;
          if (idx < selStart || idx > selEnd) continue;
          var html = items[i] || '';
          if (!html) continue;
          if (html.toLowerCase().indexOf(qLower) === -1) continue;

          found++;
          var id = meta.ids && meta.ids[idx] ? meta.ids[idx] : '';
          var k = kindLabel(kindCharAt(idx)).toUpperCase();
          var ts = meta.ts && meta.ts[idx] ? meta.ts[idx] : '';
          var snippet = snippetFromHtml(html, q);

          var div = document.createElement('div');
          div.className = 'search-result';
          div.innerHTML = '<a href="#' + escapeHtml(id) + '" data-index="' + idx + '">' +
            '<small>' + escapeHtml(k) + ' · ' + escapeHtml(ts) + '</small>' +
            '<div class="search-result-snippet">' + snippet + '</div>' +
            '</a>';
          searchResults.appendChild(div);

          if (found % 10 === 0) await yieldToUI();
        }
        searchStatus.textContent = 'Found ' + found + ' result(s)…';
        await yieldToUI();
      }
      searchStatus.textContent = 'Found ' + found + ' result(s)';
    }

    searchBtn.addEventListener('click', function() { openModal(searchInput.value); });
    searchInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') openModal(searchInput.value);
    });
    modalSearchBtn.addEventListener('click', function() { performSearch(modalInput.value); });
    modalInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') performSearch(modalInput.value);
    });
    modalCloseBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', function(e) {
      if (e.target === modal) closeModal();
    });
    searchResults.addEventListener('click', function(e) {
      var a = e.target && e.target.closest ? e.target.closest('a[data-index]') : null;
      if (!a) return;
      e.preventDefault();
      var idx = parseInt(a.getAttribute('data-index') || '0', 10);
      closeModal();
      scrollToIndex(idx);
    });
  }

  function setupConversationToggles() {
    document.querySelectorAll('.conversation').forEach(function(d) {
      d.addEventListener('toggle', function() {
        if (d.open) {
          var gidx = parseInt(d.getAttribute('data-group-index') || '0', 10);
          loadConversation(gidx);
        }
      });
    });

    // Intercept clicks on timestamp permalinks so we can open the right group first.
    document.addEventListener('click', function(e) {
      var a = e.target && e.target.closest ? e.target.closest('a.timestamp-link') : null;
      if (!a) return;
      var href = a.getAttribute('href') || '';
      if (!href || href.charAt(0) !== '#') return;
      var id = href.slice(1);
      if (!id) return;
      var idx = indexForId(id);
      if (idx < 0) return;
      e.preventDefault();
      scrollToIndex(idx);
    });
  }

  function init() {
    if (!meta || !meta.total) return;

    // Load first chunk eagerly (permits quick initial navigation).
    loadChunk(0);

    enhance(document);

    setupKeyboard();
    setupSearch();
    setupConversationToggles();

    // Minimap
    resizeMinimap();
    setupBrush();
    setupTooltip();
    updateBrushUI();
    drawMinimap(activeIndex);

    window.addEventListener('resize', function() { resizeMinimap(); });
    var themeBtn = document.getElementById('theme-toggle');
    if (themeBtn) themeBtn.addEventListener('click', function() { setTimeout(function() { drawMinimap(activeIndex); }, 20); });

    // Hash navigation
    handleHash();
    window.addEventListener('hashchange', handleHash);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
