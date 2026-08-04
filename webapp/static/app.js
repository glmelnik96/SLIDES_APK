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
  // "limit:" — сообщения о принятых за пользователя решениях (напр. план обрезан
  // по потолку слайдов). Движок пишет их сразу по-русски, поэтому показываем
  // дословно: молча отдать неполную деку было бы нечестно.
  if (detail.startsWith("limit:")) return detail.slice(6).trim();
  // "notice:" — решения, принятые за пользователя во время сборки (напр. переезд
  // на резервную модель, когда основная не отвечает). Показываем дословно и
  // отдельно от "limit:": тот помечает деку как обрезанную в статистике.
  if (detail.startsWith("notice:")) return detail.slice(7).trim();
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

/* ---- icons + расход формата ---- */
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
function toolSvg(mode) {
  const t = HIST_TOOL[mode] || HIST_TOOL.htmlnew;
  return `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><g ${HIST_ICON_STROKE}>${t.paths}</g></svg>`;
}
function toolTitle(mode) { return (HIST_TOOL[mode] || HIST_TOOL.htmlnew).title; }
// Галочка «Готово» — тот же острый контур 24×24, что и у иконок инструментов.
const CHECK_SVG = `<svg class="st-ico" viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M5 12L10 17L19 6.5" ${HIST_ICON_STROKE}/></svg>`;
// Расход прогона по-русски: неразрывный пробел в тысячах, запятая в копейках, мм:сс.
const histInt = (n) => Number(n).toLocaleString("ru-RU");
const histRub = (n) => Number(n).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₽";
function histDur(ms) {
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/* ============================================================================
   ЕДИНЫЙ ФИД «Презентации» — один список всех сборок (активные + черновики +
   история) с чипами-фильтрами. Карточка стабильна по session_id и эволюционирует
   на месте: пока идёт сборка — прогресс-бар + аккордеон с живым логом; после
   завершения тот же элемент превращается в «Готово». Три источника (/api/jobs/
   active, /api/drafts, /api/history) сливаются в один массив и раскладываются
   в DOM keyed-реконсиляцией (чтобы поллинг не стирал раскрытый лог активной
   сборки и не мигал).
   ============================================================================ */
const CHIPS = [
  { key: "all", label: "Все" },
  { key: "work", label: "В работе" },   // running + queued
  { key: "draft", label: "Черновики" },
  { key: "done", label: "Готовые" },
  { key: "fail", label: "Ошибки" },     // failed + cancelled
];
let activeFilter = "all";
let feedTotal = 0;                       // всего карточек (до фильтра) — для empty-state
const feedCards = new Map();             // id -> элемент карточки (реконсиляция)
const feedItemById = new Map();          // id -> нормализованный элемент данных (для live-перерисовки)
const livePct = {};                      // id -> % из SSE (свежее поллинга)
const liveDetail = {};                   // id -> человекочитаемая строка шага из SSE
let expandedId = null;                   // какая карточка сейчас раскрыта (аккордеон)

// Нормализация трёх источников к единому виду карточки.
function baseExt(raw) {
  const m = String(raw).match(/^(.*?)(\.[a-zа-я0-9]+)$/i);
  return m ? { base: m[1], ext: m[2] } : { base: String(raw), ext: "" };
}
function nameParts(it) {
  if (it.display_name) return { base: it.display_name, ext: "" };
  if (it.source_filename) return baseExt(it.source_filename);
  return { base: MODE_LABEL[it.mode] || it.mode || "Без названия", ext: "" };
}
function normActive(a) {
  const running = a.stage && a.stage !== "queued";
  return {
    id: a.session_id, mode: a.mode, source_filename: a.source_filename || null, display_name: null,
    section_count: a.section_count ?? null,
    state: running ? "running" : "queued", active: true, started_at: a.started_at || null,
    stage: a.stage || "queued", pct: a.progress_pct || 0, ts: Infinity,
    kind: a.mode === "htmlnew" ? "build" : "draft",
  };
}
function normHistory(h) {
  return {
    id: h.id, mode: h.mode, source_filename: h.source_filename, display_name: h.display_name,
    state: h.status, active: false, ts: h.created_at ? Date.parse(h.created_at) : 0,
    created_at: h.created_at, error: h.error, kind: h.kind,
    in_tokens: h.in_tokens, out_tokens: h.out_tokens, cost_rub: h.cost_rub, duration_ms: h.duration_ms,
  };
}
function normDraft(d) {
  return {
    id: d.id, mode: d.mode, source_filename: null, display_name: null, state: "draft",
    active: false, ts: d.created_at ? Date.parse(d.created_at) : 0, created_at: d.created_at, kind: "draft",
  };
}

function inFilter(state, key) {
  if (key === "all") return true;
  if (key === "work") return state === "running" || state === "queued";
  if (key === "draft") return state === "draft";
  if (key === "done") return state === "done";
  if (key === "fail") return state === "failed" || state === "cancelled";
  return true;
}
// Активные сверху (running перед queued), остальное — по времени убыванием.
function feedSort(a, b) {
  const ga = a.active ? 0 : 1, gb = b.active ? 0 : 1;
  if (ga !== gb) return ga - gb;
  if (a.active && b.active) {
    const ra = a.state === "running" ? 0 : 1, rb = b.state === "running" ? 0 : 1;
    if (ra !== rb) return ra - rb;
  }
  return (b.ts || 0) - (a.ts || 0);
}

function statusPill(state) {
  switch (state) {
    case "running": return `<span class="st st-run"><span class="dot"></span>В работе</span>`;
    case "queued": return `<span class="st st-queue">В очереди</span>`;
    case "done": return `<span class="st st-done">${CHECK_SVG}Готово</span>`;
    case "draft": return `<span class="st st-draft">Черновик</span>`;
    case "failed": return `<span class="st st-fail">Ошибка</span>`;
    case "cancelled": return `<span class="st st-cancel">Отменено</span>`;
    default: return "";
  }
}
// Сегмент «N разделов · примерно X мин» для активной карточки. estimateLine —
// из errtext.js (window); guard на случай, если модуль не загрузился.
function estSegment(it) {
  if (typeof estimateLine !== "function") return "";
  const e = estimateLine(it.section_count, it.source_filename);
  if (!e) return "";
  return e.warn ? `<span class="est-warn">${esc(e.text)}</span>` : esc(e.text);
}
function cardMeta(it) {
  const SEP = `<span class="sep">·</span>`;
  const when = it.created_at ? new Date(it.created_at).toLocaleString("ru-RU") : "";
  if (it.state === "running") {
    const pct = Math.max(it.pct || 0, livePct[it.id] || 0);
    const detail = liveDetail[it.id] || STAGE_LABEL[it.stage] || "Идёт сборка";
    const seg = [toolTitle(it.mode), esc(detail), pct + "%"];
    const est = estSegment(it);
    if (est) seg.push(est);
    return seg.join(SEP);
  }
  if (it.state === "queued") {
    const seg = [toolTitle(it.mode), "скоро начнётся"];
    const est = estSegment(it);
    if (est) seg.push(est);
    return seg.join(SEP);
  }
  if (it.state === "done") {
    const seg = [];
    if (when) seg.push(esc(when));
    if (it.duration_ms != null) seg.push(`⏱ ${histDur(it.duration_ms)}`);
    if (it.out_tokens != null) seg.push(`↓ ${histInt(it.out_tokens)}`);
    if (it.cost_rub != null) seg.push(`<span class="cost">≈ ${histRub(it.cost_rub)}</span>`);
    return seg.join(SEP);
  }
  if (it.state === "draft") return [toolTitle(it.mode), when ? `изменён ${esc(when)}` : ""].filter(Boolean).join(SEP);
  if (it.state === "failed") return [`<span class="err">${esc(it.error || "Ошибка сборки")}</span>`, when ? esc(when) : ""].filter(Boolean).join(SEP);
  if (it.state === "cancelled") return ["Остановлено вручную", when ? esc(when) : ""].filter(Boolean).join(SEP);
  return "";
}
// Кнопки карточки — только на реально существующие эндпоинты (без «мёртвых»):
// готовая → «Открыть» (редактор); черновик → «Продолжить» + «Удалить»;
// сбой/отмена черновика → то же; сбой загрузки → «Повторить» (снова через загрузку);
// активная → «Остановить».
function cardActions(it) {
  if (it.state === "running" || it.state === "queued")
    return `<button class="btn btn-stop btn-sm" data-stop="${it.id}">Остановить</button>`;
  if (it.state === "done")
    return `<a class="btn btn-ghost btn-sm" href="${U(`/editor?session=${it.id}`)}">Открыть</a>`;
  if (it.state === "draft" || ((it.state === "failed" || it.state === "cancelled") && it.kind === "draft"))
    return `<a class="btn btn-ghost btn-sm" href="${U(`/editor?session=${it.id}&mode=${it.mode}`)}">Продолжить</a>` +
      `<button class="btn-link" data-del="${it.id}">Удалить</button>`;
  if (it.state === "failed" || it.state === "cancelled")
    return `<button class="btn btn-ghost btn-sm" data-retry="${it.id}">Повторить</button>`;
  return "";
}

function updateCard(card, it) {
  card.dataset.state = it.state;
  card.dataset.mode = it.mode || "";
  const expandable = it.state === "running" || it.state === "queued";
  card.classList.toggle("expandable", expandable);
  const np = nameParts(it);
  const pct = Math.max(it.pct || 0, livePct[it.id] || 0);
  card.querySelector(".pcard-head").innerHTML =
    `<div class="pcard-tool">${toolSvg(it.mode)}</div>` +
    `<div class="pcard-main">` +
      `<div class="pcard-trow">` +
        `<span class="pcard-title">${esc(np.base)}${np.ext ? `<span class="ext">${esc(np.ext)}</span>` : ""}</span>` +
        statusPill(it.state) +
      `</div>` +
      `<div class="pcard-meta">${cardMeta(it)}</div>` +
    `</div>` +
    `<div class="pcard-actions">${cardActions(it)}</div>`;
  const bar = card.querySelector(".pcard-bar");
  if (it.state === "running") {          // бар — только у идущей сборки (не у очереди)
    bar.classList.remove("hidden");
    bar.querySelector("i").style.width = pct + "%";
  } else {
    bar.classList.add("hidden");
  }
  if (!expandable) {                      // терминальная карточка — лог не нужен, свернуть
    const lines = card.querySelector(".pcard-loglines");
    if (lines) lines.innerHTML = "";
    card.classList.remove("open");
  }
}

function renderChips(counts) {
  $("#feedChips").innerHTML = CHIPS.map((c) =>
    `<button class="chip${activeFilter === c.key ? " on" : ""}" data-filter="${c.key}">` +
    `${c.label} <span class="n">${counts[c.key] || 0}</span></button>`).join("");
}

function renderFeed(items) {
  const feed = $("#feed");
  feedItemById.clear();
  const seen = new Set();
  let prev = null;
  for (const it of items) {
    seen.add(it.id);
    feedItemById.set(it.id, it);
    let card = feedCards.get(it.id);
    if (!card) {
      card = document.createElement("div");
      card.className = "pcard";
      card.dataset.id = it.id;
      card.innerHTML =
        `<div class="pcard-head"></div>` +
        `<div class="pcard-bar hidden"><i></i></div>` +
        `<div class="pcard-log">` +
          `<div class="pcard-stage"></div>` +
          `<div class="pcard-hb"></div>` +
          `<div class="pcard-loglines"></div>` +
        `</div>`;
      feedCards.set(it.id, card);
    }
    updateCard(card, it);
    const ref = prev ? prev.nextSibling : feed.firstChild;
    if (ref !== card) feed.insertBefore(card, ref);
    prev = card;
  }
  for (const [id, el] of feedCards) {
    if (!seen.has(id)) { el.remove(); feedCards.delete(id); if (expandedId === id) expandedId = null; }
  }
  // Раскрытие держим только на активной карточке; терминальная/исчезнувшая — закрыть.
  const ex = expandedId ? feedCards.get(expandedId) : null;
  if (!ex || !(ex.dataset.state === "running" || ex.dataset.state === "queued")) expandedId = null;
  for (const [id, el] of feedCards) el.classList.toggle("open", id === expandedId);
}

async function loadFeed() {
  const [active, drafts, history] = await Promise.all([
    fetch(U("/api/jobs/active")).then((r) => r.json()).catch(() => []),
    fetch(U("/api/drafts")).then((r) => r.json()).catch(() => []),
    fetch(U("/api/history")).then((r) => r.json()).catch(() => []),
  ]);
  const byId = new Map();
  for (const h of history) byId.set(h.id, normHistory(h));
  for (const d of drafts) if (!byId.has(d.id)) byId.set(d.id, normDraft(d));
  for (const a of active) byId.set(a.session_id, normActive(a));  // живая запись перекрывает
  const items = [...byId.values()];
  feedTotal = items.length;
  const counts = { all: items.length, work: 0, draft: 0, done: 0, fail: 0 };
  for (const it of items) {
    if (it.state === "running" || it.state === "queued") counts.work++;
    else if (it.state === "draft") counts.draft++;
    else if (it.state === "done") counts.done++;
    else if (it.state === "failed" || it.state === "cancelled") counts.fail++;
  }
  renderChips(counts);
  items.sort(feedSort);
  renderFeed(items.filter((it) => inFilter(it.state, activeFilter)));
  updateEmptyState();
}

function updateEmptyState() {
  const shown = $("#feed").children.length;
  const resultVisible = !$("#result").classList.contains("hidden");
  $("#feedWrap").hidden = feedTotal === 0;
  $("#feedEmpty").classList.toggle("hidden", !(feedTotal > 0 && shown === 0));
  const empty = $("#workspaceEmpty");
  if (empty) empty.hidden = feedTotal > 0 || resultVisible;
}

/* ---- действия над карточками (делегирование на #feed) ---- */
async function stopBuild(id, btn) {
  if (btn) { btn.disabled = true; btn.textContent = "Останавливаю…"; }
  // Мгновенная реакция: если это отслеживаемая сборка — сразу гасим поток, не ждём
  // терминального события (уже идущие вызовы модели ещё доедают ~до минуты).
  if (currentSession === id && currentES) { currentES.close(); currentES = null; stopHeartbeat(); currentSession = null; }
  try { await fetch(U(`/api/jobs/${id}/cancel`), { method: "POST" }); } catch (e) { /* best-effort */ }
  loadFeed();
}
async function deleteDraft(id, btn) {
  if (!confirm("Удалить черновик? Это действие необратимо.")) return;
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(U(`/api/drafts/${id}`), { method: "DELETE" });
    if (!r.ok) throw new Error("draft delete failed");
    loadFeed();
  } catch (e) {
    if (btn) btn.disabled = false;
  }
}
// Отказ запуска — в панель #result (своя визуальная система, не alert).
function launchError(title, bodyHtml) {
  const box = $("#result");
  box.classList.remove("hidden");
  box.classList.add("error");
  box.innerHTML = `<h3>${esc(title)}</h3>${bodyHtml}`;
  updateEmptyState();
}

// Перезапуск неудавшейся сборки идёт на сервере: он копирует сохранённый исходник
// сессии в новую и ставит её в очередь. Раньше это жило в браузере (скролл к
// дропзоне + повтор, если файл ещё выбран) и после F5 не делало ничего.
async function retryBuild(id, btn) {
  const prev = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Запускаю…"; }
  let r;
  try {
    r = await fetch(U(`/api/jobs/${id}/retry`), { method: "POST" });
  } catch (e) {
    launchError("Не удалось перезапустить сборку",
      "<p>Похоже, пропала связь с сервером — проверьте интернет и попробуйте ещё раз.</p>");
    if (btn) { btn.disabled = false; btn.textContent = prev; }
    return;
  }
  if (!r.ok) {
    const text = await r.text();
    let detail = "";
    try { detail = JSON.parse(text).detail; } catch (e) { detail = ""; }
    // 410 — исходник уже удалён по сроку хранения: серверный текст объясняет, что
    // делать (загрузить файл заново). 429 — очередь занята.
    const body = r.status === 429
      ? "<p>Очередь занята — дождитесь завершения текущих сборок.</p>"
      : (detail ? `<p>${esc(detail)}</p>`
                : `<details><summary>Детали</summary><pre>${esc(text)}</pre></details>`);
    launchError("Не удалось перезапустить сборку", body);
    if (btn) { btn.disabled = false; btn.textContent = prev; }
    return;
  }
  const { session_id, kind } = await r.json();
  $("#result").classList.add("hidden");
  streamProgress(session_id, kind);
}
function toggleCard(id) {
  const card = feedCards.get(id);
  if (!card) return;
  if (expandedId === id) { expandedId = null; card.classList.remove("open"); return; }
  if (expandedId) { const p = feedCards.get(expandedId); if (p) p.classList.remove("open"); }
  expandedId = id;
  card.classList.add("open");
  // Раскрыли активную сборку, за которой ещё не следим, — переводим поток на неё.
  const st = card.dataset.state;
  if ((st === "running" || st === "queued") && currentSession !== id)
    streamProgress(id, "html", { resumed: true });
}

$("#feed").addEventListener("click", (e) => {
  const stopEl = e.target.closest("[data-stop]");
  if (stopEl) { e.stopPropagation(); stopBuild(stopEl.dataset.stop, stopEl); return; }
  const delEl = e.target.closest("[data-del]");
  if (delEl) { e.stopPropagation(); deleteDraft(delEl.dataset.del, delEl); return; }
  const retryEl = e.target.closest("[data-retry]");
  if (retryEl) { e.stopPropagation(); retryBuild(retryEl.dataset.retry, retryEl); return; }
  if (e.target.closest("a, button")) return;          // ссылки/кнопки работают сами
  const card = e.target.closest(".pcard.expandable");
  if (card) toggleCard(card.dataset.id);
});
$("#feedChips").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-filter]");
  if (!chip || chip.dataset.filter === activeFilter) return;
  activeFilter = chip.dataset.filter;
  loadFeed();
});

$("#clear").onclick = async () => {
  await fetch(U("/api/history/clear"), { method: "POST" });
  loadFeed();
};

// Contract §8 — rehydrate the in-flight run after a full page reload (canon-nav
// links reload the page, dropping the SSE/JS state, but the build keeps running
// server-side). On load, if a run is active and we're not already streaming one,
// re-attach the progress view so navigation away and back never looks "stopped".
async function autoResumeActive() {
  if (currentSession) return;
  let items = [];
  try { items = await (await fetch(U("/api/jobs/active"))).json(); } catch (e) { return; }
  if (!items.length || currentSession) return;
  const it = items[0];
  streamProgress(it.session_id, "html", { stage: it.stage, progress_pct: it.progress_pct, resumed: true });
}

/* ---- create job ---- */
// Г§8 — extracted so a failed card's «Повторить» can re-run the build directly.
async function createJob(opts) {
  if (!selectedFile) return;
  const force = !!(opts && opts.force);
  const ex = document.getElementById("exactTransfer");
  const isExact = !!(ex && ex.checked);
  // Client-side gate: «Точный перенос» works only on slide-structured inputs
  // (.pptx) or slide-delimited text (.md/.txt). A .docx has no slide boundaries,
  // so this combo can only fail deep in the worker — reject it instantly with a
  // clear, actionable message, no wasted round-trip or queue slot. The server
  // enforces the same rule (400) as the source of truth.
  if (isExact && !/\.(pptx|md|txt)$/i.test(selectedFile.name || "")) {
    launchError("Не удалось запустить сборку",
      `<p>«Точный перенос» поддерживает .pptx, .md и .txt. ` +
      `Для .docx снимите галочку «Точный перенос» — обычная сборка отлично ` +
      `переносит Word, — либо сохраните файл как .pptx.</p>`);
    return;
  }
  // Сервис ИИ лежал на момент последней пробы — предупреждаем, но не запрещаем:
  // состояние могло измениться за секунды, и решать должен пользователь.
  if (!force && mhData && mhData.state === "down") {
    const age = typeof checkedAgo === "function" ? checkedAgo(mhAgeSec()) : "";
    launchError("Сервис ИИ сейчас недоступен",
      `<p>Не отвечают ни основная, ни резервная модель${age ? ` (проверено ${esc(age)})` : ""} — ` +
      `сборка, скорее всего, оборвётся. Можно подождать пару минут или запустить сейчас.</p>` +
      `<p><button class="btn btn-ghost btn-sm" id="forceCreate">Всё равно запустить</button></p>`);
    const f = $("#forceCreate");
    if (f) f.onclick = () => createJob({ force: true });
    loadModelHealth();          // заодно перепроверяем — данные явно устарели
    return;
  }
  // Лимит: не больше двух своих сборок одновременно (остальное — дождаться).
  try {
    const act = await (await fetch(U("/api/jobs/active"))).json();
    if (Array.isArray(act) && act.length >= 2) {
      launchError("Дождитесь завершения",
        `<p>Одновременно можно собирать не больше двух презентаций. ` +
        `Как только одна закончится — запустите следующую.</p>`);
      return;
    }
  } catch (e) { /* сеть — не блокируем запуск, сервер всё равно поставит лимит */ }
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
    launchError("Не удалось запустить сборку", body);
    $("#create").disabled = false;
    return;
  }
  const { session_id, kind } = await res.json();
  $("#result").classList.add("hidden");   // прошлая ошибка запуска больше не актуальна
  streamProgress(session_id, kind);
  resetFile();                            // дропзона свободна — можно готовить следующую
}
// Стрелка, а не сама функция: onclick передал бы MouseEvent в opts.
$("#create").onclick = () => createJob();

/* ---- доступность сервиса ИИ (плашка над кнопкой «Создать») ---- */
// Пробы стоят вызовов к провайдеру, поэтому в фоне не поллим: спрашиваем при
// загрузке страницы и при возврате во вкладку, если данным больше TTL. Возраст
// показываем живым таймером — зелёный индикатор часовой давности иначе читался бы
// как «сейчас всё хорошо».
const MH_TTL_MS = 60000;
let mhData = null;
let mhCheckedAt = 0;          // локальный момент последней серверной проверки
let mhUnknownTries = 0;       // холодный старт: не более трёх дозапросов

function mhAgeSec() {
  return mhCheckedAt ? Math.round((Date.now() - mhCheckedAt) / 1000) : null;
}

async function loadModelHealth() {
  let d;
  try { d = await (await fetch(U("/api/models/health"))).json(); }
  catch (e) { return; }         // сеть — плашка просто не меняется
  mhData = d;
  mhCheckedAt = d.checked_ago_sec == null ? 0 : Date.now() - d.checked_ago_sec * 1000;
  renderModelHealth();
  // "unknown" — проба ещё идёт (или клиент не собрался). Дозапрашиваем несколько
  // раз, а не бесконечно: без ключа API состояние останется неизвестным навсегда.
  if (d.state === "unknown" && mhUnknownTries < 3) {
    mhUnknownTries++;
    setTimeout(loadModelHealth, 3000);
  }
}

function renderModelHealth() {
  const box = $("#modelHealth");
  if (!box || !mhData || typeof healthLine !== "function") return;
  const line = healthLine(mhData.state);
  box.hidden = false;
  box.dataset.state = line.tone;
  const prim = $("#mhPrimary");
  if (prim) prim.dataset.up = mhData.primary_up == null ? "" : String(mhData.primary_up);
  const fb = $("#mhFallback");
  if (fb) {                       // резерв не настроен → второго индикатора нет
    fb.hidden = mhData.fallback_up == null;
    fb.dataset.up = String(mhData.fallback_up);
  }
  $("#mhNote").textContent = line.text;
  mhTick();
}

function mhTick() {
  const el = $("#mhAge");
  if (!el || !mhData) return;
  const age = typeof checkedAgo === "function" ? checkedAgo(mhAgeSec()) : "";
  el.textContent = age ? `Проверено ${age}` : "";
}

/* If no progress event arrives for this many seconds, warn that the step is
   taking long (helps tell a slow model call apart from a real hang). */
const STALL_SECONDS = 30;
let lastEventAt = 0;
let watchStartAt = 0;   // клиентский запасной старт (если сервер не дал started_at)
let heartbeatTimer = null;
let currentSession = null;
let currentStage = null;
// Live SSE stream for the watched card. Hoisted so «Остановить» can close it
// instantly — without waiting for the terminal event.
let currentES = null;
// Consecutive SSE reconnect attempts: incremented on each es.onerror, reset on
// any es.onmessage. Module-scoped so it survives the streamProgress
// re-invocation used to re-attach after a transient drop.
let reconnects = 0;

// Живой лог пишем в аккордеон конкретной карточки (.pcard-loglines). Поллинг
// эту область не трогает, поэтому строки не мигают и не пропадают.
function logLineCard(id, stage, detail) {
  const card = feedCards.get(id);
  if (!card) return;
  const log = card.querySelector(".pcard-loglines");
  if (!log) return;
  const time = new Date().toLocaleTimeString("ru-RU");
  const text = friendlyDetail(detail) || STAGE_LABEL[stage] || stage || "";
  if (!text) return;
  const last = log.lastElementChild;
  if (last && last.dataset.text === text) return;  // skip identical repeats
  const row = document.createElement("div");
  row.dataset.text = text;
  row.innerHTML = `<span class="t">${time}</span>${esc(text)}`;
  log.appendChild(row);
  while (log.children.length > 100) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
}

// Прошедшее время сборки → «M:SS». Старт берём из серверного started_at (переживает
// перезагрузку страницы), запасной вариант — клиентский watchStartAt.
function elapsedClock() {
  const it = feedItemById.get(currentSession);
  let startMs = it && it.started_at ? Date.parse(it.started_at) : NaN;
  if (!Number.isFinite(startMs)) startMs = watchStartAt || lastEventAt;
  const s = Math.max(0, Math.round((Date.now() - startMs) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function tickHeartbeat() {
  if (!currentSession || !lastEventAt) return;
  const card = feedCards.get(currentSession);
  if (!card) return;
  const hb = card.querySelector(".pcard-hb");
  if (!hb) return;
  const clock = elapsedClock();
  // «Тишина» (нет SSE-событий) считается отдельно от общего времени — по ней
  // включаем жёлтую подсказку про долгий шаг модели.
  const silent = Math.round((Date.now() - lastEventAt) / 1000);
  if (silent >= STALL_SECONDS) {
    hb.classList.add("stale");
    const where = STAGE_HINT[currentStage] || "идёт долгий шаг модели.";
    hb.textContent = `Обрабатываю уже ${clock} — это нормально: ${where}`;
  } else {
    hb.classList.remove("stale");
    hb.textContent = `Обрабатываю ${clock}`;
  }
}
function stopHeartbeat() {
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
}

// Живая перерисовка отслеживаемой карточки из данных SSE (шапка + бар + заголовок
// шага в аккордеоне). Данные берём из последнего снимка фида + live-карт.
function paintCard(id) {
  const card = feedCards.get(id);
  if (!card) return;
  const it = feedItemById.get(id);
  if (it) updateCard(card, it);
  const stageEl = card.querySelector(".pcard-stage");
  if (stageEl) stageEl.textContent = STAGE_LABEL[currentStage] || currentStage || "Идёт сборка";
}

function streamProgress(sessionId, kind, initial) {
  // A fresh stream starts the reconnect counter clean; a resumed reconnect
  // (initial.resumed) keeps the running count so 5 failures in a row still bail.
  if (!(initial && initial.resumed)) reconnects = 0;
  currentSession = sessionId;
  currentStage = initial && initial.stage ? initial.stage : currentStage;
  expandedId = sessionId;                 // следим — значит раскрываем эту карточку
  if (initial && initial.progress_pct) livePct[sessionId] = Math.max(livePct[sessionId] || 0, initial.progress_pct);
  lastEventAt = Date.now();
  // Свежий поток (не reconnect) — сбрасываем запасной счётчик времени; на resume
  // держим прежний, чтобы M:SS не начинался заново после разрыва SSE.
  if (!(initial && initial.resumed)) watchStartAt = lastEventAt;
  stopHeartbeat();
  heartbeatTimer = setInterval(tickHeartbeat, 1000);
  loadFeed();                             // карточка появится/раскроется немедленно

  // Progress via SSE (the gateway proxies HTTP streaming, not WebSocket).
  let finished = false;
  const es = new EventSource(U(`/api/jobs/${sessionId}/events`));
  currentES = es;
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    lastEventAt = Date.now();
    reconnects = 0;  // a live event arrived — the stream is healthy again
    const pct = ev.progress_pct || 0;
    currentStage = ev.stage || currentStage;
    livePct[sessionId] = Math.max(livePct[sessionId] || 0, pct);
    const friendly = friendlyDetail(ev.detail);
    if (friendly) liveDetail[sessionId] = friendly;
    paintCard(sessionId);
    tickHeartbeat();
    logLineCard(sessionId, ev.stage, ev.detail || ev.error);
    if (ev.terminal) {
      finished = true;
      es.close();
      currentES = null;
      stopHeartbeat();
      currentSession = null;
      delete livePct[sessionId];
      delete liveDetail[sessionId];
      // Готовая дека — сразу в редактор; сбой/отмена — остаёмся, карточка сама
      // перерисуется в терминальное состояние следующим поллингом.
      if (ev.stage === "done") { location.href = U(`/editor?session=${sessionId}`); return; }
      loadFeed();
    }
  };
  // An SSE drop is NOT a build failure: the job keeps running on the server.
  // Mark the heartbeat stale and try to re-attach; only a real terminal `failed`
  // event (handled in onmessage) turns the card red.
  es.onerror = () => {
    if (finished) return; // normal stream close after the terminal event
    es.close();
    stopHeartbeat();
    reconnects++;
    if (reconnects > 5) { tickHeartbeat(); return; }
    tickHeartbeat();
    setTimeout(async () => {
      if (currentSession !== sessionId) return;  // superseded by another stream
      let items = [];
      try { items = await (await fetch(U("/api/jobs/active"))).json(); } catch (e) {}
      const job = items.find((it) => it.session_id === sessionId);
      if (job) {
        streamProgress(sessionId, kind, { stage: job.stage, progress_pct: job.progress_pct, resumed: true });
        return;
      }
      loadFeed();  // no longer active — finished/failed while disconnected
    }, 3000);
  };
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
  if (card.classList.contains("is-disabled")) return;  // фича в разработке — не вешаем обработчики
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
    // Отключённые точки входа (фича в разработке) не воскрешаем — оставляем «Скоро».
    if (!b || b.closest(".entry-alt.is-disabled")) return;
    b.disabled = false; b.classList.remove("is-error"); b.textContent = label;
  });
  loadFeed();  // возврат по Back мог создать/удалить сборку или черновик — обновляем
});

/* ===== Интро: карточки подготовки исходника (промпт / скилл) ===== */
// Ссылка на .skill живёт в HTML без префикса — доклеиваем gateway-префикс, как
// у всех остальных URL (иначе на проде /downloads уйдёт мимо /slides).
const _skillLink = $("#skillDownload");
if (_skillLink) _skillLink.href = U("/downloads/cloud-ru-slides.skill");

function syncPromptToggle() {
  const pre = $("#promptText"), t = $("#togglePrompt");
  if (!pre || !t) return;
  t.textContent = pre.hidden ? "Показать текст" : "Скрыть текст";
  t.setAttribute("aria-expanded", String(!pre.hidden));
}
const _togglePrompt = $("#togglePrompt");
if (_togglePrompt) _togglePrompt.addEventListener("click", () => {
  const pre = $("#promptText");
  pre.hidden = !pre.hidden;
  syncPromptToggle();
});
const _copyPrompt = $("#copyPrompt");
if (_copyPrompt) _copyPrompt.addEventListener("click", async () => {
  const text = $("#promptText").textContent.trim();
  let ok = true;
  try { await navigator.clipboard.writeText(text); }
  catch { ok = false; }  // clipboard API требует secure context — фолбэк: раскрыть текст
  _copyPrompt.textContent = ok ? "Скопировано ✓" : "Скопируйте вручную ↓";
  if (!ok) { $("#promptText").hidden = false; syncPromptToggle(); }
  setTimeout(() => { _copyPrompt.textContent = "Скопировать промпт"; }, 2000);
});

/* init */
// Г§4 — empty-state превью настоящего слайда: src через U() (несёт gateway-префикс);
// lazy + скрытый родитель → грузится только когда empty-state показан.
const _emptyPrev = $("#emptyPrev");
if (_emptyPrev) _emptyPrev.src = U("/api/templates/cover/preview");
// Г§9 — объявляем окно ретеншена (истории и черновиков) прямо под заголовком.
const _retHours = window.__RETENTION_HOURS__ || 24;
const _retCap = $("#retentionCap");
if (_retCap) _retCap.textContent = _retHours + " часа";
resetFile();
loadFeed();
autoResumeActive();
setInterval(loadFeed, 2000);
loadModelHealth();
setInterval(mhTick, 1000);      // живой возраст проверки (сам опрос — по событию)
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (!mhCheckedAt || Date.now() - mhCheckedAt > MH_TTL_MS) {
    mhUnknownTries = 0;
    loadModelHealth();
  }
});
