// Gateway URL prefix (e.g. /slides); empty in standalone dev. Injected by server.
const PREFIX = window.__APP_PREFIX__ || "";
const U = (p) => PREFIX + p;

const params = new URLSearchParams(location.search);
const sessionId = params.get("session");
const mode = params.get("mode") || "";        // "manual" | "chat" | "" (built deck)
const isDraft = mode === "manual" || mode === "chat";
const frame = document.getElementById("deck");
document.getElementById("html").href = U(`/api/jobs/${sessionId}/deck?download=1`);

let slides = [];
let current = 0;
let pendingGoTo = 0; // slide to show after the next iframe load

function loadDeck() {
  // cache-bust so edits/chat rewrites are reflected on reload
  frame.src = U(`/api/jobs/${sessionId}/deck?t=${Date.now()}`);
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

function showOverlay(show) { overlay && overlay.classList.toggle("hidden", !show); }

// Opening the editor for a run whose deck isn't built yet would otherwise show a
// blank 404 iframe. Instead, gate on readiness: if the deck exists, load it; if
// the run is still building, show progress (SSE) and load the deck when done.
async function initEditor() {
  let head;
  try {
    head = await fetch(U(`/api/jobs/${sessionId}/deck?probe=${Date.now()}`),
                       { method: "GET", headers: { Range: "bytes=0-0" } });
  } catch (e) { head = null; }
  if (head && head.ok) { showOverlay(false); loadDeck(); return; }
  if (head && head.status === 404) { waitForBuild(); return; }
  // any other status (e.g. 401) — fall back to a plain load attempt
  showOverlay(false); loadDeck();
}

function waitForBuild() {
  showOverlay(true);
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
      }
    }
  };
  es.onerror = () => {
    if (done) return;
    es.close();
    // Stream dropped but run may still be alive — retry readiness shortly.
    setTimeout(initEditor, 3000);
  };
}

frame.onload = () => {
  const doc = frame.contentDocument;
  if (!doc) return;
  slides = [...doc.querySelectorAll(".slide")];
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
        el.addEventListener("blur", () => {
          const changed = el.__origHtml !== undefined && el.innerHTML !== el.__origHtml;
          el.__origHtml = undefined;
          if (changed) {
            // A green count-up number (.js-count) carries a persistent
            // data-count-final (set once by deck.js, never refreshed). An inline
            // edit changes textContent but NOT that attribute, so syncDraftSlideHtml
            // would bake the stale final value and silently revert the edit on the
            // next navigation. Refresh it to the edited text first.
            if (el.hasAttribute("data-count-final")) {
              el.setAttribute("data-count-final", el.textContent);
            }
            syncDraftSlideHtml(i);
          }
        });
      }
    }
  }));
  suppressDeckNavOnEdit(doc);
  buildThumbs();
  goTo(Math.min(pendingGoTo, slides.length - 1));
};

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
  clone.querySelectorAll("[data-count-final]").forEach((el) => {
    el.textContent = el.getAttribute("data-count-final");
    el.removeAttribute("data-count-final");
  });
  draftHtmlSaving = true;
  try {
    await fetch(U(`/api/drafts/${sessionId}/slides/${i + 1}/html`), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ html: clone.outerHTML }),
    });
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
  slides.forEach((_, i) => {
    const t = document.createElement("div");
    t.className = "thumb";
    t.textContent = "Слайд " + (i + 1);
    t.onclick = () => goTo(i);
    box.appendChild(t);
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
  document.getElementById("counter").textContent = `${current + 1} / ${slides.length}`;
  document.getElementById("chatTarget").textContent =
    mode === "chat" ? "Ассистент" : `Слайд ${current + 1}`;
  [...document.querySelectorAll(".thumb")].forEach((t, idx) =>
    t.classList.toggle("active", idx === current));
  // Only rebuild the form when the shown slide changed — NOT on the preview
  // reloads that follow each save (those would wipe focus and in-progress rows).
  if (mode === "manual" && builtFormFor !== current) renderBuilderForm();
}

document.getElementById("prev").onclick = () => goTo(current - 1);
document.getElementById("next").onclick = () => goTo(current + 1);

function currentDeckHtml() {
  const doc = frame.contentDocument;
  if (!doc || !doc.documentElement) {
    // iframe is mid-reload or not ready — caller must handle this.
    throw new Error("дека ещё не загрузилась, подождите секунду");
  }
  // Strip the editor-only contenteditable attributes we inject at load time so
  // the persisted/downloaded/exported deck stays clean (otherwise a downloaded
  // HTML deck would be globally editable). Clone so the live editing DOM keeps
  // its contenteditable and in-place editing keeps working.
  const clone = doc.documentElement.cloneNode(true);
  clone.querySelectorAll("[contenteditable]").forEach(
    (el) => el.removeAttribute("contenteditable"));
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
const EXPORT_LABEL = {
  png: { idle: "Экспорт в PNG (ZIP)", busy: "Готовлю PNG…", ready: "Скачать PNG (ZIP)" },
  pptx: { idle: "Экспорт в PPTX", busy: "Готовлю PPTX…", ready: "Скачать PPTX" },
};

// Any deck edit invalidates a finished (or in-flight) export — a "Скачать" pill
// would otherwise hand back the pre-edit file. Edit funnels call this to reset the
// controls back to "Экспорт", forcing a fresh render on the next click.
const _exportResets = [];
function markExportsStale() { _exportResets.forEach((fn) => fn()); }

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
    if (state === "ready") toReady();
    else if (state === "error") toIdle("Ошибка — повторить");
  }

  btn.onclick = async () => {
    // A ready control downloads; anything else (re)starts an export.
    if (btn.classList.contains("is-ready")) {
      location.href = U(`/api/jobs/${sessionId}/export/${fmt}/file`);
      return;
    }
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

function flash(btn, text) {
  const orig = btn.textContent;
  btn.textContent = text;
  setTimeout(() => { btn.textContent = orig; }, 1500);
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
  // If a request is already running, the button acts as Cancel.
  if (chatInFlight) {
    chatInFlight.abort();
    return;
  }
  if (mode === "chat") return sendAgent();   // feature 3: slide-building agent
  const instruction = chatText.value.trim();
  if (!instruction) return;
  const slideIndex = current + 1;
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
      thinking.textContent = "Ошибка: " + (await r.text());
    } else {
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
    form.innerHTML = `<p class="builder-note">Этот слайд отредактирован через чат. ` +
      `Дальнейшие правки — через чат справа.</p>`;
    return;
  }
  const tpl = tplOf(slide.template_id);
  tplBox.innerHTML =
    `<span class="tpl-name">Макет: ${slide.template_id}</span>` +
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
  label.textContent = name + (spec.required ? " *" : "");
  wrap.appendChild(label);

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

function scheduleSave() {
  clearTimeout(putTimer);
  putTimer = setTimeout(saveCurrentSlide, 600);
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

async function saveCurrentSlide() {
  // Capture the slide index up front: `current` can change (navigation) during
  // the awaited PUT, and the URL/marking must stay bound to the edited slide.
  const idx = current;
  putTimer = null;
  const slide = draftPlan.slides[idx];
  if (!slide || slide.freeform) return;
  const content = collectContent();
  slide.content = content; // optimistic local update
  setSaveStatus("saving");
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
    loadDeck(); // refresh preview
  } else {
    // Save failed (network/server): resync from the server so we never write stale
    // state back on the next edit, and say so in words (persistent red status)
    // instead of an alert() on every failure.
    await reloadDraft(idx);
    setSaveStatus("error");
  }
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

byId("addSlide")?.addEventListener("click", () =>
  openPicker(async (tid) => {
    await flushPendingSave(); // preserve the current slide's edit before inserting
    await fetch(U(`/api/drafts/${sessionId}/slides`), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: tid }),
    });
    await reloadDraft(draftPlan.slides.length); // jump to the new last slide
  }));

byId("slideDelete")?.addEventListener("click", async () => {
  if (!draftPlan.slides[current]) return;
  clearTimeout(putTimer); putTimer = null; // slide is going away — drop pending save
  await fetch(U(`/api/drafts/${sessionId}/slides/${current + 1}`), { method: "DELETE" });
  await reloadDraft(Math.max(0, current - 1));
});

byId("slideUp")?.addEventListener("click", () => moveSlide(current, current));     // to 1-based current = up
byId("slideDown")?.addEventListener("click", () => moveSlide(current, current + 2));

async function moveSlide(idx, to1) {
  if (to1 < 1 || to1 > draftPlan.slides.length) return;
  await flushPendingSave(); // preserve the moving slide's edit before reordering
  await fetch(U(`/api/drafts/${sessionId}/slides/${idx + 1}/move`), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ to: to1 }),
  });
  await reloadDraft(to1 - 1);
}

/* ---- template picker ---- */
function openPicker(onPick) {
  const picker = byId("picker");
  const grid = byId("pickerGrid");
  grid.innerHTML = "";
  catalog.forEach((t) => {
    const card = document.createElement("button");
    card.type = "button"; card.className = "picker-item";
    // visual preview: a scaled iframe of the real one-slide render (lazy src)
    const prev = document.createElement("div");
    prev.className = "picker-prev";
    const ifr = document.createElement("iframe");
    ifr.loading = "lazy";
    ifr.tabIndex = -1;
    ifr.src = U(`/api/templates/${t.id}/preview`);
    prev.appendChild(ifr);
    const meta = document.createElement("div");
    meta.className = "picker-meta";
    meta.innerHTML = `<span class="picker-id">${t.id}</span>` +
      `<span class="picker-intent">${t.intent || ""}</span>`;
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
  byId("rebuild")?.classList.remove("hidden");   // «Собрать через движок» — в обоих режимах
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
  if (!draftPlan.slides || !draftPlan.slides.length) {
    alert("Черновик пуст — добавьте хотя бы один слайд."); return;
  }
  const n = draftPlan.slides.length;
  if (!confirm(`Прогнать черновик через движок? Каждый из ${n} слайд(ов) пройдёт ` +
               "вёрстку, визуальную проверку качества и автоисправление; после этого " +
               "дека станет обычной (правки прямо на слайде). Это ~1–2 мин на слайд.")) return;
  rebuilding = true;
  const btn = byId("rebuild");
  btn.disabled = true; btn.textContent = "Запускаю…";
  try {
    // Make sure the last form edit reached the server's plan.json before rebuild
    // reads it — otherwise a quick type→rebuild rebuilds a stale deck.
    await flushPendingSave();
    const r = await fetch(U(`/api/drafts/${sessionId}/rebuild`), { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    watchRebuild();
  } catch (e) {
    rebuilding = false; btn.disabled = false; btn.textContent = "Собрать через движок";
    alert("Не удалось запустить пересборку: " + (e && e.message ? e.message : e));
  }
});

// Show the build overlay + stream progress; on success reload as a normal built
// deck (drop the draft mode so the editor switches to HTML-as-truth editing).
function watchRebuild() {
  showOverlay(true);
  buildTitle.textContent = "Пересобираю через движок…";
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
        location.href = U(`/editor?session=${sessionId}`); // reload as built deck
      } else {
        buildTitle.textContent =
          ev.stage === "cancelled" ? "Пересборка остановлена" : "Не удалось пересобрать";
        buildSub.textContent = ev.error || "";
        rebuilding = false;
        const btn = byId("rebuild");
        btn.disabled = false; btn.textContent = "Собрать через движок";
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

// Append a hint line into a typed-slide field card. Named distinctly from the
// manual builder's hint(text)->node (below) so the two never collide — a prior
// duplicate `hint` shadowed that one and crashed the whole builder form render.
function appendHint(card, text) {
  const h = document.createElement("div");
  h.className = "field-hint";
  h.textContent = text;
  card.appendChild(h);
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
    addRow("Тезисы", fieldInput(f.bullets.join(" | "), (e) => {
      f.bullets = e.target.value.split("|").map((x) => x.trim()).filter(Boolean);
      commit();
    }));
    appendHint(card, "Пункты через | (вертикальная черта)");
  } else if (s.slide_type === "stats") {
    f.stats = f.stats || [];
    addRow("Цифры", fieldInput(
      f.stats.map((x) => `${x.value}=${x.label}`).join(" | "), (e) => {
        f.stats = e.target.value.split("|").map((p) => {
          const [value, label] = p.split("=");
          return { value: (value || "").trim(), label: (label || "").trim() };
        }).filter((x) => x.value || x.label);
        commit();
      }));
    appendHint(card, "value=label, пары через |  (напр. 99%=аптайм | 3=региона)");
  } else if (s.slide_type === "two_col") {
    f.left = f.left || []; f.right = f.right || [];
    addRow("Левая колонка", fieldInput(f.left.join(" | "), (e) => {
      f.left = e.target.value.split("|").map((x) => x.trim()).filter(Boolean);
      commit();
    }));
    addRow("Правая колонка", fieldInput(f.right.join(" | "), (e) => {
      f.right = e.target.value.split("|").map((x) => x.trim()).filter(Boolean);
      commit();
    }));
    appendHint(card, "Пункты через | в каждой колонке");
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
}

// Fill the whole outline in one shot via POST /api/drafts/{id}/build. Synchronous
// endpoint (tens of seconds) — we show the indeterminate build overlay meanwhile.
let building = false;
async function doBuild() {
  if (building) return;
  if (!hasBuildTargets()) { addMsg("bot", "Аутлайн пуст — нечего собирать."); return; }
  building = true;
  showOverlay(true);
  buildTitle.textContent = "Собираю деку…";
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
  const message = chatText.value.trim();
  if (!message) return;
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
      thinking.textContent = "Ошибка: " + (await r.text());
    } else {
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
if (isDraft) { initDraftBuilder().then(initEditor); } else { initEditor(); }
