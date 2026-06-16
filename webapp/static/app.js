const $ = (s) => document.querySelector(s);

function selectedMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

async function loadHistory() {
  const items = await (await fetch('/api/history')).json();
  const ul = $('#histlist');
  ul.innerHTML = '';
  for (const it of items) {
    const li = document.createElement('li');
    const when = new Date(it.created_at).toLocaleString();
    const action = it.kind === 'html'
      ? `<a href="/editor?session=${it.id}">открыть</a>`
      : `<a href="/api/jobs/${it.id}/result">скачать .pptx</a>`;
    li.innerHTML = `<span>${it.mode} · ${it.source_filename || ''} · ${when}</span>${action}`;
    ul.appendChild(li);
  }
}

$('#clear').onclick = async () => { await fetch('/api/history/clear', {method:'POST'}); loadHistory(); };

$('#create').onclick = async () => {
  const file = $('#file').files[0];
  if (!file) { alert('Выберите файл'); return; }
  const fd = new FormData();
  fd.append('mode', selectedMode());
  fd.append('file', file);
  const res = await fetch('/api/jobs', {method:'POST', body: fd});
  if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }
  const { session_id, kind } = await res.json();
  streamProgress(session_id, kind);
};

function streamProgress(sessionId, kind) {
  $('#progress').classList.remove('hidden');
  $('#result').classList.add('hidden');
  const ws = new WebSocket(`ws://${location.host}/ws/${sessionId}`);
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    $('#barfill').style.width = (ev.progress_pct || 0) + '%';
    $('#stage').textContent = (ev.stage || '') + ' — ' + (ev.detail || '');
    if (ev.terminal) {
      ws.close();
      $('#progress').classList.add('hidden');
      showResult(sessionId, kind, ev);
      loadHistory();
    }
  };
}

function showResult(sessionId, kind, ev) {
  const box = $('#result');
  box.classList.remove('hidden');
  if (ev.stage === 'failed') {
    box.innerHTML = `<p>Ошибка: ${ev.error || 'сбой'}</p>
      <button class="btn" onclick="location.reload()">Начать заново</button>`;
    return;
  }
  if (kind === 'pptx') {
    box.innerHTML = `<p>Готово.</p>
      <a class="btn" href="/api/jobs/${sessionId}/result">Скачать .pptx</a>`;
  } else {
    location.href = `/editor?session=${sessionId}`;
  }
}

loadHistory();
