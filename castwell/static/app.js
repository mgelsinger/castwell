'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const ACTIVE = new Set(['queued', 'downloading', 'transcribing', 'detecting', 'rendering']);
  const STATUS = {
    downloaded: ['Audio downloaded', 'download'], cancelled: ['Preparation cancelled', 'info'],
    available: ['Ready to prepare', 'clock'], queued: ['In the queue', 'clock'],
    downloading: ['Downloading audio', 'download'], transcribing: ['Transcribing', 'text'],
    detecting: ['Finding advertisements', 'spark'], rendering: ['Creating cleaned audio', 'spark'],
    ready: ['Ready to listen', 'check'], review: ['Review suggested cuts', 'info'], error: ['Needs attention', 'info'],
  };
  const PALETTES = [['#5e7364', '#f4e8cb'], ['#cc8065', '#342c31'], ['#c5b985', '#343f37'], ['#6b8093', '#eee5cd'], ['#6c6889', '#f1d5bc'], ['#9f614f', '#f4d8b7']];
  const state = {
    episodes: [], settings: {}, selected: new Set(), filter: 'all', search: '', detail: null,
    cuts: [], cutsDirty: false, audioVersion: 'original', activeTab: 'transcript',
    listSignature: '', transcriptSignature: '', cutsSignature: '', refreshBusy: false,
    detailRequest: 0, actionBusy: false, lastFocus: null, loaded: false,
    podcast: '', sort: 'newest', feeds: [], jobs: [], feedBusy: new Set(),
    waveform: null, waveformKey: '', waveformBusy: false, markStart: null,
    pendingSeek: null, pendingPlay: false, lastPositionSave: 0, lastPosition: null,
    transcriptVersion: 'original', cleanedTranscript: null, cleanedKey: '', cleanedBusy: false,
    modelPreparing: false, settingsLoaded: false, settingsBusy: false,
    revisions: [], progressWarning: false,
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function icon(name) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    node.classList.add('icon');
    node.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `#i-${name}`);
    node.append(use);
    return node;
  }
  function visible(id, show) { $(id).hidden = !show; }
  function showError(id, error) {
    $(id).textContent = error ? error.message || String(error) : '';
    visible(id, Boolean(error));
  }
  function toast(message, error = false) {
    const node = el('div', `toast${error ? ' error' : ''}`, message);
    $('toasts').append(node);
    window.setTimeout(() => node.remove(), 6500);
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...options.headers },
      ...(options.body !== undefined ? { body: options.body instanceof FormData ? options.body : JSON.stringify(options.body) } : {}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.error || payload.detail || `Request failed (${response.status}).`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return payload;
  }
  function episodeURL(id, suffix = '') { return `/api/episodes/${encodeURIComponent(id)}${suffix}`; }
  function mediaURL(id, variant) {
    const episode = state.detail?.id === id ? state.detail : state.episodes.find((item) => item.id === id);
    const version = variant === 'cleaned' ? episode?.media_version : null;
    return `/media/${encodeURIComponent(id)}/${variant}${version ? `?v=${encodeURIComponent(version)}` : ''}`;
  }
  function seconds(value) { const n = Number(value); return Number.isFinite(n) ? Math.max(0, n) : 0; }
  function clock(value) {
    const n = Math.floor(seconds(value));
    return n >= 3600 ? `${Math.floor(n / 3600)}:${String(Math.floor(n % 3600 / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}` : `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
  }
  function duration(value) {
    if (!seconds(value)) return '';
    const minutes = Math.max(1, Math.round(seconds(value) / 60));
    return minutes >= 60 ? `${Math.floor(minutes / 60)} hr ${minutes % 60 ? `${minutes % 60} min` : ''}`.trim() : `${minutes} min`;
  }
  function date(value) {
    if (!value) return '';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: d.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined });
  }
  function plainText(value) {
    return String(value || '').replace(/<[^>]*>/g, '').replace(/&(?:amp|lt|gt|quot|apos|nbsp);/g, (entity) => ({ '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'", '&nbsp;': ' ' }[entity]));
  }
  function statusBadge(episode) {
    const [label, glyph] = STATUS[episode.status] || [episode.status || 'Ready to prepare', 'clock'];
    const busy = ACTIVE.has(episode.status);
    const badge = el('span', `status-badge ${busy ? 'busy' : episode.status || ''}`);
    badge.append(busy ? el('span', 'status-spinner') : icon(glyph), document.createTextNode(label));
    return badge;
  }
  function cover(episode, play = false) {
    const node = el('div', 'cover');
    let hash = 0;
    for (const ch of String(episode.podcast || episode.title || 'Podcast')) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
    const palette = PALETTES[hash % PALETTES.length];
    node.style.setProperty('--cover-bg', palette[0]);
    node.style.setProperty('--cover-fg', palette[1]);
    node.append(el('div', 'cover-pattern'), el('span', 'cover-title', episode.podcast || episode.title || 'Podcast'));
    const footer = el('span', 'cover-footer', 'A GOOD LISTEN');
    footer.prepend(icon('headphones'));
    node.append(footer);
    if (episode.image) {
      try {
        const url = new URL(episode.image, window.location.origin);
        if (['http:', 'https:'].includes(url.protocol)) {
          const image = el('img');
          image.src = url.href;
          image.alt = '';
          image.loading = 'lazy';
          image.referrerPolicy = 'no-referrer';
          image.addEventListener('error', () => image.remove(), { once: true });
          node.append(image);
        }
      } catch (_) { /* A missing or malformed cover uses the local artwork. */ }
    }
    if (play) { const button = el('span', 'cover-play'); button.append(icon('play')); node.append(button); }
    return node;
  }
  function renderSelection() {
    const count = state.selected.size;
    visible('selection-bar', count > 0);
    $('selection-count').textContent = `${count} episode${count === 1 ? '' : 's'} selected`;
    $('process-label').textContent = count ? `Prepare ${count} selected` : 'Prepare all';
    const processable = state.episodes.some((episode) => !episode.archived && ['available', 'downloaded', 'cancelled', 'error'].includes(episode.status));
    $('process-button').disabled = state.actionBusy || (count === 0 && !processable);
    $('hero-process').disabled = state.actionBusy || (state.episodes.length > 0 && !processable && count === 0);
  }
  function card(episode) {
    const article = el('article', 'episode-card');
    const select = el('label', 'select-episode');
    const checkbox = el('input');
    checkbox.type = 'checkbox';
    checkbox.checked = state.selected.has(episode.id);
    checkbox.setAttribute('aria-label', `Select ${episode.title}`);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) state.selected.add(episode.id); else state.selected.delete(episode.id);
      renderSelection();
    });
    select.append(checkbox);
    const open = el('button', 'card-open');
    open.setAttribute('aria-label', `Open ${episode.title}`);
    open.addEventListener('click', () => openDetail(episode.id));
    open.append(cover(episode, true));
    const content = el('div', 'card-content');
    content.append(el('p', 'card-podcast', episode.podcast || 'Podcast'), el('h3', 'card-title', episode.title || 'Untitled episode'));
    const meta = el('div', 'card-meta');
    if (duration(episode.duration)) meta.append(icon('clock'), document.createTextNode(duration(episode.duration)));
    if (date(episode.published)) {
      if (meta.childNodes.length) meta.append(el('span', 'meta-dot', '•'));
      meta.append(document.createTextNode(date(episode.published)));
    }
    if (!meta.childNodes.length) meta.append(document.createTextNode('Ready for your rotation'));
    content.append(meta);
    const bottom = el('div', 'card-bottom');
    bottom.append(statusBadge(episode), icon(episode.has_transcript ? 'text' : 'arrow'));
    open.append(content, bottom);
    const favorite = el('button', `card-favorite${episode.favorite ? ' selected' : ''}`);
    favorite.append(icon('heart'));
    favorite.setAttribute('aria-label', `${episode.favorite ? 'Unfavorite' : 'Favorite'} ${episode.title}`);
    favorite.setAttribute('aria-pressed', String(Boolean(episode.favorite)));
    favorite.addEventListener('click', () => updateEpisodeState(episode.id, { favorite: !episode.favorite }).catch((error) => toast(error.message, true)));
    article.append(select, open, favorite);
    if (episode.played || seconds(episode.position) > 0) {
      const progress = el('div', 'card-listening-progress');
      progress.title = episode.played ? 'Played' : `Continue from ${clock(episode.position)}`;
      progress.setAttribute('aria-label', progress.title);
      const fill = el('span');
      fill.style.width = `${episode.played ? 100 : Math.min(100, seconds(episode.position) / Math.max(1, seconds(episode.duration)) * 100)}%`;
      progress.append(fill); article.append(progress);
      if (episode.played) content.append(el('span', 'played-label', '✓ Played'));
    }
    return article;
  }
  function renderList() {
    const search = state.search.toLocaleLowerCase();
    const episodes = state.episodes.filter((episode) => {
      if (state.filter === 'archived' ? !episode.archived : episode.archived) return false;
      if (state.filter === 'favorites' && !episode.favorite) return false;
      if (state.filter === 'unplayed' && episode.played) return false;
      if (state.podcast && episode.podcast !== state.podcast) return false;
      if (state.filter === 'ready' && !['ready', 'review'].includes(episode.status)) return false;
      if (state.filter === 'review' && episode.status !== 'review') return false;
      return !search || `${episode.title} ${episode.podcast} ${plainText(episode.description)}`.toLocaleLowerCase().includes(search);
    });
    episodes.sort((a, b) => {
      if (state.sort === 'title') return String(a.title || '').localeCompare(String(b.title || ''));
      if (state.sort === 'duration') return (seconds(a.duration) || Infinity) - (seconds(b.duration) || Infinity);
      if (state.sort === 'recent') return (Date.parse(b.last_played || b.updated_at) || 0) - (Date.parse(a.last_played || a.updated_at) || 0);
      const order = (Date.parse(b.published || b.created_at) || 0) - (Date.parse(a.published || a.created_at) || 0);
      return state.sort === 'oldest' ? -order : order;
    });
    $('episodes').replaceChildren(...episodes.map(card));
    const podcasts = [...new Set(state.episodes.map((episode) => episode.podcast).filter(Boolean))].sort();
    const podcastKey = JSON.stringify(podcasts);
    if ($('podcast-filter').dataset.key !== podcastKey) {
      $('podcast-filter').replaceChildren(new Option('All podcasts', ''), ...podcasts.map((name) => new Option(name, name)));
      $('podcast-filter').dataset.key = podcastKey;
      if (!podcasts.includes(state.podcast)) state.podcast = '';
      $('podcast-filter').value = state.podcast;
    }
    $('episodes').setAttribute('aria-busy', 'false');
    visible('empty-state', !state.episodes.length && state.loaded);
    visible('no-results', state.episodes.length > 0 && episodes.length === 0);
    renderSelection();
  }
  function renderStats(stats = {}) {
    const count = stats.episodes ?? state.episodes.length;
    $('nav-count').textContent = count;
    $('stat-episodes').textContent = Number(count).toLocaleString();
    $('stat-ready').textContent = Number(stats.ready ?? state.episodes.filter((item) => item.has_cleaned).length).toLocaleString();
    $('stat-removed').textContent = `${Math.floor(seconds(stats.removed_seconds ?? state.episodes.reduce((sum, item) => sum + seconds(item.removed_seconds), 0)) / 60).toLocaleString()} min`;
    $('collection-count').textContent = `${count} episode${count === 1 ? '' : 's'}`;
    const review = state.episodes.filter((item) => item.status === 'review').length;
    $('review-count').textContent = review;
    visible('review-count', review > 0);
  }
  async function refreshLibrary() {
    if (state.refreshBusy) return;
    state.refreshBusy = true;
    try {
      const payload = await api('/api/episodes');
      state.episodes = payload.episodes || [];
      state.settings = { ...state.settings, ...payload.settings };
      state.loaded = true;
      for (const id of state.selected) if (!state.episodes.some((item) => item.id === id)) state.selected.delete(id);
      const signature = JSON.stringify(state.episodes);
      if (signature !== state.listSignature) { state.listSignature = signature; renderList(); }
      renderStats(payload.stats || {});
      showError('library-error', null);
      if (state.detail) await refreshDetail(state.detail.id);
      await refreshQueue();
    } catch (error) {
      showError('library-error', new Error(`Couldn’t refresh your library. ${error.message}`));
      $('episodes').setAttribute('aria-busy', 'false');
      if (!state.loaded) $('episodes').replaceChildren();
    } finally { state.refreshBusy = false; }
  }
  function openImport() {
    state.lastFocus = document.activeElement;
    showError('import-error', null);
    showDialog('import-dialog');
    $('feed-url').focus();
  }
  async function importFeed(event) {
    event.preventDefault();
    const button = $('import-submit');
    button.disabled = true;
    button.textContent = 'Adding podcast…';
    showError('import-error', null);
    try {
      const result = await api('/api/feeds', { method: 'POST', body: { url: $('feed-url').value.trim() } });
      $('import-dialog').close();
      $('feed-url').value = '';
      toast(`${result.added ?? 0} new episode${result.added === 1 ? '' : 's'} added to your library.`);
      await refreshLibrary();
    } catch (error) { showError('import-error', error); }
    finally { button.disabled = false; button.replaceChildren(document.createTextNode('Add to my library '), icon('arrow')); }
  }
  async function processEpisodes(ids, downloadOnly = false) {
    ids = ids.filter((id) => !ACTIVE.has(state.episodes.find((episode) => episode.id === id)?.status));
    if (!ids.length) {
      if (!state.episodes.length) openImport(); else toast('Your episodes are already prepared or in the queue.');
      return;
    }
    state.actionBusy = true;
    renderSelection();
    try {
      const result = await api('/api/process', { method: 'POST', body: { ids, remove_ads: true, ...(downloadOnly ? { download_only: true } : {}) } });
      const count = typeof result.queued === 'number' ? result.queued : ids.length;
      state.selected.clear();
      toast(count ? `${count} episode${count === 1 ? '' : 's'} queued ${downloadOnly ? 'for download' : 'for preparation'}.` : 'These episodes are already being prepared.');
      renderList();
      await refreshLibrary();
    } catch (error) { toast(error.message, true); }
    finally { state.actionBusy = false; renderSelection(); }
  }
  function queueSelectedOrAll() {
    const ids = state.selected.size ? [...state.selected] : state.episodes.filter((item) => !item.archived && ['available', 'downloaded', 'cancelled', 'error'].includes(item.status)).map((item) => item.id);
    return processEpisodes(ids);
  }
  async function openDetail(id) {
    if (state.detail?.id === id) {
      state.lastFocus = document.activeElement;
      showDialog('episode-dialog'); updateMiniPlayer(); drawWaveform(); return;
    }
    if (state.cutsDirty && !window.confirm('Discard your unsaved cut changes and open another episode?')) return;
    await persistPosition(true);
    state.lastFocus = document.activeElement;
    state.detail = state.episodes.find((episode) => episode.id === id) || { id };
    state.cuts = [];
    state.cutsDirty = false;
    state.audioVersion = state.detail.has_cleaned ? 'cleaned' : 'original';
    state.transcriptSignature = '';
    state.cutsSignature = '';
    state.waveform = null; state.waveformKey = ''; state.waveformBusy = false;
    state.markStart = null; state.revisions = [];
    state.lastPosition = null; state.lastPositionSave = 0;
    state.pendingSeek = seconds(state.detail.position); state.pendingPlay = false;
    state.transcriptVersion = state.audioVersion;
    state.cleanedTranscript = null; state.cleanedKey = ''; state.cleanedBusy = false;
    $('transcript-version').value = state.transcriptVersion;
    visible('cut-history', false); updateMarkControls();
    $('transcript-search').value = '';
    showError('detail-error', null);
    switchTab('transcript');
    renderDetail(true);
    showDialog('episode-dialog'); updateMiniPlayer();
    $('episode-dialog').scrollTop = 0;
    await refreshDetail(id);
  }
  async function refreshDetail(id) {
    const request = ++state.detailRequest;
    try {
      const result = await api(episodeURL(id));
      if (request !== state.detailRequest || !state.detail || state.detail.id !== id) return;
      state.detail = result.episode || result;
      renderDetail();
    } catch (error) { if (state.detail?.id === id && $('episode-dialog').open) showError('detail-error', error); }
  }
  function renderDetail(initial = false) {
    const episode = state.detail;
    if (!episode) return;
    if (initial) $('detail-cover').replaceChildren(cover(episode));
    $('detail-title').textContent = episode.title || 'Episode';
    $('detail-podcast').textContent = episode.podcast || 'Podcast';
    $('detail-meta').textContent = [date(episode.published), duration(episode.duration)].filter(Boolean).join(' · ');
    $('detail-status').replaceChildren(statusBadge(episode));
    $('detail-description').textContent = plainText(episode.description);
    visible('detail-description', Boolean(episode.description));
    if (episode.error) showError('detail-error', new Error(episode.error));
    else if (ACTIVE.has(episode.status)) showError('detail-error', null);
    const busy = ACTIVE.has(episode.status);
    visible('detail-progress', busy || Boolean(episode.progress));
    if (busy) {
      const status = STATUS[episode.status]?.[0] || 'Preparing episode';
      $('detail-progress').textContent = `${status}${typeof episode.progress === 'string' && episode.progress ? ` · ${episode.progress}` : ''}. You can keep browsing while this runs.`;
    } else $('detail-progress').textContent = episode.progress || '';
    $('detail-process').disabled = busy || state.actionBusy;
    $('detail-download').disabled = busy || state.actionBusy || episode.has_audio;
    visible('detail-cancel', busy);
    $('detail-cancel').disabled = state.actionBusy;
    $('detail-favorite').setAttribute('aria-pressed', String(Boolean(episode.favorite)));
    $('detail-favorite').classList.toggle('selected', Boolean(episode.favorite));
    $('detail-favorite').replaceChildren(icon('heart'), document.createTextNode(episode.favorite ? 'Favorited' : 'Favorite'));
    $('detail-played').setAttribute('aria-pressed', String(Boolean(episode.played)));
    $('detail-played').replaceChildren(icon('check'), document.createTextNode(episode.played ? 'Played' : 'Mark played'));
    $('detail-archive').setAttribute('aria-pressed', String(Boolean(episode.archived)));
    $('detail-archive').textContent = episode.archived ? 'Unarchive' : 'Archive';
    $('download-decisions').href = episodeURL(episode.id, '/decisions');
    visible('download-decisions', episode.has_transcript || (episode.cuts || []).length > 0);
    $('transcript-version').options[1].disabled = !episode.has_cleaned;
    if (!episode.has_cleaned && state.transcriptVersion === 'cleaned') { state.transcriptVersion = 'original'; $('transcript-version').value = 'original'; }
    $('mark-cut-start').disabled = busy || state.actionBusy || !episode.has_audio;
    $('mark-cut-end').disabled = busy || state.actionBusy || state.markStart === null || !episode.has_audio;
    const processLabel = episode.status === 'error' ? 'Retry preparation' : episode.has_transcript ? 'Prepare again' : 'Prepare episode';
    $('detail-process').replaceChildren(icon('spark'), document.createTextNode(busy ? 'Preparing…' : processLabel));
    const usesAI = state.settings.ai_configured && !['heuristic', 'Local rules'].includes(state.settings.detector);
    $('detail-preparation-note').textContent = usesAI ? 'Transcribe · Analyze context · Create cleaned audio' : 'Transcribe · Find ad cues · Review suggested cuts';
    $('processing-method').textContent = `Transcription: Whisper ${state.settings.transcription_model || 'base'} · Ad detection: ${usesAI ? 'contextual AI' : 'local rules; review suggestions carefully'}`;
    visible('detail-redetect', episode.has_transcript);
    $('detail-redetect').disabled = busy || state.actionBusy;
    $('render-cuts').disabled = !episode.has_audio || busy || state.actionBusy;
    $('save-cuts').disabled = busy || state.actionBusy;
    $('add-cut').disabled = busy;
    $('transcript-file').disabled = busy || state.actionBusy;
    if (!state.cutsDirty) {
      const cuts = episode.cuts || [];
      const signature = JSON.stringify(cuts);
      if (signature !== state.cutsSignature || initial) {
        state.cuts = cuts.map((cut) => ({ ...cut, approved: Boolean(cut.approved) }));
        state.cutsSignature = signature;
        renderCuts();
      }
    }
    $('detail-cut-count').textContent = state.cuts.length;
    for (const input of $('cut-list').querySelectorAll('input, button')) input.disabled = busy || state.actionBusy || (input.classList.contains('button') && !episode.has_audio);
    updatePlayer();
    renderTranscript();
    renderCutSummary();
    ensureWaveform();
    updateMiniPlayer();
  }
  function mergedCuts(source = state.detail?.cuts || []) {
    const cuts = source.filter((cut) => cut.approved && seconds(cut.end) > seconds(cut.start)).map((cut) => ({ start: seconds(cut.start), end: seconds(cut.end) })).sort((a, b) => a.start - b.start);
    const merged = [];
    for (const cut of cuts) {
      const previous = merged[merged.length - 1];
      if (previous && cut.start <= previous.end) previous.end = Math.max(previous.end, cut.end);
      else merged.push({ ...cut });
    }
    return merged;
  }
  function cleanTime(originalTime) {
    let removed = 0;
    for (const cut of mergedCuts()) {
      if (originalTime < cut.start) break;
      if (originalTime < cut.end) return Math.max(0, cut.start - removed);
      removed += cut.end - cut.start;
    }
    return Math.max(0, originalTime - removed);
  }
  function originalTime(cleanedTime) {
    let removed = 0;
    for (const cut of mergedCuts()) {
      if (cleanedTime < cut.start - removed) break;
      removed += cut.end - cut.start;
    }
    return cleanedTime + removed;
  }
  function updatePlayer() {
    const episode = state.detail;
    if (!episode) return;
    if (state.audioVersion === 'cleaned' && !episode.has_cleaned) state.audioVersion = 'original';
    const hasAudio = state.audioVersion === 'cleaned' ? episode.has_cleaned : episode.has_audio;
    $('play-cleaned').disabled = !episode.has_cleaned;
    $('play-original').disabled = !episode.has_audio;
    for (const variant of ['original', 'cleaned']) {
      $(`play-${variant}`).classList.toggle('active', state.audioVersion === variant);
      $(`play-${variant}`).setAttribute('aria-pressed', String(state.audioVersion === variant));
    }
    visible('audio-player', hasAudio);
    visible('player-empty', !hasAudio);
    visible('playback-tools', hasAudio);
    visible('waveform-section', hasAudio);
    visible('download-cleaned', episode.has_cleaned);
    $('download-cleaned').href = mediaURL(episode.id, 'cleaned');
    const src = hasAudio ? mediaURL(episode.id, state.audioVersion) : '';
    const audio = $('audio-player');
    if (audio.getAttribute('src') !== src) {
      if (src) {
        if (state.pendingSeek === null) { state.pendingSeek = audio.readyState >= 1 ? playhead() : seconds(episode.position); state.pendingPlay = !audio.paused; }
        audio.src = src;
      }
      else { audio.removeAttribute('src'); audio.load(); }
    }
  }
  function playhead() {
    const value = seconds($('audio-player').currentTime);
    return state.audioVersion === 'cleaned' ? originalTime(value) : value;
  }
  function switchAudio(version) {
    if (state.audioVersion === version || !state.detail) return;
    state.pendingSeek = playhead();
    state.pendingPlay = !$('audio-player').paused;
    state.audioVersion = version;
    state.transcriptVersion = version;
    $('transcript-version').value = version;
    updatePlayer(); renderTranscript(true); updateMiniPlayer();
  }
  function seek(time, original = false) {
    const audio = $('audio-player');
    if (!state.detail?.has_audio) return;
    if (original && state.audioVersion !== 'original') {
      state.pendingSeek = seconds(time); state.pendingPlay = true;
      state.audioVersion = 'original'; state.transcriptVersion = 'original'; $('transcript-version').value = 'original';
      updatePlayer(); renderTranscript(true);
    } else {
      audio.currentTime = Math.min(Number.isFinite(audio.duration) ? audio.duration : Infinity, state.audioVersion === 'cleaned' ? cleanTime(seconds(time)) : seconds(time));
      audio.play().catch(() => {});
    }
  }
  function transcriptSegments() {
    const transcript = state.transcriptVersion === 'cleaned' ? state.cleanedTranscript : state.detail?.transcript;
    return Array.isArray(transcript) ? transcript : transcript?.segments || [];
  }
  function renderTranscript(force = false) {
    const episode = state.detail;
    if (state.transcriptVersion === 'cleaned' && episode?.has_cleaned) ensureCleanedTranscript();
    const segments = transcriptSegments();
    const search = $('transcript-search').value.toLocaleLowerCase();
    const signature = JSON.stringify([segments, search, episode?.cuts, episode?.has_audio, state.transcriptVersion]);
    if (!force && state.transcriptSignature === signature) return;
    state.transcriptSignature = signature;
    visible('transcript-empty', !segments.length);
    visible('transcript-downloads', segments.length > 0);
    $('transcript-search').disabled = segments.length === 0;
    for (const format of ['txt', 'srt', 'vtt', 'json']) {
      $(`download-${format}`).href = episodeURL(episode.id, `/transcript?format=${format}&version=${state.transcriptVersion}`);
      $(`download-${format}`).setAttribute('aria-label', `Download ${state.transcriptVersion} transcript as ${format.toUpperCase()}`);
    }
    const lines = [];
    const cuts = mergedCuts();
    for (const segment of segments) {
      if (search && !String(segment.text || '').toLocaleLowerCase().includes(search)) continue;
      const line = el('div', 'transcript-line');
      const originalStart = state.transcriptVersion === 'cleaned' ? originalTime(seconds(segment.start)) : seconds(segment.start);
      const originalEnd = state.transcriptVersion === 'cleaned' ? originalTime(seconds(segment.end)) : seconds(segment.end);
      line.dataset.start = originalStart;
      line.dataset.end = originalEnd;
      line.classList.toggle('ad-segment', state.transcriptVersion === 'original' && cuts.some((cut) => seconds(segment.start) < cut.end && seconds(segment.end) > cut.start));
      const timestamp = el('button', 'timestamp', clock(segment.start));
      timestamp.setAttribute('aria-label', `Play from ${clock(segment.start)}`);
      timestamp.disabled = !episode.has_audio;
      timestamp.addEventListener('click', () => seek(originalStart));
      line.append(timestamp, el('p', '', String(segment.text || '').trim()));
      if (segment.partial) line.append(el('span', 'partial-label', 'Partial segment · text may include removed words'));
      lines.push(line);
    }
    if (!lines.length && search && segments.length) lines.push(el('p', 'muted', 'No matching transcript lines.'));
    $('transcript-lines').replaceChildren(...lines);
    if (state.transcriptVersion === 'cleaned' && state.cleanedBusy) { visible('transcript-empty', false); $('transcript-lines').textContent = 'Loading cleaned transcript…'; }
  }
  function switchTab(tab) {
    state.activeTab = tab;
    for (const name of ['transcript', 'cuts']) {
      const active = name === tab;
      $(`tab-${name}`).classList.toggle('active', active);
      $(`tab-${name}`).setAttribute('aria-selected', String(active));
      $(`tab-${name}`).tabIndex = active ? 0 : -1;
      visible(`panel-${name}`, active);
    }
  }
  function markCutsDirty() {
    state.cutsDirty = true;
    renderCutSummary(); drawWaveform();
  }
  function renderCutSummary() {
    const approved = state.cuts.filter((cut) => cut.approved);
    const secondsRemoved = mergedCuts(state.cuts).reduce((sum, cut) => sum + cut.end - cut.start, 0);
    $('cut-summary').textContent = `${approved.length} cut${approved.length === 1 ? '' : 's'} selected · ${clock(secondsRemoved)} to remove${state.cutsDirty ? ' · Unsaved changes' : ''}`;
    $('save-cuts').textContent = state.cutsDirty ? 'Save changes' : 'Save cuts';
    $('detail-cut-count').textContent = state.cuts.length;
    drawWaveform();
  }
  function renderCuts() {
    const rows = state.cuts.map((cut, index) => {
      const row = el('div', `cut-row${cut.approved ? '' : ' unapproved'}`);
      const top = el('div', 'cut-top');
      const label = el('label', 'cut-toggle');
      const checkbox = el('input');
      checkbox.type = 'checkbox';
      checkbox.checked = cut.approved;
      checkbox.addEventListener('change', () => { cut.approved = checkbox.checked; row.classList.toggle('unapproved', !cut.approved); markCutsDirty(); });
      label.append(checkbox, document.createTextNode(`Cut ${index + 1}`));
      const confidence = typeof cut.confidence === 'number' ? `${Math.round(cut.confidence * 100)}% confidence` : 'Manual cut';
      const remove = el('button', 'icon-button');
      remove.setAttribute('aria-label', `Delete cut ${index + 1}`);
      remove.append(icon('trash'));
      remove.addEventListener('click', () => { state.cuts.splice(index, 1); markCutsDirty(); renderCuts(); });
      top.append(label, el('span', 'cut-confidence', cut.source === 'manual' ? 'Manual cut' : confidence), remove);
      const times = el('div', 'cut-times');
      for (const key of ['start', 'end']) {
        const timeLabel = el('label', '', `${key === 'start' ? 'Start' : 'End'} · seconds`);
        const input = el('input');
        input.type = 'number'; input.min = '0'; input.step = '0.01'; input.required = true;
        input.value = Number.isFinite(Number(cut[key])) ? Number(cut[key]) : '';
        input.setAttribute('aria-label', `Cut ${index + 1} ${key} in seconds`);
        input.addEventListener('input', () => { cut[key] = input.value === '' ? null : Number(input.value); markCutsDirty(); });
        const setTime = el('button', 'set-playhead', 'Use playhead');
        setTime.type = 'button';
        setTime.setAttribute('aria-label', `Set cut ${index + 1} ${key} to playhead`);
        setTime.disabled = !state.detail?.has_audio;
        setTime.addEventListener('click', () => { cut[key] = Number(playhead().toFixed(2)); input.value = cut[key]; markCutsDirty(); });
        timeLabel.append(input, setTime); times.append(timeLabel);
      }
      const preview = el('button', 'button subtle');
      preview.append(icon('play'), document.createTextNode(' Preview'));
      preview.setAttribute('aria-label', `Preview cut ${index + 1} in original audio`);
      preview.disabled = !state.detail?.has_audio;
      preview.addEventListener('click', () => seek(Math.max(0, seconds(cut.start) - 2), true));
      times.append(preview);
      const reasonLabel = el('label', 'cut-reason-label', 'Reason');
      const reason = el('input'); reason.type = 'text'; reason.value = cut.reason || ''; reason.maxLength = 2000;
      reason.setAttribute('aria-label', `Reason for cut ${index + 1}`);
      reason.addEventListener('input', () => { cut.reason = reason.value; markCutsDirty(); });
      reasonLabel.append(reason);
      row.append(top, times, reasonLabel);
      return row;
    });
    $('cut-list').replaceChildren(...rows);
    visible('cuts-empty', !rows.length);
    renderCutSummary();
  }
  function validateCuts() {
    for (const [index, cut] of state.cuts.entries()) {
      if (cut.start === null || cut.end === null || !Number.isFinite(Number(cut.start)) || !Number.isFinite(Number(cut.end)) || Number(cut.start) < 0 || Number(cut.end) <= Number(cut.start)) throw new Error(`Cut ${index + 1} needs a valid start and a later end, in seconds.`);
      if (state.detail.duration && Number(cut.end) > Number(state.detail.duration) + 0.1) throw new Error(`Cut ${index + 1} extends beyond the end of the episode.`);
    }
    return state.cuts.map((cut) => ({ start: Number(cut.start), end: Number(cut.end), approved: Boolean(cut.approved), confidence: Number.isFinite(cut.confidence) ? cut.confidence : 1, reason: cut.reason || 'Manual cut', source: cut.source || 'manual' }));
  }
  async function saveCuts(silent = false) {
    const cuts = validateCuts();
    const result = await api(episodeURL(state.detail.id, '/cuts'), { method: 'POST', body: { cuts } });
    state.cutsDirty = false;
    state.cutsSignature = '';
    state.cleanedKey = ''; state.cleanedTranscript = null;
    if (result.id || result.episode) state.detail = result.episode || result;
    else state.detail.cuts = cuts;
    renderDetail();
    if (!$('cut-history').hidden) loadCutHistory();
    if (!silent) toast('Cuts saved. Export cleaned audio to apply them.');
  }
  async function detailAction(action) {
    if (state.actionBusy) return;
    state.actionBusy = true;
    showError('detail-error', null);
    renderDetail();
    try {
      if (action === 'save') await saveCuts();
      if (action === 'render') {
        await saveCuts(true);
        await api(episodeURL(state.detail.id, '/render'), { method: 'POST', body: {} });
        toast('Creating your cleaned audio. It will be available here shortly.');
      }
      if (action === 'process') {
        if (state.cutsDirty) await saveCuts(true);
        await api(episodeURL(state.detail.id, '/process'), { method: 'POST', body: { remove_ads: true } });
        toast('Episode queued for preparation.');
      }
      if (action === 'download') {
        await api(episodeURL(state.detail.id, '/process'), { method: 'POST', body: { download_only: true } });
        toast('Audio download queued.');
      }
      if (action === 'cancel') {
        await api(episodeURL(state.detail.id, '/cancel'), { method: 'POST', body: {} });
        toast('Cancellation requested.');
      }
      if (action === 'redetect') {
        await api(episodeURL(state.detail.id, '/process'), { method: 'POST', body: { remove_ads: true, redetect: true } });
        state.cutsDirty = false;
        state.cutsSignature = '';
        toast('Finding ads again. New suggestions will replace the previous cuts.');
      }
      await refreshLibrary();
    } catch (error) { showError('detail-error', error); }
    finally { state.actionBusy = false; renderDetail(); }
  }
  async function importTranscript(event) {
    const file = event.target.files[0];
    if (!file || !state.detail) return;
    if ((state.detail.has_transcript || state.cuts.length || state.cutsDirty) && !window.confirm('Replace this transcript? Current cuts and the cleaned audio will be reset. Your original audio is preserved.')) { event.target.value = ''; return; }
    state.actionBusy = true;
    renderDetail();
    showError('detail-error', null);
    try {
      if (file.size > 20 * 1024 * 1024) throw new Error('Please choose a transcript smaller than 20 MB.');
      const payload = JSON.parse(await file.text());
      const segments = Array.isArray(payload) ? payload : payload.segments;
      if (!Array.isArray(segments)) throw new Error('Expected a JSON object with a segments array containing start, end, and text.');
      await api(episodeURL(state.detail.id, '/transcript'), { method: 'POST', body: { segments, language: payload.language || 'unknown', ...(payload.duration === undefined ? {} : { duration: payload.duration }) } });
      state.cutsDirty = false;
      state.cutsSignature = '';
      toast('Transcript imported. Prepare the episode to find advertisements.');
      await refreshLibrary();
    } catch (error) { showError('detail-error', error); }
    finally { event.target.value = ''; state.actionBusy = false; renderDetail(); }
  }

  function showDialog(id) {
    const dialog = $(id);
    dialog._returnFocus = document.activeElement;
    if (!dialog.open) dialog.showModal();
    $('add-menu').hidden = true; $('add-more').setAttribute('aria-expanded', 'false');
  }
  async function updateEpisodeState(id, changes) {
    const result = await api(episodeURL(id, '/state'), { method: 'PATCH', body: changes });
    const record = result.episode || result;
    const index = state.episodes.findIndex((episode) => episode.id === id);
    if (index !== -1) state.episodes[index] = { ...state.episodes[index], ...record };
    if (state.detail?.id === id) { state.detail = { ...state.detail, ...record }; renderDetail(); }
    renderList();
    return record;
  }
  async function persistPosition(force = false) {
    const audio = $('audio-player');
    if (!state.detail?.has_audio || !audio.getAttribute('src') || audio.readyState < 1 || state.pendingSeek !== null) return;
    const now = Date.now();
    const position = Number(playhead().toFixed(2));
    if ((!force && (now - state.lastPositionSave < 5000 || audio.paused)) || state.lastPosition === position) return;
    const id = state.detail.id;
    state.lastPositionSave = now; state.lastPosition = position;
    try {
      await api(episodeURL(id, '/state'), { method: 'PATCH', body: { position }, keepalive: force });
      const episode = state.episodes.find((item) => item.id === id);
      if (episode) episode.position = position;
      if (state.detail?.id === id) state.detail.position = position;
    } catch (error) {
      state.lastPosition = null;
      if (!state.progressWarning) { state.progressWarning = true; toast(`Playback progress could not be saved. ${error.message}`, true); }
    }
  }
  function updateMiniPlayer() {
    const episode = state.detail;
    const show = Boolean(episode?.has_audio && $('audio-player').getAttribute('src') && !$('episode-dialog').open);
    visible('mini-player', show);
    document.body.classList.toggle('has-mini-player', show);
    if (!episode) { if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'none'; return; }
    updateMediaSession();
    $('mini-title').textContent = episode.title || 'Episode';
    $('mini-meta').textContent = `${episode.podcast || 'Podcast'} · ${clock(playhead())} · ${state.audioVersion === 'cleaned' ? 'Cleaned audio' : 'Original audio'}`;
    const paused = $('audio-player').paused;
    $('mini-toggle').replaceChildren(icon(paused ? 'play' : 'pause'));
    $('mini-toggle').setAttribute('aria-label', paused ? 'Play' : 'Pause');
  }
  function updateMediaSession() {
    if (!('mediaSession' in navigator) || !state.detail) return;
    const audio = $('audio-player'), episode = state.detail;
    try {
      if (state.mediaSessionId !== episode.id && 'MediaMetadata' in window) {
        const metadata = { title: episode.title || 'Episode', artist: episode.podcast || 'Castwell', album: 'Castwell listening library' };
        if (episode.image) {
          const image = new URL(episode.image, location.origin);
          if (['https:', 'http:'].includes(image.protocol)) metadata.artwork = [{ src: image.href }];
        }
        navigator.mediaSession.metadata = new MediaMetadata(metadata);
        state.mediaSessionId = episode.id;
      }
      navigator.mediaSession.playbackState = audio.paused ? 'paused' : 'playing';
      if ('setPositionState' in navigator.mediaSession && Number.isFinite(audio.duration) && audio.duration > 0) navigator.mediaSession.setPositionState({ duration: audio.duration, playbackRate: audio.playbackRate, position: Math.min(audio.duration, Math.max(0, audio.currentTime)) });
    } catch (_) { /* Browser media controls are optional. */ }
  }
  if ('mediaSession' in navigator) {
    const handlers = {
      play: () => $('audio-player').play().catch(() => {}), pause: () => $('audio-player').pause(),
      seekbackward: (event) => skipAudio(-(event.seekOffset || 15)), seekforward: (event) => skipAudio(event.seekOffset || 15),
      seekto: (event) => { if (Number.isFinite(event.seekTime)) { $('audio-player').currentTime = event.seekTime; persistPosition(true); } },
    };
    for (const [action, handler] of Object.entries(handlers)) { try { navigator.mediaSession.setActionHandler(action, handler); } catch (_) {} }
  }
  function togglePlayback() {
    const audio = $('audio-player');
    if (!state.detail?.has_audio) return;
    if (audio.paused) audio.play().catch((error) => toast(`Couldn’t play audio. ${error.message}`, true)); else audio.pause();
  }
  function skipAudio(delta) {
    const audio = $('audio-player');
    if (!state.detail?.has_audio) return;
    audio.currentTime = Math.max(0, Math.min(Number.isFinite(audio.duration) ? audio.duration : Infinity, audio.currentTime + delta));
    persistPosition(true);
  }
  async function refreshQueue() {
    try {
      const payload = await api('/api/jobs');
      const activeJobs = payload.jobs || [];
      const listed = new Set(activeJobs.map((job) => job.episode_id));
      const attention = state.episodes.filter((episode) => !episode.archived && ['error', 'cancelled'].includes(episode.status) && !listed.has(episode.id));
      state.jobs = [...activeJobs, ...attention.map((episode) => ({ episode_id: episode.id, title: episode.title, status: episode.status, progress: episode.error || episode.progress }))];
      const active = state.jobs.filter((job) => ACTIVE.has(job.status));
      visible('queue-panel', state.jobs.length > 0);
      if (active.length && !state.lastQueueActive) $('queue-panel').open = true;
      state.lastQueueActive = active.length;
      $('queue-title').textContent = active.length ? 'Preparing your next listen' : 'Preparation needs attention';
      $('queue-count').textContent = active.length ? `${active.length} active` : `${state.jobs.length} episode${state.jobs.length === 1 ? '' : 's'}`;
      $('queue-panel').classList.toggle('idle', !active.length);
      const signature = JSON.stringify(state.jobs);
      if (signature === state.jobsSignature) return;
      state.jobsSignature = signature;
      $('queue-jobs').replaceChildren(...state.jobs.slice(0, 15).map((job) => {
        const row = el('div', 'queue-job');
        const content = el('div', 'queue-job-content');
        const title = el('button', 'text-button queue-job-title', job.title || 'Episode');
        title.addEventListener('click', () => openDetail(job.episode_id));
        const info = [STATUS[job.status]?.[0] || job.status, typeof job.progress === 'string' ? job.progress : '', job.status === 'queued' && job.position ? `Queue position ${job.position}` : ''].filter(Boolean).join(' · ');
        content.append(title, el('p', '', info)); row.append(content);
        if (ACTIVE.has(job.status) || ['error', 'cancelled'].includes(job.status)) {
          const activeJob = ACTIVE.has(job.status);
          const action = el('button', 'button subtle', activeJob ? 'Cancel' : 'Retry');
          action.addEventListener('click', async () => {
            action.disabled = true;
            try {
              if (activeJob) await api(episodeURL(job.episode_id, '/cancel'), { method: 'POST', body: {} });
              else await api(episodeURL(job.episode_id, '/process'), { method: 'POST', body: { remove_ads: true } });
              await refreshLibrary();
            } catch (error) { toast(error.message, true); action.disabled = false; }
          });
          row.append(action);
        }
        return row;
      }));
    } catch (_) { /* The library remains usable if queue reporting is temporarily unavailable. */ }
  }
  async function ensureWaveform() {
    const episode = state.detail;
    if (!episode?.has_audio || state.waveformBusy || state.waveformKey === episode.id) return;
    const id = episode.id;
    state.waveformKey = id; state.waveformBusy = true;
    $('waveform-notice').textContent = 'Preparing waveform…';
    try {
      const waveform = await api(episodeURL(id, '/waveform'));
      if (state.detail?.id !== id) return;
      state.waveform = waveform;
      $('waveform-notice').textContent = 'Original timeline · click the waveform to seek. Arrow keys move 5 seconds.';
      drawWaveform();
    } catch (error) {
      if (state.detail?.id === id) $('waveform-notice').textContent = `Waveform unavailable. ${error.message}`;
    } finally { if (state.detail?.id === id) state.waveformBusy = false; }
  }
  function drawWaveform() {
    const canvas = $('audio-waveform');
    if (!canvas || !state.detail?.has_audio || !$('episode-dialog').open) return;
    const width = Math.max(1, canvas.getBoundingClientRect().width);
    const height = 90;
    const scale = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width * scale) || canvas.height !== height * scale) { canvas.width = Math.round(width * scale); canvas.height = height * scale; }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(scale, 0, 0, scale, 0, 0); ctx.clearRect(0, 0, width, height);
    const total = seconds(state.waveform?.duration || state.detail.duration);
    if (!total) return;
    const time = playhead();
    const x = (value) => Math.min(width, Math.max(0, seconds(value) / total * width));
    const peaks = state.waveform?.peaks || [];
    ctx.fillStyle = '#3d4b5a'; ctx.fillRect(0, height / 2, width, 1);
    const count = Math.ceil(width / 3);
    for (let i = 0; i < count; i++) {
      const from = Math.floor(i / count * peaks.length), to = Math.max(from + 1, Math.floor((i + 1) / count * peaks.length));
      let peak = 0;
      for (let j = from; j < Math.min(to, peaks.length); j++) peak = Math.max(peak, Math.abs(Number(peaks[j]) || 0));
      const bar = Math.max(2, Math.min(1, peak) * 68);
      ctx.fillStyle = i / count * total <= time ? '#a4c7b1' : '#526961';
      if (peaks.length) ctx.fillRect(i * 3, (height - bar) / 2, 2, bar);
    }
    for (const cut of state.cuts) {
      const left = x(cut.start), right = x(cut.end);
      if (right <= left) continue;
      ctx.fillStyle = cut.approved ? '#ff9c7233' : '#c6b7751d'; ctx.fillRect(left, 5, right - left, height - 10);
      ctx.strokeStyle = cut.approved ? '#ffad84' : '#c6b775'; ctx.setLineDash(cut.approved ? [] : [3, 3]);
      ctx.strokeRect(left + 0.5, 5.5, Math.max(0, right - left - 1), height - 11);
    }
    ctx.setLineDash([]);
    if (state.markStart !== null) { ctx.strokeStyle = '#d7c78d'; ctx.beginPath(); ctx.moveTo(x(state.markStart), 0); ctx.lineTo(x(state.markStart), height); ctx.stroke(); }
    ctx.strokeStyle = '#fff1d8'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(x(time), 0); ctx.lineTo(x(time), height); ctx.stroke(); ctx.lineWidth = 1;
    $('waveform-position').textContent = clock(time);
    $('waveform-duration').textContent = clock(total);
    canvas.setAttribute('aria-valuemax', String(Math.round(total)));
    canvas.setAttribute('aria-valuenow', String(Math.round(time)));
    canvas.setAttribute('aria-valuetext', `${clock(time)} of ${clock(total)}`);
  }
  function updateMarkControls() {
    $('mark-cut-end').disabled = state.markStart === null || !state.detail?.has_audio || ACTIVE.has(state.detail?.status);
    $('mark-cut-start').textContent = state.markStart === null ? 'Mark start' : 'Reset start';
    $('mark-cut-label').textContent = state.markStart === null ? 'Mark an ad while listening.' : `Starts at ${clock(state.markStart)} · mark the end next.`;
    drawWaveform();
  }
  async function loadCutHistory() {
    if (!state.detail) return;
    const id = state.detail.id;
    visible('cut-history', true); $('cut-history').textContent = 'Loading saved revisions…';
    try {
      const result = await api(episodeURL(id, '/revisions'));
      if (state.detail?.id !== id) return;
      state.revisions = result.revisions || [];
      $('cut-history').replaceChildren(el('h3', '', 'Saved cut history'));
      if (!state.revisions.length) $('cut-history').append(el('p', 'muted', 'Saved edits will appear here.'));
      for (const revision of state.revisions) {
        const row = el('div', 'revision-row');
        const revisionTime = new Date(revision.created_at);
        const label = el('div', '', revision.reason || 'Saved cuts');
        label.append(el('small', '', `${Number.isNaN(revisionTime.getTime()) ? '' : revisionTime.toLocaleString()} · ${(revision.cuts || []).length} cuts`));
        const restore = el('button', 'text-button', 'Restore');
        restore.disabled = ACTIVE.has(state.detail.status);
        restore.addEventListener('click', async () => {
          if (!window.confirm('Restore this saved cut revision? Current cuts will be replaced, and you can export a new cleaned audio copy.')) return;
          restore.disabled = true;
          try {
            await api(episodeURL(id, `/revisions/${encodeURIComponent(revision.id)}/restore`), { method: 'POST', body: {} });
            state.cutsDirty = false; state.cutsSignature = ''; state.cleanedKey = ''; state.cleanedTranscript = null;
            await refreshLibrary(); await loadCutHistory();
            toast('Cut revision restored. Export cleaned audio to apply it.');
          } catch (error) { showError('detail-error', error); restore.disabled = false; }
        });
        row.append(label, restore); $('cut-history').append(row);
      }
    } catch (error) { $('cut-history').textContent = error.message; }
  }
  async function ensureCleanedTranscript() {
    const episode = state.detail;
    if (!episode?.has_cleaned) return;
    const key = JSON.stringify([episode.id, episode.cuts, episode.transcript]);
    if (state.cleanedBusy || state.cleanedKey === key) return;
    state.cleanedKey = key; state.cleanedBusy = true; state.cleanedTranscript = null;
    try {
      const transcript = await api(episodeURL(episode.id, '/transcript?format=json&version=cleaned'));
      if (state.detail?.id !== episode.id || state.cleanedKey !== key) return;
      state.cleanedTranscript = transcript;
    } catch (error) { if (state.detail?.id === episode.id) showError('detail-error', error); }
    finally {
      if (state.detail?.id === episode.id) { state.cleanedBusy = false; renderTranscript(true); }
    }
  }
  async function openSubscriptions() {
    showDialog('subscriptions-dialog'); await refreshFeeds();
  }
  async function refreshFeeds() {
    showError('subscriptions-error', null);
    try { const result = await api('/api/feeds'); state.feeds = result.feeds || []; renderFeeds(); }
    catch (error) { showError('subscriptions-error', error); }
  }
  function renderFeeds() {
    $('refresh-all-feeds').disabled = !state.feeds.length || state.feedBusy.size > 0;
    if (!state.feeds.length) { $('subscription-list').replaceChildren(el('p', 'panel-empty', 'No subscriptions yet. Add an RSS feed or import your podcast app’s OPML file.')); return; }
    $('subscription-list').replaceChildren(...state.feeds.map((feed) => {
      const row = el('article', 'subscription-row');
      const details = el('div', 'subscription-info');
      details.append(el('h3', '', feed.title || 'Podcast'), el('p', 'feed-url', feed.url_display || ''), el('small', '', `${feed.episode_count ?? 0} episodes${feed.last_checked ? ` · Checked ${date(feed.last_checked)}` : ''}`));
      if (feed.error) details.append(el('p', 'feed-error', feed.error));
      const actions = el('div', 'subscription-actions');
      const refresh = el('button', 'button subtle', state.feedBusy.has(feed.id) ? 'Refreshing…' : 'Refresh');
      refresh.disabled = state.feedBusy.has(feed.id);
      refresh.addEventListener('click', () => refreshFeed(feed.id));
      const remove = el('button', 'text-button', 'Unsubscribe');
      remove.disabled = state.feedBusy.has(feed.id);
      remove.addEventListener('click', async () => {
        if (!window.confirm(`Unsubscribe from ${feed.title || 'this podcast'}? Episodes already in your library will stay available.`)) return;
        remove.disabled = true;
        try { await api(`/api/feeds/${encodeURIComponent(feed.id)}`, { method: 'DELETE' }); await refreshFeeds(); toast('Subscription removed. Your episodes are still in the library.'); }
        catch (error) { showError('subscriptions-error', error); remove.disabled = false; }
      });
      actions.append(refresh, remove); row.append(details, actions); return row;
    }));
  }
  async function refreshFeed(id, silent = false) {
    if (state.feedBusy.has(id)) return;
    state.feedBusy.add(id); renderFeeds();
    try {
      const result = await api(`/api/feeds/${encodeURIComponent(id)}/refresh`, { method: 'POST', body: {} });
      if (!silent) toast(`${result.added || 0} new episode${result.added === 1 ? '' : 's'} collected.`);
      return result.added || 0;
    } catch (error) { showError('subscriptions-error', error); return 0; }
    finally { state.feedBusy.delete(id); await refreshFeeds(); if (!silent) await refreshLibrary(); }
  }
  async function uploadAudio(event) {
    event.preventDefault();
    const file = $('audio-upload').files[0];
    if (!file) return;
    const body = new FormData(); body.append('file', file);
    $('upload-submit').disabled = true; $('upload-submit').textContent = 'Uploading audio…'; showError('upload-error', null);
    try {
      const result = await api('/api/audio', { method: 'POST', body });
      $('upload-dialog').close(); $('upload-form').reset();
      toast('Audio added. Prepare the episode to transcribe it and find ads.');
      await refreshLibrary(); const episode = result.episode || result;
      if (episode.id) await openDetail(episode.id);
    } catch (error) { showError('upload-error', error); }
    finally { $('upload-submit').disabled = false; $('upload-submit').textContent = 'Add to library'; }
  }
  async function importOPML(event) {
    event.preventDefault();
    const file = $('opml-file').files[0];
    if (!file) return;
    $('opml-submit').disabled = true; $('opml-submit').textContent = 'Importing subscriptions…'; showError('opml-error', null); visible('opml-result', false);
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error('Choose an OPML file smaller than 2 MB.');
      const result = await api('/api/feeds/opml', { method: 'POST', body: { xml: await file.text() } });
      $('opml-result').replaceChildren(el('p', '', `${result.imported || 0} subscriptions imported · ${result.added || 0} new episodes.`));
      for (const error of result.errors || []) $('opml-result').append(el('p', 'feed-error', `${error.title || 'Feed'}: ${error.error}`));
      visible('opml-result', true);
      if (!result.errors?.length) { $('opml-dialog').close(); toast(`${result.imported || 0} subscriptions imported.`); $('opml-form').reset(); }
      await refreshLibrary();
    } catch (error) { showError('opml-error', error); }
    finally { $('opml-submit').disabled = false; $('opml-submit').textContent = 'Import subscriptions'; }
  }
  const settingFields = { transcription_model: 'setting-model', language: 'setting-language', detector: 'setting-detector', auto_approve_threshold: 'setting-threshold', review_only: 'setting-review-only', ai_base_url: 'setting-ai-base', ai_model: 'setting-ai-model' };
  async function openSettings() {
    showDialog('settings-dialog'); showError('settings-error', null); $('save-settings').disabled = true;
    try {
      const settings = await api('/api/settings');
      state.settings = { ...state.settings, ...settings }; state.settingsLoaded = true;
      const defaults = { transcription_model: 'base', language: '', detector: 'auto', auto_approve_threshold: 0.9, review_only: false, ai_base_url: '', ai_model: '' };
      for (const [key, id] of Object.entries(settingFields)) {
        const input = $(id), value = settings[key] ?? defaults[key];
        if (input.type === 'checkbox') input.checked = Boolean(value);
        else {
          if (input.tagName === 'SELECT' && ![...input.options].some((option) => option.value === String(value))) input.add(new Option(String(value), String(value)));
          input.value = value;
        }
        input.disabled = (settings.env_overrides || []).includes(key);
        input.title = input.disabled ? 'Set by the server environment' : '';
      }
      $('ai-key-status').textContent = settings.key_configured ? 'An API key is securely configured on the server.' : 'No server API key configured. Local AI servers may not need one. For a hosted service, set CASTWELL_AI_KEY in the server environment.';
      $('ai-configured-indicator').textContent = settings.ai_base_url && settings.ai_model ? 'Connected settings' : 'Optional';
      $('settings-overrides').textContent = settings.env_overrides?.length ? 'Some settings are managed by the server environment.' : 'Settings are stored locally on this server.';
      $('save-settings').disabled = false;
      await Promise.allSettled([loadDiagnostics(), pollModelStatus()]);
    } catch (error) { showError('settings-error', error); }
  }
  async function saveSettings(showToast = true) {
    if (!$('settings-form').reportValidity()) throw new Error('Check the highlighted settings before continuing.');
    const changes = {};
    for (const [key, id] of Object.entries(settingFields)) {
      const input = $(id); if (input.disabled) continue;
      changes[key] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value.trim();
    }
    const result = await api('/api/settings', { method: 'PATCH', body: changes });
    state.settings = { ...state.settings, ...(result.settings || result) };
    if (showToast) toast('Settings saved. They’ll apply to the next processing job.');
    renderDetail(); return result;
  }
  async function loadDiagnostics() {
    $('refresh-diagnostics').disabled = true;
    try {
      const result = await api('/api/diagnostics');
      $('diagnostics-list').replaceChildren(...(result.checks || []).map((check) => {
        const item = el('div', `diagnostic ${check.status}`);
        const mark = el('span', 'diagnostic-mark', check.status === 'ok' ? '✓' : '!');
        mark.setAttribute('aria-label', check.status);
        const content = el('div'); content.append(el('strong', '', check.label), el('p', '', check.detail));
        item.append(mark, content); return item;
      }));
    } catch (error) { $('diagnostics-list').textContent = error.message; }
    finally { $('refresh-diagnostics').disabled = false; }
  }
  async function pollModelStatus() {
    try {
      const status = await api('/api/models/status');
      state.modelPreparing = ['queued', 'preparing', 'downloading', 'loading', 'running'].includes(status.status);
      $('prepare-model').disabled = state.modelPreparing;
      $('model-status').textContent = status.error || status.progress || (status.status === 'ready' ? 'Model downloaded and ready for transcription.' : 'Prepare the selected model for local transcription.');
      $('model-status').classList.toggle('has-error', Boolean(status.error));
      window.clearTimeout(state.modelTimer);
      if (state.modelPreparing) state.modelTimer = window.setTimeout(pollModelStatus, 2000);
    } catch (error) { $('model-status').textContent = error.message; $('prepare-model').disabled = false; state.modelPreparing = false; }
  }

  $('add-more').addEventListener('click', () => { const show = $('add-menu').hidden; visible('add-menu', show); $('add-more').setAttribute('aria-expanded', String(show)); if (show) $('upload-button').focus(); });
  document.addEventListener('click', (event) => { if (!event.target.closest('.add-menu-wrap')) { visible('add-menu', false); $('add-more').setAttribute('aria-expanded', 'false'); } });
  $('add-menu').addEventListener('keydown', (event) => { if (event.key === 'Escape') { visible('add-menu', false); $('add-more').setAttribute('aria-expanded', 'false'); $('add-more').focus(); event.stopPropagation(); } });
  for (const button of document.querySelectorAll('[data-close]')) button.addEventListener('click', () => $(button.dataset.close).close());
  for (const dialog of document.querySelectorAll('dialog:not(#episode-dialog)')) dialog.addEventListener('close', () => dialog._returnFocus?.focus());
  for (const id of ['sidebar-subscriptions', 'mobile-subscriptions']) $(id).addEventListener('click', openSubscriptions);
  for (const id of ['sidebar-settings', 'mobile-settings']) $(id).addEventListener('click', openSettings);
  $('subscriptions-add').addEventListener('click', () => { $('subscriptions-dialog').close(); openImport(); });
  $('refresh-all-feeds').addEventListener('click', async () => {
    let added = 0;
    for (const feed of [...state.feeds]) added += await refreshFeed(feed.id, true);
    toast(`${added} new episode${added === 1 ? '' : 's'} collected.`); await refreshLibrary();
  });
  $('upload-button').addEventListener('click', () => { showError('upload-error', null); showDialog('upload-dialog'); });
  $('upload-form').addEventListener('submit', uploadAudio);
  $('import-opml-button').addEventListener('click', () => { showError('opml-error', null); visible('opml-result', false); showDialog('opml-dialog'); });
  $('opml-form').addEventListener('submit', importOPML);
  $('podcast-filter').addEventListener('change', (event) => { state.podcast = event.target.value; renderList(); });
  $('library-sort').addEventListener('change', (event) => { state.sort = event.target.value; renderList(); });
  $('download-selection').addEventListener('click', () => processEpisodes([...state.selected], true));
  $('detail-download').addEventListener('click', () => detailAction('download'));
  $('detail-cancel').addEventListener('click', () => detailAction('cancel'));
  for (const [id, field] of [['detail-favorite', 'favorite'], ['detail-played', 'played'], ['detail-archive', 'archived']]) $(id).addEventListener('click', async () => {
    if (!state.detail) return;
    $(id).disabled = true;
    try { await updateEpisodeState(state.detail.id, { [field]: !state.detail[field] }); }
    catch (error) { showError('detail-error', error); }
    finally { $(id).disabled = false; }
  });
  $('skip-back').addEventListener('click', () => skipAudio(-15));
  $('skip-forward').addEventListener('click', () => skipAudio(15));
  $('mini-back').addEventListener('click', () => skipAudio(-15));
  $('mini-toggle').addEventListener('click', togglePlayback);
  $('mini-open').addEventListener('click', () => { if (state.detail) openDetail(state.detail.id); });
  $('mini-dismiss').addEventListener('click', async () => { $('audio-player').pause(); await persistPosition(true); $('audio-player').removeAttribute('src'); $('audio-player').load(); state.detail = null; state.detailRequest++; updateMiniPlayer(); });
  try { const savedSpeed = localStorage.getItem('castwell.playbackSpeed') ?? localStorage.getItem('podgrab.playbackSpeed'); if ([...$('playback-speed').options].some((option) => option.value === savedSpeed)) $('playback-speed').value = savedSpeed; }
  catch (_) { /* Private browsing can disable storage. */ }
  $('playback-speed').addEventListener('change', () => { $('audio-player').playbackRate = Number($('playback-speed').value); try { localStorage.setItem('castwell.playbackSpeed', $('playback-speed').value); } catch (_) {} });
  $('audio-waveform').addEventListener('click', (event) => { const rect = event.currentTarget.getBoundingClientRect(); seek((event.clientX - rect.left) / rect.width * seconds(state.waveform?.duration || state.detail?.duration)); });
  $('audio-waveform').addEventListener('keydown', (event) => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) { event.preventDefault(); event.stopPropagation(); seek(event.key === 'Home' ? 0 : event.key === 'End' ? seconds(state.detail?.duration) : Math.max(0, playhead() + (event.key === 'ArrowLeft' ? -5 : 5))); }
  });
  if ('ResizeObserver' in window) new ResizeObserver(drawWaveform).observe($('audio-waveform'));
  $('mark-cut-start').addEventListener('click', () => { state.markStart = Number(playhead().toFixed(2)); updateMarkControls(); });
  $('mark-cut-end').addEventListener('click', () => {
    const end = Number(playhead().toFixed(2));
    if (state.markStart === null || end <= state.markStart) { toast('Move the playhead after the marked start, then mark the end.', true); return; }
    state.cuts.push({ start: state.markStart, end, reason: 'Manual cut', confidence: 1, approved: true, source: 'manual' });
    state.markStart = null; markCutsDirty(); renderCuts(); updateMarkControls();
  });
  $('cut-history-button').addEventListener('click', () => { if ($('cut-history').hidden) loadCutHistory(); else visible('cut-history', false); });
  $('transcript-version').addEventListener('change', () => { state.transcriptVersion = $('transcript-version').value; renderTranscript(true); });
  document.addEventListener('keydown', (event) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.target.closest('input, textarea, select, button, a, audio, summary, [contenteditable=true], [role=tab]') || [...document.querySelectorAll('dialog[open]')].some((dialog) => dialog.id !== 'episode-dialog') || !state.detail?.has_audio) return;
    if (event.code === 'Space') { event.preventDefault(); togglePlayback(); }
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); skipAudio(event.key === 'ArrowLeft' ? -15 : 15); }
  });
  $('settings-form').addEventListener('submit', async (event) => {
    event.preventDefault(); $('save-settings').disabled = true; showError('settings-error', null);
    try { await saveSettings(); await loadDiagnostics(); }
    catch (error) { showError('settings-error', error); }
    finally { $('save-settings').disabled = false; }
  });
  $('prepare-model').addEventListener('click', async () => {
    $('prepare-model').disabled = true; showError('settings-error', null);
    try { await saveSettings(false); await api('/api/models/prepare', { method: 'POST', body: {} }); state.modelPreparing = true; await pollModelStatus(); }
    catch (error) { showError('settings-error', error); $('prepare-model').disabled = false; }
  });
  $('test-classifier').addEventListener('click', async () => {
    $('test-classifier').disabled = true; $('classifier-result').textContent = 'Testing the classifier…'; showError('settings-error', null);
    try { await saveSettings(false); const result = await api('/api/settings/test-classifier', { method: 'POST', body: {} }); $('classifier-result').textContent = result.message || (result.ok ? 'Classifier is responding.' : 'The classifier did not pass the test.'); $('classifier-result').classList.toggle('has-error', !result.ok); await loadDiagnostics(); }
    catch (error) { $('classifier-result').textContent = error.message; $('classifier-result').classList.add('has-error'); }
    finally { $('test-classifier').disabled = false; }
  });
  $('refresh-diagnostics').addEventListener('click', loadDiagnostics);

  for (const id of ['import-button', 'sidebar-import', 'empty-import']) $(id).addEventListener('click', openImport);
  $('close-import').addEventListener('click', () => $('import-dialog').close());
  $('import-form').addEventListener('submit', importFeed);
  $('import-dialog').addEventListener('close', () => state.lastFocus?.focus());
  $('hero-process').addEventListener('click', queueSelectedOrAll);
  $('process-button').addEventListener('click', queueSelectedOrAll);
  $('library-search').addEventListener('input', (event) => { state.search = event.target.value; renderList(); });
  $('clear-selection').addEventListener('click', () => { state.selected.clear(); renderList(); });
  for (const tab of document.querySelectorAll('[data-filter]')) tab.addEventListener('click', () => {
    state.filter = tab.dataset.filter;
    for (const button of document.querySelectorAll('[data-filter]')) {
      button.classList.toggle('active', button === tab);
      button.setAttribute('aria-pressed', String(button === tab));
    }
    renderList();
  });
  function closeDetail() {
    if (state.actionBusy) return;
    if (state.cutsDirty && !window.confirm('Discard unsaved cut changes? Your saved cuts will be kept.')) return;
    state.cutsDirty = false; state.cutsSignature = ''; renderDetail();
    $('episode-dialog').close();
  }
  $('close-detail').addEventListener('click', closeDetail);
  $('episode-dialog').addEventListener('cancel', (event) => { event.preventDefault(); closeDetail(); });
  $('episode-dialog').addEventListener('close', () => {
    persistPosition(true);
    updateMiniPlayer();
    state.lastFocus?.focus();
  });
  $('play-original').addEventListener('click', () => switchAudio('original'));
  $('play-cleaned').addEventListener('click', () => switchAudio('cleaned'));
  $('detail-process').addEventListener('click', () => detailAction('process'));
  $('detail-redetect').addEventListener('click', () => {
    if ((state.cuts.length || state.cutsDirty) && !window.confirm('Find ads again? This will replace the current cuts, including saved decisions and unsaved changes, with new suggestions. Your original audio will be preserved.')) return;
    detailAction('redetect');
  });
  $('save-cuts').addEventListener('click', () => detailAction('save'));
  $('render-cuts').addEventListener('click', () => detailAction('render'));
  $('add-cut').addEventListener('click', () => {
    const start = Number(Math.min(playhead(), Math.max(0, seconds(state.detail?.duration) - 0.1) || playhead()).toFixed(2));
    const end = Math.max(start + 0.1, Math.min(start + 30, state.detail?.duration || start + 30));
    state.cuts.push({ start, end: Number(end.toFixed(2)), reason: 'Manual cut', confidence: 1, approved: true, source: 'manual' });
    markCutsDirty(); renderCuts();
    $('cut-list').lastElementChild?.querySelector('input[type=number]')?.focus();
  });
  for (const tab of ['transcript', 'cuts']) {
    $(`tab-${tab}`).addEventListener('click', () => switchTab(tab));
    $(`tab-${tab}`).addEventListener('keydown', (event) => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const next = event.key === 'Home' ? 'transcript' : event.key === 'End' ? 'cuts' : tab === 'transcript' ? 'cuts' : 'transcript';
        switchTab(next); $(`tab-${next}`).focus();
      }
    });
  }
  $('transcript-search').addEventListener('input', () => renderTranscript(true));
  $('transcript-file').addEventListener('change', importTranscript);
  $('audio-player').addEventListener('timeupdate', () => {
    const time = playhead();
    for (const line of $('transcript-lines').children) line.classList.toggle('active', Number(line.dataset.start) <= time && Number(line.dataset.end) > time);
    drawWaveform(); updateMiniPlayer(); persistPosition();
  });
  $('audio-player').addEventListener('loadedmetadata', () => {
    const audio = $('audio-player');
    if (state.pendingSeek !== null) {
      audio.currentTime = Math.min(Number.isFinite(audio.duration) ? audio.duration : Infinity, state.audioVersion === 'cleaned' ? cleanTime(state.pendingSeek) : state.pendingSeek);
      state.pendingSeek = null;
    }
    audio.playbackRate = Number($('playback-speed').value) || 1;
    if (state.pendingPlay) { state.pendingPlay = false; audio.play().catch(() => {}); }
    drawWaveform();
  });
  $('audio-player').addEventListener('pause', () => { persistPosition(true); updateMiniPlayer(); });
  $('audio-player').addEventListener('play', updateMiniPlayer);
  $('audio-player').addEventListener('ended', () => {
    if (state.detail) updateEpisodeState(state.detail.id, { played: true, position: seconds(state.detail.duration) }).catch((error) => toast(error.message, true));
  });
  window.addEventListener('beforeunload', (event) => { if (state.cutsDirty) { event.preventDefault(); event.returnValue = ''; } });
  $('episodes').replaceChildren(...Array.from({ length: 4 }, () => el('div', 'loading-card')));
  refreshLibrary();
  window.setInterval(() => { if (!document.hidden && !state.actionBusy) refreshLibrary(); }, 3500);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshLibrary(); else persistPosition(true); });
})();
