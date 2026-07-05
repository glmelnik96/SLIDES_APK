const $ = (s) => document.querySelector(s);

// HTML-escape untrusted strings before innerHTML (e.g. an engine error message
// surfaced in history) so they can't inject markup.
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// The gateway mounts this app under a URL prefix (e.g. /slides) and the browser
// lives at /<prefix>/, so every API/WS/navigation URL must carry it. Empty for
// standalone local dev. Injected by the server into the page.
const PREFIX = window.__APP_PREFIX__ || "";
const U = (p) => PREFIX + p;

// App2 is HTML-only: uploads always build with the single engine mode.
const MODE = "htmlnew";
// Job rows carry the entry-point mode: htmlnew (upload) / manual / chat.
const MODE_LABEL = {
  htmlnew: "HTML-презентация",
  manual: "Конструктор",
  chat: "Чат-ассистент",
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

// One-line "what's happening now" in plain language, expanded so the user knows
// roughly how long each phase takes and that waiting is normal.
const STAGE_HINT = {
  queued: "Сборка скоро начнётся — вы в очереди.",
  parsing: "Читаю ваш документ.",
  classifying: "Продумываю структуру презентации — это один из самых долгих шагов (до пары минут).",
  designing: "Оформляю слайды по очереди. Каждый слайд модель пишет отдельно, поэтому это занимает время.",
  rendering: "Собираю презентацию воедино.",
  validating: "Проверяю внешний вид каждого слайда на скриншотах — шаг небыстрый.",
  autofixing: "Улучшаю слайды по результатам проверки.",
  finalizing: "Почти готово.",
};

// Engine progress strings (e.g. "fill: слайд 4/5") → friendly Russian for the
// detail line. Source of these prefixes: htmlslides/pipeline + worker/tasks.
function friendlyDetail(detail) {
  if (!detail) return "";
  let m;
  if ((m = detail.match(/^fill:\s*слайд\s+(\d+)\s*\/\s*(\d+)/)))
    return `Оформляю слайд ${m[1]} из ${m[2]}`;
  if ((m = detail.match(/^vision-qa:\s*слайд\s+(\d+)/)))
    return `Проверяю внешний вид слайда ${m[1]}`;
  if (detail.startsWith("старт")) return "Запускаю сборку";
  if (detail.startsWith("parse:")) return "Читаю документ";
  if (detail.startsWith("rebrand")) return "Анализирую исходные слайды";
  if (detail.startsWith("plan:")) return "Продумываю структуру презентации";
  if (detail.startsWith("fill: заполняю")) return "Оформляю слайды";
  if (detail.startsWith("assemble:")) return "Собираю презентацию";
  if (detail.startsWith("lint:")) return "Проверяю вёрстку";
  if (detail.startsWith("vision-qa")) return "Проверяю внешний вид";
  if (detail.startsWith("autofix:")) return "Улучшаю слайды по результатам проверки";
  if (detail.startsWith("done:")) return "Готово";
  return "";  // warnings & internal notes: keep them out of the user-facing line
}

let selectedFile = null;

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

/* ---- empty state (канон v4): показать при первом входе, скрыть при контенте ---- */
function updateEmptyState() {
  const el = $("#workspaceEmpty");
  if (!el) return;
  const hasContent =
    !$("#progress").classList.contains("hidden") ||
    !$("#result").classList.contains("hidden") ||
    !$("#activeWrap").hidden ||
    $("#histlist").children.length > 0;
  el.hidden = hasContent;
  // Пока показан empty-state, секция истории с «Пока пусто» дублирует его — прячем
  // (как на /images: пустая история скрыта, вместо неё сценарии).
  const hist = $("#histlist").closest(".history");
  if (hist) hist.hidden = !hasContent;
}

/* ---- history ---- */
async function loadHistory() {
  const items = await (await fetch(U("/api/history"))).json();
  const ul = $("#histlist");
  ul.innerHTML = "";
  $("#histEmpty").classList.toggle("hidden", items.length > 0);
  for (const it of items) {
    const li = document.createElement("li");
    const when = it.created_at ? new Date(it.created_at).toLocaleString("ru-RU") : "";
    const label = MODE_LABEL[it.mode] || it.mode;
    // Only a successful build has a deck to open. failed/cancelled builds show
    // their outcome (and the reason) instead of an "Открыть" link that would
    // lead to a 404 deck in the editor.
    const ok = it.status === "done";
    const action = ok
      ? `<a class="btn btn-ghost" href="${U(`/editor?session=${it.id}`)}">Открыть</a>`
      : `<span class="hist-status hist-status--${it.status}">${STAGE_LABEL[it.status] || it.status}</span>`;
    const meta = ok || !it.error
      ? `${it.source_filename || ""} &middot; ${when}`
      : `${it.source_filename || ""} &middot; ${when} &middot; ${esc(it.error)}`;
    li.innerHTML =
      `<div><div class="hist-mode">${label}</div>` +
      `<div class="hist-meta">${meta}</div></div>` +
      `<div class="hist-spacer"></div>${action}`;
    ul.appendChild(li);
  }
  updateEmptyState();
}

$("#clear").onclick = async () => {
  await fetch(U("/api/history/clear"), { method: "POST" });
  loadHistory();
};

/* ---- active queue ---- */
const MAX_ACTIVE = 5;

async function loadActive() {
  let items = [];
  try { items = await (await fetch(U("/api/jobs/active"))).json(); } catch (e) { return; }
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
    li.innerHTML =
      `<div><div class="hist-mode">${label}</div>` +
      `<div class="hist-meta">${state}</div></div>` +
      `<div class="hist-spacer"></div>` +
      `<button class="btn btn-ghost" data-open="${it.session_id}" data-kind="html">Открыть</button>` +
      `<button class="btn-link btn-stop" data-stop="${it.session_id}">Остановить</button>`;
    ul.appendChild(li);
  }
  ul.querySelectorAll("[data-open]").forEach((b) =>
    b.addEventListener("click", () => streamProgress(b.dataset.open, b.dataset.kind)));
  ul.querySelectorAll("[data-stop]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      await fetch(U(`/api/jobs/${b.dataset.stop}/cancel`), { method: "POST" });
      loadActive();
    }));
  updateEmptyState();
}

setInterval(loadActive, 2000);

// Contract §8 — rehydrate the in-flight run after a full page reload (canon-nav
// links reload the page, dropping the SSE/JS state, but the build keeps running
// server-side). On load, if a run is active and we're not already streaming one,
// re-attach the progress view so navigation away and back never looks "stopped".
async function autoResumeActive() {
  if (currentSession) return;
  let items = [];
  try { items = await (await fetch(U("/api/jobs/active"))).json(); } catch (e) { return; }
  if (!items.length || currentSession) return;
  // Seed the panel from the run's known stage/pct so returning to the page shows
  // live progress immediately (not a reset "0%"), making clear the build kept going.
  const it = items[0];
  streamProgress(it.session_id, "html",
                 { stage: it.stage, progress_pct: it.progress_pct, resumed: true });
}

/* ---- create job ---- */
$("#create").onclick = async () => {
  if (!selectedFile) return;
  const fd = new FormData();
  fd.append("mode", MODE);
  fd.append("file", selectedFile);
  $("#create").disabled = true;
  const res = await fetch(U("/api/jobs"), { method: "POST", body: fd });
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
let currentStage = null;

function logLine(stage, detail) {
  const log = $("#progressLog");
  const time = new Date().toLocaleTimeString("ru-RU");
  const friendly = friendlyDetail(detail);
  const label = STAGE_LABEL[stage] || stage || "";
  const text = friendly || label;
  if (!text) return;
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
    const where = STAGE_HINT[currentStage] || "идёт долгий шаг модели.";
    hb.textContent = `Обрабатываю уже ${secs} сек — это нормально: ${where}`;
  } else {
    hb.classList.remove("stale");
    hb.textContent = secs <= 1 ? "обновлено только что" : `обновлено ${secs} сек назад`;
  }
}

function stopHeartbeat() {
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
}

function streamProgress(sessionId, kind, initial) {
  const prog = $("#progress");
  prog.classList.remove("hidden");
  $("#result").classList.add("hidden");
  updateEmptyState();
  const seedPct = initial && initial.progress_pct ? initial.progress_pct : 0;
  const seedStage = initial && initial.stage ? initial.stage : null;
  $("#barfill").style.width = seedPct + "%";
  $("#stageLabel").textContent = seedStage ? (STAGE_LABEL[seedStage] || seedStage) : "Подготовка…";
  $("#stagePct").textContent = seedPct + "%";
  $("#stageDetail").textContent = initial && initial.resumed
    ? "Сборка продолжается — она не прерывается при переходе между разделами."
    : "Полная сборка обычно занимает несколько минут. Можно уйти со страницы или переключить раздел — сборка не прервётся, прогресс сохранится.";
  $("#progressLog").innerHTML = "";
  $("#stopBtn").disabled = false;
  currentSession = sessionId;
  currentStage = seedStage;
  lastEventAt = Date.now();
  stopHeartbeat();
  heartbeatTimer = setInterval(tickHeartbeat, 1000);
  tickHeartbeat();

  // Progress via SSE (the gateway proxies HTTP streaming, not WebSocket).
  let finished = false;
  const es = new EventSource(U(`/api/jobs/${sessionId}/events`));
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    lastEventAt = Date.now();
    tickHeartbeat();
    const pct = ev.progress_pct || 0;
    currentStage = ev.stage || currentStage;
    $("#barfill").style.width = pct + "%";
    $("#stagePct").textContent = pct + "%";
    $("#stageLabel").textContent = STAGE_LABEL[ev.stage] || ev.stage || "";
    // Detail line: live step in plain language, falling back to the stage hint.
    const friendly = friendlyDetail(ev.detail);
    $("#stageDetail").textContent =
      friendly || STAGE_HINT[ev.stage] || $("#stageDetail").textContent;
    logLine(ev.stage, ev.detail || ev.error);
    if (ev.terminal) {
      finished = true;
      es.close();
      stopHeartbeat();
      currentSession = null;
      prog.classList.add("hidden");
      showResult(sessionId, kind, ev);
      loadHistory();
    }
  };
  es.onerror = () => {
    if (finished) return; // normal stream close after the terminal event
    es.close();
    stopHeartbeat();
    currentSession = null;
    prog.classList.add("hidden");
    showResult(sessionId, kind, { stage: "failed", error: "Потеряно соединение с сервером" });
  };
}

$("#stopBtn").onclick = async () => {
  if (!currentSession) return;
  $("#stopBtn").disabled = true;
  $("#stageDetail").textContent = "Останавливаю…";
  try {
    await fetch(U(`/api/jobs/${currentSession}/cancel`), { method: "POST" });
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
  // HTML deck — go straight to the editor.
  location.href = U(`/editor?session=${sessionId}`);
}

/* entry cards: choose how to start (upload | manual draft | chat draft) */
async function startDraft(mode, btn) {
  const prev = btn.textContent;
  btn.disabled = true;
  btn.querySelector(".entry-cta").textContent = "Создаю…";
  try {
    const r = await fetch(U("/api/drafts"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    if (!r.ok) throw new Error("draft create failed");
    const { session_id } = await r.json();
    location.href = U(`/editor?session=${session_id}&mode=${mode}`);
  } catch (e) {
    btn.disabled = false;
    btn.querySelector(".entry-cta").textContent = "Ошибка, попробуйте ещё раз";
  }
}

const ENTRY_CTA = { upload: "Выбрано ↓", manual: "Открыть конструктор →",
                    chat: "Открыть чат →" };

document.querySelectorAll(".entry-card").forEach((card) => {
  card.onclick = () => {
    const entry = card.dataset.entry;
    if (entry === "upload") {
      document.querySelectorAll(".entry-card").forEach((c) =>
        c.classList.toggle("is-active", c === card));
      $("#uploadFlow").classList.remove("hidden");
      $("#uploadFlow").scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      startDraft(entry, card);
    }
  };
});

// Returning via browser Back restores this page from the bfcache with a draft
// card still stuck "Создаю…"/disabled — re-enable cards so they're clickable again.
window.addEventListener("pageshow", () => {
  document.querySelectorAll(".entry-card").forEach((c) => {
    c.disabled = false;
    const cta = c.querySelector(".entry-cta");
    if (cta) cta.textContent = ENTRY_CTA[c.dataset.entry] || "";
  });
});

/* init */
resetFile();
loadHistory();
loadActive();
autoResumeActive();
