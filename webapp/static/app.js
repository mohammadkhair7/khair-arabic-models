/* alarabia.chat - inference UI.
 *
 * Plain browser JavaScript: no build step, no dependencies, nothing loaded
 * from a third party. Every piece of model output and every server message
 * reaches the DOM through textContent, never innerHTML, so a document full of
 * angle brackets renders as the text it is.
 */
'use strict';

const $ = (id) => document.getElementById(id);

const el = {
  nav: $('nav'),
  tasks: $('tasks'), text: $('text'), textHint: $('text-hint'),
  file: $('file'), drop: $('drop'), dropLabel: $('drop-label'),
  fileHint: $('file-hint'), columnRow: $('column-row'), column: $('column'),
  tabPaste: $('tab-paste'), tabFile: $('tab-file'),
  panePaste: $('pane-paste'), paneFile: $('pane-file'),
  run: $('run'), status: $('status'), results: $('results'),
  meta: $('meta'), notes: $('notes'), downloads: $('download-buttons'),
  output: $('output'), showSource: $('show-source'), copy: $('copy'),
  previewNote: $('preview-note'),
  langPick: $('lang-pick'), langButtons: $('lang-buttons'),
  donateWidget: $('donate-widget'), tiers: $('tiers'),
  fbName: $('fb-name'), fbEmail: $('fb-email'), fbMessage: $('fb-message'),
  fbWebsite: $('fb-website'), fbSend: $('fb-send'), fbStatus: $('fb-status'),
  fbHint: $('fb-hint'), fbForm: $('feedback-form'), fbOff: $('feedback-off'),
  themeToggle: $('theme-toggle'),
};

let config = null;
let mode = 'paste';
let task = 'tashkeel';
let last = null;
// Language the POS and structure tag names are shown in. The models emit
// codes; this only picks which dictionary those codes are read through, so
// changing it redraws the result without asking the server to run again.
// Must match labels.DEFAULT_LANGUAGE, or the first result would arrive from
// the server worded one way and the language buttons would claim the other.
let lang = 'ar';

init();

async function init() {
  try {
    config = await (await fetch('/api/config')).json();
  } catch {
    return setError(el.status, 'Could not reach the server. Is it still running?');
  }
  buildTasks();
  buildLangs();
  buildHints();
  buildDonate();
  buildFeedback();
  wire();
}

/* ------------------------------------------------------------ page nav */

function showPage(name) {
  for (const button of el.nav.querySelectorAll('button')) {
    button.classList.toggle('is-active', button.dataset.page === name);
  }
  for (const page of document.querySelectorAll('.page')) {
    page.classList.toggle('is-hidden', page.id !== `page-${name}`);
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* --------------------------------------------------------------- tasks */

function buildTasks() {
  el.tasks.textContent = '';
  config.tasks.forEach((t, i) => {
    const label = document.createElement('label');
    label.className = 'task' + (i === 0 ? ' is-active' : '');
    label.dataset.key = t.key;

    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'task';
    radio.value = t.key;
    radio.checked = i === 0;

    label.append(radio, span('task-title', t.label),
      spanRtl('task-title-ar', t.label_ar), span('task-desc', t.description));

    radio.addEventListener('change', () => {
      task = t.key;
      document.querySelectorAll('.task').forEach(
        (n) => n.classList.toggle('is-active', n.dataset.key === t.key));
    });
    el.tasks.append(label);
  });
  task = config.tasks[0].key;
}

function buildHints() {
  const lim = config.limits;
  el.textHint.textContent =
    `Up to ${lim.max_text_chars.toLocaleString()} characters or ` +
    `${lim.max_units.toLocaleString()} lines. Each line is processed separately.`;
  el.fileHint.textContent =
    `${config.accept.join(', ')} — up to ${lim.max_upload_mb} MB. ` +
    (config.virus_scanner
      ? 'Every upload is virus-scanned and processed in memory.'
      : 'Uploads are processed in memory and never written to disk.');
}

/* -------------------------------------------------------------- donate */

function buildDonate() {
  const options = config.donate || [];
  if (!options.length) return;

  // Header dropdown, mirroring hadith.chat's donate menu.
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'donate-btn';
  button.append(document.createTextNode('Donate '),
    spanRtl('ar-inline', 'تبرّع'));

  const menu = document.createElement('div');
  menu.className = 'donate-menu is-hidden';
  for (const option of options) {
    menu.append(donateLink(option, `donate-${option.key}`));
  }

  button.addEventListener('click', (ev) => {
    ev.stopPropagation();
    menu.classList.toggle('is-hidden');
  });
  document.addEventListener('click', () => menu.classList.add('is-hidden'));

  el.donateWidget.append(button, menu);

  // Full cards on the Donate page.
  el.tiers.textContent = '';
  for (const option of options) {
    const card = document.createElement('div');
    card.className = `tier tier-${option.key}`;
    const h3 = document.createElement('h3');
    h3.append(document.createTextNode(option.label + ' '),
      spanRtl('ar-inline', option.label_ar));
    const note = document.createElement('p');
    note.textContent = option.note;
    const noteAr = document.createElement('p');
    noteAr.className = 'rtl';
    noteAr.textContent = option.note_ar;
    card.append(h3, note, noteAr, donateLink(option, ''));
    el.tiers.append(card);
  }
}

function donateLink(option, cls) {
  const a = document.createElement('a');
  if (cls) a.className = cls;
  a.href = option.url;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  a.append(document.createTextNode(option.label + ' '),
    spanRtl('ar-inline', option.label_ar));
  if (cls) {
    const small = document.createElement('small');
    small.textContent = option.note;
    a.append(small);
  }
  return a;
}

/* ------------------------------------------------------------ feedback */

function buildFeedback() {
  if (config.feedback) {
    el.fbHint.textContent =
      `Goes straight to ${config.contact_email}. We keep your address only to reply.`;
    return;
  }
  el.fbForm.classList.add('is-hidden');
  el.fbOff.classList.remove('is-hidden');
  el.fbOff.textContent =
    `The feedback form is not configured on this server. Please email ` +
    `${config.contact_email} directly.`;
}

async function sendFeedback() {
  const body = new FormData();
  body.append('name', el.fbName.value);
  body.append('email', el.fbEmail.value);
  body.append('message', el.fbMessage.value);
  body.append('website', el.fbWebsite.value);   // honeypot

  el.fbSend.disabled = true;
  el.fbStatus.className = 'status is-busy';
  el.fbStatus.textContent = 'Sending';
  try {
    const response = await fetch('/api/feedback', { method: 'POST', body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(el.fbStatus, data.error || data.detail || 'Could not send the message.');
    } else {
      el.fbStatus.className = 'status is-ok';
      el.fbStatus.textContent = 'Thank you — your message is on its way.';
      el.fbName.value = el.fbEmail.value = el.fbMessage.value = '';
    }
  } catch {
    setError(el.fbStatus, 'Could not reach the server.');
  } finally {
    el.fbSend.disabled = false;
  }
}

/* --------------------------------------------------------------- wiring */

// `static/theme.js` has already chosen the theme by the time this runs — it
// has to, from <head>, or the page paints light and then flips. All that is
// left here is the button: reflect the current state, and record a deliberate
// choice so it outlives the session and stops tracking the OS setting.
function wireTheme() {
  const paint = () => {
    const dark = document.documentElement.classList.contains('dark');
    el.themeToggle.setAttribute('aria-pressed', String(dark));
    el.themeToggle.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
  };
  paint();
  el.themeToggle.addEventListener('click', () => {
    const dark = !document.documentElement.classList.contains('dark');
    document.documentElement.classList.toggle('dark', dark);
    try {
      localStorage.setItem('theme', dark ? 'dark' : 'light');
    } catch (e) {
      // Private mode: the theme still applies, it just will not be remembered.
    }
    paint();
  });
}

function wire() {
  wireTheme();
  el.nav.addEventListener('click', (ev) => {
    const button = ev.target.closest('button[data-page]');
    if (button) showPage(button.dataset.page);
  });

  el.tabPaste.addEventListener('click', () => setMode('paste'));
  el.tabFile.addEventListener('click', () => setMode('file'));
  el.file.addEventListener('change', onPick);
  el.run.addEventListener('click', submit);
  el.showSource.addEventListener('change', () => last && draw(last));
  el.copy.addEventListener('click', copyOutput);
  el.fbSend.addEventListener('click', sendFeedback);

  ['dragenter', 'dragover'].forEach((e) => el.drop.addEventListener(e, (ev) => {
    ev.preventDefault();
    el.drop.classList.add('is-over');
  }));
  ['dragleave', 'drop'].forEach((e) => el.drop.addEventListener(e, (ev) => {
    ev.preventDefault();
    el.drop.classList.remove('is-over');
  }));
  el.drop.addEventListener('drop', (ev) => {
    if (ev.dataTransfer.files.length) {
      el.file.files = ev.dataTransfer.files;
      onPick();
    }
  });

  el.text.addEventListener('keydown', (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') submit();
  });
}

function setMode(next) {
  mode = next;
  const paste = next === 'paste';
  el.tabPaste.classList.toggle('is-active', paste);
  el.tabFile.classList.toggle('is-active', !paste);
  el.tabPaste.setAttribute('aria-selected', String(paste));
  el.tabFile.setAttribute('aria-selected', String(!paste));
  el.panePaste.classList.toggle('is-hidden', !paste);
  el.paneFile.classList.toggle('is-hidden', paste);
}

function onPick() {
  const file = el.file.files[0];
  if (!file) return;
  el.drop.classList.add('has-file');
  el.dropLabel.textContent = `${file.name} — ${(file.size / 1024).toFixed(0)} KB`;
  el.columnRow.classList.toggle('is-hidden', !/\.(csv|xlsx)$/i.test(file.name));
  setMode('file');
}

/* ------------------------------------------------------------- request */

async function submit() {
  const body = new FormData();
  body.append('task', task);
  body.append('lang', lang);
  if (mode === 'paste') {
    if (!el.text.value.trim()) return setError(el.status, 'Please paste some Arabic text first.');
    body.append('text', el.text.value);
  } else {
    if (!el.file.files[0]) return setError(el.status, 'Please choose a file first.');
    body.append('file', el.file.files[0]);
    if (el.column.value.trim()) body.append('column', el.column.value.trim());
  }

  el.run.disabled = true;
  el.status.className = 'status is-busy';
  el.status.textContent =
    'Running the model. The first request also loads it, so it can take a few seconds';
  try {
    const response = await fetch('/api/process', { method: 'POST', body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      return setError(el.status, data.error || data.detail || `Request failed (${response.status}).`);
    }
    last = data;
    draw(data);
    el.status.className = 'status';
    el.status.textContent = `Done in ${data.elapsed_s}s.`;
  } catch {
    setError(el.status, 'Could not reach the server.');
  } finally {
    el.run.disabled = false;
  }
}

function setError(node, message) {
  node.className = 'status is-error';
  node.textContent = message;
}

/* ------------------------------------------------------- tag language */

function buildLangs() {
  el.langButtons.textContent = '';
  for (const option of config.languages) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'lang' + (option.key === lang ? ' is-active' : '');
    button.dataset.key = option.key;
    button.textContent = option.label;
    if (option.key === 'ar') { button.dir = 'rtl'; button.lang = 'ar'; }
    button.addEventListener('click', () => setLang(option.key));
    el.langButtons.append(button);
  }
}

function setLang(next) {
  if (next === lang) return;
  lang = next;
  for (const button of el.langButtons.querySelectorAll('button')) {
    button.classList.toggle('is-active', button.dataset.key === lang);
  }
  // Redraw from the codes we already hold - no second inference run.
  if (last) { drawMeta(last); drawOutput(last); drawDownloads(last); }
}

function labelFor(key) {
  const found = config.languages.find((l) => l.key === key);
  return found ? found.label : key;
}

/** Display name for one tag code, in the currently selected language. */
function tagName(kind, code) {
  const entry = (config.tag_sets[kind] || []).find((t) => t.code === code);
  return entry ? entry[lang] || entry.code : code;
}

/** True when the running task produces tags that can be named. */
function tagged(data) {
  return Boolean(data.tag_kind);
}

/* ------------------------------------------------------------- results */

function draw(data) {
  el.results.classList.remove('is-hidden');
  drawMeta(data);
  drawNotes(data);
  drawDownloads(data);
  drawOutput(data);
  el.previewNote.textContent = data.shown_units < data.total_units
    ? `Showing the first ${data.shown_units.toLocaleString()} of ` +
      `${data.total_units.toLocaleString()} units — download the file for all of them.`
    : '';
  el.results.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function drawMeta(data) {
  const spec = config.tasks.find((t) => t.key === data.task);
  const title = spec && lang === 'ar' ? spec.label_ar : data.task_label;
  const rows = [
    ['Task', title],
    ['Source', data.source],
    ['Units', data.total_units.toLocaleString()],
    ['Time', `${data.elapsed_s}s`],
  ];
  if (data.scan !== 'not applicable') rows.push(['Virus scan', data.scan]);
  el.meta.textContent = '';
  for (const [key, value] of rows) {
    const wrap = document.createElement('div');
    const dt = document.createElement('dt');
    dt.textContent = key;
    const dd = document.createElement('dd');
    dd.textContent = value;
    wrap.append(dt, dd);
    el.meta.append(wrap);
  }
}

function drawNotes(data) {
  const notes = [...(data.notes || [])];
  if (data.truncated) notes.push('The input was longer than the limit, so it was trimmed.');
  el.notes.textContent = '';
  el.notes.classList.toggle('is-hidden', !notes.length);
  for (const note of notes) {
    const li = document.createElement('li');
    li.textContent = note;
    el.notes.append(li);
  }
}

function drawDownloads(data) {
  el.downloads.textContent = '';
  for (const fmt of config.formats) {
    const a = document.createElement('a');
    a.className = 'dl' + (fmt.available ? '' : ' is-off');
    a.textContent = fmt.label;
    if (fmt.available) {
      a.href = `/api/download/${encodeURIComponent(data.token)}/${fmt.key}`
        + `?lang=${encodeURIComponent(lang)}`;
      a.setAttribute('download', '');
    } else {
      a.title = 'Unavailable: the server has no Arabic font installed for PDF output.';
    }
    el.downloads.append(a);
  }
}

function drawOutput(data) {
  el.langPick.classList.toggle('is-hidden', !tagged(data));
  el.output.textContent = '';
  if (data.is_table) return drawTable(data);

  const withSource = el.showSource.checked;
  for (const unit of data.units) {
    if (!unit.output.trim()) {
      el.output.append(div('blank'));
      continue;
    }
    if (withSource) el.output.append(p('src', unit.source));
    if (data.tag_kind === 'pos') el.output.append(pairsRow(unit));
    else if (data.tag_kind === 'structure') el.output.append(segments(unit));
    else el.output.append(p('', unit.output));
  }
}

/** The unit's text in the selected language, rebuilt from the tag codes. */
function unitText(data, unit) {
  if (!tagged(data) || !unit.pairs.length) return unit.output;
  if (data.tag_kind === 'pos') {
    return unit.pairs.map(([w, c]) => `${w}/${tagName('pos', c)}`).join(' ');
  }
  // Same layout the server writes into .txt, Markdown and the PDF, so what a
  // reader copies off the page is what they would have downloaded.
  const heading = (config.narrator_words[lang] || {}).heading;
  const lines = [];
  unit.pairs.forEach(([c, chunk], i) => {
    lines.push(`[${tagName('structure', c)}] ${chunk}`);
    const chain = (unit.narrators || []).filter((h) => h.seg === i);
    if (!chain.length) return;
    lines.push(`    ${heading}:`);
    for (const h of chain) lines.push(`      ${h.n}. ${h.verb} — ${h.name}`);
  });
  return lines.join('\n');
}

function drawTable(data) {
  const table = document.createElement('table');
  table.className = 'grid';
  const head = document.createElement('tr');
  head.append(cell('th', 'input'), cell('th', data.task));
  table.append(head);
  for (const unit of data.units) {
    const tr = document.createElement('tr');
    tr.append(cell('td', unit.source));
    const out = cell('td', unitText(data, unit));
    out.className = 'out';
    tr.append(out);
    table.append(tr);
  }
  el.output.append(table);
}

function pairsRow(unit) {
  const wrap = div('');
  for (const [word, code] of unit.pairs) {
    const tok = document.createElement('span');
    tok.className = 'tok';
    const name = span('t', tagName('pos', code));
    if (lang === 'ar') { name.dir = 'rtl'; name.lang = 'ar'; }
    // The code stays reachable on hover, so a reader who wants the canonical
    // CAMeL tag is one gesture away in either language.
    name.title = code;
    tok.append(span('w', word), name);
    wrap.append(tok);
  }
  return wrap;
}

function segments(unit) {
  const wrap = div('');
  unit.pairs.forEach(([code, chunk], i) => {
    const row = div('seg');
    const name = span('lab lab-' + code.replace(/[^A-Z]/g, ''),
      tagName('structure', code));
    if (lang === 'ar') { name.dir = 'rtl'; name.lang = 'ar'; }
    name.title = code;
    row.append(name, span('txt', chunk));
    wrap.append(row);
    const chain = (unit.narrators || []).filter((h) => h.seg === i);
    if (chain.length) wrap.append(narratorChain(chain));
  });
  return wrap;
}

/** The narrators of one isnād, numbered in transmission order.
 *
 * Directly under its own isnād rather than in a panel of its own, because the
 * chain is a reading OF that segment: seeing where each name sits in the line
 * above is most of the value. Verb and name are separate elements so the
 * styling can hold them apart — حدثنا is how the report travelled, عبد الله بن
 * يوسف is who it travelled through, and running them together is what makes an
 * unfamiliar chain hard to read. */
function narratorChain(chain) {
  const wrap = div('chain');
  for (const hop of chain) {
    const row = div('hop');
    row.append(span('hop-n', hop.n));
    row.append(spanRtl('hop-verb', hop.verb));
    row.append(spanRtl('hop-name', hop.name));
    wrap.append(row);
  }
  return wrap;
}

async function copyOutput() {
  if (!last) return;
  try {
    await navigator.clipboard.writeText(
      last.units.map((u) => unitText(last, u)).join('\n'));
    el.copy.textContent = 'Copied';
    setTimeout(() => { el.copy.textContent = 'Copy text'; }, 1500);
  } catch {
    setError(el.status, 'The browser blocked clipboard access. Use a download instead.');
  }
}

/* ------------------------------------------------------------ elements */

function span(cls, text) {
  const n = document.createElement('span');
  if (cls) n.className = cls;
  n.textContent = text;
  return n;
}

function spanRtl(cls, text) {
  const n = span(cls, text);
  n.dir = 'rtl';
  n.lang = 'ar';
  return n;
}

function div(cls) {
  const n = document.createElement('div');
  if (cls) n.className = cls;
  return n;
}

function p(cls, text) {
  const n = document.createElement('p');
  if (cls) n.className = cls;
  n.textContent = text;
  return n;
}

function cell(tag, text) {
  const n = document.createElement(tag);
  n.textContent = text;
  return n;
}
