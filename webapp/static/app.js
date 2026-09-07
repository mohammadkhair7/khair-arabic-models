/* Khair Arabic Models - inference UI.
 *
 * Plain ES modules-free JavaScript: no build step, no dependencies, nothing
 * loaded from a third party. Every piece of model output reaches the DOM
 * through textContent, never innerHTML, so a document full of angle brackets
 * renders as the text it is.
 */
'use strict';

const $ = (id) => document.getElementById(id);

const el = {
  tasks: $('tasks'), text: $('text'), textHint: $('text-hint'),
  file: $('file'), drop: $('drop'), dropLabel: $('drop-label'),
  fileHint: $('file-hint'), columnRow: $('column-row'), column: $('column'),
  tabPaste: $('tab-paste'), tabFile: $('tab-file'),
  panePaste: $('pane-paste'), paneFile: $('pane-file'),
  run: $('run'), status: $('status'), results: $('results'),
  meta: $('meta'), notes: $('notes'), downloads: $('download-buttons'),
  output: $('output'), showSource: $('show-source'), copy: $('copy'),
  previewNote: $('preview-note'),
};

let config = null;
let mode = 'paste';
let task = 'tashkeel';
let last = null;          // the most recent /api/process response

/* ---------------------------------------------------------------- setup */

init();

async function init() {
  try {
    config = await (await fetch('/api/config')).json();
  } catch {
    return setError('Could not reach the server. Is it still running?');
  }
  buildTasks();
  buildHints();
  wire();
}

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

    label.append(radio,
      span('task-title', t.label),
      spanRtl('task-title-ar', t.label_ar),
      span('task-desc', t.description));

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

function wire() {
  el.tabPaste.addEventListener('click', () => setMode('paste'));
  el.tabFile.addEventListener('click', () => setMode('file'));
  el.file.addEventListener('change', onPick);
  el.run.addEventListener('click', submit);
  el.showSource.addEventListener('change', () => last && draw(last));
  el.copy.addEventListener('click', copyOutput);

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

  // Ctrl/Cmd+Enter runs, which is what anyone pasting a long passage expects.
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
  const tabular = /\.(csv|xlsx)$/i.test(file.name);
  el.columnRow.classList.toggle('is-hidden', !tabular);
  setMode('file');
}

/* ------------------------------------------------------------- request */

async function submit() {
  const body = new FormData();
  body.append('task', task);
  if (mode === 'paste') {
    if (!el.text.value.trim()) return setError('Please paste some Arabic text first.');
    body.append('text', el.text.value);
  } else {
    if (!el.file.files[0]) return setError('Please choose a file first.');
    body.append('file', el.file.files[0]);
    if (el.column.value.trim()) body.append('column', el.column.value.trim());
  }

  busy(true, 'Running the model. The first request also loads it, so it can take a few seconds');
  try {
    const response = await fetch('/api/process', { method: 'POST', body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return setError(data.error || data.detail || `Request failed (${response.status}).`);
    last = data;
    draw(data);
    setStatus(`Done in ${data.elapsed_s}s.`);
  } catch {
    setError('Could not reach the server.');
  } finally {
    busy(false);
  }
}

function busy(on, message) {
  el.run.disabled = on;
  if (on) {
    el.status.className = 'status is-busy';
    el.status.textContent = message;
  }
}

function setStatus(message) {
  el.status.className = 'status';
  el.status.textContent = message;
}

function setError(message) {
  el.status.className = 'status is-error';
  el.status.textContent = message;
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
  const rows = [
    ['Task', data.task_label],
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
      a.href = `/api/download/${encodeURIComponent(data.token)}/${fmt.key}`;
      a.setAttribute('download', '');
    } else {
      a.title = 'Unavailable: the server has no Arabic font installed for PDF output.';
    }
    el.downloads.append(a);
  }
}

function drawOutput(data) {
  el.output.textContent = '';
  const withSource = el.showSource.checked;

  if (data.is_table) return drawTable(data);

  for (const unit of data.units) {
    if (!unit.output.trim()) {
      el.output.append(div('blank'));
      continue;
    }
    if (withSource) el.output.append(p('src', unit.source));
    if (data.task === 'pos') el.output.append(pairsRow(unit));
    else if (data.task === 'structure') el.output.append(segments(unit));
    else el.output.append(p('', unit.output));
  }
}

function drawTable(data) {
  const table = document.createElement('table');
  table.className = 'grid';
  const head = document.createElement('tr');
  for (const name of data.columns) head.append(cell('th', name));
  head.append(cell('th', data.task));
  table.append(head);

  for (const unit of data.units) {
    const tr = document.createElement('tr');
    // The preview only needs the source column and the result; the exporters
    // are what reproduce the user's full sheet.
    tr.append(cell('td', unit.source));
    const out = cell('td', unit.output);
    out.className = 'out';
    tr.append(out);
    table.append(tr);
  }
  // Re-header to match the two columns we actually show.
  head.textContent = '';
  head.append(cell('th', 'input'), cell('th', data.task));
  el.output.append(table);
}

function pairsRow(unit) {
  const wrap = div('');
  for (const [word, tag] of unit.pairs) {
    const tok = document.createElement('span');
    tok.className = 'tok';
    tok.append(span('w', word), span('t', tag));
    wrap.append(tok);
  }
  return wrap;
}

function segments(unit) {
  const wrap = div('');
  for (const [label, chunk] of unit.pairs) {
    const row = div('seg');
    const lab = span('lab lab-' + label.replace(/[^A-Z]/g, ''), label);
    row.append(lab, span('txt', chunk));
    wrap.append(row);
  }
  return wrap;
}

async function copyOutput() {
  if (!last) return;
  const text = last.units.map((u) => u.output).join('\n');
  try {
    await navigator.clipboard.writeText(text);
    el.copy.textContent = 'Copied';
    setTimeout(() => { el.copy.textContent = 'Copy text'; }, 1500);
  } catch {
    setError('The browser blocked clipboard access. Use a download instead.');
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
