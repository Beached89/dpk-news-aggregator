/* Kim Jong Lingo — front end for the static JSON export.
 *
 * Why this file exists: the export in data/*.json is the whole product, and Pages
 * serves static files only, so every rendering decision lives here. There is no
 * build step and no framework on purpose — the deploy directory is produced by
 * `python -m app.publish` and must work as plain files on any host or subpath.
 *
 * Rules this code sticks to:
 *   - Paths are resolved against document.baseURI, never "/absolute", so the same
 *     export works at https://host/ and https://host/dpk-news-aggregator/.
 *   - Scraped text is untrusted input: it only ever reaches the page through
 *     textContent, never innerHTML.
 *   - Anything that can fail (a missing translation, a dead image, an empty
 *     database) degrades to something readable instead of a blank area.
 */
(function () {
  'use strict';

  var BASE = document.baseURI || location.href;
  function dataUrl(rel) { return new URL('data/' + rel, BASE).href; }
  function assetUrl(rel) {
    // Media paths in the export are relative to the site root ("media/<id>/x.jpg").
    if (!rel) return '';
    if (/^https?:\/\//i.test(rel)) return rel;
    return new URL(rel.replace(/^\/+/, ''), BASE).href;
  }

  var state = {
    manifest: null,
    categories: [],
    stories: [],
    byId: {},
    lang: 'en',
    category: '',
    detail: null
  };

  try {
    var saved = localStorage.getItem('kjlingo.lang');
    if (saved === 'ko' || saved === 'en') state.lang = saved;
  } catch (e) { /* private mode: keep the default */ }

  /* ---------------------------------------------------------------- helpers */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
    return node;
  }

  function byId(id) { return document.getElementById(id); }

  function show(node, visible) {
    if (node) node.hidden = !visible;
  }

  var SOURCE_LABELS = {
    rodong: 'Rodong Sinmun',
    kcna: 'KCNA',
    vok: 'Voice of Korea',
    magazine: 'Magazine'
  };

  function sourceLabel(key) {
    return SOURCE_LABELS[String(key).toLowerCase()] || String(key);
  }

  function dayParts(story) {
    // published_date is a date string ("2026-10-23") or null on curated feeds.
    var iso = story && story.date;
    if (!iso) return null;
    var d = new Date(iso.length > 10 ? iso : iso + 'T00:00:00Z');
    return isNaN(d.getTime()) ? null : d;
  }

  function relTime(story) {
    var d = dayParts(story);
    if (!d) return 'undated';
    var mins = Math.floor((Date.now() - d.getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + ' min ago';
    var hours = Math.floor(mins / 60);
    if (hours < 24) return hours + ' hour' + (hours === 1 ? '' : 's') + ' ago';
    var days = Math.floor(hours / 24);
    if (days < 30) return days + ' day' + (days === 1 ? '' : 's') + ' ago';
    return d.toISOString().slice(0, 10);
  }

  function absDate(story) {
    var d = dayParts(story);
    return d ? d.toISOString().slice(0, 10) : 'no date';
  }

  function titleOf(story) {
    if (state.lang === 'ko') {
      return story.title_ko || story.title_en || '(untitled)';
    }
    return story.title_en || story.title_ko || '(untitled)';
  }

  function paraSplit(text) {
    if (!text) return [];
    var parts = String(text).split(/\n\s*\n+/);
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i].replace(/^\s+|\s+$/g, '');
      if (p) out.push(p);
    }
    return out;
  }

  function juche(year) { return 'Juche ' + (Number(year) - 1911); }

  /* ------------------------------------------------------------ fetch layer */
  function getJson(url) {
    return fetch(url, { cache: 'no-cache' }).then(function (res) {
      if (!res.ok) throw new Error(url + ' -> HTTP ' + res.status);
      return res.json();
    });
  }

  /* --------------------------------------------------------------- coverage */
  function coverNode(story, cls) {
    var wrap = el('div', 'cover-wrap ' + (cls || ''));
    if (!story.cover) {
      wrap.appendChild(el('div', 'cover-none', 'no photo in source'));
      return wrap;
    }
    var img = el('img');
    img.alt = '';
    img.loading = 'lazy';
    img.addEventListener('error', function () {
      // Mirrored media can still 404 when a run wrote JSON but the file did not
      // make it into the deploy directory. Say so rather than show a broken icon.
      clear(wrap).appendChild(el('div', 'cover-none', 'image unavailable'));
    });
    img.src = assetUrl(story.cover);
    wrap.appendChild(img);
    return wrap;
  }

  function catTags(story, max) {
    var box = el('span', 'inline-flex flex-wrap gap-1');
    var cats = (story.categories || []).slice(0, max || 99);
    for (var i = 0; i < cats.length; i++) {
      box.appendChild(el('span', 'cat-tag', (cats[i].en || cats[i].key || '').toUpperCase()));
    }
    return box;
  }

  function sourceLine(story, extra) {
    var row = el('div', 'mt-1 pt-1 flex justify-between items-center text-[10px] text-slate-500 uppercase font-bold source-text');
    var srcs = (story.sources || []).map(sourceLabel).join(' / ') || 'unknown source';
    row.appendChild(el('span', null, 'Source: ' + srcs));
    row.appendChild(el('span', null, extra || relTime(story)));
    return row;
  }

  /* ------------------------------------------------------------------- list */
  function filtered() {
    if (!state.category) return state.stories;
    var key = state.category;
    return state.stories.filter(function (s) {
      return (s.categories || []).some(function (c) { return c.key === key; });
    });
  }

  function cardRow(story) {
    var a = el('a', 'news-list-item no-underline text-inherit');
    a.href = '#/a/' + encodeURIComponent(story.id);
    a.appendChild(catTags(story, 3));

    var h = el('h4', 'font-bold mt-1 mb-1 mobile-subtitle star-bullet' +
      (state.lang === 'ko' ? ' ko' : ''), titleOf(story));
    a.appendChild(h);

    if (story.snippet) {
      var p = el('p', 'text-sm text-slate-600 line-clamp-2' +
        (state.lang === 'ko' ? ' ko' : ''), story.snippet);
      a.appendChild(p);
    }

    var badges = [];
    if (!story.has_translation) badges.push('no translation');
    if (story.videos && story.videos.length) badges.push(story.videos.length + ' video');
    a.appendChild(sourceLine(story, relTime(story) +
      (badges.length ? ' · ' + badges.join(', ') : '')));
    return a;
  }

  function leadBlock(story) {
    var section = el('section', 'mb-8');
    var grid = el('div', 'grid md:grid-cols-3 gap-0 bg-white border-2 border-[#CC0000]');

    grid.appendChild(coverNode(story, 'md:col-span-2'));

    var text = el('div', 'p-4 md:p-5 md:col-span-1 flex flex-col');
    text.appendChild(catTags(story, 3));
    var link = el('a', 'block no-underline mt-2 hover:underline');
    link.href = '#/a/' + encodeURIComponent(story.id);
    link.appendChild(el('h3', 'text-2xl font-black leading-snug mobile-title' +
      (state.lang === 'ko' ? ' ko' : ''), titleOf(story)));
    text.appendChild(link);

    if (state.lang === 'en' && story.title_ko) {
      text.appendChild(el('p', 'ko text-sm text-slate-500 mt-2', story.title_ko));
    } else if (story.title_en) {
      text.appendChild(el('p', 'text-xs text-slate-500 mt-2 uppercase tracking-wide', story.title_en));
    }
    if (story.snippet) {
      text.appendChild(el('p', 'text-sm text-slate-700 mt-3 line-clamp-4' +
        (state.lang === 'ko' ? ' ko' : ''), story.snippet));
    }
    text.appendChild(el('div', 'mt-auto pt-3'));
    text.appendChild(sourceLine(story, absDate(story) + ' · ' + relTime(story)));
    grid.appendChild(text);

    section.appendChild(grid);
    return section;
  }

  function renderList() {
    var listHost = clear(byId('view-list'));
    var stories = filtered();

    if (!stories.length) {
      var none = el('div', 'bg-white border-2 border-[#DDD8D0] p-6 text-center');
      none.appendChild(el('p', 'text-sm text-slate-600',
        state.category ? 'No published article in this category yet.'
                       : 'No article has been published yet.'));
      none.appendChild(el('p', 'text-xs text-slate-500 mt-2',
        'Every scrape runs on GitHub Actions; check the Actions tab if this looks wrong.'));
      listHost.appendChild(none);
      return;
    }

    listHost.appendChild(sectionHead(storyHeading()));
    listHost.appendChild(leadBlock(stories[0]));

    if (stories.length > 1) {
      listHost.appendChild(sectionHead('Latest'));
      var box = el('div', 'bg-white border-2 border-[#CC0000] divide-y divide-[#DDD8D0]');
      for (var i = 1; i < stories.length; i++) box.appendChild(cardRow(stories[i]));
      listHost.appendChild(box);
    }
  }

  function storyHeading() {
    if (!state.category) return 'Latest dispatches';
    for (var i = 0; i < state.categories.length; i++) {
      if (state.categories[i].key === state.category) {
        return state.categories[i].en || state.category;
      }
    }
    return state.category;
  }

  function sectionHead(label) {
    var row = el('div', 'flex items-center gap-2 mb-4');
    row.appendChild(el('h2', 'text-sm font-black uppercase tracking-widest', '★ ' + label));
    var rule = el('div', 'red-rule flex-grow');
    row.appendChild(rule);
    return row;
  }

  /* --------------------------------------------------------------- reader */
  function paraNode(text, cls) {
    return el('p', 'para ' + cls, text);
  }

  function bodyBlock(detail) {
    var host = el('div', 'mt-6');
    var ko = paraSplit(detail.body_ko);
    var en = paraSplit(detail.body_en);
    var mode = state.mode;

    if (!ko.length && !en.length) {
      host.appendChild(el('p', 'text-sm text-slate-500',
        'No article text was stored for this item — the scraper kept only the headline.'));
      return host;
    }

    var options = [];
    if (ko.length && en.length) options.push(['parallel', '한 / EN together']);
    if (en.length) options.push(['en', 'English']);
    if (ko.length) options.push(['ko', '조선말']);

    if (options.length > 1) {
      var controls = el('div', 'flex flex-wrap gap-2 mb-5');
      options.forEach(function (opt) {
        var b = el('button', 'nav-tab' + (mode === opt[0] ? ' active' : ''), opt[1]);
        b.type = 'button';
        b.addEventListener('click', function () {
          state.mode = opt[0];
          try { localStorage.setItem('kjlingo.mode', opt[0]); } catch (e) { /* ignore */ }
          renderDetail(state.detailId, true);
        });
        controls.appendChild(b);
      });
      host.appendChild(controls);
    } else {
      mode = (ko.length && !en.length) ? 'ko' : 'en';
    }

    if (mode === 'parallel' && ko.length !== en.length) {
      // Paragraph counts drift when a translation drops or merges a paragraph, so
      // parallel mode degrades to two stacked blocks rather than mis-pairing lines.
      host.appendChild(el('p', 'text-[11px] text-slate-500 mb-3',
        'Korean and English do not break into the same number of paragraphs (' +
        ko.length + ' vs ' + en.length + '), so they are shown one block after the other.'));
      mode = 'stacked';
    }

    var cols;
    if (mode === 'parallel') {
      cols = el('div', 'grid md:grid-cols-2 gap-6 items-start');
      var koCol = el('div', null);
      var enCol = el('div', null);
      ko.forEach(function (p) { koCol.appendChild(paraNode(p, 'para-ko ko text-[15px]')); });
      en.forEach(function (p) { enCol.appendChild(paraNode(p, 'para-en text-[15px]')); });
      cols.appendChild(koCol);
      cols.appendChild(enCol);
    } else if (mode === 'ko') {
      cols = el('div', null);
      ko.forEach(function (p) { cols.appendChild(paraNode(p, 'para-ko ko text-[15px]')); });
    } else if (mode === 'en') {
      cols = el('div', null);
      en.forEach(function (p) { cols.appendChild(paraNode(p, 'para-en text-[15px]')); });
    } else {
      cols = el('div', 'space-y-6');
      var koBox = el('div', 'bg-white border border-[#DDD8D0] p-4');
      koBox.appendChild(el('h4', 'text-[10px] font-black uppercase tracking-widest mb-3', '조선말'));
      ko.forEach(function (p) { koBox.appendChild(paraNode(p, 'para-ko ko text-[15px]')); });
      var enBox = el('div', 'bg-white border border-[#DDD8D0] p-4');
      enBox.appendChild(el('h4', 'text-[10px] font-black uppercase tracking-widest mb-3', 'English'));
      en.forEach(function (p) { enBox.appendChild(paraNode(p, 'para-en text-[15px]')); });
      cols.appendChild(koBox);
      cols.appendChild(enBox);
    }
    host.appendChild(cols);
    return host;
  }

  function neighbour(id, delta) {
    var list = filtered();
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) {
        var j = i + delta;
        return (list[j] && list[j].id !== id) ? list[j] : null;
      }
    }
    return null;
  }

  function neighbourRow(id) {
    var row = el('div', 'flex justify-between gap-3 mt-8 pt-4 border-t border-[#DDD8D0]');
    var prev = neighbour(id, -1);
    var next = neighbour(id, 1);
    if (prev) {
      var a = el('a', 'text-xs font-bold no-underline hover:underline');
      a.href = '#/a/' + encodeURIComponent(prev.id);
      a.textContent = '← ' + titleOf(prev);
      row.appendChild(a);
    } else {
      row.appendChild(el('span'));
    }
    if (next) {
      var b = el('a', 'text-xs font-bold no-underline hover:underline text-right');
      b.href = '#/a/' + encodeURIComponent(next.id);
      b.textContent = titleOf(next) + ' →';
      row.appendChild(b);
    }
    return row;
  }

  function renderDetail(id, keepDetail) {
    var card = state.byId[id];
    var host = clear(byId('view-article'));
    if (!card) {
      show(byId('view-article'), false);
      show(byId('view-list'), true);
      renderList();
      setStatus('Article not found', 'The current export has no story ' + id +
        '. It was likely published and then aged out of the rolling window.');
      return;
    }
    // articles.json is deliberately compact (no url, no bodies); the per-story
    // file carries them. Render from the merge so the original-article link and
    // the full text survive once they arrive.
    var story = (keepDetail && state.detail && state.detail.id === id)
      ? Object.assign({}, card, state.detail)
      : card;
    state.detailId = id;
    show(byId('status'), false);
    show(byId('view-list'), false);
    show(byId('view-article'), true);

    var back = el('a', 'text-xs font-bold uppercase tracking-widest no-underline hover:underline',
      '← all dispatches');
    back.href = '#/';
    host.appendChild(back);

    var koFirst = state.lang === 'ko';
    var main = koFirst ? (story.title_ko || story.title_en || '(untitled)')
                       : (story.title_en || story.title_ko || '(untitled)');
    var other = koFirst ? story.title_en : story.title_ko;

    var head = el('div', 'mt-4 mb-4');
    head.appendChild(catTags(story, 6));
    head.appendChild(el('h2', 'text-3xl font-black leading-tight mt-2 mobile-title' +
      (koFirst ? ' ko' : ''), main));
    if (other && other !== main) {
      head.appendChild(el('p', 'text-base text-slate-500 mt-2' + (koFirst ? '' : ' ko'), other));
    }
    head.appendChild(el('div', 'red-rule mt-4'));
    host.appendChild(head);

    var meta = el('div', 'text-[11px] uppercase tracking-wide text-slate-500 font-bold flex flex-wrap gap-x-4 gap-y-1');
    meta.appendChild(el('span', null, 'Source: ' +
      ((story.sources || []).map(sourceLabel).join(' / ') || 'unknown')));
    meta.appendChild(el('span', null, 'Published: ' + absDate(story)));
    meta.appendChild(el('span', null, story.has_translation
      ? 'Machine translation, unedited' : 'No translation — Korean only'));
    if (story.dateline) meta.appendChild(el('span', null, story.dateline));
    host.appendChild(meta);

    if (story.url) {
      var orig = el('a', 'inline-block mt-3 text-xs font-bold no-underline hover:underline',
        'Open the original on the publisher site');
      orig.href = story.url;
      orig.rel = 'noopener noreferrer nofollow';
      orig.target = '_blank';
      host.appendChild(orig);
      host.appendChild(el('p', 'text-[10px] text-slate-400 mt-1',
        'Originals are served over plain HTTP by the DPRK site and open in a new tab.'));
    }

    if (story.cover) host.appendChild(coverNode(story, 'mt-5'));

    if (story.videos && story.videos.length) {
      var vids = el('div', 'mt-4 space-y-1');
      story.videos.forEach(function (v) {
        var va = el('a', 'block text-xs font-bold no-underline hover:underline',
          'Video item from this article');
        va.href = assetUrl(v);
        va.rel = 'noopener noreferrer';
        vids.appendChild(va);
      });
      host.appendChild(vids);
    }

    if (keepDetail && state.detail && state.detail.id === id) {
      host.appendChild(bodyBlock(state.detail));
    } else {
      host.appendChild(el('p', 'text-sm text-slate-500 mt-6', 'Loading full text...'));
      getJson(dataUrl('a/' + encodeURIComponent(id) + '.json')).then(function (detail) {
        state.detail = detail;
        renderDetail(id, true);
      }).catch(function (err) {
        host.appendChild(el('p', 'text-sm text-[#CC0000] mt-6',
          'Could not load the full text (' + err.message + ').'));
      });
    }

    host.appendChild(neighbourRow(id));
    window.scrollTo(0, 0);
  }

  /* ---------------------------------------------------------------- chrome */
  try {
    var savedMode = localStorage.getItem('kjlingo.mode');
    state.mode = (savedMode === 'en' || savedMode === 'ko' || savedMode === 'parallel')
      ? savedMode : 'parallel';
  } catch (e) {
    state.mode = 'parallel';
  }

  function setStatus(title, body) {
    byId('status-title').textContent = title || '';
    byId('status-body').textContent = body || '';
    show(byId('status'), Boolean(title));
  }

  function fmtWhen(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
  }

  function tabButton(key, label) {
    var b = el('button', 'nav-tab' + (state.category === key ? ' active' : ''), label);
    b.type = 'button';
    b.addEventListener('click', function () {
      state.category = key;
      renderCatNav();
      if (currentId()) {
        location.hash = '#/';          // route() redraws the list
      } else {
        renderList();
      }
    });
    return b;
  }

  function renderCatNav() {
    var hosts = [byId('catnav'), byId('catnav-mobile')];
    for (var i = 0; i < hosts.length; i++) {
      var host = hosts[i];
      if (!host) continue;
      clear(host);
      host.appendChild(tabButton('', 'All (' + state.stories.length + ')'));
      state.categories.forEach(function (c) {
        host.appendChild(tabButton(c.key, (c.en || c.key) + ' (' + c.count + ')'));
      });
    }
  }

  function renderChrome() {
    var m = state.manifest || {};
    var heads = state.stories.slice(0, 6).map(function (s) {
      return s.title_en || s.title_ko || '';
    }).filter(Boolean);
    byId('ticker').textContent = heads.length
      ? '★ TOP NEWS ★ ' + heads.join('   ★   ')
      : '★ TOP NEWS ★ nothing published in the current window ★';

    byId('updated').textContent = m.generated_at
      ? 'updated ' + fmtWhen(m.generated_at)
      : 'no export timestamp';

    var stats = [];
    stats.push((m.stories || 0) + ' article' + (m.stories === 1 ? '' : 's'));
    stats.push((m.translated || 0) + ' translated');
    stats.push((m.with_photo || 0) + ' with photo');
    if (m.sources && m.sources.length) {
      stats.push('source' + (m.sources.length === 1 ? '' : 's') + ': ' +
        m.sources.map(sourceLabel).join(', '));
    }
    stats.push(m.window_hours ? 'window: last ' + m.window_hours + ' h'
                              : 'window: everything mirrored');
    byId('footer-stats').textContent = stats.join('  ·  ');

    var now = new Date().getFullYear();
    byId('juche-label').textContent = juche(now) + ' (' + now + ')';
  }

  function setLang(lang) {
    state.lang = lang;
    try { localStorage.setItem('kjlingo.lang', lang); } catch (e) { /* ignore */ }
    byId('lang-en').classList.toggle('active', lang === 'en');
    byId('lang-ko').classList.toggle('active', lang === 'ko');
    document.documentElement.lang = lang === 'ko' ? 'ko' : 'en';
    var id = currentId();
    if (id) {
      renderDetail(id, Boolean(state.detail && state.detail.id === id));
    } else {
      renderList();
    }
  }

  /* ---------------------------------------------------------------- router */
  function currentId() {
    var m = /^#\/a\/(.+)$/.exec(location.hash || '');
    return m ? decodeURIComponent(m[1]) : null;
  }

  function route() {
    var id = currentId();
    if (id) {
      renderDetail(id, Boolean(state.detail && state.detail.id === id));
      return;
    }
    show(byId('view-article'), false);
    show(byId('view-list'), true);
    renderList();
  }

  /* ------------------------------------------------------------------ boot */
  function wireChrome() {
    byId('lang-en').addEventListener('click', function () { setLang('en'); });
    byId('lang-ko').addEventListener('click', function () { setLang('ko'); });

    var toggle = byId('menu-toggle');
    var menu = byId('mobile-menu');
    toggle.addEventListener('click', function () {
      var open = menu.classList.toggle('open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', function (e) {
      if (!menu.contains(e.target) && !toggle.contains(e.target)) {
        menu.classList.remove('open');
        toggle.setAttribute('aria-expanded', 'false');
      }
    });

    var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var d = new Date();
    byId('current-date').textContent =
      months[d.getMonth()] + ' ' + d.getDate() + ', ' + d.getFullYear();

    window.addEventListener('hashchange', route);
  }

  function boot() {
    wireChrome();
    byId('lang-en').classList.toggle('active', state.lang === 'en');
    byId('lang-ko').classList.toggle('active', state.lang === 'ko');
    document.documentElement.lang = state.lang === 'ko' ? 'ko' : 'en';

    Promise.all([
      getJson(dataUrl('manifest.json')).catch(function () { return null; }),
      getJson(dataUrl('categories.json')).catch(function () {
        return { categories: [] };
      }),
      getJson(dataUrl('articles.json'))
    ]).then(function (res) {
      state.manifest = res[0];
      state.categories = (res[1] && res[1].categories) || [];
      state.stories = (res[2] && res[2].stories) || [];
      state.byId = {};
      state.stories.forEach(function (s) { state.byId[s.id] = s; });

      renderChrome();
      renderCatNav();
      route();

      if (!state.manifest) {
        setStatus('Partial export',
          'data/manifest.json could not be read, so the counts are missing. ' +
          'The list itself loaded from data/articles.json.');
      }
    }).catch(function (err) {
      setStatus('Could not load the export',
        err.message + '. This deploy directory has no data/articles.json, so no ' +
        'scrape has published here yet. The pipeline runs every six hours on ' +
        'GitHub Actions.');
    });
  }

  boot();
})();

