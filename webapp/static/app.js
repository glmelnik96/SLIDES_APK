const $ = (s) => document.querySelector(s);

const ACCEPT = {
  verstai: ".pptx",
  design: ".pptx",
  htmlnew: ".md,.txt,.docx,.pptx",
};
const HINT = {
  verstai: "Допустимо: .pptx",
  design: "Допустимо: .pptx",
  htmlnew: "Допустимо: .md, .txt, .docx, .pptx",
};
const MODE_LABEL = {
  verstai: "Ребрендинг PPTX",
  design: "Генерация PPTX",
  htmlnew: "HTML-презентация",
};
const STAGE_LABEL = {
  queued: "В очереди",
  parsing: "Разбор документа",
  classifying: "Планирование структуры",
  designing: "Заполнение слайдов",
  rendering: "Сборка",
  validating: "Проверка качества",
  autofixing: "Автоисправление",
  finalizing: "Финализация",
  done: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено",
};

let selectedFile = null;

function selectedMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

function syncModeHints() {
  const m = selectedMode();
  $("#file").setAttribute("accept", ACCEPT[m]);
  $("#dropHint").textContent = HINT[m];
}

document.querySelectorAll('input[name="mode"]').forEach((r) =>
  r.addEventListener("change", () => { syncModeHints(); resetFile(); }));

/* ---- file selection (click + drag&drop) ---- */
const drop = $("#drop");
const fileInput = $("#file");

function resetFile() {
  selectedFile = null;
  fileInput.value = "";
  drop.classList.remove("has-file");
  $("#dropText").textContent = "Перетащите файл сюда или нажмите, чтобы выбрать";
  $("#create").disabled = true;
}

function setFile(file) {
  selectedFile = file;
  drop.classList.add("has-file");
  $("#dropText").textContent = "Файл: " + file.name;
  $("#create").disabled = false;
}

fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) setFile(fileInput.files[0]);
});
["dragenter", "dragover"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("dragover"); }));
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("dragover"); }));
drop.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
});

/* ---- history ---- */
async function loadHistory() {
  const items = await (await fetch("/api/history")).json();
  const ul = $("#histlist");
  ul.innerHTML = "";
  $("#histEmpty").classList.toggle("hidden", items.length > 0);
  for (const it of items) {
    const li = document.createElement("li");
    const when = it.created_at ? new Date(it.created_at).toLocaleString("ru-RU") : "";
    const label = MODE_LABEL[it.mode] || it.mode;
    const action = it.kind === "html"
      ? `<a class="btn btn-ghost" href="/editor?session=${it.id}">Открыть</a>`
      : `<a class="btn btn-ghost" href="/api/jobs/${it.id}/result">Скачать .pptx</a>`;
    li.innerHTML =
      `<div><div class="hist-mode">${label}</div>` +
      `<div class="hist-meta">${it.source_filename || ""} &middot; ${when}</div></div>` +
      `<div class="hist-spacer"></div>${action}`;
    ul.appendChild(li);
  }
}

$("#clear").onclick = async () => {
  await fetch("/api/history/clear", { method: "POST" });
  loadHistory();
};

/* ---- active queue ---- */
const MAX_ACTIVE = 5;

async function loadActive() {
  let items = [];
  try { items = await (await fetch("/api/jobs/active")).json(); } catch (e) { return; }
  const wrap = $("#activeWrap");
  const ul = $("#activeList");
  wrap.hidden = items.length === 0;
  $("#queueCap").textContent = `${items.length} / ${MAX_ACTIVE}`;
  ul.innerHTML = "";
  for (const it of items) {
    const running = it.stage && it.stage !== "queued";
    const li = document.createElement("li");
    const label = MODE_LABEL[it.mode] || it.mode;
    const state = running
      ? `${STAGE_LABEL[it.stage] || it.stage} · ${it.progress_pct || 0}%`
      : "В очереди";
    const kind = (it.mode === "verstai" || it.mode === "design") ? "pptx" : "html";
    li.innerHTML =
      `<div><div class="hist-mode">${label}</div>` +
      `<div class="hist-meta">${state}</div></div>` +
      `<div class="hist-spacer"></div>` +
      `<button class="btn btn-ghost" data-open="${it.session_id}" data-kind="${kind}">Открыть</button>` +
      `<button class="btn-link btn-stop" data-stop="${it.session_id}">Остановить</button>`;
    ul.appendChild(li);
  }
  ul.querySelectorAll("[data-open]").forEach((b) =>
    b.addEventListener("click", () => streamProgress(b.dataset.open, b.dataset.kind)));
  ul.querySelectorAll("[data-stop]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      await fetch(`/api/jobs/${b.dataset.stop}/cancel`, { method: "POST" });
      loadActive();
    }));
}

setInterval(loadActive, 2000);

/* ---- create job ---- */
$("#create").onclick = async () => {
  if (!selectedFile) return;
  const fd = new FormData();
  fd.append("mode", selectedMode());
  fd.append("file", selectedFile);
  $("#create").disabled = true;
  const res = await fetch("/api/jobs", { method: "POST", body: fd });
  if (!res.ok) {
    alert("Ошибка: " + (await res.text()));
    $("#create").disabled = false;
    return;
  }
  const { session_id, kind } = await res.json();
  streamProgress(session_id, kind);
};

/* If no progress event arrives for this many seconds, warn that the step is
   taking long (helps tell a slow model call apart from a real hang). */
const STALL_SECONDS = 30;
let lastEventAt = 0;
let heartbeatTimer = null;
let currentSession = null;

function logLine(stage, detail) {
  const log = $("#progressLog");
  const time = new Date().toLocaleTimeString("ru-RU");
  const label = STAGE_LABEL[stage] || stage || "";
  const text = detail ? `${label} · ${detail}` : label;
  const last = log.lastElementChild;
  if (last && last.dataset.text === text) return;  // skip identical repeats
  const row = document.createElement("div");
  row.dataset.text = text;
  row.innerHTML = `<span class="log-time">${time}</span>${text}`;
  log.appendChild(row);
  while (log.children.length > 100) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
}

function tickHeartbeat() {
  const hb = $("#heartbeat");
  if (!hb || !lastEventAt) return;
  const secs = Math.round((Date.now() - lastEventAt) / 1000);
  if (secs >= STALL_SECONDS) {
    hb.classList.add("stale");
    hb.textContent = `нет событий уже ${secs} сек — идёт долгий шаг модели, обычно это нормально`;
  } else {
    hb.classList.remove("stale");
    hb.textContent = secs <= 1 ? "обновлено только что" : `обновлено ${secs} сек назад`;
  }
}

function stopHeartbeat() {
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
}

function streamProgress(sessionId, kind) {
  const prog = $("#progress");
  prog.classList.remove("hidden");
  $("#result").classList.add("hidden");
  $("#barfill").style.width = "0%";
  $("#stageLabel").textContent = "Подготовка…";
  $("#stagePct").textContent = "0%";
  $("#stageDetail").textContent = "";
  $("#progressLog").innerHTML = "";
  $("#stopBtn").disabled = false;
  currentSession = sessionId;
  lastEventAt = Date.now();
  stopHeartbeat();
  heartbeatTimer = setInterval(tickHeartbeat, 1000);
  tickHeartbeat();

  const ws = new WebSocket(`ws://${location.host}/ws/${sessionId}`);
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    lastEventAt = Date.now();
    tickHeartbeat();
    const pct = ev.progress_pct || 0;
    $("#barfill").style.width = pct + "%";
    $("#stagePct").textContent = pct + "%";
    $("#stageLabel").textContent = STAGE_LABEL[ev.stage] || ev.stage || "";
    if (ev.detail) $("#stageDetail").textContent = ev.detail;
    logLine(ev.stage, ev.detail || ev.error);
    if (ev.terminal) {
      ws.close();
      stopHeartbeat();
      currentSession = null;
      prog.classList.add("hidden");
      showResult(sessionId, kind, ev);
      loadHistory();
    }
  };
  ws.onerror = () => {
    stopHeartbeat();
    prog.classList.add("hidden");
    showResult(sessionId, kind, { stage: "failed", error: "Потеряно соединение с сервером" });
  };
}

$("#stopBtn").onclick = async () => {
  if (!currentSession) return;
  $("#stopBtn").disabled = true;
  $("#stageDetail").textContent = "Останавливаю…";
  try {
    await fetch(`/api/jobs/${currentSession}/cancel`, { method: "POST" });
  } catch (e) {
    $("#stopBtn").disabled = false;
  }
};

function showResult(sessionId, kind, ev) {
  const box = $("#result");
  box.classList.remove("hidden");
  box.classList.toggle("error", ev.stage === "failed");
  if (ev.stage === "cancelled") {
    box.innerHTML =
      `<h3>Сборка остановлена</h3>` +
      `<p>Генерация прервана по запросу.</p>` +
      `<div class="res-actions"><button class="btn" onclick="location.reload()">Начать заново</button></div>`;
    return;
  }
  if (ev.stage === "failed") {
    box.innerHTML =
      `<h3>Не удалось собрать презентацию</h3>` +
      `<p>${ev.error || "Произошла ошибка."}</p>` +
      `<div class="res-actions"><button class="btn" onclick="location.reload()">Начать заново</button></div>`;
    return;
  }
  if (kind === "pptx") {
    box.innerHTML =
      `<h3>Презентация готова</h3>` +
      `<p>Файл .pptx собран по бренду Cloud.ru.</p>` +
      `<div class="res-actions">` +
      `<a class="btn" href="/api/jobs/${sessionId}/result">Скачать .pptx</a>` +
      `<button class="btn btn-ghost" onclick="location.reload()">Создать ещё</button></div>`;
  } else {
    // HTML deck — go straight to the editor.
    location.href = `/editor?session=${sessionId}`;
  }
}

/* init */
syncModeHints();
resetFile();
loadHistory();
loadActive();
