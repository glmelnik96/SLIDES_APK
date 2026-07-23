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
  $("#dropText").textContent = "Перетащите файл или нажмите, чтобы выбрать";
  $("#create").disabled = true;
  const hint = $("#createHint");        // Г§7 — объясняем, чего не хватает
  if (hint) hint.hidden = false;
}

function setFile(file) {
  selectedFile = file;
  drop.classList.add("has-file");
  $("#dropText").textContent = "Файл: " + file.name;
  $("#create").disabled = false;
  const hint = $("#createHint");        // Г§7 — файл выбран → причина исчезла
  if (hint) hint.hidden = true;
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
    !$("#draftsWrap").hidden ||
    $("#histlist").children.length > 0;
  el.hidden = hasContent;
  // Пока показан empty-state, секция истории с «Пока пусто» дублирует его — прячем
  // (как на /images: пустая история скрыта, вместо неё сценарии).
  const hist = $("#histlist").closest(".history");
  if (hist) hist.hidden = !hasContent;
}

/* ---- history ---- */
// Иконка инструмента сборки (из файла / конструктор / чат) — острые контуры 24×24.
const HIST_ICON_STROKE = `fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="butt" stroke-linejoin="miter"`;
const HIST_TOOL = {
  htmlnew: { title: "Из файла",
    paths: `<path d="M6 4H14L18 8V20H6Z"/><path d="M14 4V8H18"/><path d="M9 12.5H15"/><path d="M9 15.5H15"/>` },
  manual: { title: "Конструктор",
    paths: `<path d="M4.5 19.5L5.5 15.5L15.7 5.3L18.7 8.3L8.5 18.5Z"/><path d="M5.5 15.5L8.5 18.5"/><path d="M14.4 6.6L17.4 9.6"/>` },
  chat: { title: "Чат-ассистент",
    paths: `<path d="M4 5H20V15H11L7 19V15H4Z"/><path d="M8 9H16"/><path d="M8 11.5H13"/>` },
};
function histToolBadge(mode) {
  const t = HIST_TOOL[mode] || HIST_TOOL.htmlnew;
  return `<span class="hist-tool" title="${t.title}">` +
    `<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><g ${HIST_ICON_STROKE}>${t.paths}</g></svg></span>`;
}
// Расход прогона по-русски: неразрывный пробел в тысячах, запятая в копейках, мм:сс.
const histInt = (n) => Number(n).toLocaleString("ru-RU");
const histRub = (n) => Number(n).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₽";
function histDur(ms) {
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

async function loadHistory() {
  const items = await (await fetch(U("/api/history"))).json();
  const ul = $("#histlist");
  ul.innerHTML = "";
  $("#histEmpty").classList.toggle("hidden", items.length > 0);
  for (const it of items) {
    const li = document.createElement("li");
    const when = it.created_at ? new Date(it.created_at).toLocaleString("ru-RU") : "";
    const ok = it.status === "done";
    const name = it.display_name || it.source_filename || "Без названия";
    // Строка расхода: у готовой сборки — время/токены/деньги (только те, что есть);
    // у неуспешной — причина. Доступные части разделяем средней точкой.
    const seg = [];
    if (when) seg.push(esc(when));
    if (ok) {
      if (it.duration_ms != null) seg.push(`⏱ ${histDur(it.duration_ms)}`);
      if (it.in_tokens != null) seg.push(`↑ ${histInt(it.in_tokens)}`);
      if (it.out_tokens != null) seg.push(`↓ ${histInt(it.out_tokens)}`);
      if (it.cost_rub != null) seg.push(`<span class="cost">≈ ${histRub(it.cost_rub)}</span>`);
    } else if (it.error) {
      seg.push(`<span class="err">${esc(it.error)}</span>`);
    }
    const row2 = seg.join(`<span class="sep">·</span>`);
    // Только у готовой сборки есть дека — показываем «Открыть»; у неуспешной статус
    // виден лейблом рядом с именем (ссылки на 404-деку быть не должно).
    const action = ok
      ? `<a class="btn btn-ghost" href="${U(`/editor?session=${it.id}`)}">Открыть</a>`
      : "";
    li.innerHTML =
      `<div class="hist-main">` +
        `<div class="hist-row1">` +
          histToolBadge(it.mode) +
          `<span class="hist-name">${esc(name)}</span>` +
          `<span class="hist-tag hist-tag--${it.status}">${STAGE_LABEL[it.status] || it.status}</span>` +
        `</div>` +
        `<div class="hist-row2">${row2}</div>` +
      `</div>` +
      action;
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

/* ---- drafts (незавершённая работа: конструктор/чат) ---- */
async function loadDrafts() {
  let items = [];
  try { items = await (await fetch(U("/api/drafts"))).json(); } catch (e) { return; }
  const wrap = $("#draftsWrap");
  const ul = $("#draftsList");
  wrap.hidden = items.length === 0;
  // Две колонки (Черновики │ История) — только когда черновики есть.
  $("#historyGrid")?.classList.toggle("two-col", items.length > 0);
  ul.innerHTML = "";
  for (const it of items) {
    const li = document.createElement("li");
    const label = MODE_LABEL[it.mode] || it.mode;
    const when = it.created_at ? new Date(it.created_at).toLocaleString("ru-RU") : "";
    const meta = ["Без названия", when].filter(Boolean).join(" · ");
    // mode в URL обязателен — иначе редактор откроет сессию как built-деку.
    li.innerHTML =
      `<div><div class="hist-mode">${label}</div>` +
      `<div class="hist-meta">${meta}</div></div>` +
      `<div class="hist-spacer"></div>` +
      `<a class="btn btn-ghost" href="${U(`/editor?session=${it.id}&mode=${it.mode}`)}">Продолжить</a>` +
      `<button class="btn-link" data-del="${it.id}">Удалить</button>`;
    ul.appendChild(li);
  }
  ul.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Удалить черновик? Это действие необратимо.")) return;
      b.disabled = true;  // deleting — «Удалить» неактивна до ответа
      try {
        const r = await fetch(U(`/api/drafts/${b.dataset.del}`), { method: "DELETE" });
        if (!r.ok) throw new Error("draft delete failed");
        loadDrafts();
      } catch (e) {
        b.disabled = false;  // при ошибке вернуть кнопку активной
      }
    }));
  updateEmptyState();
}

/* ---- create job ---- */
// Г§8 — extracted so the result panel's "Повторить с тем же файлом" can re-run the
// build directly (after streamProgress, #create stays disabled, so a synthetic click
// wouldn't fire — we call the function).
async function createJob() {
  if (!selectedFile) return;
  const ex = document.getElementById("exactTransfer");
  const isExact = !!(ex && ex.checked);
  // Client-side gate: «Точный перенос» works only on slide-structured inputs
  // (.pptx) or slide-delimited text (.md/.txt). A .docx has no slide boundaries,
  // so this combo can only fail deep in the worker — reject it instantly with a
  // clear, actionable message, no wasted round-trip or queue slot. The server
  // enforces the same rule (400) as the source of truth.
  if (isExact && !/\.(pptx|md|txt)$/i.test(selectedFile.name || "")) {
    const box = $("#result");
    box.classList.remove("hidden");
    box.classList.add("error");
    box.innerHTML =
      `<h3>Не удалось запустить сборку</h3>` +
      `<p>«Точный перенос» поддерживает .pptx, .md и .txt. ` +
      `Для .docx снимите галочку «Точный перенос» — обычная сборка отлично ` +
      `переносит Word, — либо сохраните файл как .pptx.</p>`;
    updateEmptyState();
    return;
  }
  const fd = new FormData();
  fd.append("mode", MODE);
  fd.append("file", selectedFile);
  if (isExact) fd.append("exact_transfer", "true");
  $("#create").disabled = true;
  const res = await fetch(U("/api/jobs"), { method: "POST", body: fd });
  if (!res.ok) {
    // Surface the failure in the #result panel (own visual system, SB Sans),
    // not a native alert. 429 = queue full; 400 already carries a Russian
    // detail; anything else falls back to raw text in a collapsed <details>.
    const text = await res.text();
    const box = $("#result");
    box.classList.remove("hidden");
    box.classList.add("error");
    let body;
    if (res.status === 429) {
      body = "<p>Очередь занята — дождитесь завершения текущих сборок.</p>";
    } else {
      let detail = "";
      try { detail = JSON.parse(text).detail; } catch (e) { detail = ""; }
      body = detail
        ? `<p>${esc(detail)}</p>`
        : `<details><summary>Детали</summary><pre>${esc(text)}</pre></details>`;
    }
    box.innerHTML = `<h3>Не удалось запустить сборку</h3>${body}`;
    updateEmptyState();
    $("#create").disabled = false;
    return;
  }
  const { session_id, kind } = await res.json();
  streamProgress(session_id, kind);
}
$("#create").onclick = createJob;

/* If no progress event arrives for this many seconds, warn that the step is
   taking long (helps tell a slow model call apart from a real hang). */
const STALL_SECONDS = 30;
let lastEventAt = 0;
let heartbeatTimer = null;
let currentSession = null;
let currentStage = null;
// Live SSE stream for the panel on screen. Hoisted to module scope so «Остановить»
// (§ вариант A) can close it instantly — without waiting for the terminal event.
let currentES = null;
// Consecutive SSE reconnect attempts (Ч§1): incremented on each es.onerror,
// reset on any es.onmessage. Module-scoped so it survives the streamProgress
// re-invocation used to re-attach after a transient drop.
let reconnects = 0;

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
  // A fresh stream starts the reconnect counter clean; a resumed reconnect
  // (initial.resumed) keeps the running count so 5 failures in a row still bail.
  if (!(initial && initial.resumed)) reconnects = 0;
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
  $("#stopBtn").textContent = "Остановить";   // сбрасываем метку после прошлого стопа
  currentSession = sessionId;
  currentStage = seedStage;
  lastEventAt = Date.now();
  stopHeartbeat();
  heartbeatTimer = setInterval(tickHeartbeat, 1000);
  tickHeartbeat();

  // Progress via SSE (the gateway proxies HTTP streaming, not WebSocket).
  let finished = false;
  const es = new EventSource(U(`/api/jobs/${sessionId}/events`));
  currentES = es;
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    lastEventAt = Date.now();
    reconnects = 0;  // a live event arrived — the stream is healthy again
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
      currentES = null;
      stopHeartbeat();
      currentSession = null;
      prog.classList.add("hidden");
      showResult(sessionId, kind, ev);
      loadHistory();
    }
  };
  // Ч§1 — an SSE drop is NOT a build failure: the job keeps running on the
  // server. Keep the progress panel, mark the heartbeat stale, and try to
  // re-attach; only a real terminal `failed` event (handled in onmessage) draws
  // the "Не удалось собрать презентацию" screen.
  es.onerror = () => {
    if (finished) return; // normal stream close after the terminal event
    es.close();
    stopHeartbeat();
    reconnects++;
    const hb = $("#heartbeat");
    if (reconnects > 5) {
      // Five reconnect attempts failed in a row. Stop retrying, but the build
      // may still be alive server-side — keep the panel, be honest, no false
      // "failed" screen.
      if (hb) hb.classList.add("stale");
      $("#stageDetail").textContent =
        "Связь с сервером потеряна. Обновите страницу — если сборка продолжается, прогресс подхватится автоматически.";
      return;
    }
    if (hb) {
      hb.classList.add("stale");
      hb.textContent = `Связь прервалась — переподключаюсь… (попытка ${reconnects} из 5)`;
    }
    setTimeout(async () => {
      if (currentSession !== sessionId) return;  // superseded by another stream
      let items = [];
      try { items = await (await fetch(U("/api/jobs/active"))).json(); } catch (e) {}
      const job = items.find((it) => it.session_id === sessionId);
      if (job) {
        // Still building — re-attach, seeding the panel from its known state.
        streamProgress(sessionId, kind,
          { stage: job.stage, progress_pct: job.progress_pct, resumed: true });
        return;
      }
      // No longer active — it finished (or failed) while we were disconnected.
      let hist = [];
      try { hist = await (await fetch(U("/api/history"))).json(); } catch (e) {}
      const rec = hist.find((it) => it.id === sessionId);
      if (rec) {
        stopHeartbeat();
        currentSession = null;
        prog.classList.add("hidden");
        showResult(sessionId, kind, { stage: rec.status, error: rec.error });
      }
      // Neither active nor in history yet: leave the panel as-is (no false
      // failure); a refresh or the next poll will resolve it.
    }, 3000);
  };
}

$("#stopBtn").onclick = async () => {
  if (!currentSession) return;
  const sid = currentSession;
  // Вариант A — мгновенная реакция. Не ждём терминального события (уже идущие
  // вызовы модели ещё доходят до конца ~до минуты): сразу закрываем поток и прячем
  // прогресс. Кнопка остаётся на «Останавливаю…» — финальный экран НЕ показываем
  // (сборка ещё доедет и сама уйдёт в «Историю» как «Отменено»).
  $("#stopBtn").disabled = true;
  $("#stopBtn").textContent = "Останавливаю…";
  $("#stageDetail").textContent = "Останавливаю…";
  if (currentES) { currentES.close(); currentES = null; }
  stopHeartbeat();
  currentSession = null;
  $("#progress").classList.add("hidden");
  updateEmptyState();
  loadActive();               // сборка ещё «активна», пока доедает начатое
  try {
    await fetch(U(`/api/jobs/${sid}/cancel`), { method: "POST" });
  } catch (e) { /* запрос best-effort — UI уже обновлён */ }
  loadActive();
  loadHistory();
};

function showResult(sessionId, kind, ev) {
  const box = $("#result");
  box.classList.remove("hidden");
  const failed = ev.stage === "failed";
  box.classList.toggle("error", failed);
  if (failed || ev.stage === "cancelled") {
    // Г§8 — recovery that keeps the user's context: retry with the SAME file
    // (and «Точный перенос» choice), or pick another. ev.error is escaped —
    // it can carry engine text. If the file is gone (e.g. a build resumed after
    // reload), only «Выбрать другой файл» is offered.
    const title = failed ? "Не удалось собрать презентацию" : "Сборка остановлена";
    const msg = failed
      ? esc(ev.error || "Произошла ошибка.")
      : "Генерация прервана по запросу.";
    const retry = selectedFile
      ? `<button class="btn btn-accent" data-res="retry">Повторить с тем же файлом</button>`
      : "";
    box.innerHTML =
      `<h3>${title}</h3><p>${msg}</p>` +
      `<div class="res-actions">${retry}` +
      `<button class="btn btn-ghost" data-res="other">Выбрать другой файл</button></div>`;
    const retryBtn = box.querySelector('[data-res="retry"]');
    if (retryBtn) retryBtn.addEventListener("click", () => {
      box.classList.add("hidden");
      updateEmptyState();
      createJob();
    });
    box.querySelector('[data-res="other"]').addEventListener("click", () => {
      resetFile();
      box.classList.add("hidden");
      updateEmptyState();
      $("#drop").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    });
    updateEmptyState();
    return;
  }
  // HTML deck — go straight to the editor.
  location.href = U(`/editor?session=${sessionId}`);
}

/* entry cards: choose how to start (upload | manual draft | chat draft) */
async function startDraft(mode, btn) {
  const prev = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Создаю…";
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
    btn.classList.add("is-error");  // Г§6 — ошибка одета в danger, не в акцент
    btn.textContent = "Ошибка, попробуйте ещё раз";
    setTimeout(() => { btn.classList.remove("is-error"); btn.textContent = prev; }, 4000);
  }
}

// Зоны точек входа: кликабельна вся рамка .entry-alt (не только ссылка). Черновик
// создаётся при клике/Enter/Space; ссылка внутри — лишь визуальная подсказка.
document.querySelectorAll(".entry-alt").forEach((card) => {
  const mode = card.dataset.mode;
  const label = card.querySelector(".entry-open");
  if (!mode || !label) return;
  const go = () => {
    if (card.dataset.busy) return;  // не плодим черновики по дабл-клику
    card.dataset.busy = "1";
    Promise.resolve(startDraft(mode, label)).finally(() => { delete card.dataset.busy; });
  };
  card.addEventListener("click", go);
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
  });
});

// Возврат по Back из bfcache мог оставить кнопку залипшей на «Создаю…»/disabled — сбрасываем.
const _OPEN_LABEL = { openChat: "Открыть чат →", openManual: "Открыть конструктор →" };
window.addEventListener("pageshow", () => {
  Object.entries(_OPEN_LABEL).forEach(([id, label]) => {
    const b = $("#" + id);
    if (b) { b.disabled = false; b.classList.remove("is-error"); b.textContent = label; }
  });
  loadDrafts();  // возврат по Back мог оставить/удалить черновик — обновляем список
});

/* init */
// Г§4 — empty-state превью настоящего слайда: src через U() (несёт gateway-префикс);
// lazy + скрытый родитель → грузится только когда empty-state показан.
const _emptyPrev = $("#emptyPrev");
if (_emptyPrev) _emptyPrev.src = U("/api/templates/cover/preview");
// Г§9 — объявляем окно ретеншена (истории и черновиков) прямо в заголовках секций.
const _retHours = window.__RETENTION_HOURS__ || 24;
const _retCap = $("#retentionCap");
if (_retCap) _retCap.textContent = "хранится " + _retHours + " ч";
const _draftsCap = $("#draftsCap");
if (_draftsCap) _draftsCap.textContent = "хранится " + _retHours + " ч";
resetFile();
loadHistory();
loadActive();
loadDrafts();
autoResumeActive();
