/* Converter page: info → slider/format options → job → progress → download. */
(function () {
  'use strict';
  const Y = window.Yout;
  const $ = (s) => document.querySelector(s);
  const url = new URLSearchParams(location.search).get('url');
  if (!url) { location.replace('/'); return; }

  const form = $('#recordingForm');
  const btn = $('#format-shift-btn');
  const quality = $('#quality');
  const alertBox = $('#pageAlert');
  const statusLine = $('#statusLine');
  const progressWrap = $('#progressWrap');
  const progressBar = $('#progressBar');
  const fromInput = $('#from-input');
  const toInput = $('#to-input');
  const slider = $('#slider');
  const range = slider.querySelector('.ui-slider-range');
  const handles = slider.querySelectorAll('.ui-slider-handle');
  const titleInput = $('#title');
  const artistInput = $('#artist');
  const discover = $('#checkbox3');

  const AUDIO = [[32, '32 kbit/s - منخفض'], [64, '64 kbit/s - منخفض'], [128, '128 kbit/s - منخفض'],
                 [192, '192 kbit/s - متوسطة'], [256, '256 kbit/s - مرتفعة'], [320, '320 kbit/s - أعلى']];
  const WAV = [[22050, '22.05 kHz - منخفض'], [44100, '44.1 kHz - متوسطة'], [48000, '48 kHz - مرتفعة']];
  const GIF = [[240, '240px - منخفض'], [320, '320px - منخفض'], [480, '480px - متوسطة'], [640, '640px - مرتفعة']];
  const MP4_ALLOWED = [144, 240, 360, 480, 720, 1080, 1440, 2160, 4320];
  // same labels as the original site's MP4 quality list
  const heightLabel = (h) => (h >= 4320 ? '8k - UHD' : h >= 2160 ? '4k - UHD' : h >= 720 ? h + 'p - HD' : h + 'p');

  let info = null, duration = 0, from = 0, to = 0, auto = { title: '', artist: '' };

  const controls = () => form.querySelectorAll('input, select, button');
  const setDisabled = (v) => { controls().forEach((c) => (c.disabled = v)); fromInput.disabled = toInput.disabled = v; };
  setDisabled(true);

  function showAlert(msg) { alertBox.textContent = msg; alertBox.classList.remove('d-none'); }
  function status(msg, isErr) { statusLine.textContent = msg || ''; statusLine.classList.toggle('error', !!isErr); statusLine.classList.toggle('text-muted', !isErr); }
  const fmt = () => form.querySelector('input[name=format]:checked').value;

  // ------------------------------------------------------------------ info
  Y.api('/api/info?url=' + encodeURIComponent(url)).then((d) => {
    if (d.type === 'playlist') return showPlaylist(d);
    info = d;
    init();
  }).catch((e) => showAlert(e.message));

  function showPlaylist(d) {
    const col = $('.yout-col');
    col.innerHTML = '<div class="bg-light-gray p-2 rounded-4"><div class="video-meta-title fw-semibold px-2 py-2"></div><div id="results"></div></div>';
    col.querySelector('.video-meta-title').textContent = d.title;
    Y.renderList(col.querySelector('#results'), d.entries);
  }

  function init() {
    duration = Math.floor(info.duration || 0);
    to = duration;
    $('#videoTitleDisplay').textContent = info.title;
    $('#videoTitleDisplay').title = info.title;
    $('#videoDurationDisplay').textContent = info.duration_text || '';
    $('#thumbLink').href = $('#watchLink').href = info.url;
    $('#trickId').textContent = info.id || 'VIDEO_ID';
    if (info.thumbnail) {
      const img = $('#thumbImg');
      img.referrerPolicy = 'no-referrer';
      img.src = info.thumbnail;
    }
    if (info.is_live) { status('البث المباشر مش مدعوم حالياً', true); return; }
    setDisabled(false);
    if (!duration) { fromInput.disabled = toInput.disabled = true; }
    applyFormat();
    applyDiscover(true);
    renderSlider();
  }

  // ------------------------------------------------------------------ slider
  // rough size estimate shown under the button before converting
  function updateHint() {
    if (!info || busy) return;
    const secs = Math.max(0, to - from);
    const q = Number(quality.value), f = fmt();
    let bytes = 0;
    if (!secs) bytes = 0;
    else if (f === 'mp3') bytes = (q * 1000 / 8) * secs;
    else if (f === 'wav') bytes = q * 4 * secs;
    else if (f === 'mp4') {
      const full = info.sizes && info.sizes[q];
      bytes = full && duration ? full * (secs / duration) : 0;
    }
    status(bytes ? 'الحجم التقريبي: ' + (bytes > 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB') : '');
  }
  quality.addEventListener('change', updateHint);

  function renderSlider() {
    updateHint();
    const a = duration ? (from / duration) * 100 : 0;
    const b = duration ? (to / duration) * 100 : 100;
    handles[0].style.left = a + '%';
    handles[1].style.left = b + '%';
    range.style.left = a + '%';
    range.style.width = (b - a) + '%';
    fromInput.value = Y.hms(from);
    toInput.value = Y.hms(to);
    handles[0].setAttribute('aria-valuenow', from);
    handles[1].setAttribute('aria-valuenow', to);
  }

  function setValue(which, v) {
    v = Math.round(v);
    if (which === 0) from = Math.min(Math.max(0, v), Math.max(0, to - 1));
    else to = Math.max(Math.min(duration, v), Math.min(duration, from + 1));
    renderSlider();
  }

  function valueAt(ev) {
    const r = slider.getBoundingClientRect();
    return Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)) * duration;
  }

  handles.forEach((h, i) => {
    h.addEventListener('pointerdown', (e) => {
      if (!duration || h.dataset.off) return;
      e.preventDefault();
      h.setPointerCapture(e.pointerId);
      h.focus();
      const move = (ev) => setValue(i, valueAt(ev));
      const up = () => { h.removeEventListener('pointermove', move); h.removeEventListener('pointerup', up); h.removeEventListener('pointercancel', up); };
      h.addEventListener('pointermove', move);
      h.addEventListener('pointerup', up);
      h.addEventListener('pointercancel', up);
    });
    h.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 10 : 1;
      const cur = i === 0 ? from : to;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { setValue(i, cur - step); e.preventDefault(); }
      if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { setValue(i, cur + step); e.preventDefault(); }
      if (e.key === 'Home') { setValue(i, 0); e.preventDefault(); }
      if (e.key === 'End') { setValue(i, duration); e.preventDefault(); }
    });
  });

  // click on the bar: move the nearest handle
  slider.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.ui-slider-handle') || !duration) return;
    const v = valueAt(e);
    setValue(Math.abs(v - from) <= Math.abs(v - to) ? 0 : 1, v);
  });

  [[fromInput, 0], [toInput, 1]].forEach(([el, i]) => {
    const commit = () => {
      const t = Y.parseTime(el.value);
      if (t === null) return renderSlider();
      setValue(i, t);
    };
    el.addEventListener('change', commit);
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); commit(); } });
  });

  // ------------------------------------------------------------------ format options
  function fillQuality(items, def) {
    quality.innerHTML = items.map(([v, l]) => '<option value="' + v + '"' + (v === def ? ' selected' : '') + '>' + l + '</option>').join('');
  }

  function applyFormat() {
    const f = fmt();
    $('#format-shift-btn span').textContent = f.toUpperCase();
    if (f === 'mp3') fillQuality(AUDIO, 128);
    else if (f === 'wav') fillQuality(WAV, 44100);
    else if (f === 'gif') fillQuality(GIF, 480);
    else {
      let hs = (info.heights || []).filter((h) => MP4_ALLOWED.includes(h));
      if (!hs.length) hs = [144, 240, 360, 480, 720, 1080, 1440, 2160, 4320];
      const def = hs.includes(360) ? 360 : (hs.filter((h) => h <= 360).pop() || hs[0]);
      fillQuality(hs.map((h) => [h, heightLabel(h)]), def);
    }
    $('#checkbox1').disabled = f === 'mp4' || f === 'gif';
    if ($('#checkbox1').disabled) $('#checkbox1').checked = false;
    updateHint();
    $('.h264').classList.toggle('d-none', f !== 'mp4');
    $('.subtitle-container-wrapper').classList.toggle('d-none', f !== 'mp4');
    // silence/normalize act on audio — not applicable to GIF
    ['#checkbox1', '#checkbox2'].forEach((s) => ($(s).closest('.form-check').style.display = f === 'gif' ? 'none' : ''));
  }
  form.querySelectorAll('input[name=format]').forEach((r) => r.addEventListener('change', applyFormat));

  // ------------------------------------------------------------------ discovered metadata
  function applyDiscover(initial) {
    const m = info.meta || { title: '', artist: '' };
    if (discover.checked) {
      if (!titleInput.value || initial) titleInput.value = m.title;
      if (!artistInput.value || initial) artistInput.value = m.artist;
      auto = { title: m.title, artist: m.artist };
    } else {
      if (titleInput.value === auto.title) titleInput.value = '';
      if (artistInput.value === auto.artist) artistInput.value = '';
    }
  }
  discover.addEventListener('change', () => applyDiscover(false));

  // ------------------------------------------------------------------ submit
  let busy = false;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (busy || !info) return;
    busy = true;
    btn.disabled = true;
    btn.classList.add('m-progress');
    progressWrap.classList.remove('d-none');
    progressBar.style.width = '0%';
    status('جارٍ الاستعداد…');

    const body = {
      url: info.url || url,
      format: fmt(),
      quality: Number(quality.value),
      start: Y.hms(from), end: Y.hms(to),
      title: titleInput.value, artist: artistInput.value,
      remove_silence: $('#checkbox1').checked,
      normalize: $('#checkbox2').checked,
      discover_metadata: discover.checked,
      h264: $('#h264-checkbox').checked,
      subtitles: $('#checkbox-add-subtitles').checked,
      subtitle_language: $('#subtitle_language').value,
    };

    try {
      const job = await Y.api('/api/jobs', { body });
      await follow(job.id);
    } catch (err) {
      status(err.message, true);
      progressWrap.classList.add('d-none');
    } finally {
      busy = false;
      btn.disabled = false;
      btn.classList.remove('m-progress');
    }
  });

  async function follow(id) {
    for (;;) {
      const j = await Y.api('/api/jobs/' + id);
      progressBar.style.width = j.progress + '%';
      if (j.status === 'error') throw new Error(j.error || 'فشل');
      if (j.status === 'done') {
        progressBar.style.width = '100%';
        status('تم ✓  ' + j.filename);
        const a = document.createElement('a');
        a.href = '/api/jobs/' + id + '/file';
        a.download = j.filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        return;
      }
      status(j.stage + (j.progress ? '  ' + Math.round(j.progress) + '%' : ''));
      await new Promise((r) => setTimeout(r, 700));
    }
  }
})();
