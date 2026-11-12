/* Shared behaviour: navbar, search box + suggestions, result lists, search & downloads pages. */
(function () {
  'use strict';

  const Yout = (window.Yout = {});

  Yout.api = async function (path, opts) {
    opts = opts || {};
    const init = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined) {
      init.method = opts.method || 'POST';
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    if (init.method !== 'GET') init.headers['X-Requested-With'] = 'yout-local';
    let res;
    try {
      res = await fetch(path, init);
    } catch (e) {
      throw new Error('مفيش اتصال بالسيرفر المحلي — تأكد إن server.py شغّال');
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* not json */ }
    if (!res.ok) throw new Error((data && data.error) || 'HTTP ' + res.status);
    return data;
  };

  Yout.hms = function (s) {
    s = Math.max(0, Math.round(s || 0));
    const p = (n) => String(n).padStart(2, '0');
    return p(Math.floor(s / 3600)) + ':' + p(Math.floor((s % 3600) / 60)) + ':' + p(s % 60);
  };

  Yout.parseTime = function (v) {
    v = String(v || '').trim();
    if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(v)) return null;
    const parts = v.split(':').map(Number);
    let s = 0;
    for (const p of parts) s = s * 60 + p;
    return s;
  };

  Yout.esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  };

  Yout.renderList = function (el, entries) {
    if (!entries || !entries.length) {
      el.innerHTML = '<div class="text-center text-muted py-4">لا توجد نتائج</div>';
      return;
    }
    el.innerHTML = entries.map((e) => {
      const href = '/video/?url=' + encodeURIComponent(e.url);
      const sub = [e.uploader, e.duration_text].filter(Boolean).join(' · ');
      const img = e.thumbnail
        ? '<img class="img-fluid p-2" referrerpolicy="no-referrer" loading="lazy" alt="" src="' + Yout.esc(e.thumbnail) + '">'
        : '';
      return '<a class="result-row d-flex align-items-center gap-3 bg-white rounded-4 p-2 mb-2 text-decoration-none text-reset" href="' + Yout.esc(href) + '">' +
        '<div class="thumb bg-black rounded-4">' + img + '</div>' +
        '<div class="flex-grow-1" style="min-width:0">' +
        '<div class="fw-semibold" style="font-size:15px;line-height:1.3">' + Yout.esc(e.title) + '</div>' +
        '<div class="small text-muted">' + Yout.esc(sub) + '</div></div></a>';
    }).join('');
  };

  // ---- viewport unit used by styles.css
  const setVh = () => document.documentElement.style.setProperty('--vh', window.innerHeight * 0.01 + 'px');
  setVh();
  window.addEventListener('resize', setVh);

  // ---- bootstrap-style collapse (navbar toggler, H.264 hint) without bootstrap.js
  document.addEventListener('click', (e) => {
    const a = e.target.closest('[data-bs-toggle="collapse"]');
    if (!a) return;
    e.preventDefault();
    const sel = a.getAttribute('data-bs-target') || a.getAttribute('href');
    const t = sel && document.querySelector(sel);
    if (!t) return;
    const open = t.classList.toggle('show');
    a.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  // ---- "cool trick" popover close button
  document.addEventListener('click', (e) => {
    const b = e.target.closest('.cool-trick-close');
    if (b) b.closest('details').open = false;
  });

  // ---- navbar search box with suggestions
  const input = document.querySelector('.auto-suggest');
  const list = document.querySelector('.auto-suggest-list');
  if (input && list) {
    let timer = null, seq = 0;
    const hide = () => { list.classList.add('empty'); list.innerHTML = ''; };
    input.addEventListener('input', () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (!q || /^https?:\/\//i.test(q)) return hide();
      timer = setTimeout(async () => {
        const my = ++seq;
        try {
          const items = await Yout.api('/api/suggest?q=' + encodeURIComponent(q));
          if (my !== seq) return;
          if (!items.length) return hide();
          list.innerHTML = items.map((s) => '<a href="/s/?q=' + encodeURIComponent(s) + '">' + Yout.esc(s) + '</a>').join('');
          list.classList.remove('empty');
        } catch (e) { hide(); }
      }, 200);
    });
    document.addEventListener('click', (e) => { if (!e.target.closest('.auto-suggest, .auto-suggest-list')) hide(); });
    input.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
  }

  // ---- /s/ page
  const results = document.getElementById('results');
  const head = document.getElementById('searchHead');
  if (results && head && location.pathname.startsWith('/s')) {
    const q = new URLSearchParams(location.search).get('q') || '';
    head.textContent = q;
    if (input) input.value = q;
    Yout.api('/api/search?q=' + encodeURIComponent(q))
      .then((d) => Yout.renderList(results, d.entries))
      .catch((e) => { results.innerHTML = '<div class="alert alert-danger rounded-4 m-0">' + Yout.esc(e.message) + '</div>'; });
  }

  // ---- /downloads/ page
  const dl = document.getElementById('dlList');
  if (dl) {
    const size = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB');
    const load = async () => {
      try {
        const d = await Yout.api('/api/downloads');
        document.getElementById('dlDir').textContent = d.dir;
        if (!d.items.length) { dl.innerHTML = '<div class="text-center text-muted py-4">لا توجد ملفات بعد</div>'; return; }
        dl.innerHTML = d.items.map((f) =>
          '<div class="d-flex align-items-center gap-2 bg-white rounded-4 p-2 mb-2">' +
          '<a class="flex-grow-1 text-decoration-none text-truncate" style="min-width:0" href="/api/downloads/' + encodeURIComponent(f.name) + '">' + Yout.esc(f.name) + '</a>' +
          '<span class="small text-muted flex-shrink-0">' + size(f.size) + '</span>' +
          '<button type="button" class="btn btn-sm btn-outline-danger rounded-pill flex-shrink-0" data-del="' + Yout.esc(f.name) + '">×</button></div>').join('');
      } catch (e) {
        dl.innerHTML = '<div class="alert alert-danger rounded-4 m-0">' + Yout.esc(e.message) + '</div>';
      }
    };
    dl.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-del]');
      if (!b || !confirm('حذف الملف؟')) return;
      try { await Yout.api('/api/downloads/' + encodeURIComponent(b.dataset.del), { method: 'DELETE' }); } catch (err) { alert(err.message); }
      load();
    });
    load();
  }
})();
