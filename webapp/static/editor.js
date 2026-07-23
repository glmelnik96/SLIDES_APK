// Gateway URL prefix (e.g. /slides); empty in standalone dev. Injected by server.
const PREFIX = window.__APP_PREFIX__ || "";
const U = (p) => PREFIX + p;

const params = new URLSearchParams(location.search);
const sessionId = params.get("session");
const mode = params.get("mode") || "";        // "manual" | "chat" | "" (built deck)
const isDraft = mode === "manual" || mode === "chat";
// К§6 — двойная буферизация превью: два iframe'а на одном месте. loadDeck() грузит
// следующий кадр в СКРЫТЫЙ буфер, по load меняет их местами классом .hidden и
// переключает указатель frame — между сейвами нет чёрного кадра. deckT — единый
// cache-bust одного кадра (сцена + миниатюры К§11).
const frameA = document.getElementById("deck");
const frameB = document.getElementById("deck2");
let frame = frameA;
let deckT = 0;
document.getElementById("html").href = U(`/api/jobs/${sessionId}/deck?download=1`);

let slides = [];
let current = 0;
let pendingGoTo = 0; // slide to show after the next iframe load
let freeformConfirmed = false; // К§1: once the user OKs inline->freeform, don't re-ask this tab-session

function loadDeck() {
  // cache-bust so edits/chat rewrites are reflected on reload; один deckT на кадр.
  // &editor=1 — режим покоя деки (К§6): без входных анимаций/лупов в превью.
  deckT = Date.now();
  const target = (frame === frameA) ? frameB : frameA; // грузим в скрытый буфер
  target.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1`);
  // A reload means the deck content changed (chat/agent/rebuild/field edit), so any
  // finished export is now stale — reset the controls. Contenteditable saves don't
  // reload the iframe, so saveDeck() handles that path separately.
  markExportsStale?.();
}

const STAGE_LABEL = {
  queued: "В очереди", parsing: "Разбор документа",
  classifying: "Планирование структуры", designing: "Заполнение слайдов",
  rendering: "Сборка", validating: "Проверка качества",
  autofixing: "Автоисправление", finalizing: "Финализация",
};

// Engine progress strings → friendly Russian (mirrors app.js on the index page).
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
  return "";
}
const overlay = document.getElementById("buildOverlay");
const buildSub = document.getElementById("buildSub");
const buildTitle = document.getElementById("buildTitle");
const buildNote = document.getElementById("buildNote");
const buildActions = document.getElementById("buildActions");

function showOverlay(show) { overlay && overlay.classList.toggle("hidden", !show); }

// Р§2 — терминальная карточка с выходом из ошибки (Nielsen #9): гасим «ждите»-note
// и показываем действия («На главную» / «Повторить»). Никакого вечного спиннера.
const HOME_LINK = '<a class="btn btn-ghost" href="/">На главную</a>';
function showTerminal(title, sub, actionsHtml) {
  showOverlay(true);
  if (buildTitle) buildTitle.textContent = title;
  if (buildSub) buildSub.textContent = sub;
  if (buildNote) buildNote.textContent = "";
  if (buildActions) {
    buildActions.innerHTML = actionsHtml;
    buildActions.classList.remove("hidden");
  }
}
// Р§2 — бюджет ретраев SSE: после 5 неудач — терминальная карточка вместо цикла.
let buildRetries = 0;

// Opening the editor for a run whose deck isn't built yet would otherwise show a
// blank 404 iframe. Instead, gate on readiness: if the deck exists, load it; if
// the run is still building, show progress (SSE) and load the deck when done.
// Р§2: a 404 deck is ambiguous — the run may be building, OR the session is gone
// (expired / foreign). Probe /status to tell them apart: 404 there = terminal card.
async function initEditor() {
  if (buildActions) buildActions.classList.add("hidden");
  let head;
  try {
    head = await fetch(U(`/api/jobs/${sessionId}/deck?probe=${Date.now()}`),
                       { method: "GET", headers: { Range: "bytes=0-0" } });
  } catch (e) { head = null; }
  if (head && head.ok) { buildRetries = 0; showOverlay(false); loadDeck(); return; }
  if (head && head.status === 404) {
    let st;
    try {
      st = await fetch(U(`/api/jobs/${sessionId}/status?probe=${Date.now()}`));
    } catch (e) { st = null; }
    if (st && st.status === 404) {
      showTerminal("Презентация не найдена",
        "Возможно, истёк срок хранения (24 часа) или ссылка устарела.", HOME_LINK);
      return;
    }
    buildRetries = 0;              // successful probe resets the retry budget
    waitForBuild();
    return;
  }
  // any other status (e.g. 401) — fall back to a plain load attempt
  showOverlay(false); loadDeck();
}

function waitForBuild() {
  showOverlay(true);
  if (buildActions) buildActions.classList.add("hidden");
  if (buildNote) buildNote.textContent =
    "Это занимает несколько секунд — не закрывайте страницу.";
  buildTitle.textContent = "Презентация ещё собирается…";
  let done = false;
  const es = new EventSource(U(`/api/jobs/${sessionId}/events`));
  es.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch (_) { return; }
    const pct = ev.progress_pct || 0;
    const friendly = friendlyDetail(ev.detail) || STAGE_LABEL[ev.stage] || ev.stage || "";
    buildSub.textContent = `${friendly} · ${pct}%`;
    if (ev.terminal) {
      done = true; es.close();
      if (ev.stage === "done") { showOverlay(false); loadDeck(); }
      else {
        buildTitle.textContent =
          ev.stage === "cancelled" ? "Сборка остановлена" : "Не удалось собрать";
        buildSub.textContent = ev.error || "";
        if (buildNote) buildNote.textContent = "";
      }
    }
  };
  es.onerror = () => {
    if (done) return;
    es.close();
    // Р§2 — не крутить бесконечно: после 5 неудачных ретраев — выход из ошибки.
    if (++buildRetries >= 5) {
      showTerminal("Не удалось получить статус сборки",
        "Сервер не отвечает. Обновите страницу или вернитесь на главную.",
        '<button class="btn btn-ghost" id="buildRetry">Повторить</button>' + HOME_LINK);
      const rb = document.getElementById("buildRetry");
      if (rb) rb.addEventListener("click", () => {
        buildRetries = 0;
        if (buildActions) buildActions.classList.add("hidden");
        initEditor();
      });
      return;
    }
    // Stream dropped but run may still be alive — retry readiness shortly.
    setTimeout(initEditor, 3000);
  };
}

// К§6 — общий обработчик load обоих буферов: свопим на только что загруженный кадр.
function handleFrameLoad(loaded) {
  const doc = loaded.contentDocument;
  // Игнорируем about:blank / пустой (ошибочный 404) кадр: не свопим на него.
  if (!doc || !doc.querySelector(".slide")) return;
  frame = loaded; // указатель — на только что загруженный буфер
  slides = [...doc.querySelectorAll(".slide")];
  const emptyDraft = isDraft && !draftPlan.slides.length; // К§4 — синтетическая заглушка
  if (emptyDraft) {
    // Заглушка нередактируема: в плане 0 слайдов, blur-синк улетел бы в 404 и молча
    // терял бы первый ввод новичка. Клик по ней ведёт к действию — пикер / фокус чата.
    doc.addEventListener("click", () => {
      if (mode === "manual") addSlideViaPicker();
      else chatText?.focus();
    });
  } else {
  // In-place text editing works everywhere. Built decks are HTML-as-truth, so
  // edits persist via saveDeck(). Drafts are DeckPlan-as-truth, so an inline edit
  // converts that slide to a freeform slide in the plan (synced on blur).
  // CRITICAL: sync only when the content actually changed. A bare focus+blur
  // (user clicks the preview, then clicks elsewhere) must NOT convert the slide
  // to freeform — that used to wipe the builder form/template on a mere click.
  slides.forEach((s, i) => s.querySelectorAll("*").forEach((el) => {
    if (el.children.length === 0 && el.textContent.trim()) {
      el.setAttribute("contenteditable", "true");
      if (isDraft) {
        el.addEventListener("focus", () => { el.__origHtml = el.innerHTML; });
        el.addEventListener("blur", async () => {
          const orig = el.__origHtml;
          const changed = orig !== undefined && el.innerHTML !== orig;
          el.__origHtml = undefined;
          if (!changed) return;
          // К§1: перед ПЕРВОЙ конвертацией не-freeform слайда в свободный режим —
          // подтверждение. Отказ восстанавливает исходный HTML и НЕ синкает.
          if (!draftPlan.slides[i]?.freeform && !freeformConfirmed) {
            const ok = await confirmDialog(
              "Правка прямо на слайде переведёт его в свободный режим: поля формы станут недоступны. Продолжить?",
              "Продолжить", "Отмена");
            if (!ok) { el.innerHTML = orig; return; }
            freeformConfirmed = true;
          }
          // A green count-up number (.js-count) carries a persistent
          // data-count-final (set once by deck.js, never refreshed). An inline
          // edit changes textContent but NOT that attribute, so syncDraftSlideHtml
          // would bake the stale final value and silently revert the edit on the
          // next navigation. Refresh it to the edited text first.
          if (el.hasAttribute("data-count-final")) {
            el.setAttribute("data-count-final", el.textContent);
          }
          syncDraftSlideHtml(i);
        });
      }
    }
  }));
  }
  suppressDeckNavOnEdit(doc);
  buildThumbs();
  goTo(Math.min(pendingGoTo, slides.length - 1));
  markPlaceholders(); // К§3 — пометить пустые слоты после рендера превью
  // К§6 — показать подготовленный буфер, спрятать прежний (без чёрного кадра).
  loaded.classList.remove("hidden");
  (loaded === frameA ? frameB : frameA).classList.add("hidden");
  // Буфер грузился скрытым (display:none → окно 0×0), поэтому deck.js rescale() при
  // init бросил масштаб. После показа форсим resize, чтобы дека вписалась в сцену.
  requestAnimationFrame(() => {
    try { loaded.contentWindow && loaded.contentWindow.dispatchEvent(new Event("resize")); } catch (_) {}
  });
}
frameA.addEventListener("load", () => handleFrameLoad(frameA));
frameB.addEventListener("load", () => handleFrameLoad(frameB));

// Persist an in-place edit of draft slide `i` (0-based) as a freeform slide.
let draftHtmlSaving = false;
async function syncDraftSlideHtml(i) {
  if (draftHtmlSaving) return;
  const doc = frame.contentDocument;
  const section = doc && doc.querySelectorAll(".slide")[i];
  if (!section) return;
  const clone = section.cloneNode(true);
  // Strip editor + deck.js runtime state so it doesn't get baked into the plan:
  // contenteditable (ours), .is-active (current-slide marker) and the count-up
  // animation's data-count-final / mid-animation counter text (deck.js).
  clone.classList.remove("is-active");
  clone.querySelectorAll("[contenteditable]").forEach(
    (el) => el.removeAttribute("contenteditable"));
  // К§3 — гигиена: редакторская метка рыбы-плейсхолдера не должна запекаться в план.
  clone.classList.remove("is-placeholder");
  clone.querySelectorAll(".is-placeholder").forEach(
    (el) => el.classList.remove("is-placeholder"));
  clone.querySelectorAll("[data-count-final]").forEach((el) => {
    el.textContent = el.getAttribute("data-count-final");
    el.removeAttribute("data-count-final");
  });
  draftHtmlSaving = true;
  try {
    // К§1: снапшот {template_id, content} ДО перевода слайда в freeform — переживает
    // reload вкладки (sessionStorage), питает кнопку «Вернуть макет».
    const slide = draftPlan.slides[i];
    if (slide && !slide.freeform) {
      try {
        sessionStorage.setItem(`freeform-snap:${sessionId}:${i + 1}`,
          JSON.stringify({ template_id: slide.template_id, content: slide.content }));
      } catch (_) { /* sessionStorage может быть недоступен — не критично */ }
    }
    const r = await fetch(U(`/api/drafts/${sessionId}/slides/${i + 1}/html`), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ html: clone.outerHTML }),
    });
    if (!r.ok) { setSaveStatus("error"); return; } // К§4 — неуспех (404/гонка): не терять молча
    await fetchPlan();
    if (mode === "manual") renderBuilderForm(); // slide is now freeform
  } finally {
    draftHtmlSaving = false;
  }
}

// The deck engine (deck.js) attaches document-level click/keydown handlers that
// page through slides (click in left/right third → prev/next; arrows/space → nav).
// In the editor that hijacks clicking-to-edit and typing. We install capture-phase
// listeners that stop propagation when the user is interacting with editable text,
// so the deck's navigation handlers never fire during editing.
function suppressDeckNavOnEdit(doc) {
  doc.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest('[contenteditable="true"]')) {
      e.stopPropagation();
    }
  }, true);
  doc.addEventListener("keydown", (e) => {
    const el = e.target;
    if (el && (el.isContentEditable || el.closest?.('[contenteditable="true"]'))) {
      e.stopPropagation();
    }
  }, true);
}

function buildThumbs() {
  const box = document.getElementById("thumbs");
  box.innerHTML = "";
  if (isDraft && !draftPlan.slides.length) return; // К§4 — пустой драфт: тумб нет, только «+ Добавить слайд»
  // Управление слайдами (удаление/перетаскивание) — только в ручном режиме сборки.
  const editable = mode === "manual";
  slides.forEach((s, i) => {
    const t = document.createElement("div");
    t.className = "thumb";
    t.dataset.index = i;
    // К§11 — масштабированное превью деки (тот же приём, что в пикере) + подпись.
    const prev = document.createElement("div");
    prev.className = "thumb-prev";
    const ifr = document.createElement("iframe");
    ifr.loading = "lazy";
    ifr.tabIndex = -1;
    ifr.setAttribute("aria-hidden", "true");
    // Единый cache-bust deckT (К§6) — превью синхронны с канвой; #n — нужный слайд; &editor=1 — покой.
    ifr.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1#${i + 1}`);
    prev.appendChild(ifr);
    const cap = document.createElement("div");
    cap.className = "thumb-cap";
    const num = document.createElement("span");
    num.className = "thumb-num";
    num.textContent = i + 1;
    cap.appendChild(num);
    const titleText = (s.querySelector("h1,h2,h3,[data-slot=title]")?.textContent || "").trim();
    if (titleText) {
      const ttl = document.createElement("span");
      ttl.className = "thumb-title";
      ttl.textContent = titleText;
      cap.appendChild(ttl);
    }
    t.appendChild(prev);
    t.appendChild(cap);
    t.onclick = () => goTo(i);
    if (editable) {
      // Крестик удаления — виден по наведению (CSS .thumb:hover .thumb-del)
      const del = document.createElement("button");
      del.type = "button";
      del.className = "thumb-del";
      del.title = "Удалить слайд";
      del.innerHTML = "&#10005;";
      del.addEventListener("click", (e) => { e.stopPropagation(); deleteSlideAt(i); });
      t.appendChild(del);
      // Перетаскивание миниатюры меняет порядок слайдов
      t.draggable = true;
      t.addEventListener("dragstart", onThumbDragStart);
      t.addEventListener("dragover", onThumbDragOver);
      t.addEventListener("dragleave", onThumbDragLeave);
      t.addEventListener("drop", onThumbDrop);
      t.addEventListener("dragend", onThumbDragEnd);
    }
    box.appendChild(t);
  });
}

// К§3 — на превью помечаем пустые текст-слоты (и пустые ОБЯЗАТЕЛЬНЫЕ list-слоты)
// классом .is-placeholder: пример-рыба должна быть визуально отличима и не уйти
// молча в экспорт. Источник истины «слот пуст» — draftPlan + каталог; на
// freeform/exact-слайдах (каталог не покрывает шаблон) просто не срабатывает.
function markPlaceholders() {
  if (!isDraft) return;
  const doc = frame.contentDocument;
  if (!doc) return;
  const sections = doc.querySelectorAll(".slide");
  (draftPlan.slides || []).forEach((slide, i) => {
    const section = sections[i];
    if (!section || !slide || slide.freeform) return;
    const tpl = tplOf(slide.template_id);
    if (!tpl) return;
    const content = slide.content || {};
    for (const [name, spec] of Object.entries(tpl.slots)) {
      const val = content[name];
      let empty;
      if (spec.kind === "text") empty = (val == null || String(val).trim() === "");
      else if (spec.kind === "list") empty = spec.required && (!Array.isArray(val) || val.length === 0);
      else empty = false;
      if (!empty) continue;
      const node = section.querySelector(`[data-slot="${name}"]`) || section;
      node.classList.add("is-placeholder");
    }
  });
}

function goTo(i) {
  if (!slides.length) return;
  // Persist any pending edit to the slide we're leaving BEFORE switching, so the
  // debounced save can't fire later against the new slide (data loss / misroute).
  if (mode === "manual" && i !== current) flushPendingSave();
  current = Math.max(0, Math.min(slides.length - 1, i));
  // Keep the post-reload target aligned with the slide actually shown. Each save
  // calls loadDeck(), whose iframe onload runs goTo(pendingGoTo); if pendingGoTo
  // lagged behind navigation, that reload would snap back to a stale slide.
  pendingGoTo = current;
  const win = frame.contentWindow;
  if (win && win.deck && win.deck.goTo) win.deck.goTo(current);
  document.getElementById("chatTarget").textContent =
    mode === "chat" ? "Ассистент" : `Слайд ${current + 1}`;
  [...document.querySelectorAll(".thumb")].forEach((t, idx) =>
    t.classList.toggle("active", idx === current));
  // Only rebuild the form when the shown slide changed — NOT on the preview
  // reloads that follow each save (those would wipe focus and in-progress rows).
  if (mode === "manual" && builtFormFor !== current) renderBuilderForm();
}

// Листание слайдов стрелками с самой страницы редактора: ↑/← — предыдущий,
// ↓/→ — следующий. У превью (iframe) свой обработчик — он срабатывает, когда
// фокус внутри картинки слайда; этот нужен для случая, когда фокус на редакторе
// (миниатюры, панель). В поле ввода или чате стрелки отдаём тексту — они двигают
// курсор, а не листают. Модификаторы (Cmd/Ctrl/Alt) не трогаем — это чужие
// сочетания. goTo сам ограничивает края (без зацикливания), как кнопки «‹ ›».
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const prev = e.key === "ArrowUp" || e.key === "ArrowLeft";
  const next = e.key === "ArrowDown" || e.key === "ArrowRight";
  if (!prev && !next) return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable))
    return;
  e.preventDefault();
  goTo(current + (next ? 1 : -1));
});

function currentDeckHtml() {
  const doc = frame.contentDocument;
  if (!doc || !doc.documentElement) {
    // iframe is mid-reload or not ready — caller must handle this.
    throw new Error("презентация ещё не загрузилась — подождите секунду");
  }
  // Strip the editor-only contenteditable attributes we inject at load time so
  // the persisted/downloaded/exported deck stays clean (otherwise a downloaded
  // HTML deck would be globally editable). Clone so the live editing DOM keeps
  // its contenteditable and in-place editing keeps working.
  const clone = doc.documentElement.cloneNode(true);
  clone.querySelectorAll("[contenteditable]").forEach(
    (el) => el.removeAttribute("contenteditable"));
  // К§3 — снять редакторскую метку рыбы-плейсхолдера перед сохранением/скачиванием/экспортом.
  clone.querySelectorAll(".is-placeholder").forEach(
    (el) => el.classList.remove("is-placeholder"));
  return "<!DOCTYPE html>" + clone.outerHTML;
}

async function saveDeck(silent) {
  const r = await fetch(U(`/api/jobs/${sessionId}/deck`), {
    method: "POST", body: currentDeckHtml(),
  });
  // A user edit invalidates any finished export; the export flow saves silently
  // (silent=true) because it renders exactly this state — no need to reset itself.
  if (!silent) markExportsStale();
  return r.ok;
}

document.getElementById("save").onclick = async () => {
  const ok = await saveDeck();
  flash(document.getElementById("save"), ok ? "Сохранено" : "Ошибка");
};

// Export (PNG-ZIP / PPTX) is async: screenshotting every slide via Chromium takes
// seconds, so we don't block on it. Click "Экспорт" → start the render and show a
// "Готовлю…" pill → poll until ready → the control becomes an active "Скачать…"
// button that downloads the finished file. No blind wait, download only when ready.
// Ч§9 — один глагол «Скачать» на все три формата; busy честно объясняет задержку.
const EXPORT_LABEL = {
  png: { idle: "Скачать PNG (ZIP)", busy: "Готовлю PNG… ~10–20 сек", ready: "Скачать PNG (ZIP)" },
  pptx: { idle: "Скачать PPTX", busy: "Готовлю PPTX… ~10–20 сек", ready: "Скачать PPTX" },
};

// Any deck edit invalidates a finished (or in-flight) export — a "Скачать" pill
// would otherwise hand back the pre-edit file. Edit funnels call this to reset the
// controls back to "Экспорт", forcing a fresh render on the next click.
const _exportResets = [];
function markExportsStale() { _exportResets.forEach((fn) => fn()); }

// Скачивание за один клик: как только рендер готов, poll() дёргает это сам —
// пользователь нажал «Скачать» один раз, файл прилетает без второго клика.
// Скрытый <a download> вместо location.href, чтобы вкладка не «моргала» навигацией
// и чтобы тот же контрол остался живым для ручного повторного скачивания.
function triggerDownload(fmt) {
  const a = document.createElement("a");
  a.href = U(`/api/jobs/${sessionId}/export/${fmt}/file`);
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// К§3 — предэкспортная проверка: не выпустить пример-текст молча. В chat-режиме
// каталог не загружен — дозапрашиваем лениво. Возвращает true, если можно экспортировать.
async function ensureCatalog() {
  if (catalog.length) return;
  try {
    const r = await fetch(U("/api/templates"));
    if (r.ok) catalog = await r.json();
  } catch (_) { /* сеть — не блокируем экспорт */ }
}
function countPlaceholderSlides() {
  let n = 0;
  (draftPlan.slides || []).forEach((slide) => {
    if (!slide || slide.freeform) return;
    const tpl = tplOf(slide.template_id);
    if (!tpl) return;
    const content = slide.content || {};
    const hasEmptyRequired = Object.entries(tpl.slots).some(([name, spec]) => {
      if (!spec.required) return false;
      const val = content[name];
      if (spec.kind === "list") return !Array.isArray(val) || val.length === 0;
      return val == null || String(val).trim() === "";
    });
    if (hasEmptyRequired) n++;
  });
  return n;
}
async function confirmExportWithPlaceholders() {
  if (!isDraft) return true;
  await ensureCatalog();
  const n = countPlaceholderSlides();
  if (!n) return true;
  return confirmDialog(
    `На ${n} ${plural(n, "слайде", "слайдах", "слайдах")} остался пример-текст — ` +
    `он попадёт в файл. Экспортировать?`,
    "Экспортировать", "Вернуться к заполнению");
}

function setupExport(btn) {
  const fmt = btn.dataset.fmt;
  const L = EXPORT_LABEL[fmt];
  let poller = null;

  const toIdle = (text) => {
    clearInterval(poller); poller = null;
    btn.disabled = false;
    btn.classList.remove("is-busy", "is-ready");
    btn.textContent = text || L.idle;
  };
  const toReady = () => {
    clearInterval(poller); poller = null;
    btn.disabled = false;
    btn.classList.remove("is-busy");
    btn.classList.add("is-ready");
    btn.textContent = L.ready;
  };
  const toBusy = () => {
    btn.disabled = true;
    btn.classList.remove("is-ready");
    btn.classList.add("is-busy");
    btn.textContent = L.busy;
  };

  async function poll() {
    let r;
    try {
      r = await fetch(U(`/api/jobs/${sessionId}/export/${fmt}`));
    } catch { return; }               // transient network blip — keep polling
    if (!r.ok) { toIdle("Ошибка — повторить"); return; }
    const { state } = await r.json();
    // Рендер готов — автоскачивание (один клик), контрол остаётся «Скачать»
    // для ручного повтора. wasBusy: качаем только по завершению активного
    // рендера, а не когда poll() застал уже готовый прошлый файл.
    if (state === "ready") {
      const wasBusy = btn.classList.contains("is-busy");
      toReady();
      if (wasBusy) triggerDownload(fmt);
    }
    else if (state === "error") toIdle("Ошибка — повторить");
  }

  btn.onclick = async () => {
    // A ready control downloads; anything else (re)starts an export.
    if (btn.classList.contains("is-ready")) {
      triggerDownload(fmt);
      return;
    }
    if (!(await confirmExportWithPlaceholders())) return; // К§3 — пример-текст в экспорт?
    toBusy();
    await saveDeck(true);              // persist in-place edits (no stale-reset: this IS the export)
    const r = await fetch(U(`/api/jobs/${sessionId}/export/${fmt}`), { method: "POST" });
    if (!r.ok) { toIdle("Ошибка — повторить"); return; }
    poller = setInterval(poll, 1500);
    poll();
  };

  _exportResets.push(() => toIdle());
}

document.querySelectorAll("[data-fmt]").forEach(setupExport);

// Р§1 — overflow-меню «Скачать»: закрытие по клику вне и по Esc. Клик по пункту
// ВНУТРИ меню его НЕ закрывает — пользователь видит смену «Готовлю…» → «Скачать».
const exportMenu = document.getElementById("exportMenu");
if (exportMenu) {
  document.addEventListener("click", (e) => {
    if (exportMenu.open && !exportMenu.contains(e.target)) exportMenu.removeAttribute("open");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && exportMenu.open) exportMenu.removeAttribute("open");
  });
}

// К§3 — «Скачать HTML» для драфта: та же предэкспортная проверка на пример-текст.
const htmlLink = document.getElementById("html");
htmlLink?.addEventListener("click", async (e) => {
  if (!isDraft) return;
  e.preventDefault();
  if (await confirmExportWithPlaceholders()) location.href = htmlLink.href;
});

function flash(btn, text) {
  const orig = btn.textContent;
  btn.textContent = text;
  setTimeout(() => { btn.textContent = orig; }, 1500);
}

// К§17 — бренд-диалоги вместо нативных alert/confirm (стиль .picker, прямые углы,
// SB Sans). Фокус на первую кнопку, Esc = отмена. Возвращают Promise.
function _dialog(text, buttons) {
  return new Promise((resolve) => {
    const ov = document.createElement("div");
    ov.className = "dialog";
    const card = document.createElement("div");
    card.className = "dialog-card";
    const p = document.createElement("p");
    p.className = "dialog-text";
    p.textContent = text;
    const row = document.createElement("div");
    row.className = "dialog-actions";
    const cancelVal = (buttons.find((b) => b.cancel) || {}).value;
    function close(val) {
      document.removeEventListener("keydown", onKey);
      ov.remove();
      resolve(val);
    }
    function onKey(e) { if (e.key === "Escape") close(cancelVal); }
    buttons.forEach((b, idx) => {
      const btn = document.createElement("button");
      btn.className = b.className;
      btn.textContent = b.label;
      btn.onclick = () => close(b.value);
      row.appendChild(btn);
      if (idx === 0) setTimeout(() => btn.focus(), 0);
    });
    card.appendChild(p);
    card.appendChild(row);
    ov.appendChild(card);
    document.body.appendChild(ov);
    document.addEventListener("keydown", onKey);
  });
}
function confirmDialog(text, okLabel, cancelLabel) {
  return _dialog(text, [
    { label: okLabel || "OK", className: "btn", value: true },
    { label: cancelLabel || "Отмена", className: "btn btn-ghost", value: false, cancel: true },
  ]);
}
function alertDialog(text) {
  return _dialog(text, [{ label: "Понятно", className: "btn", value: true }]);
}

/* ---- chat ---- */
const chatLog = document.getElementById("chatLog");
const chatText = document.getElementById("chatText");
const chatSend = document.getElementById("chatSend");

function addMsg(cls, text) {
  document.getElementById("chatEmpty")?.remove();
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}

// Ч§7 — последняя инструкция чата: «Повторить» после ошибки возвращает её в поле,
// поэтому запоминаем ДО очистки textarea (иначе ретраить нечем).
let lastInstruction = "";

// Ч§7 — продуктовый текст ошибки чата вместо сырого JSON/Python-трассы. Сырьё —
// только в console.error. 409 — русский detail сервера как есть; 429 — очередь;
// остальное — нейтральный конструктивный текст.
async function chatErrText(r) {
  let raw = "";
  try { raw = await r.text(); } catch (_) { raw = ""; }
  let detail = raw;
  try { const j = JSON.parse(raw); if (j && j.detail) detail = j.detail; } catch (_) { /* не JSON */ }
  console.error("chat error", r.status, raw);
  if (r.status === 409) return detail || "Не получилось применить правку — попробуйте переформулировать.";
  if (r.status === 429) return "Очередь занята — попробуйте через минуту.";
  return "Не получилось применить правку. Попробуйте переформулировать или повторить.";
}

// Ч§7 — кнопка «Повторить» под сообщением об ошибке: возвращает инструкцию в поле.
function appendRetry(node) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn btn-ghost btn-sm";
  btn.style.display = "block";
  btn.style.marginTop = "8px";
  btn.textContent = "Повторить";
  btn.onclick = () => { chatText.value = lastInstruction; chatText.focus(); };
  node.appendChild(btn);
}

// A chat edit calls Kimi (a reasoning model) and can legitimately take a couple
// of minutes (server budget: one ~210s pass). Auto-abort backstop sits above
// that so a real hang can't lock the page forever; the user can also cancel any
// time via the button, so this ceiling can be generous.
const CHAT_TIMEOUT_MS = 300000; // 5 min hard ceiling
let chatInFlight = null;        // AbortController while a request is running
let chatTimerId = null;

function setChatBusy(busy) {
  const idle = mode === "chat" ? "Отправить" : "Применить к слайду";
  chatSend.textContent = busy ? "Отмена" : idle;
  chatSend.classList.toggle("btn-stop", busy);
}

function tickElapsed(thinking, t0) {
  const secs = Math.round((Date.now() - t0) / 1000);
  thinking.textContent = `Применяю правку… ${secs} сек (можно отменить)`;
}

async function sendChat() {
  if (CHAT_EDIT_DISABLED) return;   // правки в чате — фича в разработке
  // If a request is already running, the button acts as Cancel.
  if (chatInFlight) {
    chatInFlight.abort();
    return;
  }
  if (mode === "chat") return sendAgent();   // feature 3: slide-building agent
  const instruction = chatText.value.trim();
  if (!instruction) return;
  const slideIndex = current + 1;
  lastInstruction = instruction;   // Ч§7 — сохранить до очистки поля (для «Повторить»)
  addMsg("user", `Слайд ${slideIndex}: ${instruction}`);
  chatText.value = "";

  const thinking = addMsg("bot", "Применяю правку…");
  const t0 = Date.now();
  const controller = new AbortController();
  chatInFlight = controller;
  let timedOut = false;
  setChatBusy(true);
  chatTimerId = setInterval(() => tickElapsed(thinking, t0), 1000);
  const timeoutId = setTimeout(() => { timedOut = true; controller.abort(); },
                               CHAT_TIMEOUT_MS);

  try {
    // Persist current in-place edits first so the model edits the latest version.
    // Inside try so a failure here can't leave the UI permanently stuck.
    await saveDeck();
    const r = await fetch(U(`/api/jobs/${sessionId}/chat`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slide_index: slideIndex, instruction }),
      signal: controller.signal,
    });
    if (!r.ok) {
      thinking.className = "msg err";
      thinking.textContent = await chatErrText(r);   // Ч§7 — продуктовый текст + «Повторить»
      appendRetry(thinking);
    } else {
      lastInstruction = "";
      thinking.textContent = `Слайд ${slideIndex} обновлён.`;
      pendingGoTo = slideIndex - 1; // show the edited slide, even if user navigated away
      loadDeck(); // reload iframe with the rewritten slide
    }
  } catch (e) {
    thinking.className = "msg err";
    if (timedOut) {
      thinking.textContent =
        "Правка отменена: превышено время ожидания (5 мин). Попробуйте ещё раз.";
    } else if (e && e.name === "AbortError") {
      thinking.textContent = "Правка отменена.";
    } else {
      thinking.textContent = "Ошибка: " + (e && e.message ? e.message : e);
    }
  } finally {
    clearTimeout(timeoutId);
    clearInterval(chatTimerId);
    chatTimerId = null;
    chatInFlight = null;
    setChatBusy(false);
  }
}

chatSend.onclick = sendChat;
chatText.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendChat(); }
});

// Правки/сборка слайдов через чат — фича в разработке. Оставляем панель видимой,
// но неинтерактивной: серые контролы, курсор-стоп и дружелюбная подсказка при
// наведении (см. [data-tip] в styles.css). Идемпотентно — можно звать повторно.
const CHAT_EDIT_DISABLED = true;
const CHAT_DEV_TIP =
  "Чат-ассистент пока на стажировке 🙂";
function disableChatEditing() {
  if (!CHAT_EDIT_DISABLED) return;
  const head = document.querySelector(".chat-head");
  if (head) head.innerHTML =
    '<h3>Правки в чате <span class="soon-badge">в разработке</span></h3>' +
    "<p>Пока меняйте текст прямо на слайде — правки через чат в разработке.</p>";
  byId("chatEmpty")?.remove();
  byId("outline")?.classList.add("hidden");   // план-аутлайн — часть чат-сборки
  byId("chatTarget")?.classList.add("hidden");
  if (chatText) {
    chatText.disabled = true;
    chatText.value = "";
    chatText.placeholder = "Правки через чат в разработке";
  }
  if (chatSend) {
    chatSend.disabled = true;
    chatSend.textContent = "В разработке";
    chatSend.classList.remove("btn-stop");
  }
  const box = document.querySelector(".chat-input");
  if (box) { box.classList.add("is-disabled"); box.setAttribute("data-tip", CHAT_DEV_TIP); }
}

/* ===================== DRAFT BUILDER (manual mode) ===================== */
let catalog = [];          // [{id,type,intent,slots}]
let draftPlan = { title: "", slides: [] };
let putTimer = null;
// Which slide index the builder form is currently rendered for. The preview
// iframe reloads after every keystroke-save, which re-fires goTo(); rebuilding
// the form there would destroy the input the user is typing in (losing focus and
// any unsaved/empty rows). So we only (re)build the form when the shown slide
// actually changes (navigation) or after a structural edit (which resets this).
let builtFormFor = -1;

const byId = (id) => document.getElementById(id);

async function fetchPlan() {
  const r = await fetch(U(`/api/drafts/${sessionId}`));
  if (r.ok) draftPlan = await r.json();
}

async function reloadDraft(goToIndex) {
  await fetchPlan();
  if (goToIndex != null) pendingGoTo = goToIndex;
  builtFormFor = -1;  // plan changed structurally → force a form rebuild
  loadDeck(); // re-render preview from the server's derived deck.html
}

function tplOf(id) { return catalog.find((t) => t.id === id); }

function renderBuilderForm() {
  const form = byId("builderForm");
  const tplBox = byId("builderTpl");
  const empty = byId("builderEmpty");
  if (!form) return;
  builtFormFor = current;   // mark the form as built for the current slide
  const slide = draftPlan.slides[current];
  if (!slide) { form.innerHTML = ""; tplBox.innerHTML = ""; empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");

  if (slide.freeform) {
    tplBox.innerHTML = `<span class="tpl-name">Свободный слайд</span>`;
    // К§1: честная записка (без выдуманной истории про чат) + возврат к макету.
    form.innerHTML = `<p class="builder-note">Свободный слайд — он больше не привязан к макету, ` +
      `поэтому полей здесь нет. Правьте текст прямо на слайде или опишите изменение в чате справа.</p>`;
    const snapKey = `freeform-snap:${sessionId}:${current + 1}`;
    const snapRaw = sessionStorage.getItem(snapKey);
    if (snapRaw) {
      const revert = document.createElement("button");
      revert.type = "button";
      revert.className = "btn btn-ghost btn-sm";
      revert.style.marginTop = "8px";
      revert.textContent = "Вернуть макет";
      revert.onclick = async () => {
        let snap; try { snap = JSON.parse(snapRaw); } catch (_) { return; }
        const n = current + 1;
        await fetch(U(`/api/drafts/${sessionId}/slides/${n}`), { method: "DELETE" });
        await fetch(U(`/api/drafts/${sessionId}/slides`), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ template_id: snap.template_id, at: n, content: snap.content }),
        });
        sessionStorage.removeItem(snapKey);
        await reloadDraft(current);
      };
      form.appendChild(revert);
    }
    return;
  }
  const tpl = tplOf(slide.template_id);
  const tplIdx = catalog.findIndex((t) => t.id === slide.template_id);
  const tplNo = tplIdx >= 0 ? String(tplIdx + 1).padStart(2, "0") : "—";
  tplBox.innerHTML =
    `<span class="tpl-name">Макет: ${tpl?.display_name || slide.template_id}</span>` +  // К§2: имя макета, фолбэк на id
    `<button type="button" class="btn btn-ghost btn-sm" id="changeTpl">Сменить макет</button>`;
  byId("changeTpl").onclick = () => openPicker((tid) => changeTemplate(tid));

  form.innerHTML = "";
  if (!tpl) return;
  for (const [name, spec] of Object.entries(tpl.slots)) {
    form.appendChild(renderSlot(name, spec, slide.content[name]));
  }
}

// Читает файл-картинку и возвращает data-URI, ужатый до maxW по ширине (с
// сохранением пропорций) и перекодированный в JPEG — чтобы не тащить многомегабайтный
// оригинал в plan.json/экспорт. Рисуем через canvas; если браузер не смог декодировать
// файл — промис реджектится (вызывающий покажет ошибку).
function imageFileToDataURL(file, maxW, quality) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      try {
        const scale = Math.min(1, (maxW / img.naturalWidth) || 1);
        const w = Math.max(1, Math.round(img.naturalWidth * scale));
        const h = Math.max(1, Math.round(img.naturalHeight * scale));
        const c = document.createElement("canvas");
        c.width = w;
        c.height = h;
        c.getContext("2d").drawImage(img, 0, 0, w, h);
        URL.revokeObjectURL(url);
        resolve(c.toDataURL("image/jpeg", quality));
      } catch (e) {
        URL.revokeObjectURL(url);
        reject(e);
      }
    };
    img.onerror = (e) => { URL.revokeObjectURL(url); reject(e); };
    img.src = url;
  });
}

function renderSlot(name, spec, value) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("label");
  label.textContent = (spec.label || name) + (spec.required ? " *" : "");  // К§2: рус. лейбл, фолбэк на id
  wrap.appendChild(label);
  if (spec.hint) {
    const hint = document.createElement("div");
    hint.className = "field-hint";
    hint.textContent = spec.hint;
    wrap.appendChild(hint);
  }

  if (spec.kind === "text") {
    const long = (spec.max_chars || 0) > 40;
    const el = document.createElement(long ? "textarea" : "input");
    if (name === "image") {
      // Загрузка своего изображения. data-URI храним в скрытом text-слоте, который
      // collectContent() кладёт в content.image — шаблон сам вписывает его в маску
      // (cover-image) или в <img> (service-table/timeline). Пустое значение → шаблон
      // показывает дефолтную картинку из библиотеки. Крупные файлы ужимаем на клиенте
      // (canvas → JPEG, до 2040px), чтобы data-URI не раздувал plan.json/экспорт.
      const holder = document.createElement("input");
      holder.type = "hidden";
      holder.dataset.slot = name;
      holder.dataset.kind = "text";
      holder.value = value == null ? "" : String(value);

      const file = document.createElement("input");
      file.type = "file";
      file.accept = "image/*";
      file.hidden = true;

      // Нативный <input type=file> прячем внутрь <label>, стилизованного как кнопка
      // интерфейса (.btn-ghost) — клик по лейблу открывает системный диалог выбора.
      const pick = document.createElement("label");
      pick.className = "btn btn-ghost btn-sm field-file";
      pick.textContent = "Загрузить изображение";
      pick.appendChild(file);

      const reset = document.createElement("button");
      reset.type = "button";
      reset.className = "btn btn-ghost btn-sm";
      reset.textContent = "Сбросить к шаблону";

      const row = document.createElement("div");
      row.className = "field-file-row";
      row.appendChild(pick);
      row.appendChild(reset);

      const status = document.createElement("div");
      status.className = "field-hint";

      const refresh = () => {
        const custom = !!holder.value;
        status.textContent = custom
          ? "Загружено своё изображение"
          : "По умолчанию — изображение из шаблона";
        reset.hidden = !custom;
      };
      file.addEventListener("change", async () => {
        const f = file.files && file.files[0];
        if (!f) return;
        try {
          holder.value = await imageFileToDataURL(f, 2040, 0.85);
        } catch (e) {
          status.textContent = "Не удалось прочитать файл — попробуйте другой";
          return;
        }
        refresh();
        scheduleSave();
      });
      reset.addEventListener("click", () => {
        holder.value = "";
        file.value = "";
        refresh();
        scheduleSave();
      });

      wrap.appendChild(holder);
      wrap.appendChild(row);
      wrap.appendChild(status);
      refresh();
      return wrap;
    }
    if (spec.max_chars) el.maxLength = spec.max_chars;
    el.value = value == null ? "" : String(value);
    el.dataset.slot = name;
    el.dataset.kind = "text";
    el.addEventListener("input", scheduleSave);
    wrap.appendChild(el);
    if (spec.max_chars) wrap.appendChild(charCounter(el, spec.max_chars));
  } else if (spec.kind === "list") {
    const list = document.createElement("div");
    list.className = "field-list";
    list.dataset.slot = name;
    list.dataset.kind = "list";
    const items = Array.isArray(value) ? value : [];
    // Seed one empty row when the slot has no items yet, so the input fields are
    // immediately visible (instead of a lone "+ пункт" the user has to discover).
    const seed = items.length ? items : [{}];
    seed.forEach((item) => list.appendChild(renderItem(spec, item)));
    wrap.appendChild(list);
    const add = document.createElement("button");
    add.type = "button"; add.className = "btn btn-ghost btn-sm";
    add.textContent = "+ пункт";
    add.onclick = () => {
      if (spec.max_items && list.children.length >= spec.max_items) return;
      const row = renderItem(spec, {});
      list.appendChild(row);
      row.querySelector("input")?.focus();   // ready to type; saved on first input
    };
    wrap.appendChild(add);
    if (spec.max_items) wrap.appendChild(hint(`до ${spec.max_items} пунктов`));
  } else if (spec.kind === "group") {
    wrap.appendChild(renderItem(spec, value || {}, name));
  }
  return wrap;
}

function renderItem(spec, item, groupSlot) {
  const row = document.createElement("div");
  row.className = "field-item";
  if (groupSlot) { row.dataset.slot = groupSlot; row.dataset.kind = "group"; }
  for (const [sub, subSpec] of Object.entries(spec.item_slots || {})) {
    const inp = document.createElement("input");
    inp.placeholder = sub + (subSpec.required ? " *" : "");
    if (subSpec.max_chars) inp.maxLength = subSpec.max_chars;
    inp.value = item[sub] == null ? "" : String(item[sub]);
    inp.dataset.sub = sub;
    inp.oninput = scheduleSave;
    row.appendChild(inp);
  }
  if (!groupSlot) {
    const del = document.createElement("button");
    del.type = "button"; del.className = "btn btn-ghost btn-sm item-del";
    del.textContent = "✕";
    del.onclick = () => { row.remove(); scheduleSave(); };
    row.appendChild(del);
  }
  return row;
}

function hint(text) {
  const s = document.createElement("span");
  s.className = "field-hint"; s.textContent = text; return s;
}

// Живой счётчик «M/N» для текстового поля: тот же узел, что hint(), но обновляется
// на ввод и краснеет (.field-hint--over) при переполнении M > max. maxLength обычно
// не даёт превысить руками — красный нужен для значений, пришедших из плана.
function charCounter(el, max) {
  const h = hint("");
  const upd = () => {
    const n = (el.value || "").length;
    h.textContent = `${n}/${max}`;
    h.classList.toggle("field-hint--over", n > max);
  };
  el.addEventListener("input", upd);
  upd();
  return h;
}

function collectContent() {
  const form = byId("builderForm");
  const content = {};
  form.querySelectorAll('[data-kind="text"]').forEach((el) => {
    if (el.value.trim()) content[el.dataset.slot] = el.value;
  });
  form.querySelectorAll('[data-kind="list"]').forEach((list) => {
    const items = [];
    list.querySelectorAll(".field-item").forEach((row) => {
      const obj = {};
      row.querySelectorAll("[data-sub]").forEach((inp) => {
        if (inp.value.trim()) obj[inp.dataset.sub] = inp.value;
      });
      if (Object.keys(obj).length) items.push(obj);
    });
    if (items.length) content[list.dataset.slot] = items;
  });
  form.querySelectorAll('[data-kind="group"]').forEach((row) => {
    const obj = {};
    row.querySelectorAll("[data-sub]").forEach((inp) => {
      if (inp.value.trim()) obj[inp.dataset.sub] = inp.value;
    });
    if (Object.keys(obj).length) content[row.dataset.slot] = obj;
  });
  return content;
}

// К§5 — бэкофф ретраев автосейва (1с/3с/7с) на сетевой/5xx-ошибке.
const SAVE_RETRY_MS = [1000, 3000, 7000];
let saveRetryTimer = null;

function scheduleSave() {
  clearTimeout(putTimer);
  clearTimeout(saveRetryTimer); // свежий ввод отменяет незавершённую цепочку ретраев
  putTimer = setTimeout(() => saveCurrentSlide(0), 600);
}

// Run any pending debounced save NOW. Must be called before leaving the current
// slide (navigation) or before a rebuild — otherwise the in-flight 600ms timer
// fires later against the wrong `current`/DOM form and the last edit is lost or
// written to the wrong slide. Returns the save promise so callers that need the
// server to have the edit first (rebuild) can await it.
function flushPendingSave() {
  if (putTimer == null) return Promise.resolve();
  clearTimeout(putTimer);
  putTimer = null;
  return saveCurrentSlide();
}

// К§6 — точечный патч ТЕКСТ-слотов текущего слайда прямо в DOM превью (без полного
// релоада: нет вспышки, не перезапускаются входы/count-up, живёт .slot-highlight).
// Возвращает true, если патч применим и выполнен; false — если нужен полный loadDeck
// (list/group/image-слоты, опустевший слот → сервер вернёт рыбу, .js-count/.sr-value
// пересчитывает приватный autofitStats движка, или узел не найден).
function patchPreviewText(idx) {
  const doc = frame.contentDocument;
  const section = doc && doc.querySelectorAll(".slide")[idx];
  const slide = draftPlan.slides[idx];
  if (!section || !slide) return false;
  const tpl = tplOf(slide.template_id);
  if (!tpl) return false;
  // Любой не-текстовый слот (или загруженное изображение) — превью не гарантируем → релоад.
  if (Object.entries(tpl.slots).some(([n, s]) => s.kind !== "text" || n === "image")) return false;
  const content = slide.content || {};
  for (const [name, spec] of Object.entries(tpl.slots)) {
    if (spec.kind !== "text") continue;
    const el = section.querySelector(`[data-slot="${name}"]`);
    if (!el) return false; // узел не найден — надёжнее полный релоад
    const val = content[name];
    if (val == null || String(val).trim() === "") {
      // Пустой слот: если сейчас показана рыба (.is-placeholder) — так и оставляем.
      // Если реальный контент опустошили — сервер подставит рыбу-пример → нужен релоад.
      if (!el.classList.contains("is-placeholder")) return false;
      continue;
    }
    if (el.classList.contains("js-count") || el.classList.contains("sr-value")) return false;
    el.textContent = String(val);
    el.classList.remove("is-placeholder"); // слот заполнен — снять метку рыбы (К§3)
  }
  return true;
}

async function saveCurrentSlide(attempt = 0) {
  // Capture the slide index up front: `current` can change (navigation) during
  // the awaited PUT, and the URL/marking must stay bound to the edited slide.
  const idx = current;
  putTimer = null;
  const slide = draftPlan.slides[idx];
  if (!slide || slide.freeform) return;
  const content = collectContent();
  slide.content = content; // optimistic local update
  setSaveStatus(attempt ? "retrying" : "saving");
  let r;
  try {
    r = await fetch(U(`/api/drafts/${sessionId}/slides/${idx + 1}`), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
  } catch (e) {
    r = null;
  }
  if (r && r.ok) {
    const { errors } = await r.json();
    if (current === idx) markFieldErrors(errors || []); // only if still shown
    setSaveStatus("saved");
    // К§6 — точечный патч, если слайд ещё показан и правка чисто текстовая; иначе релоад.
    if (current === idx && patchPreviewText(idx)) markExportsStale?.();
    else loadDeck();
    return;
  }
  // К§5 — 409: серверное состояние действительно главнее (дека уже собрана / идёт
  // пересборка), только тут ресинкаем форму. try/catch — чтобы офлайн-исключение
  // reloadDraft всё равно оставило видимый error-статус.
  if (r && r.status === 409) {
    try { await reloadDraft(idx); } catch (_) { /* оставить локальный ввод виден */ }
    setSaveStatus("error");
    return;
  }
  // К§5 — сеть/5xx: НЕ трогаем форму и draftPlan (локальное новее серверного), не
  // стираем набранный текст. Ретраим сам PUT с бэкоффом; свежий ввод пользователя
  // (scheduleSave) отменяет эту цепочку.
  if (attempt < SAVE_RETRY_MS.length) {
    setSaveStatus("retrying");
    clearTimeout(saveRetryTimer);
    saveRetryTimer = setTimeout(() => saveCurrentSlide(attempt + 1), SAVE_RETRY_MS[attempt]);
    return;
  }
  // Исчерпали ретраи — красный статус с кликабельным «Повторить» (запускает цепочку заново).
  setSaveStatusRetry();
}

// Индикатор автосейва в шапке формы: «Сохранение…» → «Сохранено ✓» → «Не сохранено».
// Успех мягко гаснет через 1.6с; процесс/ошибка висят до следующего сейва. Узел
// #saveStatus живёт в .builder-head, который не перестраивается renderBuilderForm,
// поэтому статус переживает пересборку формы.
let saveStatusTimer = null;
function setSaveStatus(state) {
  const el = byId("saveStatus");
  if (!el) return;
  clearTimeout(saveStatusTimer);
  el.textContent = SAVE_STATUS[state] || "";
  el.className = "save-status save-status--" + state;
  if (state === "saved") {
    saveStatusTimer = setTimeout(() => {
      el.textContent = "";
      el.className = "save-status";
    }, 1600);
  }
}

// К§5 — терминальный статус после исчерпания ретраев: красный текст + кликабельное
// «Повторить» (btn-link), которое запускает цепочку сейва заново.
function setSaveStatusRetry() {
  const el = byId("saveStatus");
  if (!el) return;
  clearTimeout(saveStatusTimer);
  el.className = "save-status save-status--error";
  el.textContent = (SAVE_STATUS.error || "") + " · ";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn-link";
  btn.textContent = "Повторить";
  btn.onclick = () => saveCurrentSlide(0);
  el.appendChild(btn);
}

function markFieldErrors(errors) {
  // Первую ошибку на каждый слот верхнего уровня печатаем под соответствующим
  // полем (узел .field-hint--error). Рамку красит существующий .field-error.
  const bySlot = new Map();
  errors.forEach((e) => {
    const top = e.slot.split(/[.[]/)[0];
    if (!bySlot.has(top)) bySlot.set(top, e);
  });
  byId("builderForm").querySelectorAll(".field").forEach((f) => {
    const slot = f.querySelector("[data-slot]")?.dataset.slot;
    const err = slot ? bySlot.get(slot) : null;
    f.classList.toggle("field-error", !!err);
    const text = err ? errText(err.code, err.detail) : "";
    let msg = f.querySelector(".field-hint--error");
    if (text) {
      if (!msg) {
        msg = document.createElement("div");
        msg.className = "field-hint field-hint--error";
        f.appendChild(msg);
      }
      msg.textContent = text;
    } else if (msg) {
      msg.remove();
    }
  });
}

/* ---- slide actions ---- */
async function changeTemplate(templateId) {
  // Drop any pending debounced save — we take the freshest form values directly.
  clearTimeout(putTimer); putTimer = null;
  pushUndo();
  const slide = draftPlan.slides[current];
  // Merge: plan content keeps slots the current form doesn't render (so a swap
  // A→B→A restores A's slots), form values win for the slots the user can see.
  const content = slide && !slide.freeform
    ? { ...(slide.content || {}), ...collectContent() }
    : (slide && slide.content) || {};
  // template change = delete + re-add at the same position with the new template.
  // The content rides along: overlapping slots (title, items, …) carry over; the
  // rest stays in plan.json (draft_render ignores unknown slots), so switching
  // back restores it. Without this the swap silently wiped the slide's content.
  await fetch(U(`/api/drafts/${sessionId}/slides/${current + 1}`), { method: "DELETE" });
  await fetch(U(`/api/drafts/${sessionId}/slides`), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template_id: templateId, at: current + 1, content }),
  });
  await reloadDraft(current);
}

// К§4 — общий обработчик добавления слайда: кнопка рейла (#addSlide), кнопка пустой
// панели (#builderAdd) и клик по заглушке пустого драфта ведут в один пикер.
function addSlideViaPicker() {
  openPicker(async (tid) => {
    await flushPendingSave(); // preserve the current slide's edit before inserting
    pushUndo();
    // Вставляем новый слайд сразу после активного (1-based позиция at).
    const at = Math.min(current + 2, draftPlan.slides.length + 1);
    await fetch(U(`/api/drafts/${sessionId}/slides`), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: tid, at }),
    });
    await reloadDraft(at - 1); // переходим на только что добавленный слайд
  });
}
byId("addSlide")?.addEventListener("click", addSlideViaPicker);
byId("builderAdd")?.addEventListener("click", addSlideViaPicker);

// Подсветка редактируемого блока на слайде: #deck — iframe того же origin, поэтому
// дотягиваемся до его DOM напрямую (без postMessage). По фокусу поля конструктора
// находим в деке элемент [data-slot=<слот>] и обводим его. На exact/freeform-слайдах
// data-slot нет — подсветка просто не срабатывает.
function highlightSlot(slot, on) {
  const doc = frame.contentDocument;
  if (!doc) return;
  doc.querySelectorAll(".slot-highlight").forEach((n) =>
    n.classList.remove("slot-highlight"));
  if (on && slot) {
    const el = doc.querySelector(`[data-slot="${slot}"]`);
    if (el) el.classList.add("slot-highlight");
  }
}

byId("builderForm")?.addEventListener("focusin", (e) => {
  const holder = e.target.closest("[data-slot]");
  if (holder) highlightSlot(holder.dataset.slot, true);
});
byId("builderForm")?.addEventListener("focusout", (e) => {
  const holder = e.target.closest("[data-slot]");
  if (holder) highlightSlot(holder.dataset.slot, false);
});

// Удаление слайда по индексу (0-based) — вызывается крестиком на миниатюре.
async function deleteSlideAt(i) {
  if (!draftPlan.slides[i]) return;
  clearTimeout(putTimer); putTimer = null; // slide is going away — drop pending save
  pushUndo();
  await fetch(U(`/api/drafts/${sessionId}/slides/${i + 1}`), { method: "DELETE" });
  await reloadDraft(Math.max(0, i - 1));
}

/* ---- перетаскивание миниатюр для смены порядка ---- */
let dragFromIndex = null;

function onThumbDragStart(e) {
  dragFromIndex = Number(this.dataset.index);
  this.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  // Firefox требует установить данные, иначе перетаскивание не стартует
  try { e.dataTransfer.setData("text/plain", String(dragFromIndex)); } catch {}
}

function onThumbDragOver(e) {
  if (dragFromIndex === null) return;
  e.preventDefault(); // разрешаем drop
  e.dataTransfer.dropEffect = "move";
  const rect = this.getBoundingClientRect();
  const after = (e.clientY - rect.top) > rect.height / 2;
  this.classList.toggle("drop-after", after);
  this.classList.toggle("drop-before", !after);
}

function onThumbDragLeave() {
  this.classList.remove("drop-before", "drop-after");
}

async function onThumbDrop(e) {
  e.preventDefault();
  const from = dragFromIndex;
  const over = Number(this.dataset.index);
  const rect = this.getBoundingClientRect();
  const after = (e.clientY - rect.top) > rect.height / 2;
  this.classList.remove("drop-before", "drop-after");
  if (from === null) return;
  // Позиция вставки в исходной нумерации (0-based, «перед элементом insertBefore»).
  const insertBefore = over + (after ? 1 : 0);
  // No-op: бросили на то же место.
  if (insertBefore === from || insertBefore === from + 1) return;
  // Бэкенд reorder = pop(from), затем insert(target). После удаления исходного
  // слайда индексы правее сдвигаются на 1 — корректируем цель.
  const target0 = insertBefore > from ? insertBefore - 1 : insertBefore;
  await moveSlide(from, target0 + 1); // moveSlide ждёт 1-based позицию
}

function onThumbDragEnd() {
  this.classList.remove("dragging");
  document.querySelectorAll(".thumb.drop-before, .thumb.drop-after")
    .forEach((t) => t.classList.remove("drop-before", "drop-after"));
  dragFromIndex = null;
}

async function moveSlide(idx, to1) {
  if (to1 < 1 || to1 > draftPlan.slides.length) return;
  await flushPendingSave(); // preserve the moving slide's edit before reordering
  pushUndo();
  await fetch(U(`/api/drafts/${sessionId}/slides/${idx + 1}/move`), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ to: to1 }),
  });
  await reloadDraft(to1 - 1);
}

/* ---- отмена/повтор структурных действий (Cmd/Ctrl+Z, Shift+Z / Ctrl+Y) ----
   Снимок всего плана кладётся в стек ПЕРЕД каждым структурным изменением
   (добавить / удалить / переместить / сменить макет). Отмена восстанавливает
   снимок целиком через PUT /api/drafts/{sid}. Набор текста в полях сюда не
   входит — там работает встроенная отмена браузера, поэтому обработчик
   пропускает нажатия, когда фокус в поле ввода. История живёт в открытой
   вкладке (перезагрузку страницы не переживает). */
const HISTORY_CAP = 50;
let undoStack = [];
let redoStack = [];
let historyBusy = false;

function snapshotPlan() { return JSON.parse(JSON.stringify(draftPlan)); }

function pushUndo() {
  undoStack.push(snapshotPlan());
  if (undoStack.length > HISTORY_CAP) undoStack.shift();
  redoStack = []; // новое действие обнуляет ветку повтора
}

async function applyPlan(snapshot) {
  clearTimeout(putTimer); putTimer = null; // снимок заменяет план целиком
  let r;
  try {
    r = await fetch(U(`/api/drafts/${sessionId}`), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(snapshot),
    });
  } catch (_) { r = null; }
  if (!r || !r.ok) return false; // 409/оффлайн — оставляем состояние как есть
  draftPlan = await r.json();
  const focus = Math.max(0, Math.min(current, draftPlan.slides.length - 1));
  await reloadDraft(focus);
  return true;
}

async function undo() {
  if (historyBusy || !undoStack.length) return;
  historyBusy = true;
  try {
    const prev = undoStack[undoStack.length - 1];
    const cur = snapshotPlan();
    if (await applyPlan(prev)) {
      undoStack.pop();
      redoStack.push(cur);
      if (redoStack.length > HISTORY_CAP) redoStack.shift();
    }
  } finally { historyBusy = false; }
}

async function redo() {
  if (historyBusy || !redoStack.length) return;
  historyBusy = true;
  try {
    const next = redoStack[redoStack.length - 1];
    const cur = snapshotPlan();
    if (await applyPlan(next)) {
      redoStack.pop();
      undoStack.push(cur);
      if (undoStack.length > HISTORY_CAP) undoStack.shift();
    }
  } finally { historyBusy = false; }
}

// Горячие клавиши только в черновике (ручной/чат‑режим); в собранной деке плана
// нет. Берём e.code, а не e.key — он не зависит от раскладки, поэтому русские
// «я»/«н» на тех же физических клавишах Z/Y тоже сработают.
document.addEventListener("keydown", (e) => {
  if (!(mode === "manual" || mode === "chat")) return;
  if (!(e.metaKey || e.ctrlKey)) return;
  if (e.code !== "KeyZ" && e.code !== "KeyY") return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable))
    return; // в поле ввода — отдаём отмену браузеру
  e.preventDefault();
  if (e.code === "KeyY" || (e.code === "KeyZ" && e.shiftKey)) redo();
  else undo();
});

/* ---- template picker ---- */
function openPicker(onPick) {
  const picker = byId("picker");
  const grid = byId("pickerGrid");
  grid.innerHTML = "";
  catalog.forEach((t, i) => {
    const card = document.createElement("button");
    card.type = "button"; card.className = "picker-item";
    // visual preview: a scaled iframe of the real one-slide render (lazy src)
    const prev = document.createElement("div");
    prev.className = "picker-prev";
    const ifr = document.createElement("iframe");
    ifr.loading = "lazy";
    ifr.tabIndex = -1;
    ifr.src = U(`/api/templates/${t.id}/preview?static=1`);  // К§16: покойные превью, без лупов
    prev.appendChild(ifr);
    const num = document.createElement("span");
    num.className = "picker-num";
    num.textContent = String(i + 1).padStart(2, "0");
    prev.appendChild(num);
    const meta = document.createElement("div");
    meta.className = "picker-meta";
    // К§2: человекочитаемое имя макета крупно; сырой id — приглушённой третьей строкой (фолбэк на id).
    meta.innerHTML = `<span class="picker-id">${t.display_name || t.id}</span>` +
      `<span class="picker-intent">${t.intent || ""}</span>` +
      (t.display_name ? `<span class="picker-code">${t.id}</span>` : "");
    card.appendChild(prev);
    card.appendChild(meta);
    card.onclick = () => { picker.classList.add("hidden"); onPick(t.id); };
    grid.appendChild(card);
  });
  picker.classList.remove("hidden");
}
byId("pickerClose")?.addEventListener("click", () =>
  byId("picker").classList.add("hidden"));

async function initDraftBuilder() {
  byId("rebuild")?.classList.remove("hidden");   // «Проверить и улучшить слайды» — в обоих режимах
  if (mode === "manual") {
    byId("addSlide")?.classList.remove("hidden");
    byId("builder")?.classList.remove("hidden");
    const r = await fetch(U("/api/templates"));
    if (r.ok) catalog = await r.json();
  }
  if (mode === "chat") setupChatMode();
  await fetchPlan();
  if (mode === "chat") renderOutline();
}

/* ---- rebuild the draft through the engine (mode=htmlpolish) ---- */
let rebuilding = false;
byId("rebuild")?.addEventListener("click", async () => {
  if (rebuilding) return;
  if (!draftPlan.slides || !draftPlan.slides.length) return;  // К§17: кнопка дизейблится при пустом плане
  const n = draftPlan.slides.length;
  // Ч§3: единый копирайт, «вы», без «движка»; продуктовая формулировка + русская плюрализация.
  const ok = await confirmDialog(
    `Улучшить ${n} ${plural(n, "слайд", "слайда", "слайдов")}? Проверим вёрстку и внешний вид ` +
    `каждого и исправим ошибки — примерно ${n}–${2 * n} мин.`,
    "Улучшить", "Отмена");
  if (!ok) return;
  rebuilding = true;
  const btn = byId("rebuild");
  btn.disabled = true; btn.textContent = REBUILD_LABEL.busy;
  try {
    // Make sure the last form edit reached the server's plan.json before rebuild
    // reads it — otherwise a quick type→rebuild rebuilds a stale deck.
    await flushPendingSave();
    const r = await fetch(U(`/api/drafts/${sessionId}/rebuild`), { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    watchRebuild();
  } catch (e) {
    rebuilding = false; btn.disabled = false; btn.textContent = REBUILD_LABEL.idle;
    // Ч§3/К§17 — продуктовый текст + бренд-диалог вместо нативного alert.
    alertDialog("Не удалось запустить улучшение: " + (e && e.message ? e.message : e));
  }
});

// Show the build overlay + stream progress; on success reload as a normal built
// deck (drop the draft mode so the editor switches to HTML-as-truth editing).
function watchRebuild() {
  showOverlay(true);
  buildTitle.textContent = "Улучшаю слайды…";
  let done = false;
  const es = new EventSource(U(`/api/jobs/${sessionId}/events`));
  es.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch (_) { return; }
    const pct = ev.progress_pct || 0;
    const friendly = friendlyDetail(ev.detail) || STAGE_LABEL[ev.stage] || ev.stage || "";
    buildSub.textContent = `${friendly} · ${pct}%`;
    if (ev.terminal) {
      done = true; es.close();
      if (ev.stage === "done") {
        // Ч§5 — &rebuilt=1: на готовой деке один раз показать пояснение после улучшения.
        location.href = U(`/editor?session=${sessionId}&rebuilt=1`); // reload as built deck
      } else {
        buildTitle.textContent =
          ev.stage === "cancelled" ? "Улучшение остановлено" : "Не удалось улучшить слайды";
        buildSub.textContent = ev.error || "";
        rebuilding = false;
        const btn = byId("rebuild");
        btn.disabled = false; btn.textContent = REBUILD_LABEL.idle;
      }
    }
  };
  es.onerror = () => { if (!done) { es.close(); setTimeout(watchRebuild, 3000); } };
}

/* ---- feature 3: slide-building chat agent ---- */
function setupChatMode() {
  const head = document.querySelector(".chat-head");
  if (head) head.innerHTML =
    "<h3>Сборка в чате</h3>" +
    "<p>Опишите презентацию — ассистент спланирует структуру и создаст слайды. " +
    "Команды: «добавь слайд про…», «перепиши покороче», «удали слайд», «назови презентацию…».</p>";
  const target = byId("chatTarget");
  if (target) target.textContent = "Ассистент";
  chatText.placeholder = "Например: сделай презентацию про наш продукт для инвесторов";
  chatSend.textContent = "Отправить";
  // Ч§6 — примеры пустого чата про СБОРКУ, а не про точечные правки.
  const empty = byId("chatEmpty");
  if (empty) empty.textContent = CHAT_BUILD_EMPTY;
  byId("outline")?.classList.remove("hidden");   // показать живую панель аутлайна
}

// True while at least one outline slide is planned but not yet filled (a real
// build target). Used to toggle the "Собрать деку" button and guard doBuild().
function hasBuildTargets() {
  return (draftPlan.slides || []).some(
    (s) => s && s.brief && !s.filled && !s.freeform && !s.slide_type);
}

const TYPE_LABEL = { title: "титул", bullets: "список", stats: "цифры",
                     two_col: "две колонки" };

// Debounced per-slide field save. Mirrors the manual builder's edit debounce so a
// rapid edit followed by navigation/rebuild isn't lost.
const fieldTimers = {};
function saveFields(idx, slideType, fields) {
  clearTimeout(fieldTimers[idx]);
  fieldTimers[idx] = setTimeout(async () => {
    try {
      const r = await fetch(U(`/api/drafts/${sessionId}/slides/${idx}/fields`), {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slide_type: slideType, fields }),
      });
      if (r.ok) { draftPlan = (await r.json()).plan; loadDeck(); }
    } catch (_) { /* transient; next edit retries */ }
  }, 500);
}

function fieldInput(value, onInput) {
  const el = document.createElement("input");
  el.className = "field-input";
  el.value = value || "";
  el.addEventListener("input", onInput);
  return el;
}

// Построчный редактор списка для типизированных полей чата. `cols` описывает
// ячейки одной строки: одна ячейка без key → значение-строка (bullets/колонки),
// две ячейки с key value/label → объект (stats). Визуально повторяет список
// конструктора (.field-list/.field-item/+ пункт/✕). onChange(values) зовём после
// любой правки/добавления/удаления — там вызывается saveFields.
function lineListEditor(items, cols, onChange) {
  const list = document.createElement("div");
  list.className = "field-list";
  const single = cols.length === 1;

  const collect = () => {
    const out = [];
    list.querySelectorAll(".field-item").forEach((row) => {
      const inputs = row.querySelectorAll("input");
      if (single) {
        const v = inputs[0].value.trim();
        if (v) out.push(v);
      } else {
        const obj = {};
        cols.forEach((c, i) => { obj[c.key] = inputs[i].value.trim(); });
        if (Object.values(obj).some(Boolean)) out.push(obj);
      }
    });
    onChange(out);
  };

  const makeRow = (item) => {
    const row = document.createElement("div");
    row.className = "field-item";
    cols.forEach((c, i) => {
      const inp = document.createElement("input");
      inp.className = "field-input";
      inp.placeholder = c.placeholder;
      inp.value = single ? (item || "") : ((item && item[c.key]) || "");
      inp.addEventListener("input", collect);
      row.appendChild(inp);
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn btn-ghost btn-sm item-del";
    del.textContent = "✕";
    del.onclick = () => { row.remove(); collect(); };
    row.appendChild(del);
    list.appendChild(row);
    return row;
  };

  (items.length ? items : [single ? "" : {}]).forEach(makeRow);

  const wrap = document.createElement("div");
  wrap.className = "field-lines";
  wrap.appendChild(list);
  const add = document.createElement("button");
  add.type = "button";
  add.className = "btn btn-ghost btn-sm";
  add.textContent = "+ пункт";
  add.onclick = () => { makeRow(single ? "" : {}).querySelector("input")?.focus(); };
  wrap.appendChild(add);
  return wrap;
}

// Build the editable card for a typed slide. Reads current values from s.fields,
// writes edits back through saveFields. One small builder per type.
function renderFieldCard(s, idx) {
  const card = document.createElement("div");
  card.className = "field-card";
  const f = Object.assign({}, s.fields || {});
  const commit = () => saveFields(idx, s.slide_type, f);

  const addRow = (labelText, input) => {
    const row = document.createElement("label");
    row.className = "field-row";
    const cap = document.createElement("span");
    cap.className = "field-cap";
    cap.textContent = labelText;
    row.appendChild(cap);
    row.appendChild(input);
    card.appendChild(row);
  };

  addRow("Заголовок", fieldInput(f.heading, (e) => {
    f.heading = e.target.value; commit();
  }));

  if (s.slide_type === "title") {
    addRow("Подзаголовок", fieldInput(f.subtitle, (e) => {
      f.subtitle = e.target.value; commit();
    }));
  } else if (s.slide_type === "bullets") {
    f.bullets = f.bullets || [];
    addRow("Тезисы", lineListEditor(f.bullets, [{ placeholder: "пункт" }],
      (vals) => { f.bullets = vals; commit(); }));
  } else if (s.slide_type === "stats") {
    f.stats = f.stats || [];
    addRow("Цифры", lineListEditor(f.stats,
      [{ key: "value", placeholder: "значение" }, { key: "label", placeholder: "подпись" }],
      (vals) => { f.stats = vals; commit(); }));
  } else if (s.slide_type === "two_col") {
    f.left = f.left || []; f.right = f.right || [];
    addRow("Левая колонка", lineListEditor(f.left, [{ placeholder: "пункт" }],
      (vals) => { f.left = vals; commit(); }));
    addRow("Правая колонка", lineListEditor(f.right, [{ placeholder: "пункт" }],
      (vals) => { f.right = vals; commit(); }));
  }
  return card;
}

// A raw slide: offer to structure it via the chat agent (propose_content).
function renderRawActions(idx) {
  const wrap = document.createElement("div");
  wrap.className = "outline-raw";
  const btn = document.createElement("button");
  btn.className = "outline-propose";
  btn.textContent = "Предложить контент";
  btn.addEventListener("click", () => {
    chatText.value = "Разложи слайды по структурированным полям";
    sendAgent();
  });
  wrap.appendChild(btn);
  return wrap;
}

// Render the live outline. Typed slides show editable field-cards (what you see
// is what renders); raw slides show the brief + a "Предложить контент" action.
function renderOutline() {
  const list = byId("outlineList");
  if (!list) return;
  list.innerHTML = "";
  (draftPlan.slides || []).forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "outline-item";
    const head = document.createElement("div");
    head.className = "outline-head";
    const num = document.createElement("span");
    num.className = "outline-num";
    num.textContent = i + 1;
    const label = document.createElement("span");
    label.className = "outline-label";
    label.textContent =
      (s.fields && s.fields.heading) || (s.content && s.content.title) ||
      s.brief || "—";
    const badge = document.createElement("span");
    if (s.slide_type) {
      badge.className = "outline-badge is-typed";
      badge.textContent = TYPE_LABEL[s.slide_type] || s.slide_type;
    } else {
      badge.className = "outline-badge " + (s.filled ? "is-ready" : "is-plan");
      badge.textContent = s.filled ? "готов" : "сырой";
    }
    head.appendChild(num);
    head.appendChild(label);
    head.appendChild(badge);
    li.appendChild(head);
    if (s.slide_type) li.appendChild(renderFieldCard(s, i + 1));
    else if (s.brief && !s.filled && !s.freeform) {
      li.appendChild(renderRawActions(i + 1));
    }
    list.appendChild(li);
  });
  byId("buildDeck")?.classList.toggle("hidden", !hasBuildTargets());
  // Р§4 — ровно одна залитая primary в панели чата: пока есть незаполненные слайды,
  // primary — #buildDeck (залит в разметке), иначе — #chatSend.
  byId("chatSend")?.classList.toggle("btn-accent", !hasBuildTargets());
}

// Fill the whole outline in one shot via POST /api/drafts/{id}/build. Synchronous
// endpoint (tens of seconds) — we show the indeterminate build overlay meanwhile.
let building = false;
async function doBuild() {
  if (building) return;
  if (!hasBuildTargets()) { addMsg("bot", "В плане пока нет слайдов — опишите презентацию в чате."); return; }
  building = true;
  showOverlay(true);
  buildTitle.textContent = "Собираю презентацию…";
  buildSub.textContent = "Заполняю сырые слайды…";
  try {
    const r = await fetch(U(`/api/drafts/${sessionId}/build`), { method: "POST" });
    if (!r.ok) {
      addMsg("bot", "Не удалось собрать: " + (await r.text()));
      return;
    }
    try { draftPlan = await r.json(); } catch (_) { await fetchPlan(); }
    renderOutline();
    loadDeck();
  } catch (e) {
    addMsg("bot", "Не удалось собрать: " + (e && e.message ? e.message : e));
  } finally {
    building = false;
    showOverlay(false);
  }
}

byId("buildDeck")?.addEventListener("click", doBuild);

async function sendAgent() {
  if (CHAT_EDIT_DISABLED) return;   // сборка в чате — фича в разработке
  const message = chatText.value.trim();
  if (!message) return;
  lastInstruction = message;   // Ч§7 — сохранить до очистки поля (для «Повторить»)
  addMsg("user", message);
  chatText.value = "";
  const thinking = addMsg("bot", "Думаю…");
  const t0 = Date.now();
  const controller = new AbortController();
  chatInFlight = controller;
  let timedOut = false;
  setChatBusy(true);
  chatTimerId = setInterval(() => tickElapsed(thinking, t0), 1000);
  const timeoutId = setTimeout(() => { timedOut = true; controller.abort(); },
                               CHAT_TIMEOUT_MS);
  try {
    const r = await fetch(U(`/api/drafts/${sessionId}/agent`), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, current_index: current + 1 }),
      signal: controller.signal,
    });
    if (!r.ok) {
      thinking.className = "msg err";
      thinking.textContent = await chatErrText(r);   // Ч§7 — продуктовый текст + «Повторить»
      appendRetry(thinking);
    } else {
      lastInstruction = "";
      const res = await r.json();
      thinking.textContent = res.reply || "Готово.";
      if (res.changed) {
        await fetchPlan();
        renderOutline();
        if (res.go_to) pendingGoTo = res.go_to - 1;
        loadDeck();
      }
    }
  } catch (e) {
    thinking.className = "msg err";
    thinking.textContent = timedOut
      ? "Превышено время ожидания (5 мин). Попробуйте ещё раз."
      : (e && e.name === "AbortError" ? "Отменено."
         : "Ошибка: " + (e && e.message ? e.message : e));
  } finally {
    clearTimeout(timeoutId);
    clearInterval(chatTimerId);
    chatTimerId = null;
    chatInFlight = null;
    setChatBusy(false);
  }
}

/* init */
const homeLink = document.querySelector("a.home");
if (homeLink) homeLink.href = U("/");

// К§8 — одна правая панель с табами «Поля | Чат» (только manual: там обе панели живут
// вместе). Переключение — класс .hidden на #builder/.chat (id/DOM не трогаем — JS завязан).
// В chat-режиме и на готовой деке панель одна → табов нет.
function setupPanelTabs() {
  const tabs = byId("rpanelTabs");
  if (!tabs) return;
  if (mode !== "manual") { tabs.classList.add("hidden"); return; }
  const builder = byId("builder");
  const chat = document.querySelector(".chat");
  const tabF = byId("tabFields");
  const tabC = byId("tabChat");
  if (!builder || !chat || !tabF || !tabC) return;
  tabs.classList.remove("hidden");
  const show = (fields) => {
    builder.classList.toggle("hidden", !fields);
    chat.classList.toggle("hidden", fields);
    tabF.classList.toggle("is-active", fields);
    tabC.classList.toggle("is-active", !fields);
  };
  tabF.onclick = () => show(true);
  if (CHAT_EDIT_DISABLED) {
    // Вкладка «Правки в чате» — фича в разработке: неактивна, с дружелюбной подсказкой.
    tabC.classList.add("is-disabled");
    tabC.setAttribute("aria-disabled", "true");
    tabC.setAttribute("data-tip", CHAT_DEV_TIP);
    tabC.onclick = null;
  } else {
    tabC.onclick = () => show(false);
  }
  show(true); // дефолт в manual — «Поля»
}

// Ч§5 — бейдж режима в тулбаре + одноразовое пояснение после улучшения (rebuild-редирект).
const MODE_BADGE = { "": "Готовая презентация", manual: "Конструктор", chat: "Сборка в чате" };
(function initModeBadge() {
  const badge = byId("modeBadge");
  if (badge) badge.textContent = MODE_BADGE[mode] != null ? MODE_BADGE[mode] : MODE_BADGE[""];
  if (params.get("rebuilt")) {
    addMsg("bot", "Презентация собрана и проверена. Теперь правки — прямо на слайде.");
    // Убрать параметр, чтобы сообщение не повторялось по F5.
    const url = new URL(location.href);
    url.searchParams.delete("rebuilt");
    history.replaceState(null, "", url);
  }
})();

setupPanelTabs(); // К§8 — правая панель с табами (сам решает по mode, нужны ли табы)
disableChatEditing(); // сразу гасим чат-контролы, чтобы не мелькали активными

// Повторно после инициализации: в chat-режиме setupChatMode() возвращает чату
// активный вид — .finally перекрывает его обратно в «в разработке» (и на ошибке init).
if (isDraft) { initDraftBuilder().then(initEditor).finally(disableChatEditing); }
else { Promise.resolve(initEditor()).finally(disableChatEditing); }
