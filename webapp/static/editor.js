// Gateway URL prefix (e.g. /slides); empty in standalone dev. Injected by server.
const PREFIX = window.__APP_PREFIX__ || "";
const U = (p) => PREFIX + p;

const params = new URLSearchParams(location.search);
const sessionId = params.get("session");
const mode = params.get("mode") || "";        // "manual" | "chat" | "" (built deck)
const isDraft = mode === "manual" || mode === "chat";
// «Стеклянная» сборка поверх ручного черновика: клиент крутит цикл /glass/step,
// лента тамбов растёт, сомнительные слайды ждут ответа в панели вопросов.
// Не const: степпер включается ещё и по СОСТОЯНИЮ плана — см. resumeGlassIfUnfinished().
let isGlass = params.get("glass") === "1" && mode === "manual";
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
const HOME_LINK = `<a class="btn btn-ghost" href="${U("/")}">На главную</a>`;
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
  } else if (!glassRunning) {
  // Пока идёт стеклянная сборка, contenteditable не вешаем: blur-синк слайда
  // гонялся бы с сейвами степпера за plan.json (потеря правок). После выхода
  // из glass-режима loadDeck() перерисует кадр уже с редактированием.
  // In-place text editing works everywhere. Built decks are HTML-as-truth, so
  // edits persist via saveDeck(). Drafts are DeckPlan-as-truth, so an inline edit
  // converts that slide to a freeform slide in the plan (synced on blur).
  // CRITICAL: sync only when the content actually changed. A bare focus+blur
  // (user clicks the preview, then clicks elsewhere) must NOT convert the slide
  // to freeform — that used to wipe the builder form/template on a mere click.
  slides.forEach((s, i) => s.querySelectorAll("*").forEach((el) => {
    // Узлы схемы не редактируются на слайде: текст — в боковой панели, а
    // единственный жест на диаграмме — перетаскивание узла (drag-раскладка).
    if (el.closest && el.closest(".diagram-host")) return;
    if (el.children.length === 0 && el.textContent.trim()) {
      el.setAttribute("contenteditable", "true");
      if (isDraft) {
        el.addEventListener("focus", () => { el.__origHtml = el.innerHTML; });
        el.addEventListener("blur", async () => {
          const orig = el.__origHtml;
          const changed = orig !== undefined && el.innerHTML !== orig;
          el.__origHtml = undefined;
          if (!changed) return;
          // Ручная сборка — двусторонняя: правка, попавшая в [data-slot]
          // макетного слайда, уезжает В ПОЛЯ плана (форма справа остаётся
          // живой), а не конвертирует слайд в freeform. Свободный режим —
          // только фолбэк для правок, которые в поля не ложатся.
          if (!draftPlan.slides[i]?.freeform && await syncDraftSlotEdit(i, el, orig)) {
            if (el.hasAttribute("data-count-final")) {
              el.setAttribute("data-count-final", el.textContent);
            }
            return;
          }
          // К§1: перед ПЕРВОЙ конвертацией не-freeform слайда в свободный режим —
          // подтверждение. Отказ восстанавливает исходный HTML и НЕ синкает.
          if (!draftPlan.slides[i]?.freeform && !freeformConfirmed) {
            const ok = await confirmDialog(
              "Эта правка не ложится в поля макета, слайд перейдёт в свободный режим: поля формы станут недоступны. Продолжить?",
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
      } else {
        // Собранная дека — HTML-as-truth, и правка на слайде жила только в
        // кадре: человек правил текст, уходил «На главную» (или закрывал
        // вкладку) — и правки не оставалось, без единого предупреждения.
        // Сохраняем так же, как черновик: по уходу из блока.
        el.addEventListener("focus", () => { el.__origHtml = el.innerHTML; });
        el.addEventListener("blur", () => {
          const changed = el.__origHtml !== undefined
            && el.innerHTML !== el.__origHtml;
          el.__origHtml = undefined;
          if (!changed) return;
          if (el.hasAttribute("data-count-final")) {   // см. коммент выше
            el.setAttribute("data-count-final", el.textContent);
          }
          saveDeckEdit();
        });
      }
    }
  }));
  }
  suppressDeckNavOnEdit(doc);
  attachDiagramDrag(doc);
  markGlassRibbons(doc);
  syncThumbs();
  goTo(Math.min(pendingGoTo, slides.length - 1));
  markPlaceholders(); // К§3 — пометить пустые слоты после рендера превью
  syncThemeToggle();  // ярлык перекраса — по теме только что загруженного кадра
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
// Правки, пришедшие ПОКА идёт сейв, раньше просто отбрасывались. Человек правит
// заголовок, тут же переходит к подзаголовку — сейв первого ещё в полёте, и
// второй blur уходил в никуда: на экране текст есть, в плане его нет, статус
// говорит «сохранено». После перезагрузки правка исчезала без следа. Копим их и
// досинхронизируем сразу после текущего сейва (PUT шлёт весь слайд целиком, так
// что один повтор добирает всё, что накопилось).
const draftHtmlPending = new Set();
async function syncDraftSlideHtml(i) {
  if (draftHtmlSaving) { draftHtmlPending.add(i); return; }
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
  // Лента «ждёт ответа» — редакторский оверлей, в план ей нельзя: freeform-слайд
  // навсегда унёс бы её в контент.
  clone.querySelectorAll(".glass-ribbon").forEach((el) => el.remove());
  draftHtmlSaving = true;
  try {
    // К§1: макет, из которого уходим, запоминает сервер (prev_layout на слайде) —
    // он питает кнопку «Вернуть макет».
    const r = await fetch(U(`/api/drafts/${sessionId}/slides/${i + 1}/html`), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ html: clone.outerHTML }),
    });
    if (!r.ok) { setSaveStatus("error"); return; } // К§4 — неуспех (404/гонка): не терять молча
    await fetchPlan();
    if (mode === "manual") renderBuilderForm(); // slide is now freeform
  } finally {
    draftHtmlSaving = false;
    // Слепок DOM снят В НАЧАЛЕ этого сейва, поэтому правка того же слайда,
    // прилетевшая по ходу PUT, в него не попала — свой же индекс не вычёркиваем.
    const next = draftHtmlPending.values().next();
    if (!next.done) {
      draftHtmlPending.delete(next.value);
      await syncDraftSlideHtml(next.value);
    }
  }
}

// Правка прямо на слайде БЕЗ ухода в свободный режим: если редактируемый узел
// лежит внутри [data-slot] макетного слайда и правку можно однозначно отразить
// в content (applySlotEdit, slotmap.js) — сохраняем её как обычную правку полей
// (PUT content). Слайд остаётся макетным, форма справа продолжает работать.
// Возвращает true, если правка учтена этим путём (успешно или с честным
// error-статусом); false — пусть вызывающий решает про freeform-фолбэк.
async function syncDraftSlotEdit(i, el, origHtml) {
  const slide = draftPlan.slides[i];
  if (!slide || slide.freeform || slide.slide_type || !slide.template_id) return false;
  const tpl = tplOf(slide.template_id);
  if (!tpl) return false;
  const holder = el.closest && el.closest("[data-slot]");
  if (!holder) return false;
  const name = holder.getAttribute("data-slot");
  const spec = tpl.slots[name];
  if (!spec) return false;
  // Текст-слот рендерится «слот = один узел» (data-slot на самом листе); чужая
  // вложенность — не наш случай, надёжнее фолбэк.
  if (spec.kind === "text" && el !== holder) return false;
  const t = document.createElement("div");
  t.innerHTML = origHtml == null ? "" : origHtml;
  const content = slide.content || {};
  const newVal = applySlotEdit(spec, content[name], t.textContent, el.textContent);
  if (newVal == null) return false;
  const patched = Object.assign({}, content);
  patched[name] = newVal;
  setSaveStatus("saving");
  let r = null;
  try {
    r = await fetch(U(`/api/drafts/${sessionId}/slides/${i + 1}`), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: patched }),
    });
  } catch (_) { /* сеть упала — ниже честный error-статус */ }
  // 409 — дека уже собрана/пересобирается, серверное состояние главнее.
  if (r && r.status === 409) {
    try { await reloadDraft(i); } catch (_) {}
    setSaveStatus("error");
    return true;
  }
  if (!r || !r.ok) {
    // План НЕ мутируем (правка не принята) — но и в freeform не уводим:
    // текст остался на слайде, статус честно говорит «не сохранено».
    setSaveStatus("error");
    return true;
  }
  const { errors } = await r.json();
  slide.content = patched;
  setSaveStatus("saved");
  markExportsStale();
  if (spec.kind === "text") el.classList.remove("is-placeholder"); // слот заполнен
  if (i === current && mode === "manual") {
    renderBuilderForm();               // форма подхватывает новое значение
    markFieldErrors(errors || []);
  }
  // Опустевший слот сервер рисует рыбой/прячет — DOM надо перерисовать целиком.
  if (spec.kind === "text" && String(newVal).trim() === "") loadDeck();
  return true;
}

// Переработка glass 2026-08-20 (жалоба №2): вопрос виден на САМОМ слайде, а не
// только «?» на тамбе — авторы не замечали, что от них ждут ответа. Лента —
// редакторский оверлей поверх кадра: деривативный deck.html чист (экспорт без
// служебных плашек), а syncDraftSlideHtml вырезает её перед запеканием в план.
// Стили инлайном: дека живёт в iframe со своим CSS, styles.css туда не достаёт.
function markGlassRibbons(doc) {
  if (!isDraft) return;
  slides.forEach((sec, i) => {
    const s = (draftPlan.slides || [])[i];
    if (!s) return;
    let text = "", bg = "";
    if (s.status === "needs_input" && !s.filled) {
      text = "ИИ ждёт вашего ответа — вопрос в панели «Пошаговая сборка»";
      bg = "#b45309";
    } else if (s.status === "failed") {
      text = "Слайд не заполнился — карточка в панели даёт выбрать макет заново";
      bg = "#b91c1c";
    }
    if (!text) return;
    if (doc.defaultView.getComputedStyle(sec).position === "static")
      sec.style.position = "relative";
    const r = doc.createElement("div");
    r.className = "glass-ribbon";
    r.setAttribute("contenteditable", "false");
    r.textContent = text;
    r.style.cssText = "position:absolute;top:24px;right:24px;z-index:60;" +
      "background:" + bg + ";color:#fff;padding:10px 20px;border-radius:10px;" +
      "font:600 22px/1.35 system-ui,sans-serif;max-width:46%;pointer-events:none;";
    sec.appendChild(r);
  });
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

// Drag узлов схемы в превью. Черновики ручного режима: offsets в fields →
// обычный дебаунс-сейв (PUT /fields, DeckPlan-as-truth). Собранные деки:
// HTML-as-truth — спек живёт в data-diagram хоста, коммит пишет атрибут и
// дебаунс-сейвит всю деку (saveDeck запечёт и атрибут, и свежий SVG).
function attachDiagramDrag(doc) {
  if (!window.DiagramDrag) return;
  const eng = doc.defaultView && doc.defaultView.DiagramEngine;
  if (!eng || !eng.layout || !eng.render) return;   // старая запечённая версия
  let any = false;
  if (!isDraft) {
    doc.querySelectorAll(".diagram-host").forEach((host) => {
      any = true;
      DiagramDrag.attach(host, {
        engine: eng,
        editText: true,   // у собранной деки нет боковой панели — правим на месте
        getSpec: () => {
          try { return JSON.parse(host.getAttribute("data-diagram") || "null"); }
          catch (_) { return null; }
        },
        onCommit: (spec) => {
          host.setAttribute("data-diagram", JSON.stringify(spec));
          scheduleBuiltDeckSave();
        },
      });
    });
  } else if (mode !== "manual") {
    return;
  } else {
  const sections = doc.querySelectorAll(".slide");
  (draftPlan.slides || []).forEach((slide, i) => {
    if (!slide || slide.slide_type !== "diagram" || !slide.fields) return;
    const host = sections[i] && sections[i].querySelector(".diagram-host");
    if (!host) return;
    any = true;
    DiagramDrag.attach(host, {
      engine: eng,
      getSpec: () => slide.fields.diagram
        ? JSON.parse(JSON.stringify(slide.fields.diagram)) : null,
      onCommit: (spec) => {
        slide.fields.diagram = spec;
        // После первого сдвига в панели должна появиться «Сбросить раскладку»:
        // пересобираем форму, предварительно сняв несейвленный ввод из DOM.
        if (i === current && builtFormFor === i) {
          const form = byId("builderForm");
          if (form && !form.querySelector("#dgmResetLayout")) {
            const fresh = collectDiagramFields();
            if (fresh) slide.fields = fresh;   // diagram уже с новыми offsets (база)
            builtFormFor = -1;
            renderBuilderForm();
          }
        }
        scheduleSave();
      },
    });
  });
  }
  // Клик по зоне схемы не должен листать деку (обработчики deck.js на document).
  if (any && !doc.__dgmNavGuard) {
    doc.__dgmNavGuard = true;
    doc.addEventListener("click", (e) => {
      if (e.target.closest && e.target.closest(".diagram-host")) e.stopPropagation();
    }, true);
  }
}

// Дебаунс-сейв собранной деки после drag'а узла: текстовые правки built-дек
// сохраняются кнопкой, но у перетаскивания нет своего «blur»-момента — молча
// терять сдвиг при уходе со страницы нельзя.
let builtDeckSaveTimer = null;
function scheduleBuiltDeckSave() {
  clearTimeout(builtDeckSaveTimer);
  builtDeckSaveTimer = setTimeout(async () => {
    builtDeckSaveTimer = null;
    setSaveStatus("saving");
    let ok = false;
    try { ok = await saveDeck(); } catch (_) { /* сеть — покажем error */ }
    setSaveStatus(ok ? "saved" : "error");
  }, 800);
}

function buildThumbs() {
  const box = document.getElementById("thumbs");
  box.innerHTML = "";
  if (isDraft && !draftPlan.slides.length) return; // К§4 — пустой драфт: тумб нет, только «+ Добавить слайд»
  // Удаление слайда — только в ручном режиме сборки (у чата структурой правит агент).
  const editable = mode === "manual";
  // Порядок слайдов меняется перетаскиванием миниатюры: в ручном черновике — через
  // план (DeckPlan-as-truth, POST /slides/{i}/move), в собранной деке — перестановкой
  // секций в DOM кадра и полным сейвом (HTML-as-truth).
  const reorderable = editable || !isDraft;
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
    // Единый cache-bust deckT (К§6) — превью синхронны с канвой; &editor=1 — покой.
    // 2а: один слайд на iframe — миниатюра грузит лёгкий документ, а не всю деку.
    ifr.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1&slide=${i + 1}`);
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
    // После DOM-перестановки (moveThumbDom) захваченный i «плывёт» — живой
    // индекс читаем из dataset в момент клика.
    t.onclick = () => goTo(Number(t.dataset.index));
    if (editable) {
      // Крестик удаления — виден по наведению (CSS .thumb:hover .thumb-del)
      const del = document.createElement("button");
      del.type = "button";
      del.className = "thumb-del";
      del.title = "Удалить слайд";
      del.innerHTML = "&#10005;";
      del.addEventListener("click", (e) => {
        e.stopPropagation(); deleteSlideAt(Number(t.dataset.index));
      });
      t.appendChild(del);
    }
    if (reorderable) {
      // Перетаскивание миниатюры меняет порядок слайдов
      t.draggable = true;
      t.title = "Перетащите, чтобы изменить порядок слайдов";
      t.addEventListener("dragstart", onThumbDragStart);
      t.addEventListener("dragover", onThumbDragOver);
      t.addEventListener("dragleave", onThumbDragLeave);
      t.addEventListener("drop", onThumbDrop);
      t.addEventListener("dragend", onThumbDragEnd);
      t.appendChild(thumbGrip(i));
    }
    box.appendChild(t);
  });
  // Жест неочевиден — говорим о нём прямо в ленте, один раз на весь список.
  // Мышь тянет саму миниатюру, палец — рукоятку (см. thumbGrip): подсказку
  // выбирает CSS по типу указателя, чтобы не звать в жест, которого тут нет.
  if (reorderable && slides.length > 1) {
    const tip = document.createElement("div");
    tip.className = "thumbs-hint";
    tip.appendChild(hintVariant("only-fine",
      "Перетащите миниатюру, чтобы поменять слайды местами"));
    tip.appendChild(hintVariant("only-coarse",
      "Порядок меняется перетаскиванием за рукоятку в углу миниатюры"));
    box.appendChild(tip);
  }
  syncThumbBadges();
}

function hintVariant(cls, text) {
  const s = document.createElement("span");
  s.className = cls;
  s.textContent = text;
  return s;
}

// Полная пересборка ленты — только при реальной смене состава. Обычный сейв
// поля или glass-шаг перерисовывает точечно: текущий слайд и свежезаполненный.
let thumbsDirty = true;                    // первый рендер — полная сборка
function syncThumbs() {
  const box = document.getElementById("thumbs");
  const have = box ? box.querySelectorAll(".thumb").length : 0;
  if (thumbsDirty || have !== slides.length) {
    thumbsDirty = false;
    buildThumbs();
    return;
  }
  refreshThumb(current);
  if (pendingGoTo !== current) refreshThumb(pendingGoTo);
  syncThumbBadges();
}

function refreshThumb(i) {
  const box = document.getElementById("thumbs");
  const t = box && box.querySelectorAll(".thumb")[i];
  if (!t) return;
  const ifr = t.querySelector("iframe");
  if (ifr) ifr.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1&slide=${i + 1}`);
  const s = slides[i];
  const titleText = s
    ? (s.querySelector("h1,h2,h3,[data-slot=title]")?.textContent || "").trim() : "";
  let ttl = t.querySelector(".thumb-title");
  if (titleText && !ttl) {
    ttl = document.createElement("span");
    ttl.className = "thumb-title";
    t.querySelector(".thumb-cap")?.appendChild(ttl);
  }
  if (ttl) ttl.textContent = titleText;
}

// Метки «?» / «!» / «⟳» без пересборки: статусы в glass-режиме меняются на
// каждом шаге, а раньше их доносила только полная пересборка ленты.
// Условия меток — ровно те же, что у карточек вопросов (openGlassQuestions)
// и осечек (glassFailedSlides): failed-слайд ЗАПОЛНЕН заглушкой, поэтому
// проверять !filled нельзя — метка бы не вышла.
function syncThumbBadges() {
  const box = document.getElementById("thumbs");
  if (!box || !isDraft) return;
  const filling = glassRunning && glassLooping
    ? (glassCurrentTarget(draftPlan) || {}).index : null;
  [...box.querySelectorAll(".thumb")].forEach((t, i) => {
    t.querySelector(".thumb-quest")?.remove();
    const ds = draftPlan.slides[i];
    if (!ds) return;
    let cls = "", title = "", mark = "";
    if (ds.status === "needs_input" && !ds.filled) {
      cls = "thumb-quest"; mark = "?";
      title = "ИИ ждёт вашего ответа по этому слайду";
    } else if (ds.status === "failed") {
      cls = "thumb-quest thumb-quest--failed"; mark = "!";
      title = "Слайд не заполнился — можно выбрать макет ещё раз";
    } else if (filling === i + 1) {
      cls = "thumb-quest thumb-quest--filling"; mark = "⟳";
      title = "Слайд сейчас заполняется — подождите";
    }
    if (!cls) return;
    const m = document.createElement("span");
    m.className = cls; m.title = title; m.textContent = mark;
    t.appendChild(m);
  });
}

/* Рукоятка перестановки для пальца. HTML5-перетаскивание касанием не работает,
   а отдать всей миниатюре touch-action:none нельзя — рейл перестанет
   пролистываться. Поэтому жест начинается со своей маленькой зоны, и только
   она не скроллит. Мышь рукоятку не видит (CSS: pointer coarse) — там DnD. */
function thumbGrip(i) {
  const grip = document.createElement("button");
  grip.type = "button";
  grip.className = "thumb-grip";
  grip.setAttribute("aria-label", `Перетащить слайд ${i + 1}`);
  grip.textContent = "⠿";
  grip.addEventListener("click", (e) => e.stopPropagation()); // не листаем деку
  grip.addEventListener("pointerdown", onGripDown);
  grip.addEventListener("pointermove", onGripMove);
  grip.addEventListener("pointerup", onGripUp);
  grip.addEventListener("pointercancel", onGripUp);
  return grip;
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
    // Typed-слайды (slide_type) рендерятся из fields, а не из content — рыбы нет.
    if (!section || !slide || slide.freeform || slide.slide_type) return;
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
  [...document.querySelectorAll(".thumb")].forEach((t, idx) => {
    t.classList.toggle("active", idx === current);
    // Лента растёт по ходу пошаговой сборки — активный тамб держим в кадре,
    // иначе свежезаполненные слайды уезжали за нижний край и рост «не был виден».
    if (idx === current) t.scrollIntoView({ block: "nearest" });
  });
  // Only rebuild the form when the shown slide changed — NOT on the preview
  // reloads that follow each save (those would wipe focus and in-progress rows).
  if (mode === "manual" && builtFormFor !== current) renderBuilderForm();
  // Контекст слайда рядом с чатом — в режимах, где формы слотов нет.
  if (mode !== "manual") renderChatContext();
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
  // D-1 (аудит 2026-08-14): без catch обрыв сети в fetch давал unhandled
  // rejection — кнопка молчала вместо «Ошибка» (автосейв ниже уже был защищён).
  let ok = false;
  // 1-3 (аудит раунда 2, 2026-08-15): у черновика правда — plan.json, POST
  // целой деки сервер теперь отвергает (409); раньше он «сохранял» HTML,
  // который молча откатывала первая же правка формы. Кнопка добирает
  // несохранённый дебаунс-ввод per-slide сейвом.
  try {
    if (isDraft) { await flushPendingSave(); ok = true; }
    else ok = await saveDeck();
  } catch (e) { ok = false; }
  flash(document.getElementById("save"), ok ? "Сохранено" : "Ошибка");
};

// Автосейв правки на слайде собранной деки (у черновика своя ветка — plan.json).
// Строго по очереди: дека уходит целиком, поэтому один повтор после текущего
// сейва добирает всё, что накопилось, пока запрос был в полёте.
let deckEditSaving = false, deckEditPending = false;
async function saveDeckEdit() {
  if (deckEditSaving) { deckEditPending = true; return; }
  deckEditSaving = true;
  let ok = false;
  try {
    ok = await saveDeck();
  } catch (_) { ok = false; } finally {
    deckEditSaving = false;
    flash(document.getElementById("save"), ok ? "Сохранено" : "Ошибка");
    if (deckEditPending) { deckEditPending = false; await saveDeckEdit(); }
  }
}

// Перекрас деки (тёмная ↔ светлая). Тема — атрибут data-theme на <html> деки:
// сервер флипает его в deck.html (и в plan.json у черновика), все токены deck.css
// пересчитываются сами, экспорт перерендерит из deck.html. Ярлык кнопки — ЦЕЛЬ
// («Светлая тема» = во что перекрасим), синкается с темой загруженного кадра.
const themeToggle = document.getElementById("themeToggle");
function deckTheme() {
  const t = frame.contentDocument?.documentElement?.getAttribute("data-theme");
  return t === "light" ? "light" : "dark";
}
function syncThemeToggle() {
  if (!themeToggle) return;
  themeToggle.textContent =
    deckTheme() === "dark" ? "Светлая тема" : "Тёмная тема";
  themeToggle.classList.remove("hidden");
}
themeToggle?.addEventListener("click", async () => {
  const next = deckTheme() === "dark" ? "light" : "dark";
  themeToggle.disabled = true;
  try {
    // Несохранённые правки прямо на слайде собранной деки живут только в
    // iframe-DOM; релоад после перекраса их потерял бы — сначала тихий сейв.
    // (У черновика правки синкаются на blur, который клик уже вызвал.)
    if (!isDraft) {
      try { await saveDeck(true); } catch (_) { /* кадр не готов — не блокируем */ }
    }
    const r = await fetch(U(`/api/jobs/${sessionId}/theme`), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme: next }),
    });
    if (!r.ok) {
      await alertDialog("Не получилось перекрасить — попробуйте ещё раз");
      return;
    }
    const data = await r.json();
    loadDeck(); // релоад превью с новой темой; заодно пометит экспорт устаревшим
    // Freeform-слайды (правки чата/автофикса) могут нести собственные цвета,
    // подобранные под старый фон, — честно показать, какие слайды проверить.
    if (Array.isArray(data.check_slides) && data.check_slides.length) {
      await alertDialog(
        `Проверьте слайды ${data.check_slides.join(", ")}: у них есть свои цвета ` +
        "(правки в чате или автоисправления), перекрас мог их не затронуть.");
    }
  } finally { themeToggle.disabled = false; }
});

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
// Загрузка одна на всех: кнопка «+ Добавить слайд» видна раньше, чем приходит
// каталог, и её клик догонял ту же загрузку — без общего промиса получалось два
// запроса и гонка «кто последний записал».
let catalogLoad = null;
async function ensureCatalog() {
  if (catalog.length) return;
  if (!catalogLoad) {
    catalogLoad = (async () => {
      try {
        const r = await fetch(U("/api/templates"));
        if (r.ok) catalog = await r.json();
      } catch (_) { /* сеть — вызывающий покажет пустое состояние */ }
      catalogLoad = null;
    })();
  }
  await catalogLoad;
}
function countPlaceholderSlides() {
  let n = 0;
  (draftPlan.slides || []).forEach((slide) => {
    if (!slide || slide.freeform) return;
    const tpl = tplOf(slide.template_id);
    if (!tpl) return;
    const content = slide.content || {};
    // Пример-текст подставляется НЕ только в обязательные слоты: пустой
    // необязательный текст тоже рендерится рыбой («Короткий подзаголовок» на
    // обложке), а пустой необязательный список просто не выводится.
    const hasPlaceholder = Object.entries(tpl.slots).some(([name, spec]) => {
      const val = content[name];
      if (spec.kind === "list")
        return spec.required && (!Array.isArray(val) || val.length === 0);
      if (spec.kind !== "text" || !spec.filler) return false;
      return val == null || String(val).trim() === "";
    });
    if (hasPlaceholder) n++;
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
    // 1-3: у черновика деку целиком не шлём (сервер ответит 409) — добираем
    // несохранённый ввод формы; inline-правки черновика синкаются на blur.
    if (isDraft) await flushPendingSave();
    else await saveDeck(true);         // persist in-place edits (no stale-reset: this IS the export)
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

// Подпись возвращаем ту, что была ДО первой вспышки: два срабатывания подряд
// (двойной клик по «Сохранить», автосейв следом за ручным) запоминали уже
// вспыхнувший текст, и кнопка навсегда оставалась «Сохранено».
function flash(btn, text) {
  if (btn.__flashOrig === undefined) btn.__flashOrig = btn.textContent;
  clearTimeout(btn.__flashTimer);
  btn.textContent = text;
  btn.__flashTimer = setTimeout(() => {
    btn.textContent = btn.__flashOrig;
    btn.__flashOrig = undefined;
  }, 1500);
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
    if (buttons.some((b) => b.desc)) row.classList.add("dialog-actions--modes");
    buttons.forEach((b, idx) => {
      const btn = document.createElement("button");
      btn.className = b.className;
      if (b.cancel) btn.classList.add("dialog-cancel");
      // Кнопка-карточка: название + что произойдёт. Способы не «ок и отмена», а
      // равнозначная пара — без пояснения выбор делался бы наугад по глаголу.
      if (b.desc) {
        const name = document.createElement("span");
        name.className = "mode-card__name";
        name.textContent = b.label;
        const desc = document.createElement("span");
        desc.className = "mode-card__desc";
        desc.textContent = b.desc;
        btn.append(name, desc);
      } else {
        btn.textContent = b.label;
      }
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
// Выбор способа: несколько равнозначных карточек + отмена. options —
// [{label, desc, value}]; возвращает value выбранной или null.
function chooseDialog(text, options) {
  return _dialog(text, options.map((o) => ({
    label: o.label, desc: o.desc, value: o.value, className: "btn mode-card",
  })).concat([{ label: "Отмена", className: "btn btn-ghost", value: null,
                cancel: true }]));
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
  thumbsDirty = true;  // структурная правка — лента пересобирается честно
  await fetchPlan();
  dgmUnsaved = null;   // индексы уехали — черновик схемы больше не привязать к слайду
  if (goToIndex != null) pendingGoTo = goToIndex;
  builtFormFor = -1;  // plan changed structurally → force a form rebuild
  loadDeck(); // re-render preview from the server's derived deck.html
}

/* ---- контекст слайда в правой панели ----------------------------------- */
// Правки идут по одному полю, а решение принимается по слайду ЦЕЛИКОМ. Форма
// слотов этой картины не давала: у диаграммного слайда панель узлов вообще не
// показывала текста, у свободного — полей нет, а длинная форма прячет остальное
// под прокруткой. Блок собирается из плана (а не из превью): план — истина и
// обновляется синхронно с правкой.
const ctxOpen = { text: true, brief: false };

function renderSlideContext(slide) {
  fillCtx(byId("builderCtx"), slide ? slideTextLines(slide) : [],
          (slide && slide.brief) || "");
}

// Правая панель без формы слотов (чат-режим, готовая дека): текст берём из
// самого превью — плана там либо нет, либо слайд собран не по слотам.
function renderChatContext() {
  const box = byId("chatCtx");
  if (!box) return;
  const slide = isDraft ? (draftPlan.slides || [])[current] : null;
  fillCtx(box, slide ? slideTextLines(slide) : deckSlideTextLines(current),
          (slide && slide.brief) || "");
}

function fillCtx(box, lines, brief) {
  if (!box) return;
  box.innerHTML = "";
  brief = (brief || "").trim();
  if (!lines.length && !brief) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  if (lines.length) box.appendChild(ctxSection("text", "Текст слайда", lines));
  if (brief) {
    box.appendChild(ctxSection(
      "brief", "Исходный фрагмент документа",
      brief.split("\n").map((l) => l.trim()).filter(Boolean)));
  }
}

// Текст слайда прямо из превью — как он нарисован, включая подписи узлов схемы
// (SVG innerText не отдаёт, поэтому забираем их отдельно).
function deckSlideTextLines(i) {
  const el = slides[i];
  if (!el) return [];
  const out = [];
  // Хром слайда (копирайт-колонтитул, номер) — не контент: без фильтра первой
  // строкой «Текста слайда» показывался «ⓒ 2026 Cloud.ru Любое копирование…».
  const skip = new Set();
  el.querySelectorAll(".chrome-note, .chrome-num").forEach((c) => {
    const line = (c.textContent || "").trim().replace(/\s+/g, " ");
    if (line) skip.add(line);
  });
  const push = (s) => {
    const line = (s || "").trim().replace(/\s+/g, " ");
    if (line && !skip.has(line) && !out.includes(line)) out.push(line);
  };
  (el.innerText || el.textContent || "").split("\n").forEach(push);
  el.querySelectorAll("svg text").forEach((t) => push(t.textContent));
  return out;
}

function ctxSection(key, title, lines) {
  const d = document.createElement("details");
  d.className = "builder-ctx__sec";
  d.open = !!ctxOpen[key];
  d.addEventListener("toggle", () => { ctxOpen[key] = d.open; });
  const sum = document.createElement("summary");
  sum.textContent = title;
  d.appendChild(sum);
  const body = document.createElement("div");
  body.className = "builder-ctx__body";
  lines.forEach((line) => {
    const p = document.createElement("p");
    p.textContent = line;
    body.appendChild(p);
  });
  d.appendChild(body);
  return d;
}

// Весь текст слайда построчно. Служебные значения (id узлов, data-URI картинок,
// сырой HTML) в контекст не попадают — читать нужно текст, а не разметку.
function slideTextLines(slide) {
  const out = [];
  if (slide.slide_type === "diagram" && slide.fields) {
    const f = slide.fields;
    collectText(f.heading, out);
    collectText(f.subtitle, out);
    const d = f.diagram || {};
    (d.nodes || []).forEach((n) => {
      const value = n && n.value ? ` — ${n.value}` : "";
      const lane = n && n.lane ? ` [${n.lane}]` : "";
      collectText((n && n.label ? n.label : "") + value + lane, out);
    });
    (d.edges || []).forEach((e) => collectText(e && e.label, out));
    Object.values(d.meta || {}).forEach((v) => collectText(v, out));
    return out;
  }
  const content = slide.content || {};
  if (slide.freeform) { collectHtmlText(content.html, out); return out; }
  const tpl = tplOf(slide.template_id);
  const keys = tpl ? Object.keys(tpl.slots) : Object.keys(content);
  keys.forEach((k) => collectText(content[k], out));
  return out;
}

function collectText(v, out) {
  if (v == null) return;
  if (typeof v === "string" || typeof v === "number") {
    const s = String(v).trim();
    if (!s || s.startsWith("data:")) return;   // картинка — не текст
    if (s.startsWith("<")) { collectHtmlText(s, out); return; }
    out.push(s);
    return;
  }
  if (Array.isArray(v)) { v.forEach((x) => collectText(x, out)); return; }
  if (typeof v === "object") Object.values(v).forEach((x) => collectText(x, out));
}

function collectHtmlText(html, out) {
  if (typeof html !== "string" || !html.trim()) return;
  // DOMParser, а не innerHTML: разбор идёт в инертном документе — ни картинок,
  // ни обработчиков (у свободного слайда разметку писала модель).
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.querySelectorAll("script,style").forEach((n) => n.remove());
  const text = (doc.body.textContent || "").split("\n")
    .map((s) => s.trim()).filter(Boolean);
  out.push(...text);
}

function tplOf(id) { return catalog.find((t) => t.id === id); }
// Каталог знает и скрытые макеты (их ставит конвейер) — чтобы назвать слайд и
// построить ему форму. Предлагать их в пикере при этом нельзя.
function pickableTemplates() { return catalog.filter((t) => !t.hidden); }

function renderBuilderForm() {
  const form = byId("builderForm");
  const tplBox = byId("builderTpl");
  const empty = byId("builderEmpty");
  if (!form) return;
  builtFormFor = current;   // mark the form as built for the current slide
  const slide = draftPlan.slides[current];
  // До ветвлений: у формы несколько ранних return (пусто/замочек/схема), а
  // кнопка «Улучшить этот слайд» должна обновляться при любом из них.
  syncImproveButton();
  if (!slide) {
    form.innerHTML = ""; tplBox.innerHTML = "";
    renderSlideContext(null);
    empty.classList.remove("hidden");
    return;
  }
  // Слайд, заполняемый сейчас, залочен (спека, секция 2): параллельная правка
  // формы проиграла бы гонку с _fill_one — сервер вклеит свой результат поверх.
  if (glassRunning && glassLooping &&
      (glassCurrentTarget(draftPlan) || {}).index === current + 1) {
    form.innerHTML =
      '<p class="builder-locked">⟳ Этот слайд сейчас заполняется — форма ' +
      'откроется, когда ИИ закончит (обычно до минуты).</p>';
    builtFormFor = -1;          // после заполнения форму перерисовать заново
    return;
  }
  empty.classList.add("hidden");
  renderSlideContext(slide);

  if (slide.freeform) {
    tplBox.innerHTML = `<span class="tpl-name">Свободный слайд</span>`;
    // К§1: честная записка (без выдуманной истории про чат) + возврат к макету.
    form.innerHTML = `<p class="builder-note">Свободный слайд — он больше не привязан к макету, ` +
      `поэтому полей здесь нет. Правьте текст прямо на слайде или опишите изменение в чате справа.</p>`;
    // Макет, из которого ушли, лежит на самом слайде (plan.json), поэтому кнопка
    // переживает перезагрузку, едет со слайдом при перестановке и появляется
    // после правки через чат — раньше снимок жил в sessionStorage по НОМЕРУ
    // позиции и после перестановки возвращал чужой макет.
    const snap = slide.prev_layout;
    if (snap && snap.template_id) {
      const revert = document.createElement("button");
      revert.type = "button";
      revert.className = "btn btn-ghost btn-sm";
      revert.style.marginTop = "8px";
      revert.textContent = "Вернуть макет";
      revert.onclick = async () => {
        const n = current + 1;
        // Возврат идёт в два шага (снести свободный слайд → поставить макетный), и
        // без проверки ответов провал второго уносил слайд СОВСЕМ — человек жал
        // «Вернуть макет» и слайд просто исчезал, молча.
        const before = snapshotPlan();
        const del = await fetch(U(`/api/drafts/${sessionId}/slides/${n}`),
                                { method: "DELETE" }).catch(() => null);
        if (!del || !del.ok) { setSaveStatus("error"); return; }
        const add = await fetch(U(`/api/drafts/${sessionId}/slides`), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ template_id: snap.template_id, at: n, content: snap.content }),
        }).catch(() => null);
        if (!add || !add.ok) {
          await applyPlan(before);
          setSaveStatus("error");
          return;
        }
        pushUndo();
        await reloadDraft(current);
      };
      form.appendChild(revert);
    }
    return;
  }
  // Диаграммный typed-слайд: вместо формы слотов — панель узлов/связей.
  if (slide.slide_type === "diagram" && slide.fields) {
    renderDiagramPanel(slide);
    return;
  }
  const tpl = tplOf(slide.template_id);
  const tplIdx = pickableTemplates().findIndex((t) => t.id === slide.template_id);
  const tplNo = tplIdx >= 0 ? String(tplIdx + 1).padStart(2, "0") : "—";
  tplBox.innerHTML =
    `<span class="tpl-name">Макет: ${tpl?.display_name || slide.template_id}</span>` +  // К§2: имя макета, фолбэк на id
    `<button type="button" class="btn btn-ghost btn-sm" id="changeTpl">Сменить макет</button>`;
  byId("changeTpl").onclick = () => openPicker((tid, kind) => changeTemplateAsked(tid, kind),
                                               { id: slide.template_id });

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
      if (add.disabled) return;
      const row = renderItem(spec, {});
      list.appendChild(row);
      syncList(list, spec, add, note);
      row.querySelector("input")?.focus();   // ready to type; saved on first input
    };
    wrap.appendChild(add);
    const note = hint("");
    wrap.appendChild(note);
    // Счётчик/нумерация/кап живут на одном обработчике: input всплывает из полей
    // пункта, а удаление строки шлёт такое же событие вручную (renderItem).
    list.addEventListener("input", () => syncList(list, spec, add, note));
    syncList(list, spec, add, note);
  } else if (spec.kind === "group") {
    wrap.appendChild(renderItem(spec, value || {}, name));
  }
  return wrap;
}

function renderItem(spec, item, groupSlot) {
  const row = document.createElement("div");
  row.className = "field-item";
  if (groupSlot) { row.dataset.slot = groupSlot; row.dataset.kind = "group"; }
  else {
    const num = document.createElement("span");
    num.className = "item-num";                 // номер пункта = номер блока на слайде
    row.appendChild(num);
  }
  for (const [sub, subSpec] of Object.entries(spec.item_slots || {})) {
    const inp = document.createElement("input");
    // Подпись поля берём из библиотеки (там она русская); ключ слота — только фолбэк.
    inp.placeholder = (subSpec.label || sub) + (subSpec.required ? " *" : "");
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
    del.title = "Удалить пункт";
    del.onclick = () => {
      const list = row.parentNode;
      row.remove();
      // Список без строк = пустой обязательный слот, а его сервер рисует примером
      // (draft_render._coerce_slot). Пустая форма против блоков на слайде читается
      // как «удаление не сработало», поэтому оставляем одну пустую строку — тот же
      // вид, что и при первом открытии слайда, — а счётчик объяснит про пример.
      if (list && list.classList.contains("field-list") && !list.children.length)
        list.appendChild(renderItem(spec, {}));
      list?.dispatchEvent(new Event("input", { bubbles: true }));
      scheduleSave();
    };
    row.appendChild(del);
  }
  return row;
}

// Держит в актуальном состоянии нумерацию пунктов, счётчик «N из M» и кап «+ пункт».
function syncList(list, spec, add, note) {
  const rows = [...list.querySelectorAll(".field-item")];
  const filled = rows.filter((r) => [...r.querySelectorAll("[data-sub]")]
    .some((i) => i.value.trim())).length;
  rows.forEach((r, i) => {
    const n = r.querySelector(".item-num");
    if (n) n.textContent = String(i + 1);
  });
  const max = spec.max_items || 0;
  add.disabled = max > 0 && rows.length >= max;
  add.title = add.disabled
    ? `Этот макет показывает не больше ${max} — удалите пункт, чтобы добавить новый`
    : "";
  if (!filled) {
    // Пустой ОБЯЗАТЕЛЬНЫЙ список сервер рисует примером (draft_render._coerce_slot),
    // иначе «удалил всё, а блоки на слайде остались» читается как «не работает».
    // Необязательный пустой список просто не попадает на слайд — не врём про пример.
    note.textContent = spec.required
      ? "Пункты пустые — на слайде показан пример. Заполните поля, чтобы заменить его"
      : "Пункты пустые — этот блок на слайде не появится";
    return;
  }
  // disabled-кнопке браузер не показывает title, поэтому причина — в самом счётчике.
  note.textContent = (max ? `${rows.length} из ${max}` : `Пунктов: ${rows.length}`)
    + (add.disabled ? " — предел этого макета" : "");
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
    // Красного числа мало: сервер режет текст ЖЁСТКО по символу, и человек видит
    // на слайде обрубок посреди слова, не связывая его со счётчиком.
    h.textContent = n > max ? `${n}/${max} — на слайде обрежется` : `${n}/${max}`;
    h.classList.toggle("field-hint--over", n > max);
  };
  el.addEventListener("input", upd);
  upd();
  return h;
}

/* ---- панель диаграммного слайда (узлы/связи; drag — на самом слайде) ---- */
const DGM_SHAPES = [["start", "Начало"], ["process", "Шаг"], ["decision", "Условие"],
                    ["io", "Ввод/вывод"], ["end", "Финал"]];
// редактируемые рёбра (у остальных типов порядок задаёт список узлов)
const DGM_EDGE_KINDS = DiagramDrag.EDGE_KINDS;
const DGM_MAX_NODES = 12, DGM_MAX_EDGES = 20;        // капы схемы (schema.py)
// узлы с полем value: подпись поля зависит от типа (число слоя, длительность…)
const DGM_VALUE_KINDS = { funnel: "число", pyramid: "число",
                          gantt_lite: "длительность *", steps: "пометка" };
// lane у узла: подпись поля зависит от типа
const DGM_LANE_KINDS = { comparison: "сторона", swimlanes: "исполнитель" };
// заголовок списка связей: смысл ребра у типов разный
const DGM_EDGE_LABEL = {
  hierarchy: "Связи (родитель → подчинённый)",
  mindmap: "Ветви (от чего → к чему)",
  network: "Связи (между узлами)",
};
// смысловые подсказки к списку узлов; сами границы «сколько узлов допустимо» —
// в DiagramDrag.NODE_RANGE (по ним же переносятся подписи при смене типа)
const DGM_NODE_HINTS = {
  matrix: "ровно 4 узла: верх-лево, верх-право, низ-лево, низ-право",
  cycle: "3–8 стадий по кругу, стрелки идут по порядку",
  venn: "2–3 множества",
  pyramid: "уровни сверху вниз, вершина первой",
  hub_spoke: "первый узел — центр, остальные — лучи",
  gantt_lite: "до 8 работ: длительность в периодах, старт — номер периода (пусто — сразу за предыдущей)",
  steps: "до 6 ступеней снизу вверх, первая — нижняя",
  mindmap: "первый узел — центр карты, остальные — ветви",
  network: "раскладку задают связи, а не порядок узлов",
};
function dgmNodeRules(kind) {
  const r = DiagramDrag.NODE_RANGE[kind] || [1, DGM_MAX_NODES];
  return { min: r[0], max: r[1], hint: DGM_NODE_HINTS[kind] };
}

/* Правка схемы, отвергнутая проверкой (наши претензии или 400 сервера), на
   сервер не уходит — и при уходе со слайда пропадала молча: пользователь
   добавлял узел, переключался и возвращался к схеме без своего узла. Держим
   отвергнутый ввод здесь до возвращения на слайд. Индексы уезжают при любой
   структурной правке деки, поэтому reloadDraft черновик сбрасывает. */
let dgmUnsaved = null;   // { idx, fields, claims, tone }

function renderDiagramPanel(slide) {
  const form = byId("builderForm");
  const tplBox = byId("builderTpl");
  const stash = dgmUnsaved && dgmUnsaved.idx === current ? dgmUnsaved : null;
  const f = stash ? stash.fields : slide.fields;
  const spec = f.diagram || {};
  const t0 = dgmType(spec.kind);
  tplBox.innerHTML =
    `<span class="tpl-name">Схема: ${t0 ? t0.display_name : spec.kind || ""}</span>` +
    `<button type="button" class="btn btn-ghost btn-sm" id="changeDgmKind">Сменить тип</button>` +
    `<button type="button" class="btn btn-ghost btn-sm" id="changeTpl">Сменить макет</button>`;
  if (!t0) fetchDgmCatalog().then(() => {   // имя типа догружаем при первом заходе
    const t = dgmType(spec.kind);
    const nameEl = tplBox.querySelector(".tpl-name");
    if (t && nameEl) nameEl.textContent = `Схема: ${t.display_name}`;
  });
  byId("changeTpl").onclick = () => openPicker((tid, kind) => changeTemplateAsked(tid, kind),
                                               { id: "diagram", kind: spec.kind });
  byId("changeDgmKind").onclick = () => openDiagramPicker(async (kind) => {
    if (kind === spec.kind) return;
    const fresh = collectDiagramFields() || f;
    // Сколько подписей реально доедет — считаем тем же переносом, что и применим:
    // обещать «всё сохранится» там, где тип вмещает меньше узлов, нельзя.
    await fetchDgmCatalog();
    const t = dgmType(kind);
    const fit = t && t.sample
      ? DiagramDrag.carryLabels(JSON.parse(JSON.stringify(t.sample)),
                           fresh.diagram).nodes.length
      : 0;
    const ok = await confirmDialog(
      "Подписи узлов перенесутся в новый тип по порядку; связи, дорожки и "
      + "величины возьмутся из примера. Заголовок сохранится."
      + (fit && fit < ((fresh.diagram || {}).nodes || []).length
        ? " Часть подписей не поместится — в новом типе узлов меньше." : "")
      + " Продолжить?",
      "Сменить", "Отмена");
    if (!ok) return;
    clearTimeout(putTimer); putTimer = null;  // форму старого типа не сейвим
    pushUndo();
    await applyDiagramKind(current + 1, kind, fresh);
    await reloadDraft(current);
  }, spec.kind);

  form.innerHTML = "";
  form.appendChild(dgmTextField("Заголовок *", "heading", f.heading, 54, true));
  form.appendChild(dgmTextField("Подзаголовок", "subtitle", f.subtitle, 70));

  // Подписи, специфичные для типа (meta) — matrix: оси, venn: пересечение
  const meta = spec.meta || {};
  if (spec.kind === "matrix") {
    form.appendChild(dgmTextField("Ось X (горизонталь)", "x_axis", meta.x_axis, 40));
    form.appendChild(dgmTextField("Ось Y (вертикаль)", "y_axis", meta.y_axis, 40));
  } else if (spec.kind === "venn") {
    form.appendChild(dgmTextField("Подпись пересечения", "center_label", meta.center_label, 60));
  } else if (spec.kind === "gantt_lite") {
    form.appendChild(dgmTextField("Единица шкалы", "x_axis", meta.x_axis, 40));
  }

  // Узлы
  const nodesWrap = document.createElement("div");
  nodesWrap.className = "field";
  const nLabel = document.createElement("label");
  nLabel.textContent = "Узлы";
  nodesWrap.appendChild(nLabel);
  nodesWrap.appendChild(hint("Текст узлов — здесь, положение — перетаскиванием прямо на слайде"));
  nodesWrap.appendChild(hint("Порядок строк — это порядок узлов на схеме: ↑ ↓ переставляют"));
  nodesWrap.appendChild(hint("Узел сам выравнивается по соседям и центру — совпавшая ось подсвечивается. Alt — без выравнивания."));
  const nodeList = document.createElement("div");
  nodeList.className = "field-list";
  nodeList.id = "dgmNodeList";
  (spec.nodes || []).forEach((n) => nodeList.appendChild(dgmNodeRow(spec.kind, n)));
  nodesWrap.appendChild(nodeList);
  const rules = dgmNodeRules(spec.kind);
  nodeList.dataset.min = String(rules.min || 1);
  nodeList.dataset.max = String(rules.max || DGM_MAX_NODES);
  if (rules.max > (spec.nodes || []).length || rules.max > rules.min) {
    const addNode = document.createElement("button");
    addNode.type = "button"; addNode.className = "btn btn-ghost btn-sm";
    addNode.id = "dgmAddNode";
    addNode.textContent = "+ узел";
    addNode.onclick = () => {
      if (addNode.disabled) return;
      const row = dgmNodeRow(spec.kind, { id: dgmNewId(), label: "" });
      nodeList.appendChild(row);
      refreshDgmEdgeSelects();
      syncDgmCounts();
      row.querySelector('[data-dgm="label"]')?.focus(); // сейв — на первый ввод
    };
    nodesWrap.appendChild(addNode);
  }
  const nodesNote = hint("");
  nodesNote.id = "dgmNodesNote";
  nodesWrap.appendChild(nodesNote);
  const fitNote = hint("");
  fitNote.id = "dgmFitNote";
  fitNote.className = "field-hint field-hint--over";
  fitNote.hidden = true;
  nodesWrap.appendChild(fitNote);
  // Смысловая подсказка типа остаётся; «до N узлов» больше не нужна — число теперь
  // в живом счётчике, а два хинта подряд про одно и то же только шумят.
  if (rules.hint) nodesWrap.appendChild(hint(rules.hint));
  form.appendChild(nodesWrap);

  // Связи — только у типов, где рёбра задаются руками
  if (DGM_EDGE_KINDS.includes(spec.kind)) {
    const edgesWrap = document.createElement("div");
    edgesWrap.className = "field";
    const eLabel = document.createElement("label");
    eLabel.textContent = DGM_EDGE_LABEL[spec.kind] || "Связи (стрелки)";
    edgesWrap.appendChild(eLabel);
    const edgeList = document.createElement("div");
    edgeList.className = "field-list";
    edgeList.id = "dgmEdgeList";
    (spec.edges || []).forEach((e) => edgeList.appendChild(dgmEdgeRow(e)));
    edgesWrap.appendChild(edgeList);
    const addEdge = document.createElement("button");
    addEdge.type = "button"; addEdge.className = "btn btn-ghost btn-sm";
    addEdge.textContent = "+ связь";
    addEdge.id = "dgmAddEdge";
    addEdge.onclick = () => {
      if (addEdge.disabled) return;
      const ids = [...nodeList.querySelectorAll(".dgm-node-row")]
        .map((r) => r.dataset.nodeId);
      edgeList.appendChild(dgmEdgeRow({ from: ids[0] || "", to: ids[1] || "" }));
      refreshDgmEdgeSelects();
      syncDgmCounts();
      scheduleSave();
    };
    edgesWrap.appendChild(addEdge);
    const edgesNote = hint("");
    edgesNote.id = "dgmEdgesNote";
    edgesWrap.appendChild(edgesNote);
    form.appendChild(edgesWrap);
    refreshDgmEdgeSelects();
  }
  syncDgmCounts();

  // Сбросить ручную раскладку — только когда сдвиги есть
  if (spec.offsets && Object.keys(spec.offsets).length) {
    form.appendChild(dgmResetLayoutButton());
  }
  // Вернулись к отвергнутой правке — вместе с ней возвращаем и причину отказа.
  if (stash) dgmShowClaims(stash.claims, stash.tone);
}

function dgmResetLayoutButton() {
  const reset = document.createElement("button");
  reset.type = "button"; reset.className = "btn btn-ghost btn-sm";
  reset.id = "dgmResetLayout";
  reset.textContent = "Сбросить раскладку";
  reset.title = "Убрать ручные сдвиги узлов — вернуть авто-расстановку";
  reset.onclick = () => {
    // Поля берём из плана НА МОМЕНТ КЛИКА: saveDiagramSlide подменяет
    // slide.fields новым объектом, и замыкание на старом мутировало бы сироту —
    // кнопка исчезала, а сдвиги оставались (и локально, и на сервере).
    const slide = draftPlan && draftPlan.slides && draftPlan.slides[current];
    if (!slide || !slide.fields) return;
    slide.fields = { ...slide.fields,
                     diagram: { ...(slide.fields.diagram || {}), offsets: {} } };
    liveRenderDiagram(current);
    scheduleSave();
    reset.remove();
  };
  return reset;
}

function dgmTextField(labelText, key, value, max, required) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("label");
  label.textContent = labelText;
  wrap.appendChild(label);
  const el = document.createElement("input");
  el.maxLength = max;
  el.value = value == null ? "" : String(value);
  el.dataset.dgm = key;
  el.addEventListener("input", scheduleSave);
  wrap.appendChild(el);
  wrap.appendChild(charCounter(el, max));
  // Пустое обязательное поле схема принимает, а шаблон подставляет вместо него
  // пример («ЗАГОЛОВОК СЛАЙДА»): в панели пусто, на слайде — чужой текст. На
  // обычных слайдах об этом говорит серверная проверка; здесь её нет — говорим
  // сами, тем же текстом (errtext: missing_required).
  if (required) {
    const msg = document.createElement("div");
    msg.className = "field-hint field-hint--error";
    msg.textContent = errText("missing_required");
    const upd = () => {
      const empty = !el.value.trim();
      wrap.classList.toggle("field-error", empty);
      msg.hidden = !empty;
    };
    el.addEventListener("input", upd);
    upd();
    wrap.appendChild(msg);
  }
  return wrap;
}

function dgmNodeRow(kind, n) {
  const row = document.createElement("div");
  row.className = "field-item dgm-node-row";
  row.dataset.nodeId = n.id;
  if (kind === "flowchart") {
    const sel = document.createElement("select");
    sel.dataset.dgm = "shape";
    sel.title = "Форма узла";
    DGM_SHAPES.forEach(([v, name]) => sel.add(new Option(name, v)));
    sel.value = n.shape || "process";
    sel.onchange = scheduleSave;
    row.appendChild(sel);
  }
  const label = document.createElement("input");
  label.placeholder = "текст узла *";
  label.maxLength = 60;
  label.value = n.label == null ? "" : String(n.label);
  label.dataset.dgm = "label";
  label.oninput = () => { refreshDgmEdgeSelects(); syncDgmFit(); scheduleSave(); };
  row.appendChild(label);
  if (DGM_VALUE_KINDS[kind]) {
    const val = document.createElement("input");
    val.placeholder = DGM_VALUE_KINDS[kind];
    val.maxLength = 12;
    val.className = "dgm-val";
    val.value = n.value == null ? "" : String(n.value);
    val.dataset.dgm = "value";
    val.oninput = scheduleSave;
    row.appendChild(val);
  }
  if (kind === "gantt_lite") {
    // Стартовый период: пусто = «сразу за предыдущей работой» (каскад раскладки),
    // поэтому поле не обязательное и пустая строка не равна нулю.
    const lvl = document.createElement("input");
    lvl.type = "number";
    lvl.min = "0"; lvl.max = "11";
    lvl.placeholder = "старт";
    lvl.title = "Номер периода, с которого начинается работа";
    lvl.className = "dgm-val";
    lvl.value = n.level == null ? "" : String(n.level);
    lvl.dataset.dgm = "level";
    lvl.oninput = scheduleSave;
    row.appendChild(lvl);
  }
  if (DGM_LANE_KINDS[kind]) {
    const lane = document.createElement("input");
    lane.placeholder = DGM_LANE_KINDS[kind] + " *";
    lane.maxLength = 40;
    lane.className = "dgm-lane";
    lane.value = n.lane == null ? "" : String(n.lane);
    lane.dataset.dgm = "lane";
    lane.oninput = () => { syncDgmFit(); scheduleSave(); };   // дорожка меняет ширину плашки
    row.appendChild(lane);
  }
  const acc = document.createElement("label");
  acc.className = "dgm-acc";
  acc.title = "Акцентный узел — выделить цветом акцента темы";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = !!n.accent;
  cb.dataset.dgm = "accent";
  cb.onchange = scheduleSave;
  acc.appendChild(cb);
  acc.appendChild(document.createTextNode("акцент"));
  row.appendChild(acc);
  /* Порядок строк — это порядок узлов на схеме: этапы процесса, ступени воронки,
     уровни пирамиды, квадранты матрицы читаются именно по нему. «+ узел» умеет
     только дописать в конец, поэтому забытый второй шаг оказывался последним, и
     единственным способом его вернуть на место было перенабрать все подписи ниже.
     Стрелки двигают строку — это и двигает узел на слайде. */
  const mv = document.createElement("span");
  mv.className = "dgm-move";
  [[-1, "↑", "Переместить выше"], [1, "↓", "Переместить ниже"]].forEach(([dir, glyph, title]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-ghost btn-sm item-move";
    b.dataset.dir = String(dir);
    b.textContent = glyph;
    b.title = title;
    b.onclick = () => {
      if (b.disabled) return;
      const sib = dir < 0 ? row.previousElementSibling : row.nextElementSibling;
      if (!sib) return;
      if (dir < 0) row.parentNode.insertBefore(row, sib);
      else row.parentNode.insertBefore(sib, row);
      refreshDgmEdgeSelects();  // списки связей перечисляют узлы в том же порядке
      syncDgmCounts();
      b.focus();                // строка уехала — кнопка под курсором остаётся той же
      scheduleSave();
    };
    mv.appendChild(b);
  });
  row.appendChild(mv);
  if (kind !== "matrix") {   // у матрицы ровно 4 квадранта — удалять нечего
    const del = document.createElement("button");
    del.type = "button"; del.className = "btn btn-ghost btn-sm item-del";
    del.textContent = "✕";
    del.title = "Удалить узел";
    del.onclick = () => {
      if (del.disabled) return;   // на минимуме типа схемы удалять нельзя
      row.remove();
      const dropped = refreshDgmEdgeSelects();
      syncDgmCounts();
      // Связи исчезают вместе с узлом — молчать об этом нельзя: человек удалял
      // один блок, а со слайда уходили ещё и стрелки.
      const note = byId("dgmEdgesNote");
      if (dropped && note) note.textContent += ` · с узлом удалено связей: ${dropped}`;
      scheduleSave();
    };
    row.appendChild(del);
  }
  return row;
}

/* Капы схемы (schema.py) в панели: раньше «+ узел»/«+ связь» на пределе просто
   ничего не делали, а удаление узлов ниже минимума типа роняло сейв в «invalid» —
   человек видел сломанный сейв без причины. Теперь предел виден: кнопка гаснет,
   счётчик называет причину, крестики на минимуме заблокированы. */
function syncDgmCounts() {
  const nodes = byId("dgmNodeList");
  if (nodes) {
    const rows = [...nodes.querySelectorAll(".dgm-node-row")];
    const min = Number(nodes.dataset.min || 1);
    const max = Number(nodes.dataset.max || DGM_MAX_NODES);
    const add = byId("dgmAddNode");
    if (add) {
      add.disabled = rows.length >= max;
      add.title = add.disabled ? "Больше узлов этот тип схемы не покажет" : "";
    }
    const atMin = rows.length <= min;
    rows.forEach((r, i) => {
      const up = r.querySelector('.item-move[data-dir="-1"]');
      const dn = r.querySelector('.item-move[data-dir="1"]');
      if (up) up.disabled = i === 0;              // первый узел выше некуда
      if (dn) dn.disabled = i === rows.length - 1;
      const d = r.querySelector(".item-del");
      if (!d) return;
      d.disabled = atMin;
      d.title = atMin ? `Минимум для этого типа — ${min}` : "Удалить узел";
    });
    const note = byId("dgmNodesNote");
    if (note) {
      note.textContent = `${rows.length} из ${max}`
        + (rows.length >= max ? " — предел этого типа"
           : atMin ? ` — минимум этого типа` : "");
    }
  }
  const edges = byId("dgmEdgeList");
  if (edges) {
    const n = edges.querySelectorAll(".dgm-edge-row").length;
    const add = byId("dgmAddEdge");
    if (add) {
      // Связь из одного узла — это петля, схема её не примет: гасим кнопку, а не
      // даём человеку добавить строку, которая тут же уедет в «не сохранено».
      const lone = (byId("dgmNodeList")?.querySelectorAll(".dgm-node-row").length || 0) < 2;
      add.disabled = n >= DGM_MAX_EDGES || lone;
      add.title = n >= DGM_MAX_EDGES ? "Больше связей схема не покажет"
        : lone ? "Связь соединяет два узла — добавьте второй" : "";
    }
    const note = byId("dgmEdgesNote");
    if (note) {
      note.textContent = `${n} из ${DGM_MAX_EDGES}`
        + (n >= DGM_MAX_EDGES ? " — предел" : "");
    }
  }
  syncDgmFit();   // плашки ужимаются с каждым узлом — предупреждение пересчитываем
}

/* Чем больше узлов, тем меньше плашка: подпись, законная по схеме (до 60 симв.),
   в схеме на 9+ узлов физически не помещается и движок сокращает её многоточием.
   Молча это делать нельзя — в панели полный текст, на слайде обрезанный, и
   человек читает расхождение как потерю данных. Считаем ТЕМ ЖЕ движком, что и
   рисует (габарит из layout + clampLabel), поэтому предупреждение не разъедется
   с картинкой. Движок живёт в кадре превью — до его загрузки просто молчим. */
function syncDgmFit() {
  const note = byId("dgmFitNote");
  const list = byId("dgmNodeList");
  if (!note || !list) return;
  // Движок — ЗАПЕЧЁННЫЙ в деке, то есть той версии, что была на момент сборки:
  // у старой деки нужных функций может не быть вовсе. Спрашиваем каждую, а не
  // только layout: необъявленный вызов ронял весь renderBuilderForm, и переход
  // на диаграммный слайд оставлял человека без панели правки.
  const e0 = frame.contentDocument?.defaultView?.DiagramEngine;
  const eng = e0 && e0.layout && e0.clampLabel && e0.fitFont ? e0 : null;
  const spec = eng ? collectDiagramFields()?.diagram : null;
  const pos = spec ? eng.layout(spec) : null;
  const cut = [];
  list.querySelectorAll(".dgm-node-row").forEach((row) => {
    const inp = row.querySelector('[data-dgm="label"]');
    const text = (inp?.value || "").trim();
    const p = pos && pos[row.dataset.nodeId];
    const over = !!(p && text
      && eng.clampLabel(text, p.w, p.h, eng.fitFont(text, p.w, p.h, 28)) !== text);
    row.classList.toggle("dgm-node-row--over", over);
    if (over) cut.push(text);
  });
  note.hidden = !cut.length;
  if (cut.length) {
    // сами подписи здесь длинные — в перечислении режем их до узнаваемого начала,
    // иначе предупреждение занимает больше места, чем список узлов
    const short = (s) => `«${s.length > 22 ? s.slice(0, 21).trim() + "…" : s}»`;
    const total = list.querySelectorAll(".dgm-node-row").length;
    note.textContent = cut.length === total && total > 2
      ? "На слайде подписи всех узлов сократятся многоточием — плашки этого"
        + " размера столько текста не вмещают."
      : `На слайде сократятся многоточием: ${cut.slice(0, 2).map(short).join(", ")}`
        + (cut.length > 2 ? ` и ещё ${cut.length - 2}` : "")
        + " — плашки этого размера столько текста не вмещают.";
  }
}

function dgmEdgeRow(e) {
  const row = document.createElement("div");
  row.className = "field-item dgm-edge-row";
  const from = document.createElement("select");
  from.dataset.dgm = "from";
  from.dataset.want = e.from || "";   // выбор применяется при заполнении options
  from.onchange = scheduleSave;
  const arrow = document.createElement("span");
  arrow.className = "dgm-arrow";
  arrow.textContent = "→";
  const to = document.createElement("select");
  to.dataset.dgm = "to";
  to.dataset.want = e.to || "";
  to.onchange = scheduleSave;
  row.appendChild(from);
  row.appendChild(arrow);
  row.appendChild(to);
  /* Подпись связи — у всех типов с рёбрами, а не только у блок-схемы. Движок
     рисует label на любом ребре (оргсхема «утверждает», сеть «поставки»), и
     заполнитель их пишет: в промпте ребро описано как {from,to,label} без
     оговорок про тип. А поле было только у flowchart — при том, что связи
     собираются из DOM панели заново. Значит, у оргсхемы, дорожек, карты и сети
     подпись ребра не показывалась вовсе, и первый же автосейв (хоть правка
     заголовка) стирал её со слайда молча. Поле есть — подпись и видна, и
     переживает сейв. */
  const label = document.createElement("input");
  label.placeholder = "подпись";
  label.maxLength = 30;                 // MAX_EDGE_LABEL (schema.py)
  label.className = "dgm-edge-label";
  label.value = e.label == null ? "" : String(e.label);
  label.dataset.dgm = "label";
  label.oninput = scheduleSave;
  row.appendChild(label);
  const del = document.createElement("button");
  del.type = "button"; del.className = "btn btn-ghost btn-sm item-del";
  del.textContent = "✕";
  del.title = "Удалить связь";
  del.onclick = () => { row.remove(); syncDgmCounts(); scheduleSave(); };
  row.appendChild(del);
  return row;
}

// Пересобрать options селектов связей из текущих строк узлов (id стабилен,
// подпись — живой текст узла). Выбор сохраняется, пока узел существует.
// Возвращает число строк связей, выброшенных вместе с удалённым узлом.
function refreshDgmEdgeSelects() {
  const form = byId("builderForm");
  if (!form) return 0;
  const opts = [...form.querySelectorAll(".dgm-node-row")].map((row) => ({
    id: row.dataset.nodeId,
    label: (row.querySelector('[data-dgm="label"]')?.value.trim()
            || row.dataset.nodeId).slice(0, 24),
  }));
  /* Узел удалили — связи, которые на него ссылались, надо УБРАТЬ, а не оставлять
     селекту выбирать замену. Раньше select просто терял свой option и падал на
     первый в списке: удаление «Проверки данных» превращало «Заявка → Проверка» в
     «Заявка → Заявка», а «Проверка → Согласовано» — в «Заявка → Согласовано».
     Схема оставалась валидной и молча сохранялась: на слайде появлялись стрелки,
     которых человек не рисовал, а ветвление разъезжалось. */
  const alive = {};
  opts.forEach((o) => { alive[o.id] = 1; });
  let dropped = 0;
  form.querySelectorAll(".dgm-edge-row").forEach((row) => {
    const gone = [...row.querySelectorAll("select")]
      .some((s) => !alive[s.value || s.dataset.want || ""]);
    if (gone) { row.remove(); dropped++; }
  });
  form.querySelectorAll(".dgm-edge-row select").forEach((sel) => {
    const want = sel.value || sel.dataset.want || "";
    sel.innerHTML = "";
    opts.forEach((o) => sel.add(new Option(o.label, o.id)));
    if (opts.some((o) => o.id === want)) sel.value = want;
    sel.dataset.want = sel.value;
  });
  return dropped;
}

function dgmNewId() {
  const used = new Set([...document.querySelectorAll("#dgmNodeList .dgm-node-row")]
    .map((r) => r.dataset.nodeId));
  let i = 1;
  while (used.has("n" + i)) i++;
  return "n" + i;
}

// Живая перерисовка схемы слайда i в превью (drag-сброс, без ожидания сейва).
function liveRenderDiagram(i) {
  const doc = frame.contentDocument;
  const slide = draftPlan.slides[i];
  if (!doc || !slide || !slide.fields || !slide.fields.diagram) return;
  const host = doc.querySelectorAll(".slide")[i]?.querySelector(".diagram-host");
  const eng = doc.defaultView && doc.defaultView.DiagramEngine;
  if (host && eng) eng.render(host, JSON.parse(JSON.stringify(slide.fields.diagram)));
}

// Собрать typed-поля диаграммы из панели. База — последний слепок fields (offsets,
// direction, meta и не показанные в панели свойства узлов переживают сбор); DOM
// панели главнее для узлов/связей/шапки. null — панель не построена (гонка).
function collectDiagramFields() {
  const form = byId("builderForm");
  const slide = draftPlan.slides[current];
  if (!form || !slide || !slide.fields) return null;
  const headEl = form.querySelector('[data-dgm="heading"]');
  if (!headEl) return null;
  const base = slide.fields.diagram || {};
  const baseNodes = {};
  (base.nodes || []).forEach((n) => { baseNodes[n.id] = n; });
  const nodes = [];
  form.querySelectorAll(".dgm-node-row").forEach((row) => {
    const id = row.dataset.nodeId;
    const label = (row.querySelector('[data-dgm="label"]')?.value || "").trim();
    if (!label) return;   // пустой узел не пишем — как пустые пункты списков
    const n = { ...(baseNodes[id] || {}), id, label };
    const shape = row.querySelector('[data-dgm="shape"]');
    if (shape) n.shape = shape.value;
    const val = row.querySelector('[data-dgm="value"]');
    if (val) n.value = val.value.trim();
    const lane = row.querySelector('[data-dgm="lane"]');
    if (lane) n.lane = lane.value.trim();
    const lvl = row.querySelector('[data-dgm="level"]');
    // Пустой старт = null: 0 значил бы «начать с первого периода» и ломал каскад.
    if (lvl) n.level = lvl.value.trim() === "" ? null : Number(lvl.value);
    const acc = row.querySelector('[data-dgm="accent"]');
    if (acc) n.accent = acc.checked;
    nodes.push(n);
  });
  const ids = new Set(nodes.map((n) => n.id));
  const diagram = { ...base, nodes };
  // meta-поля панели (оси матрицы, пересечение венна) — если построены
  const metaPatch = {};
  let metaTouched = false;
  ["x_axis", "y_axis", "center_label"].forEach((k) => {
    const el = form.querySelector(`[data-dgm="${k}"]`);
    if (el) { metaPatch[k] = el.value.trim(); metaTouched = true; }
  });
  if (metaTouched) diagram.meta = { ...(base.meta || {}), ...metaPatch };
  if (form.querySelector("#dgmEdgeList")) {
    // Свойства ребра, которых в панели нет (style: пунктир), берём из слепка по
    // паре from>to — тем же приёмом, каким узлы наследуют baseNodes. Иначе сбор
    // «с нуля» превращал пунктирную связь в сплошную на первом же сейве.
    const baseEdges = {};
    (base.edges || []).forEach((e) => { baseEdges[e.from + ">" + e.to] = e; });
    const edges = [];
    form.querySelectorAll(".dgm-edge-row").forEach((row) => {
      const fromId = row.querySelector('[data-dgm="from"]')?.value || "";
      const toId = row.querySelector('[data-dgm="to"]')?.value || "";
      // связь на удалённый узел или петля — молча пропускаем (транзиент правки)
      if (!fromId || !toId || fromId === toId || !ids.has(fromId) || !ids.has(toId)) return;
      const e = { ...(baseEdges[fromId + ">" + toId] || {}), from: fromId, to: toId };
      const lbl = row.querySelector('[data-dgm="label"]');
      // Поле есть всегда (dgmEdgeRow), пустое — это осознанно снятая подпись.
      if (lbl) e.label = lbl.value.trim();
      edges.push(e);
    });
    diagram.edges = edges;
  }
  return {
    heading: headEl.value.trim(),
    subtitle: (form.querySelector('[data-dgm="subtitle"]')?.value || "").trim(),
    diagram,
  };
}

// Блок претензий к схеме над формой. tone "warn" — наша проверка ДО сейва,
// "error" — сервер отверг правку. Пустой список убирает блок.
function dgmShowClaims(claims, tone) {
  const form = byId("builderForm");
  if (!form) return;
  let box = byId("dgmClaims");
  if (!claims || !claims.length) { if (box) box.remove(); return; }
  if (!box) {
    box = document.createElement("div");
    box.id = "dgmClaims";
    form.prepend(box);   // над полями: иначе претензия уезжает под список узлов
  }
  box.className = "dgm-claims dgm-claims--" + tone;
  box.innerHTML = "";
  const head = document.createElement("div");
  head.className = "dgm-claims-head";
  head.textContent = "Правка не сохранена — схема не сходится:";
  box.appendChild(head);
  const ul = document.createElement("ul");
  claims.forEach((c) => {
    const li = document.createElement("li");
    li.textContent = c;      // текст с сервера — только textContent, без innerHTML
    ul.appendChild(li);
  });
  box.appendChild(ul);
}

// Сейв диаграммного слайда: PUT /fields (typed-контракт) вместо PUT content.
// Схему проверяем ДО запроса (diagramClaims — зеркало schema.py): сервер
// отвергает невалидный спек целиком, и без объяснения это читалось как
// «редактор сломался». 400 не ретраим — чинить должен пользователь; сеть/5xx —
// бэкофф как у обычного сейва.
async function saveDiagramSlide(idx, attempt) {
  const fields = collectDiagramFields();
  if (!fields) return;
  const slide = draftPlan.slides[idx];
  const accepted = slide.fields;   // последнее состояние, принятое сервером
  const claims = window.diagramClaims ? diagramClaims(fields.diagram) : [];
  if (claims.length) {
    // Заведомо невалидная правка: запрос вернул бы 400 и не изменил план.
    // Ни план, ни сервер не трогаем — ввод в форме остаётся, чинить его тут же.
    dgmUnsaved = { idx, fields, claims, tone: "warn" };
    dgmShowClaims(claims, "warn");
    setSaveStatus("invalid");
    return;
  }
  slide.fields = fields;   // optimistic local update
  setSaveStatus(attempt ? "retrying" : "saving");
  let r;
  try {
    r = await fetch(U(`/api/drafts/${sessionId}/slides/${idx + 1}/fields`), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slide_type: "diagram", fields }),
    });
  } catch (_) { r = null; }
  if (r && r.ok) {
    if (dgmUnsaved && dgmUnsaved.idx === idx) dgmUnsaved = null;
    dgmShowClaims([], "warn");
    setSaveStatus("saved");
    loadDeck();  // SVG живёт в data-атрибуте — точечный текст-патч не применим
    return;
  }
  if (r && r.status === 400) {
    // План не изменился — откатываем и локальную модель, иначе уход со слайда и
    // возврат покажут невалидную схему как сохранённую (а drag и «Сбросить
    // раскладку» продолжат строиться на спеке, которого на сервере нет).
    slide.fields = accepted;
    let srv = [];
    try { srv = ((await r.json()).detail || {}).errors || []; } catch (_) { /* пусто */ }
    const claimsSrv = srv.length ? srv : ["Схема не прошла проверку"];
    dgmUnsaved = { idx, fields, claims: claimsSrv, tone: "error" };
    dgmShowClaims(claimsSrv, "error");
    setSaveStatus("invalid");
    return;
  }
  if (r && r.status === 409) {
    try { await reloadDraft(idx); } catch (_) { /* оставить локальный ввод виден */ }
    setSaveStatus("error");
    return;
  }
  if (attempt < SAVE_RETRY_MS.length) {
    setSaveStatus("retrying");
    clearTimeout(saveRetryTimer);
    saveRetryTimer = setTimeout(() => saveCurrentSlide(attempt + 1), SAVE_RETRY_MS[attempt]);
    return;
  }
  setSaveStatusRetry();
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
  // Диаграммный typed-слайд сейвится по своему контракту (PUT /fields).
  if (slide.slide_type === "diagram" && slide.fields) {
    return saveDiagramSlide(idx, attempt);
  }
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
async function changeTemplate(templateId, dgmKind) {
  // Drop any pending debounced save — we take the freshest form values directly.
  clearTimeout(putTimer); putTimer = null;
  const before = snapshotPlan();   // на случай, если пересадка сорвётся на полпути
  const slide = draftPlan.slides[current];
  const wasDiagram = !!(slide && slide.slide_type === "diagram" && slide.fields);
  // Merge: plan content keeps slots the current form doesn't render (so a swap
  // A→B→A restores A's slots), form values win for the slots the user can see.
  // Диаграммный typed-слайд живёт в fields, а не в content — переносим шапку.
  const raw = wasDiagram
    ? { title: slide.fields.heading || "", subtitle: slide.fields.subtitle || "" }
    : slide && !slide.freeform
      ? { ...(slide.content || {}), ...collectContent() }
      : (slide && slide.content) || {};
  const content = wasDiagram
    ? raw
    : remapLists(tplOf(slide && slide.template_id), tplOf(templateId), raw);
  // template change = delete + re-add at the same position with the new template.
  // The content rides along: overlapping slots (title, items, …) carry over; the
  // rest stays in plan.json (draft_render ignores unknown slots), so switching
  // back restores it. Without this the swap silently wiped the slide's content.
  const del = await fetch(U(`/api/drafts/${sessionId}/slides/${current + 1}`),
                          { method: "DELETE" }).catch(() => null);
  if (!del || !del.ok) { setSaveStatus("error"); return; }
  const add = await fetch(U(`/api/drafts/${sessionId}/slides`), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template_id: templateId, at: current + 1, content }),
  }).catch(() => null);
  // Пересадка идёт в два шага, и если второй не прошёл (оффлайн, 500 на
  // пересборке деки), слайд УЖЕ удалён — без возврата снимка он бы просто
  // исчез вместе с содержимым, и никто бы не сказал почему.
  if (!add || !add.ok) {
    await applyPlan(before);
    setSaveStatus("error");
    return;
  }
  pushUndo();
  if (dgmKind) {
    await applyDiagramKind(current + 1, dgmKind,
      wasDiagram ? slide.fields : { heading: content.title || "" });
  }
  await reloadDraft(current);
  showUndoToast("Макет слайда изменён");
}

// Спрашиваем способ ПОСЛЕ выбора макета: до него вопрос был бы про
// несуществующий макет, а модификатором у кнопки («перезаполнять ИИ» галочкой)
// второй способ читался бы настройкой первого — ровно та ошибка, из-за которой
// пошаговая сборка на главной пряталась чекбоксом.
async function changeTemplateAsked(templateId, dgmKind) {
  // Пока идёт перезаполнение, менять макет нельзя — и молчать об этом тоже:
  // закрытый оверлеем кадр не объясняет, почему выбор в пикере ничего не сделал.
  if (refillBusy) {
    await alertDialog("Слайд ещё перезаполняется — дождитесь окончания.");
    return;
  }
  const slide = draftPlan.slides[current];
  const brief = ((slide && slide.brief) || "").trim();
  const ctx = brief || (slide ? slideTextLines(slide).join(" ") : "");
  // Перезаполнять нечем (пустой слайд) — выбора нет, идёт обычный перенос.
  if (!ctx) return changeTemplate(templateId, dgmKind);
  const tpl = tplOf(templateId);
  // Для схемы автор выбирал ДВА шага — макет и тип; называть в вопросе только
  // «Схема» значит подтверждать не тот выбор, который он сделал последним.
  const dgm = dgmKind ? dgmType(dgmKind) : null;
  const name = ((tpl && tpl.display_name) || templateId)
    + (dgm ? `: ${dgm.display_name}` : "");
  const mode = await chooseDialog(
    `Новый макет — «${name}». Что сделать с содержимым слайда?`, [
      // Перенос в схему — не перенос: слоты макета и узлы схемы несопоставимы,
      // и механическая пересадка ставит ПРИМЕР со старым заголовком. Обещать
      // «текст переедет как есть» тут нельзя — это прямая неправда.
      { label: dgmKind ? "Поставить пример схемы" : "Перенести текст",
        value: "carry",
        desc: dgmKind
          ? "Мгновенно: заголовок сохранится, схема встанет образцом — подписи "
            + "узлов и связи вы впишете сами."
          : "Мгновенно: текст переезжает в новый макет как есть. Что в него не "
            + "помещается, остаётся в черновике и вернётся при обратной смене." },
      { label: "Перезаполнить по документу", value: "refill",
        desc: (brief ? "ИИ перечитает исходный фрагмент документа"
                     : "ИИ перечитает текст слайда")
          + (dgmKind
            ? " и построит схему этого типа по нему — с вашими этапами, а не "
              + "образцовыми."
            : " и напишет содержимое заново — под то, что новый макет умеет "
              + "показать.")
          + " Занимает до минуты." },
    ]);
  if (!mode) return;
  if (mode === "carry") return changeTemplate(templateId, dgmKind);
  return refillTemplate(templateId, dgmKind);
}

// Смена макета руками ИИ: содержимое пишется заново под новый макет по тому же
// фрагменту документа, что и на сборке. Долгая операция (до минуты) — закрываем
// кадр оверлеем со счётчиком: без него «нажал и ничего не происходит».
let refillBusy = false;
async function refillTemplate(templateId, dgmKind) {
  // Второй запуск поверх идущего: панели под оверлеем остаются кликабельными, а
  // два перезаполнения одного слайда — это гонка за то, чей ответ ляжет последним.
  if (refillBusy) return;
  refillBusy = true;
  clearTimeout(putTimer); putTimer = null;  // форму старого макета не сейвим
  // Слайд запоминаем: за минуту автор успевает уйти на другой, и возврат к
  // «current» показал бы результат не на том слайде, который он менял.
  const idx = current;
  const noteWas = buildNote ? buildNote.textContent : "";
  const t0 = Date.now();
  showOverlay(true);
  if (buildTitle) buildTitle.textContent = "Перезаполняю слайд…";
  if (buildSub) {
    buildSub.textContent = "Пишу содержимое заново под новый макет.";
  }
  const tick = setInterval(() => {
    if (buildNote) {
      buildNote.textContent = `${Math.round((Date.now() - t0) / 1000)} с — `
        + "не закрывайте страницу.";
    }
  }, 1000);
  let r;
  try {
    r = await fetch(U(`/api/drafts/${sessionId}/slides/${idx + 1}/refill`), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: templateId, kind: dgmKind || "" }),
    });
  } catch (_) { r = null; }
  clearInterval(tick);
  refillBusy = false;
  showOverlay(false);
  if (buildNote) buildNote.textContent = noteWas;
  if (!r || !r.ok) {
    setSaveStatus("error");
    let detail = "";
    if (r) { try { detail = (await r.json()).detail || ""; } catch (_) { /* не JSON */ } }
    await alertDialog(detail || "Не удалось перезаполнить слайд — попробуйте ещё "
      + "раз или смените макет с переносом текста.");
    return;
  }
  pushUndo();
  await reloadDraft(idx);
  // Осечка модели вернула заглушку: молчать нельзя — со стороны автора это
  // «выбрал макет, применился blank». Причину сервер кладёт в question слайда.
  const s = draftPlan.slides[idx];
  if (s && s.status === "failed") {
    await alertDialog(s.question || "Не удалось заполнить макет — на слайде "
      + "заглушка с темой в заголовке.");
    return;
  }
  showUndoToast("Слайд перезаполнен под новый макет");
}

// К§4 — общий обработчик добавления слайда: кнопка рейла (#addSlide), кнопка пустой
// панели (#builderAdd) и клик по заглушке пустого драфта ведут в один пикер.
function addSlideViaPicker() {
  openPicker(async (tid, dgmKind) => {
    await flushPendingSave(); // preserve the current slide's edit before inserting
    // Вставляем новый слайд сразу после активного (1-based позиция at).
    const at = Math.min(current + 2, draftPlan.slides.length + 1);
    const r = await fetch(U(`/api/drafts/${sessionId}/slides`), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: tid, at }),
    }).catch(() => null);
    // Без проверки неуспех выглядел как «нажал — ничего не произошло»: пикер
    // закрывался, слайд не появлялся, причина нигде не показывалась.
    if (!r || !r.ok) { setSaveStatus("error"); return; }
    pushUndo();
    // Мастер «Схема»: сразу материализуем выбранный тип примером (typed-поля).
    if (dgmKind) await applyDiagramKind(at, dgmKind, null);
    await reloadDraft(at - 1); // переходим на только что добавленный слайд
  });
}
byId("addSlide")?.addEventListener("click", addSlideViaPicker);
byId("builderAdd")?.addEventListener("click", addSlideViaPicker);
// Пустой конструктор: «Начать со структуры» ставит три слайда разом (обложка →
// пустой → контакты) теми же POST, что одиночное добавление, — undo и
// перерисовка работают штатно. Тот же скелет сервер даёт при входе с главной
// (skeleton:true); эта кнопка — для «всё удалил, начну заново».
byId("emptySkeleton")?.addEventListener("click", async () => {
  const btn = byId("emptySkeleton");
  btn.disabled = true;
  try {
    for (const tid of ["cover", "blank", "contacts"]) {
      const r = await fetch(U(`/api/drafts/${sessionId}/slides`), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_id: tid }),
      }).catch(() => null);
      // Неуспех молча — «нажал и ничего»: показываем статус и не продолжаем.
      if (!r || !r.ok) { setSaveStatus("error"); return; }
    }
    pushUndo();
    await reloadDraft(0);
  } finally { btn.disabled = false; }
});

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
  const r = await fetch(U(`/api/drafts/${sessionId}/slides/${i + 1}`),
                        { method: "DELETE" }).catch(() => null);
  // Ответ проверяем ДО того, как объявить об удалении: при неуспехе (оффлайн,
  // 500 на пересборке деки) слайд остаётся на месте, и плашка «Вернуть» врала
  // бы про удаление, которого не было. Снимок для отмены тоже кладём только
  // после успеха — иначе Ctrl+Z откатывал бы несостоявшееся действие.
  if (!r || !r.ok) { setSaveStatus("error"); return; }
  pushUndo();
  await reloadDraft(Math.max(0, i - 1));
  showUndoToast(`Слайд ${i + 1} удалён`);
}

/* Плашка «Вернуть» после разрушающего действия. Отмена в редакторе есть (Ctrl+Z),
   но она невидима — человек видит только исчезнувший слайд и не знает, что его
   можно достать. Плашка дёргает тот же undo(), поэтому подтверждение перед
   удалением не нужно: путь назад показан ровно тогда, когда он нужен. */
let undoToastTimer = null;

function showUndoToast(text) {
  const el = byId("undoToast");
  if (!el) return;
  el.querySelector(".undo-toast__text").textContent = text;
  el.classList.remove("hidden");
  clearTimeout(undoToastTimer);
  undoToastTimer = setTimeout(() => el.classList.add("hidden"), 9000);
}

byId("undoToastBtn")?.addEventListener("click", () => {
  clearTimeout(undoToastTimer);
  byId("undoToast")?.classList.add("hidden");
  undo();
});

/* ---- перетаскивание миниатюр для смены порядка ---- */
let dragFromIndex = null;

function onThumbDragStart(e) {
  dragFromIndex = Number(this.dataset.index);
  this.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  // Firefox требует установить данные, иначе перетаскивание не стартует
  try { e.dataTransfer.setData("text/plain", String(dragFromIndex)); } catch {}
}

/* Половина миниатюры под курсором = «вставить после». Ось определяем по соседу,
   а не по размеру контейнера: на узком экране лента становится горизонтальной
   полосой (CSS ≤900px), и тогда «до/после» — это лево/право. Сосед честнее —
   он показывает, куда реально растёт список. */
function dropAfter(thumb, e) {
  const r = thumb.getBoundingClientRect();
  const sib = thumb.previousElementSibling || thumb.nextElementSibling;
  const s = sib && sib.classList.contains("thumb") ? sib.getBoundingClientRect() : null;
  const horizontal = s && Math.abs(s.left - r.left) > Math.abs(s.top - r.top);
  return horizontal
    ? (e.clientX - r.left) > r.width / 2
    : (e.clientY - r.top) > r.height / 2;
}

let dragRaf = 0;
function onThumbDragOver(e) {
  if (dragFromIndex === null) return;
  e.preventDefault(); // разрешаем drop
  e.dataTransfer.dropEffect = "move";
  if (dragRaf) return;                 // геометрия — не чаще раза на кадр
  const self = this, cx = e.clientX, cy = e.clientY;
  dragRaf = requestAnimationFrame(() => {
    dragRaf = 0;
    const after = dropAfter(self, { clientX: cx, clientY: cy });
    self.classList.toggle("drop-after", after);
    self.classList.toggle("drop-before", !after);
  });
}

function onThumbDragLeave() {
  this.classList.remove("drop-before", "drop-after");
}

async function onThumbDrop(e) {
  e.preventDefault();
  const from = dragFromIndex;
  const after = dropAfter(this, e);
  this.classList.remove("drop-before", "drop-after");
  if (from === null) return;
  await commitThumbMove(from, Number(this.dataset.index), after);
}

// Куда встанет слайд — общее для мыши (HTML5 DnD) и пальца (рукоятка).
async function commitThumbMove(from, over, after) {
  // Позиция вставки в исходной нумерации (0-based, «перед элементом insertBefore»).
  const insertBefore = over + (after ? 1 : 0);
  // No-op: бросили на то же место.
  if (insertBefore === from || insertBefore === from + 1) return;
  // Бэкенд reorder = pop(from), затем insert(target). После удаления исходного
  // слайда индексы правее сдвигаются на 1 — корректируем цель.
  const target0 = insertBefore > from ? insertBefore - 1 : insertBefore;
  if (isDraft) await moveSlide(from, target0 + 1); // moveSlide ждёт 1-based позицию
  else await moveSlideBuilt(from, target0);
}

/* ---- перетаскивание пальцем за рукоятку ---- */
let gripDrag = null;

function onGripDown(e) {
  const thumb = this.closest(".thumb");
  if (!thumb) return;
  e.preventDefault();   // жест наш: рейл под пальцем не скроллится
  e.stopPropagation();
  gripDrag = { from: Number(thumb.dataset.index), thumb: thumb, over: null, after: false };
  thumb.classList.add("dragging");
  try { this.setPointerCapture(e.pointerId); } catch (_) {}
}

let gripRaf = 0;
function onGripMove(e) {
  if (!gripDrag) return;
  e.preventDefault();
  if (gripRaf) return;                 // elementFromPoint — не чаще раза на кадр
  const cx = e.clientX, cy = e.clientY;
  gripRaf = requestAnimationFrame(() => {
    gripRaf = 0;
    if (!gripDrag) return;
    clearDropMarks();
    gripDrag.over = null;
    // Захват указателя увёл события на рукоятку — цель ищем по координате.
    const el = document.elementFromPoint(cx, cy);
    const over = el && el.closest && el.closest(".thumb");
    if (!over || over === gripDrag.thumb) return;
    const after = dropAfter(over, { clientX: cx, clientY: cy });
    over.classList.toggle("drop-after", after);
    over.classList.toggle("drop-before", !after);
    gripDrag.over = Number(over.dataset.index);
    gripDrag.after = after;
  });
}

async function onGripUp() {
  if (!gripDrag) return;
  const d = gripDrag;
  gripDrag = null;
  d.thumb.classList.remove("dragging");
  clearDropMarks();
  if (d.over === null) return;   // отпустили мимо ленты — порядок не трогаем
  await commitThumbMove(d.from, d.over, d.after);
}

function clearDropMarks() {
  document.querySelectorAll(".thumb.drop-before, .thumb.drop-after")
    .forEach((t) => t.classList.remove("drop-before", "drop-after"));
}

// Собранная дека — HTML-as-truth: переставляем секцию в DOM кадра и сохраняем
// деку целиком (номера слайдов — CSS-счётчик, пересчитаются сами). Кадр
// перезагружаем только после успешного сейва: иначе перестановка «прыгнет»
// обратно, скрыв ошибку сохранения.
async function moveSlideBuilt(from, to0) {
  const doc = frame.contentDocument;
  const sections = doc ? [...doc.querySelectorAll(".slide")] : [];
  const moving = sections[from];
  if (!moving) return;
  const rest = sections.filter((_, i) => i !== from);
  const ref = rest[to0] || (rest.length ? rest[rest.length - 1].nextSibling : null);
  moving.parentNode.insertBefore(moving, ref);
  setSaveStatus("saving");
  let ok = false;
  try { ok = await saveDeck(); } catch (_) { /* сеть — покажем error */ }
  setSaveStatus(ok ? "saved" : "error");
  if (!ok) return;
  pendingGoTo = to0;
  loadDeck();
}

function onThumbDragEnd() {
  this.classList.remove("dragging");
  clearDropMarks();
  dragFromIndex = null;
}

async function moveSlide(idx, to1) {
  if (to1 < 1 || to1 > draftPlan.slides.length) return;
  await flushPendingSave(); // preserve the moving slide's edit before reordering
  const r = await fetch(U(`/api/drafts/${sessionId}/slides/${idx + 1}/move`), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ to: to1 }),
  }).catch(() => null);
  // Неуспех молча оставлял ленту в старом порядке — человек видел, что слайд
  // «прыгнул обратно», и тянул его второй раз. Говорим об ошибке и не двигаем.
  if (!r || !r.ok) { setSaveStatus("error"); return; }
  pushUndo();
  // 2а: сервер уже переставил слайд в plan.json — повторяем то же локально и
  // в DOM ленты. Полной пересборки (reloadDraft) нет: она перегружала полные
  // деки во все iframe на каждый move и лента «мигала».
  const [moved] = draftPlan.slides.splice(idx, 1);
  draftPlan.slides.splice(to1 - 1, 0, moved);
  dgmUnsaved = null;
  builtFormFor = -1;
  pendingGoTo = to1 - 1;
  loadDeck();                    // сцена перерисуется; deckT обновился для src
  moveThumbDom(idx, to1 - 1);
}

function moveThumbDom(from, to0) {
  const box = document.getElementById("thumbs");
  const thumbs = box ? [...box.querySelectorAll(".thumb")] : [];
  const moving = thumbs[from];
  if (!moving) { thumbsDirty = true; return; }
  const rest = thumbs.filter((_, i) => i !== from);
  const ref = rest[to0] || box.querySelector(".thumbs-hint");
  box.insertBefore(moving, ref);
  // Подписи — у всех; iframe-адреса — только у сдвинувшегося диапазона
  // (контент узлов переехал вместе с ними, догрузка ленивая и фоновая).
  const lo = Math.min(from, to0), hi = Math.max(from, to0);
  [...box.querySelectorAll(".thumb")].forEach((t, i) => {
    t.dataset.index = i;
    const num = t.querySelector(".thumb-num");
    if (num) num.textContent = i + 1;
    if (i >= lo && i <= hi) {
      const ifr = t.querySelector("iframe");
      if (ifr) ifr.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1&slide=${i + 1}`);
    }
  });
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
let pickerSeq = 0;

function curBadge() {
  const b = document.createElement("span");
  b.className = "picker-cur";
  b.textContent = "сейчас";
  return b;
}

// Плитка превью: статичный PNG (лёгкий — 22 live-iframe тормозили пикер) с
// фолбэком на live-iframe (прежнее поведение) — сервер отдаёт 404, когда
// Chromium недоступен, и на любом сбое картинки пикер работает как раньше.
function thumbPreview(prev, thumbUrl, iframeUrl) {
  const img = document.createElement("img");
  img.className = "picker-thumb";
  img.loading = "lazy";
  img.alt = "";
  img.onerror = () => {
    const ifr = document.createElement("iframe");
    ifr.loading = "lazy";
    ifr.tabIndex = -1;
    ifr.src = iframeUrl;
    img.replaceWith(ifr);
  };
  img.src = thumbUrl;
  prev.appendChild(img);
}

// cur = {id, kind} текущего слайда (для «Сменить макет»); при добавлении — пусто.
// Без пометки «сейчас» пикер не отвечал на вопрос «а что стоит сейчас?»: список
// одинаковых карточек, среди которых уже выбранная ничем не выделена.
async function openPicker(onPick, cur) {
  const picker = byId("picker");
  const grid = byId("pickerGrid");
  const curId = cur && cur.id;
  const seq = ++pickerSeq;
  grid.innerHTML = "";
  picker.classList.remove("hidden");   // окно сразу: клик всегда отвечает
  // Каталог мог ещё не приехать (кнопка появляется раньше) — тогда пикер
  // открывался пустым и «не работал с первого раза». Ждём ту же загрузку.
  // Каталог схем тянем сразу: карточка «Схема» — не макет, а вход в список
  // типов, и это должно быть видно ДО клика (иначе выглядит как один макет).
  const [, dgm] = await Promise.all([ensureCatalog(), fetchDgmCatalog()]);
  if (seq !== pickerSeq) return;       // пикер успели открыть заново
  const kinds = (dgm || []).filter((t) => t.available);
  const pickable = pickableTemplates();
  if (!pickable.length) {
    grid.innerHTML = '<p class="picker-empty">Не удалось загрузить макеты — ' +
      "проверьте соединение и откройте список ещё раз.</p>";
    return;
  }
  // Тема миниатюр = тема загруженного кадра (как у ярлыка themeToggle):
  // draftPlan.theme после флипа не обновляется, а у собранных дек его нет вовсе.
  const th = deckTheme();
  let num = 0;
  PickerGroups.groupTemplates(pickable).forEach((group) => {
    const head = document.createElement("div");
    head.className = "picker-group";
    head.textContent = group.label;
    grid.appendChild(head);
    group.items.forEach((t) => {
      num += 1;
      const isCur = !!curId && t.id === curId;
      // Мастер, а не макет: за карточкой — второй шаг со списком типов схем.
      const wizard = t.id === "diagram" && kinds.length > 1;
      const card = document.createElement("button");
      card.type = "button";
      card.className = "picker-item" + (isCur ? " picker-item--current" : "");
      // visual preview: static PNG thumb, live-iframe as the 404 fallback
      const prev = document.createElement("div");
      prev.className = "picker-prev";
      thumbPreview(prev, U(`/api/templates/${t.id}/thumb?theme=${th}`),
                   U(`/api/templates/${t.id}/preview?static=1`));  // К§16: покойные превью, без лупов
      if (isCur) prev.appendChild(curBadge());
      const n = document.createElement("span");
      n.className = "picker-num";
      n.textContent = String(num).padStart(2, "0");
      prev.appendChild(n);
      const meta = document.createElement("div");
      meta.className = "picker-meta";
      // К§2: имя крупно; сырой id — приглушённой строкой (фолбэк на id). Бейдж
      // «N типов» — обычный чип в строке имени (раньше — плашка поверх превью,
      // вклеенная в вёрстку криво; без пометки карточка врёт, что тип один).
      meta.innerHTML = `<span class="picker-id">${t.display_name || t.id}` +
        (wizard ? ` <span class="picker-badge">${kinds.length} ` +
          `${plural(kinds.length, "тип", "типа", "типов")}</span>` : "") +
        `</span>` +
        `<span class="picker-intent">${t.intent || ""}</span>` +
        (t.display_name ? `<span class="picker-code">${t.id}</span>` : "");
      if (wizard) {
        // Перечисляем несколько имён: «много» абстрактно, а «блок-схема, воронка,
        // цикл…» сразу говорит, что именно откроется.
        const more = document.createElement("span");
        more.className = "picker-more";
        const names = kinds.slice(0, 3).map((k) => k.display_name || k.kind);
        more.textContent = "Откроется выбор: " + names.join(", ")
          + (kinds.length > names.length ? " и другие" : "");
        meta.appendChild(more);
      }
      card.appendChild(prev);
      card.appendChild(meta);
      card.onclick = () => {
        picker.classList.add("hidden");
        // Мастер «Схема» — двухшаговый выбор: сначала макет, затем тип диаграммы.
        // onPick получает вторым аргументом kind — вызывающий материализует пример.
        if (t.id === "diagram") openDiagramPicker((kind) => onPick(t.id, kind),
                                                  cur && cur.kind);
        // Пересадка «в тот же макет» — это delete+add: у диаграммного слайда она
        // сбрасывала схему к примеру. Уже выбранная карточка просто закрывает пикер.
        else if (!isCur) onPick(t.id);
      };
      grid.appendChild(card);
    });
  });
}
// Пикеры — лайтбоксы канона: закрываются кнопкой, подложкой и Escape.
function closePicker(id) { byId(id)?.classList.add("hidden"); }
for (const id of ["picker", "dgmPicker"]) {
  byId(id)?.querySelector("[data-picker-close]")
    ?.addEventListener("click", () => closePicker(id));
}
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  for (const id of ["dgmPicker", "picker"]) {
    const box = byId(id);
    if (box && !box.classList.contains("hidden")) { closePicker(id); return; }
  }
});
byId("pickerClose")?.addEventListener("click", () => closePicker("picker"));

/* ---- diagram type picker (второй шаг мастера «Схема») ---- */
let dgmCatalog = null;   // [{kind, display_name, when_to_use, available, sample}]

let dgmCatalogLoad = null;
async function fetchDgmCatalog() {
  if (dgmCatalog) return dgmCatalog;
  if (!dgmCatalogLoad) {                 // предзагрузка и клик — один запрос
    dgmCatalogLoad = (async () => {
      try {
        const r = await fetch(U("/api/diagrams/catalog"));
        if (r.ok) dgmCatalog = await r.json();
      } catch (_) { /* сеть — пикер покажет пустое состояние, не упадёт */ }
      dgmCatalogLoad = null;
    })();
  }
  await dgmCatalogLoad;
  return dgmCatalog || [];
}

function dgmType(kind) {
  return (dgmCatalog || []).find((t) => t.kind === kind) || null;
}

// Большой блок выбора типа схемы: доступные — с живым превью (тот же рендер, что
// боевой слайд), будущие волны — приглушённые карточки с пометкой «скоро».
async function openDiagramPicker(onKind, curKind) {
  const picker = byId("dgmPicker");
  const grid = byId("dgmPickerGrid");
  if (!picker || !grid) return;
  const seq = ++pickerSeq;
  grid.innerHTML = "";
  picker.classList.remove("hidden");   // окно сразу, список — как приедет
  const cat = await fetchDgmCatalog();
  if (seq !== pickerSeq) return;
  if (!cat.length) {
    grid.innerHTML = '<p class="picker-empty">Не удалось загрузить типы схем — ' +
      "проверьте соединение и откройте список ещё раз.</p>";
    return;
  }
  // Тема миниатюр = тема загруженного кадра (как в пикере макетов).
  const th = deckTheme();
  cat.forEach((t) => {
    const isCur = !!curKind && t.kind === curKind;
    const card = document.createElement("button");
    card.type = "button";
    card.className = "picker-item" + (t.available ? "" : " picker-item--soon")
      + (isCur ? " picker-item--current" : "");
    const prev = document.createElement("div");
    prev.className = "picker-prev";
    if (t.available) {
      thumbPreview(prev, U(`/api/diagrams/${t.kind}/thumb?theme=${th}`),
                   U(`/api/diagrams/${t.kind}/preview?static=1`));
      if (isCur) prev.appendChild(curBadge());
    } else {
      const soon = document.createElement("span");
      soon.className = "picker-soon";
      soon.textContent = "скоро";
      prev.appendChild(soon);
    }
    const meta = document.createElement("div");
    meta.className = "picker-meta";
    meta.innerHTML = `<span class="picker-id">${t.display_name}</span>` +
      `<span class="picker-intent">${t.when_to_use}</span>`;
    card.appendChild(prev);
    card.appendChild(meta);
    if (t.available) {
      card.onclick = () => {
        picker.classList.add("hidden");
        if (!isCur) onKind(t.kind);   // тот же тип — просто закрываем, схема цела
      };
    } else {
      card.disabled = true;
    }
    grid.appendChild(card);
  });
}
byId("dgmPickerClose")?.addEventListener("click", () => closePicker("dgmPicker"));

// Материализовать тип: записать typed-поля слайда (heading + пример спека) через
// PUT /fields — пользователь сразу правит живой пример, а не пустоту. prev
// (старые fields) сохраняет заголовок/подзаголовок при смене типа.
async function applyDiagramKind(index1, kind, prev) {
  await fetchDgmCatalog();
  const t = dgmType(kind);
  if (!t || !t.sample) return;
  const fields = {
    heading: (prev && prev.heading) || t.display_name,
    subtitle: (prev && prev.subtitle) || "",
    diagram: DiagramDrag.carryLabels(JSON.parse(JSON.stringify(t.sample)),
                                prev && prev.diagram),
  };
  // Без проверки ответа выбранный тип молча не применялся: человек нажимал
  // «Воронка», а получал схему по умолчанию — и решал, что нажал не туда.
  const r = await fetch(U(`/api/drafts/${sessionId}/slides/${index1}/fields`), {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slide_type: "diagram", fields }),
  }).catch(() => null);
  if (!r || !r.ok) setSaveStatus("error");
}

async function initDraftBuilder() {
  if (mode === "manual") {
    byId("addSlide")?.classList.remove("hidden");
    byId("builder")?.classList.remove("hidden");
    await ensureCatalog();   // тот же промис, что ждёт клик по «+ Добавить слайд»
    fetchDgmCatalog(); // типы схем — заранее: имя типа в панели, пикер без ожидания
  }
  if (mode === "chat") setupChatMode();
  await fetchPlan();
  if (mode === "chat") renderOutline();
}

/* ---- точечное «Улучшить этот слайд» (замена общедековой rebuild-кнопки) ---- */
let improving = false;
// Итог последней проверки: живёт в состоянии, а не в DOM — syncImproveButton
// дёргается из renderBuilderForm (в т.ч. после onload деки) и переписывает
// note; текст «в лоб» стирался раньше, чем пользователь его видел.
let improveResult = "";
let improveResultFor = -1;
function syncImproveButton() {
  const wrap = byId("improveWrap");
  const btn = byId("improveSlide");
  const note = byId("improveNote");
  if (!wrap || !btn) return;
  const s = (draftPlan.slides || [])[current];
  wrap.classList.toggle("hidden", mode !== "manual" || !s);
  if (!s) return;
  const building = (draftPlan.slides || []).some((x) => x.brief && !x.filled);
  btn.disabled = improving || building || !!s.freeform;
  if (note) note.textContent = improving ? "Проверяю и улучшаю…"
    : building ? "Доступно после завершения сборки"
    : s.freeform ? "Свободный слайд правится в чате"
    : (current === improveResultFor ? improveResult : "");
}

byId("improveSlide")?.addEventListener("click", async () => {
  if (improving) return;
  improving = true;
  const at = current;
  improveResult = "";
  improveResultFor = -1;
  syncImproveButton();
  try {
    await flushPendingSave();
    const r = await glassFetch(
      U(`/api/drafts/${sessionId}/slides/${at + 1}/improve`),
      { method: "POST" });
    if (!r.ok) {
      let detail = "";
      try { detail = JSON.parse(await r.text()).detail; } catch (e) { detail = ""; }
      throw new Error(detail || "не удалось улучшить слайд");
    }
    const out = await r.json();
    if (out.plan) draftPlan = out.plan;
    builtFormFor = -1;
    // Панель чата в manual скрыта табом — итог показывает note рядом с кнопкой.
    improveResult = out.improved
      ? "Слайд проверен и улучшен."
      : "Слайд проверен — замечаний нет, менять нечего.";
    improveResultFor = at;
    loadDeck();
  } catch (e) {
    alertDialog("Не удалось улучшить слайд: " + (e && e.message ? e.message : e));
  } finally {
    improving = false;
    syncImproveButton();
  }
});

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

/* ===================== GLASS BUILD (?glass=1) ===================== */
// Клиент-управляемый степпинг нового контракта 2026-08-20: /glass/start —
// parse-only (вся лента тамбов с темами видна сразу), POST /glass/step делает
// ОДНО действие (action: "fill" — заполнить слайд, "score" — разметить макет,
// null — работы сейчас нет) и возвращает план. Параллельно степперу живёт
// разведчик /glass/score: размечает следующие слайды, пока степпер заполняет
// текущий — итого не больше двух LLM-вызовов на сессию. Вопросы ИИ
// (needs_input) конвейер НЕ блокируют: слайд откладывается, ответ уходит через
// /glass/answer в любой момент, а сборка продолжает соседние. step и answer
// сериализованы очередью glassChain; score ходит мимо неё — сервер вклеивает
// каждое изменение в свежий план под замком, параллель безопасна.
let glassRunning = false;   // весь режим (гасит contenteditable на превью)
let glassLoopDone = false;  // done от сервера: все слайды с темой заполнены
let glassLooping = false;   // петля одна: ответ перезапускает её, а не дублирует
let glassScouting = false;  // разведчик один
let glassChain = Promise.resolve();
const glassCards = {};      // 1-based index слайда → карточка вопроса
const glassBaseTitle = document.title; // вернуть после сборки / без вопросов
let glassOverlayOn = false;   // полноэкранный этап сборки поверх редактора
let glassStepT0 = 0;          // старт текущего шага — таймер телеметрии
let glassTickTimer = null;    // секундный тик телеметрии и ленты
const gloFilmFilled = {};     // 1-based index → ячейка уже живой мини-рендер
let gloShown = 0;             // какой слайд крупно показан в центре
// Свежезаполненный слайд держится в центре пару секунд, потом фокус переезжает
// на скелет следующего: без паузы результат мелькал бы миллисекунды (петля
// сразу стартует следующий шаг), и автор не видел бы, что собралось.
let gloHoldUntil = 0;
const GLO_HOLD_MS = 2500;
// Этап «Вопросы»: липкий — включается, когда автозаполнение исчерпано и есть
// что разбирать (вопросы/осечки/хвост); выключается, когда карточек не
// осталось и хвоста нет (оставшиеся слайды дозаполняются под фокус-центром).
let glassResolveOn = false;

function glassTickStart() {
  if (!glassTickTimer) glassTickTimer = setInterval(renderGlassTele, 1000);
}
function glassTickStop() {
  clearInterval(glassTickTimer);
  glassTickTimer = null;
}

function glassEnqueue(fn) {
  const p = glassChain.then(fn);
  glassChain = p.catch(() => {}); // ошибка одного запроса не рвёт очередь
  return p;
}

// Слушатели оверлея вешаются один раз: после «Остановить сборку» + «Продолжить
// сборку» startGlassMode зовётся повторно, и без гарда клики двоились бы.
let glassUiWired = false;

function startGlassMode() {
  glassRunning = true;
  const badge = byId("modeBadge");
  if (badge) badge.textContent = "Пошаговая сборка";
  byId("rpanelTabs")?.classList.add("hidden");
  byId("builder")?.classList.add("hidden");
  document.querySelector(".chat")?.classList.add("hidden");
  byId("glassResume")?.classList.add("hidden");
  if (!glassUiWired) {
    glassUiWired = true;
    byId("glassFinish")?.addEventListener("click", exitGlassMode);
    byId("glassRetry")?.addEventListener("click", () => {
      byId("glassRetry").classList.add("hidden");
      byId("glassDone").classList.add("hidden");
      glassLoop();
      glassScout();
    });
    byId("glassAuto")?.addEventListener("click", glassAnswerAll);
    byId("glassRestBtn")?.addEventListener("click", startGlassRest);
    byId("glassStop")?.addEventListener("click", stopGlassBuild);
    // Подглядывание: клик по готовой ячейке ленты показывает её слайд в центре
    // (iframe внутри ячейки с pointer-events:none — клик ловит сама ячейка).
    byId("gloFilm")?.addEventListener("click", (e) => {
      const cell = e.target.closest(".glo-cell--ready");
      if (cell) gloPeek(Number(cell.dataset.index));
    });
  }
  // Каталог макетов грузится лениво, а карточки вопросов появляются на первых же
  // шагах — без этого чип-кандидат подписывался сырым id («cards-6»).
  ensureCatalog().then(refreshGlassChipNames);
  enterGlassOverlay();
  renderGlassPanel(null);
  glassLoop();
  glassScout();
}

// Полноэкранный этап сборки: с первой секунды после загрузки документа и до
// самого редактора. Раннего выхода нет (решение 2026-08-21): пока идёт работа,
// автор видит только её — редактор со всеми его органами появится, когда
// сборка и вопросы разобраны. Единственный аварийный выход — «Перейти к
// редактированию» на этапе вопросов (см. glassDone).
function enterGlassOverlay() {
  glassOverlayOn = true;
  byId("glassOverlay")?.classList.remove("hidden");
  glassTickStart();
  renderGlassOverlay();
}

// Короткое «что это» для чипа: первая фраза intent'а. По всей библиотеке intent
// начинается определением макета («Цитата или важная фраза», «Слайд „в цифрах“»),
// а целиком это абзац на 300 символов — в чип не влезает.
function tplGist(tid) {
  const t = tplOf(tid);
  return t ? gist(t.intent) : "";
}

// Переподписать чипы, когда каталог доехал позже карточек.
function refreshGlassChipNames() {
  document.querySelectorAll(".glass-chip__name[data-tid]").forEach((el) => {
    const tpl = tplOf(el.dataset.tid);
    if (!tpl) return;
    // Тип схемы дописан к имени макета («Схема: Воронка») — не затираем его,
    // иначе выбранный тип пропадал с чипа при подгрузке каталога.
    const kind = el.closest(".glass-chip")?.dataset.kind;
    el.textContent = kind
      ? `${tpl.display_name}: ${(dgmType(kind) || {}).display_name || kind}`
      : tpl.display_name;
  });
  document.querySelectorAll(".glass-chip__gist[data-tid]").forEach((el) => {
    el.textContent = tplGist(el.dataset.tid);
  });
}

// Один шаг на сервере — до ~минуты LLM-вызовов, при заторе у провайдера дольше.
// Без таймаута повисший коннект (обрыв сети без RST, спящий ноутбук) держал бы
// цикл вечно и сборка «замирала». 340 с заведомо больше серверных таймаутов
// модели — обрыв по AbortController значит «ответа не будет», не «медленно».
const GLASS_FETCH_MS = 340000;
// Все живые запросы сборки: «Остановить сборку» рвёт их разом (glassAbortAll).
const glassCtls = new Set();
async function glassFetch(url, opts = {}) {
  const ctl = new AbortController();
  glassCtls.add(ctl);
  const t = setTimeout(() => ctl.abort(), GLASS_FETCH_MS);
  try { return await fetch(url, { ...opts, signal: ctl.signal }); }
  finally { clearTimeout(t); glassCtls.delete(ctl); }
}
function glassAbortAll() {
  glassCtls.forEach((c) => c.abort());
  glassCtls.clear();
}

async function glassLoop() {
  if (glassLooping) return;
  glassLooping = true;
  // Перерисовка после выхода из петли: статус «ждёт ваших ответов» отличим от
  // «заполняю» только когда glassLooping уже снят.
  try { await glassSteps(); } finally { glassLooping = false; renderGlassPanel(null); }
}

// Самолечение застывшей панели (живой прогон 2026-08-20): наш шаг оборвался по
// клиентскому таймауту, но сервер его доработал — незаполненный слайд остался
// «занят», и петля остановилась навсегда. Один таймер перезапускает петлю: она
// заберёт свежий план (поздний результат) или дозаполнит слайд сама.
let glassRetryTimer = null;
const GLASS_RETRY_MS = 45000;
function glassRetryLater() {
  if (glassRetryTimer) return;
  glassRetryTimer = setTimeout(() => {
    glassRetryTimer = null;
    if (glassRunning && !glassLoopDone) glassLoop();
  }, GLASS_RETRY_MS);
}

async function glassSteps() {
  let failures = 0;
  for (;;) {
    if (!glassRunning) return;  // «Остановить сборку» — выходим без ретраев
    let out;
    try {
      out = await glassEnqueue(async () => {
        glassStepT0 = Date.now();
        try {
          const r = await glassFetch(U(`/api/drafts/${sessionId}/glass/step`),
                                     { method: "POST" });
          if (!r.ok) throw new Error(await r.text());
          return r.json();
        } finally { glassStepT0 = 0; }
      });
    } catch (e) {
      if (!glassRunning) return;  // обрыв — это наш же стоп, не сбой сети
      if (++failures >= 3) { glassFail(); return; }
      await new Promise((res) => setTimeout(res, 4000));
      continue;
    }
    if (!glassRunning) return;
    failures = 0;
    byId("gloErr")?.classList.add("hidden"); // успешный шаг гасит ошибку
    if (out.plan) draftPlan = out.plan;
    const d = glassStepDecision(out);
    // показываем свежезаполненный слайд — на сцене и крупно в оверлее
    if (d.jump != null) { pendingGoTo = d.jump; gloShowSlide(d.jump + 1); }
    loadDeck();
    renderGlassPanel(out);
    if (d.done) { glassLoopDone = true; break; }
    // Работы сейчас нет (action null): либо остались только вопросы — ждём
    // ответа автора (glassResume перезапустит петлю), либо разведчик ещё
    // размечает следующий слайд — его финиш перезапустит петлю сам. Третий
    // случай — слайд держит оборванный по таймауту шаг (сервер его доработает):
    // retryLater перезапускает петлю таймером, иначе панель застывала навсегда.
    if (d.stop) { if (d.retryLater) glassRetryLater(); return; }
  }
  // Всё заполнено, вопросов и осечек нет → сразу в обычный редактор. Осечка
  // держит панель наравне с вопросом: иначе карточку «макет сменился на
  // заглушку» смахивало бы этим же выходом, и автор оставался с пустым слайдом
  // без единого слова о том, что случилось. Обрезанный хвост держит её по той же
  // причине: и уведомление о потере, и кнопка «собрать остальное» живут в этой
  // панели, и на гладкой сборке автор не увидел бы ни того, ни другого.
  if (!openGlassQuestions().length && !glassFailedSlides().length &&
      !(draftPlan.rest || 0)) exitGlassMode();
}

// Разведчик: размечает макеты СЛЕДУЮЩИХ слайдов, пока степпер заполняет
// текущий — к моменту заполнения слайд уже размечен, и шаг не тратится на
// скоринг. Ходит мимо glassChain (иначе не было бы параллели); сервер вклеивает
// результат в свежий план под замком, а занятые слайды бронирует _INFLIGHT.
async function glassScout() {
  if (glassScouting) return;
  glassScouting = true;
  let failures = 0;
  try {
    for (;;) {
      if (!glassRunning) return;
      let out;
      try {
        const r = await glassFetch(U(`/api/drafts/${sessionId}/glass/score`),
                                   { method: "POST" });
        if (!r.ok) throw new Error(await r.text());
        out = await r.json();
      } catch (e) {
        if (!glassRunning) return;  // обрыв — наш же стоп
        // Разведка — ускорение, а не необходимость: степпер разметит сам.
        if (++failures >= 3) return;
        await new Promise((res) => setTimeout(res, 4000));
        continue;
      }
      if (!glassRunning) return;
      failures = 0;
      if (out.plan) draftPlan = out.plan;
      loadDeck();               // разметка меняет макет тамба
      renderGlassPanel(out);
      // У степпера могла появиться работа (слайд размечен → можно заполнять).
      if (out.action) glassLoop();
      if (!out.action) return;  // неразмеченных не осталось
    }
  } finally {
    glassScouting = false;
    renderGlassPanel(null);
  }
}

// Заполненный слайд вопросом больше не считается, даже если метку не сняли:
// карточка на готовый слайд — это «слева собрано, справа всё ещё спрашивают».
function openGlassQuestions() {
  return (draftPlan.slides || [])
    .map((s, i) => (s && s.status === "needs_input" && !s.filled ? i + 1 : 0))
    .filter(Boolean);
}

// Слайды, которые сорвались при заполнении: макет молча стал заглушкой. Им тоже
// нужна карточка — иначе со стороны автора это «выбрал один макет, применился
// другой», без объяснения и без способа переиграть.
function glassFailedSlides() {
  return (draftPlan.slides || [])
    .map((s, i) => (s && s.status === "failed" ? i + 1 : 0))
    .filter(Boolean);
}

// Центральная перерисовка экрана сборки (имя историческое: панель уехала в
// оверлей, но все вызовы идут сюда).
function renderGlassPanel(out) {
  // После выхода (стоп/финиш) хвосты петель не должны трогать заголовок вкладки
  // и липкое состояние этапов.
  if (!glassRunning) return;
  const open = openGlassQuestions();
  const failed = glassFailedSlides();
  const working = glassLooping || glassScouting;
  const rest = draftPlan.rest || 0;
  // Этап «Вопросы» (липкий): автозаполнение исчерпано, остался разбор —
  // вопросы, осечки, обрезанный хвост. Пока идёт заполнение — не включается:
  // вопросы копятся молча и не дёргают автора (тезис «только нужное сейчас»).
  if (!glassResolveOn) {
    glassResolveOn = !working && glassAutoExitReady(draftPlan) &&
      (open.length + failed.length > 0 || rest > 0);
  } else if (!open.length && !failed.length && !rest) {
    glassResolveOn = false;   // всё разобрано — фокус обратно на дозаполнение
  }
  renderGlassRest();
  renderGlassQuestions(open, failed);
  // «Решить всё на усмотрение ИИ» — закрыть все вопросы одним движением, когда
  // разбирать каждый не хочется. Без него единственной альтернативой ответу
  // было бросить сборку.
  const auto = byId("glassAuto");
  if (auto) {
    auto.classList.toggle("hidden", open.length < 2);
    auto.textContent = `Решить все ${open.length} на усмотрение ИИ`;
  }
  // Финишный футер («Перейти к редактированию») — и когда всё заполнено, и
  // когда работа упёрлась в одни лишь вопросы: строгий done без ответов не
  // наступит, а запирать автора в режиме сборки нельзя.
  const stalled = !working && open.length > 0;
  byId("glassDone")?.classList.toggle("hidden", !(glassLoopDone || stalled));
  renderGlassOverlay();
  // Перемер обрезки фрагментов ПОСЛЕ renderGlassOverlay: именно он впервые
  // показывает панель «Вопросы», а замер честен только в видимом контейнере.
  Object.values(glassCards).forEach((c) => c._syncExcerpt?.());
  // Счётчик вопросов в заголовке вкладки: сборка идёт минуты, автор уходит в
  // другую вкладку — «(2) …» возвращает его, когда ИИ ждёт решения.
  document.title = open.length
    ? `(${open.length}) ${glassBaseTitle}` : glassBaseTitle;
}

function renderGlassOverlay() {
  if (!glassOverlayOn) return;
  const targets = (draftPlan.slides || []).filter((s) => s && s.brief);
  const filled = targets.filter((s) => s.filled).length;
  const total = targets.length;
  const unscored = (draftPlan.slides || [])
    .filter((s) => s && s.status === "unscored").length;
  const open = openGlassQuestions().length + glassFailedSlides().length;
  const st = byId("gloStepper");
  if (st) {
    st.innerHTML = "";
    glassStages({ total, unscored, filled, loopDone: glassLoopDone,
                  open, quest: glassResolveOn })
      .forEach((g) => {
        const el = document.createElement("span");
        el.className = "glo-stage glo-stage--" + g.state;
        el.textContent =
          (g.state === "done" ? "✓ " : g.state === "active" ? "⟳ " : "") + g.label;
        st.appendChild(el);
      });
  }
  // Тихий счётчик вопросов: пока идёт заполнение, вопросы копятся молча —
  // никаких карточек и кнопок, только знание «они будут после».
  const q = byId("gloQuest");
  if (q) {
    const note = glassQuestNote(glassResolveOn ? 0 : open);
    q.classList.toggle("hidden", !note);
    q.textContent = note;
  }
  // Один центр — один фокус: либо слайд, который заполняется сейчас, либо
  // разбор вопросов. Никогда оба сразу.
  byId("gloStage")?.classList.toggle("hidden", glassResolveOn);
  byId("gloResolve")?.classList.toggle("hidden", !glassResolveOn);
  const bar = byId("gloBarFill");
  const pct = total ? Math.round((filled / total) * 100) : 0;
  if (bar) bar.style.width = pct + "%";
  // Та же доля крупным числом и картинкой хода на месте заполняемого слайда.
  // Источник один — filled/total, поэтому полоса, число и каркас не могут
  // разойтись между собой.
  const numEl = byId("gloPct");
  if (numEl) numEl.innerHTML = pct + "<small>%</small>";
  gloDeck()?.set(total ? filled / total : 0);
  renderGlassTele();
  renderGloFilm();
}

// Телеметрия: тикает каждую секунду — видно, что работа ИДЁТ, с первой секунды.
function renderGlassTele() {
  if (!glassOverlayOn) return;
  const secs = glassStepT0 ? (Date.now() - glassStepT0) / 1000 : null;
  const target = glassCurrentTarget(draftPlan);
  let line = "";
  if (glassResolveOn) {
    // Этап вопросов: статус собирает glassStatusText (errtext.js, под
    // node --test) — «готово N из M, сборка ждёт вашего решения…».
    const targets = (draftPlan.slides || []).filter((s) => s && s.brief);
    line = glassStatusText({
      filled: targets.filter((s) => s.filled).length, total: targets.length,
      open: openGlassQuestions().length,
      working: glassLooping || glassScouting, loopDone: glassLoopDone,
      failed: glassFailedSlides().length,
      notice: draftPlan.notice || "",
    });
  }
  else if (glassLooping && target) line = glassFillLine(target, secs);
  else if (glassLooping || glassScouting) line = "Подбираю макеты слайдов…";
  else if (glassLoopDone) line = "Все слайды заполнены.";
  const t = byId("gloTele");
  if (t) t.textContent = line || "Раскладываю документ…";
  // Ячейка заполняемого слайда: таймер тикает прямо в ленте.
  const cell = target &&
    byId("gloFilm")?.querySelector(`[data-index="${target.index}"] .glo-cell__mark`);
  if (cell && glassLooping && secs != null)
    cell.textContent = `⟳ ${Math.round(secs)} с`;
  renderGloFocus(target);
}

// Каркас деки знаками (ascii.js). Заводится по первому обращению и живёт до
// конца страницы: полотно одно, а показывается и прячется вместе со скелетом.
// Кадр рисуется, только пока скелет на экране, — фронт сборки не должен
// крутиться под спрятанной панелью.
let gloDeckAscii = null;
function gloDeck() {
  if (gloDeckAscii) return gloDeckAscii;
  const cv = byId("gloSkelDeck");
  if (!cv || !window.CloudAscii) return null;
  gloDeckAscii = window.CloudAscii.progress(cv);
  return gloDeckAscii;
}

// Фокус-центр этапа заполнения: скелет слайда, который собирается ПРЯМО СЕЙЧАС;
// свежезаполненный рендер держится GLO_HOLD_MS — видно, что получилось, — затем
// фокус переезжает на следующий скелет.
function renderGloFocus(target) {
  if (glassResolveOn) return;
  const ifr = byId("gloSlide");
  const skel = byId("gloSkel");
  const cap = byId("gloCap");
  if (!ifr || !skel) return;
  const holding = gloShown && Date.now() < gloHoldUntil;
  if (holding || (!target && gloShown)) {
    // только что собранный слайд (или пауза между шагами) — показываем рендер
    ifr.classList.remove("hidden");
    skel.classList.add("hidden");
    gloDeck()?.stop();
    return;
  }
  if (glassLooping && target) {
    ifr.classList.add("hidden");
    skel.classList.remove("hidden");
    // Кромка фронта дышит только пока скелет виден. Полотно спрятано классом,
    // и кадры под ним никто бы не увидел — а вентилятор бы услышал.
    gloDeck()?.start();
    const lbl = byId("gloSkelLabel");
    if (lbl) lbl.textContent = target.label;
    if (cap) cap.textContent =
      `Заполняю слайд ${target.index} — содержание появится здесь`;
    return;
  }
  // работы нет и показывать нечего (старт: идёт раскладка)
  if (!gloShown) {
    ifr.classList.add("hidden");
    skel.classList.add("hidden");
    gloDeck()?.stop();
    if (cap) cap.textContent = "Слайды появятся здесь по мере заполнения";
  }
}

// Крупный центр — только что собранный слайд (обновляет glassSteps через jump).
function gloShowSlide(n) {
  const ifr = byId("gloSlide");
  if (!ifr || !n) return;
  gloShown = n;
  gloHoldUntil = Date.now() + GLO_HOLD_MS;
  ifr.classList.remove("hidden");
  byId("gloSkel")?.classList.add("hidden");
  ifr.src = U(`/api/jobs/${sessionId}/deck?t=${Date.now()}&editor=1&slide=${n}`);
  const cap = byId("gloCap");
  if (cap) {
    const s = (draftPlan.slides || [])[n - 1];
    cap.textContent = `${n}. ${glassSlideLabel(s || {}, n)} — только что заполнен`;
  }
}

// Подглядывание (вопрос Глеба 2026-08-21): во время заполнения можно кликнуть
// по готовой ячейке ленты и рассмотреть собранный слайд в центре. Едет тем же
// hold-механизмом, что и «только что заполнен», только дольше: по истечении
// фокус сам вернётся на скелет текущего слайда — сборка не прерывается.
const GLO_PEEK_MS = 8000;
function gloPeek(n) {
  if (glassResolveOn) return;     // на этапе вопросов центр занят карточками
  const s = (draftPlan.slides || [])[n - 1];
  const ifr = byId("gloSlide");
  if (!s || !s.filled || !ifr || !n) return;
  gloShown = n;
  gloHoldUntil = Date.now() + GLO_PEEK_MS;
  ifr.classList.remove("hidden");
  byId("gloSkel")?.classList.add("hidden");
  // Свежий t, как в gloShowSlide: повторный peek того же слайда со старым deckT
  // отдавал бы кадр из кэша браузера — рендер до последних заполнений.
  ifr.src = U(`/api/jobs/${sessionId}/deck?t=${Date.now()}&editor=1&slide=${n}`);
  const cap = byId("gloCap");
  if (cap) cap.textContent =
    `${n}. ${glassSlideLabel(s, n)} — уже собран, заполнение продолжается`;
}

// Лента слайдов: ✓ живой мини-рендер, ⟳ + таймер, ? вопрос, честный скелет с
// темой раздела из плана. Никакого фейкового контента в заглушках.
function renderGloFilm() {
  const film = byId("gloFilm");
  if (!film) return;
  const sl = draftPlan.slides || [];
  if (film.childElementCount !== sl.length) {
    film.innerHTML = "";
    Object.keys(gloFilmFilled).forEach((k) => delete gloFilmFilled[k]);
    for (let i = 0; i < sl.length; i++) {
      const c = document.createElement("div");
      c.dataset.index = i + 1;
      film.appendChild(c);
    }
  }
  const filling = glassLooping ? (glassCurrentTarget(draftPlan) || {}).index : null;
  [...film.children].forEach((c, i) => {
    const s = sl[i], n = i + 1;
    const state = !s ? "queued"
      : s.filled && s.status !== "failed" ? "ready"
      : s.status === "failed" ? "failed"
      : s.status === "needs_input" ? "quest"
      : filling === n ? "filling" : "queued";
    if (state === "ready") {
      if (gloFilmFilled[n]) return;          // уже живой рендер — не дёргаем
      gloFilmFilled[n] = true;
      c.className = "glo-cell glo-cell--ready";
      c.innerHTML = "";
      const f = document.createElement("iframe");
      f.loading = "lazy"; f.tabIndex = -1; f.setAttribute("aria-hidden", "true");
      f.title = `Слайд ${n}`;
      f.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1&slide=${n}`);
      c.appendChild(f);
      return;
    }
    delete gloFilmFilled[n];
    c.className = "glo-cell glo-cell--" + state;
    c.innerHTML = "";
    const mark = document.createElement("span");
    mark.className = "glo-cell__mark";
    mark.textContent = state === "filling" ? "⟳"
      : state === "quest" ? "?" : state === "failed" ? "!" : "";
    const lbl = document.createElement("span");
    lbl.className = "glo-cell__label";
    lbl.textContent = glassSlideLabel(s || {}, n);
    c.append(mark, lbl);
  });
}

// Хвост документа сверх потолка. Раньше notice лишь сообщал о потере и советовал
// «соберите их отдельной декой» — как именно, автор придумывал сам и обычно резал
// исходник руками. Исходник уже лежит в сессии, номер первого невзятого раздела —
// в плане: кнопка заводит вторую деку ровно оттуда.
let glassRestBusy = false;
let glassRestLink = "";     // адрес второй деки после успешного запуска

function renderGlassRest() {
  const box = byId("glassRest");
  const btn = byId("glassRestBtn");
  const note = byId("glassRestNote");
  if (!box || !btn) return;
  const rest = draftPlan.rest || 0;
  box.classList.toggle("hidden", !rest);
  if (!rest) return;
  btn.classList.toggle("hidden", !!glassRestLink);
  btn.disabled = glassRestBusy;
  btn.textContent = glassRestBusy
    ? "Раскладываю оставшиеся разделы…"
    : `Собрать оставшиеся ${rest} ` +
      `${plural(rest, "раздел", "раздела", "разделов")} отдельной декой`;
  if (note && !glassRestBusy) note.classList.toggle("hidden", !glassRestLink);
  if (note && glassRestLink) {
    note.textContent = "Вторая дека готова к сборке: ";  // сброс прошлого текста
    const a = document.createElement("a");
    a.href = glassRestLink;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "открыть в новой вкладке";
    note.append(a, ". Эта дека остаётся как есть.");
  }
}

async function startGlassRest() {
  if (glassRestBusy || glassRestLink) return;
  const note = byId("glassRestNote");
  glassRestBusy = true;
  renderGlassRest();
  try {
    const r = await glassFetch(U(`/api/drafts/${sessionId}/glass/rest`),
                               { method: "POST" });
    if (!r.ok) {
      let detail = "";
      try { detail = JSON.parse(await r.text()).detail; } catch (e) { detail = ""; }
      throw new Error(detail || "не удалось собрать оставшиеся разделы");
    }
    const out = await r.json();
    // Новую вкладку открываем не сами: window.open после await блокируется
    // браузером как всплывающее окно — даём ссылку, её жмёт автор.
    glassRestLink = U(`/editor?session=${out.session_id}&mode=manual&glass=1`);
  } catch (e) {
    if (note) {
      note.textContent = e && e.message ? e.message : String(e);
      note.classList.remove("hidden");
    }
  } finally {
    glassRestBusy = false;
    renderGlassRest();
  }
}

function glassFail() {
  byId("glassRetry")?.classList.remove("hidden");
  byId("glassDone")?.classList.remove("hidden");
  // 3 осечки подряд на оверлее: русская ошибка + «Повторить» (спека, секция 3).
  const err = byId("gloErr");
  if (err && glassOverlayOn) {
    err.classList.remove("hidden");
    err.textContent = "Сборка прервалась: три шага подряд не удались — " +
      "проверьте соединение. ";
    const b = document.createElement("button");
    b.className = "btn btn-sm"; b.type = "button"; b.textContent = "Повторить";
    b.onclick = () => { err.classList.add("hidden"); glassLoop(); glassScout(); };
    err.appendChild(b);
  }
}

// Карточки вопросов живут между перерисовками (в textarea печатают!):
// добавляем новые, убираем отвеченные, остальные не трогаем.
function renderGlassQuestions(open, failed) {
  const box = byId("gloCards");
  if (!box) return;
  const shown = open.concat(failed || []).sort((a, b) => a - b);
  Object.keys(glassCards).forEach((k) => {
    const s = draftPlan.slides[+k - 1];
    // Карточки привязаны к номеру слайда, а номера «плывут» при удалении и
    // перестановке: карточка на чужой брифинг спрашивала бы про другой раздел.
    const stale = !shown.includes(+k) || !s ||
      glassCards[k].dataset.brief !== (s.brief || "") ||
      glassCards[k].dataset.status !== (s.status || "");
    if (stale) { glassCards[k].remove(); delete glassCards[k]; }
  });
  shown.forEach((idx) => {
    // Уже созданную карточку не перерисовываем: в textarea печатают!
    if (glassCards[idx]) return;
    const card = makeGlassCard(idx);
    // Порядок карточек = порядок слайдов: разбор читается вместе с лентой.
    const after = shown.filter((i) => i < idx).length;
    box.insertBefore(card, box.children[after] || null);
    glassCards[idx] = card;
    card._syncExcerpt?.();      // обрезку фрагмента видно только после вставки
  });
}

// Фрагмент документа, который ИИ собирается положить на слайд. Без него автор
// видел только номер слайда и общий вопрос — «непонятно, о какой информации
// вообще речь», и решение принять было нельзя.
function makeGlassCard(idx) {
  const s = draftPlan.slides[idx - 1] || {};
  const broke = s.status === "failed";
  const card = document.createElement("div");
  card.className = "glass-q" + (broke ? " glass-q--failed" : "");
  card.dataset.brief = s.brief || "";
  card.dataset.status = s.status || "";
  const head = document.createElement("div");
  head.className = "glass-q__head";
  const num = document.createElement("span");
  num.className = "glass-q__num";
  num.textContent = broke ? `Слайд ${idx} — не заполнился` : `Слайд ${idx}`;
  head.appendChild(num);
  card.appendChild(head);
  // О чём вопрос: в карточке виден только номер, поэтому добавляем тему
  // раздела — без неё автор отвечает вслепую. briefDisplay вырезает
  // пометки парсера «[картинка: …]» — человеку они шум (данные не трогаем:
  // в dataset.brief и в ответ ИИ уходит полный бриф).
  const lines = briefDisplay(s.brief).split("\n");
  const topic = (lines[0] || "").trim();
  if (topic) {
    const t = document.createElement("div");
    t.className = "glass-q__topic";
    t.textContent = topic.length > 90 ? topic.slice(0, 89) + "…" : topic;
    card.appendChild(t);
  }
  const body = lines.slice(1).join("\n").trim();
  if (body) {
    const ex = document.createElement("div");
    ex.className = "glass-q__excerpt";
    ex.textContent = body;
    card.appendChild(ex);
    const more = document.createElement("button");
    more.type = "button";
    more.className = "glass-q__more hidden";
    more.textContent = "Показать фрагмент целиком";
    more.onclick = () => {
      const open = !ex.classList.contains("is-open");
      ex.classList.toggle("is-open", open);
      more.textContent = open ? "Свернуть фрагмент"
                              : "Показать фрагмент целиком";
    };
    card.appendChild(more);
    // Обрезку меряем, а не угадываем по длине строки: панель узкая, и один и
    // тот же фрагмент то влезает в пять строк, то нет. Меряется только в
    // ВИДИМОМ свёрнутом DOM: карточки создаются, пока панель «Вопросы» ещё
    // скрыта (display:none → высоты нулевые), и разовый замер при вставке
    // навсегда прятал кнопку — перемер идёт из renderGlassPanel после показа.
    card._syncExcerpt = () => {
      if (ex.classList.contains("is-open")) return; // раскрыт — кнопка «Свернуть» нужна
      if (!ex.clientHeight) return;                 // панель скрыта — мерить нечего
      more.classList.toggle("hidden", ex.scrollHeight <= ex.clientHeight + 2);
    };
  }
  const q = document.createElement("p");
  q.className = "glass-q__text";
  q.textContent = s.question || "Какой макет выбрать для этого слайда?";
  card.appendChild(q);

  // Чипы-кандидаты: живое превью макета (тот же рендер, что боевой слайд).
  const chips = document.createElement("div");
  chips.className = "glass-chips";
  chips.setAttribute("role", "radiogroup");
  let picked = null;
  let pickedKind = null;      // тип схемы из мастера — едет ДАННЫМИ, не текстом
  // Отклик на выбор: раньше единственным сигналом была 1px рамка канона, и на
  // широкой тёмной плашке она не читалась — казалось, что клик не срабатывает.
  const pickLabel = (tid, kind) => {
    const name = (tplOf(tid) || {}).display_name || tid;
    const k = kind ? (dgmType(kind) || {}).display_name || kind : "";
    return k ? `${name}: ${k}` : name;
  };
  const syncPick = () => {
    chips.querySelectorAll(".glass-chip").forEach((c) => {
      const on = c.dataset.tid === picked;
      c.classList.toggle("is-picked", on);
      c.setAttribute("aria-checked", on ? "true" : "false");
    });
    apply.disabled = !picked && !ta.value.trim();
    apply.textContent = picked
      ? `Применить: ${pickLabel(picked, pickedKind)}`
      : "Ответить";
  };
  const addChip = (tid, kind) => {
    const was = chips.querySelector(`.glass-chip[data-tid="${CSS.escape(tid)}"]`);
    if (was) {
      // Тот же макет с другим типом схемы — не второй чип, а обновление первого:
      // иначе в ряду копились одинаковые «Схема», и было не видно, какая выбрана.
      if (kind) {
        was.dataset.kind = kind;
        was.querySelector(".glass-chip__name").textContent = pickLabel(tid, kind);
        const f = was.querySelector("iframe");
        if (f) f.src = U(`/api/diagrams/${kind}/preview?static=1`);
      }
      return;
    }
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "glass-chip";
    chip.dataset.tid = tid;
    if (kind) chip.dataset.kind = kind;
    chip.setAttribute("role", "radio");
    chip.setAttribute("aria-checked", "false");
    const prev = document.createElement("span");
    prev.className = "glass-chip__prev";
    const ifr = document.createElement("iframe");
    ifr.loading = "lazy";
    ifr.tabIndex = -1;
    ifr.setAttribute("aria-hidden", "true");
    ifr.src = kind ? U(`/api/diagrams/${kind}/preview?static=1`)
                   : U(`/api/templates/${tid}/preview?static=1`);
    prev.appendChild(ifr);
    chip.appendChild(prev);
    const meta = document.createElement("span");
    meta.className = "glass-chip__meta";
    const row = document.createElement("span");
    row.className = "glass-chip__row";
    const name = document.createElement("span");
    name.className = "glass-chip__name";
    name.dataset.tid = tid;
    name.textContent = pickLabel(tid, kind);
    row.appendChild(name);
    if (tid === s.template_id) {           // что стоит сейчас — иначе выбор вслепую
      const cur = document.createElement("i");
      cur.className = "glass-chip__cur";
      cur.textContent = "сейчас";
      row.appendChild(cur);
    }
    meta.appendChild(row);
    const gist = document.createElement("span");
    gist.className = "glass-chip__gist";
    gist.dataset.tid = tid;
    gist.textContent = tplGist(tid);
    meta.appendChild(gist);
    chip.appendChild(meta);
    const mark = document.createElement("span");
    mark.className = "glass-chip__mark";
    mark.setAttribute("aria-hidden", "true");
    chip.appendChild(mark);
    chip.onclick = () => {
      picked = picked === tid ? null : tid; // повторный клик снимает выбор
      pickedKind = picked ? chip.dataset.kind || null : null;
      syncPick();
    };
    chips.appendChild(chip);
  };
  (s.candidates || []).forEach((tid) => addChip(tid));
  card.appendChild(chips);

  // Кандидаты ИИ — подсказка, а не потолок: вся библиотека макетов должна быть
  // в руках автора прямо из карточки (иначе выбор из одного чипа = не выбор).
  const other = document.createElement("button");
  other.type = "button";
  other.className = "glass-q__other";
  other.textContent = "Выбрать из всех макетов…";
  // Тип схемы из мастера уходит отдельным полем ответа (kind), а не строчкой
  // «Тип схемы: Воронка» в уточнении: текстом это была НЕОБЯЗАТЕЛЬНАЯ подсказка,
  // и модель спокойно рисовала другой тип — «выбрал один макет, применился другой».
  other.onclick = () => openPicker((tid, kind) => {
    addChip(tid, kind);
    picked = tid;
    pickedKind = kind || null;
    syncPick();
  });
  card.appendChild(other);

  const ta = document.createElement("textarea");
  ta.className = "glass-q__input";
  ta.placeholder = "Уточнение для ИИ (необязательно)";
  ta.oninput = () => syncPick();
  card.appendChild(ta);

  const row = document.createElement("div");
  row.className = "glass-q__actions";
  const apply = document.createElement("button");
  apply.type = "button";
  apply.className = "btn btn-accent btn-sm";
  apply.textContent = "Ответить";
  const skip = document.createElement("button");
  skip.type = "button";
  skip.className = "btn btn-ghost btn-sm";
  skip.textContent = "На усмотрение ИИ";
  const send = (tid, kind, msg) => {
    apply.disabled = skip.disabled = true;
    // Ответ мгновенный (сервер лишь записывает выбор в план — заполняет слайд
    // конвейер шагов), поэтому идёт МИМО очереди шагов: раньше ответ ждал
    // текущего шага и держал HTTP всю дорогу до модели — на деградировавшем
    // провайдере карточка «висела» минуты и обрывалась ложной ошибкой сети.
    apply.textContent = "Записываю ответ…";
    glassAnswer(idx, tid, msg, kind).catch(() => {
      skip.disabled = false;
      syncPick();                 // вернуть подпись и доступность по выбору
      setSaveStatus("error");
    });
  };
  apply.onclick = () => send(picked, pickedKind, ta.value.trim());
  skip.onclick = () => send(null, null, "");
  row.appendChild(apply);
  row.appendChild(skip);
  card.appendChild(row);
  syncPick();       // «Ответить» заперт, пока нечего отвечать: пустой ответ = «На усмотрение ИИ»
  return card;
}

// Ответ мгновенный: сервер записывает выбор в план и снимает вопрос, слайд
// заполняет конвейер шагов. resume=false — для glassAnswerAll: цепочка ответов
// перезапускает петлю ОДИН раз в конце, иначе первый же перезапуск занимал бы
// сервер шагом и следующие ответы ждали его.
async function glassAnswer(idx, templateId, message, kind, resume = true) {
  const body = { index: idx };
  if (templateId) body.template_id = templateId;
  if (kind) body.kind = kind;
  if (message) body.message = message;
  const r = await glassFetch(U(`/api/drafts/${sessionId}/glass/answer`), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  // 409 — вопрос уже закрыт (ответили с другой вкладки, слайд заполнился сам):
  // карточка просто устарела, повторять ответ нечего. Подтягиваем план и
  // убираем её, вместо того чтобы показывать «ошибка».
  if (r.status === 409) { await fetchPlan(); if (resume) glassResume(); return; }
  if (!r.ok) throw new Error(await r.text());
  const out = await r.json();
  if (out.plan) draftPlan = out.plan;
  loadDeck();
  if (resume) {
    pendingGoTo = idx - 1;      // показать слайд, который сейчас заполнится
    glassResume();
  }
}

// Выход из паузы одним движением: закрыть ВСЕ вопросы выбором ИИ. Ответы идут
// по одному (параллельные записи в plan.json теряли бы друг друга), они
// мгновенные; заполняет слайды перезапущенная в конце петля шагов.
async function glassAnswerAll() {
  const btn = byId("glassAuto");
  if (btn) { btn.disabled = true; btn.textContent = "Записываю ответы…"; }
  try {
    for (;;) {
      const open = openGlassQuestions();
      if (!open.length) break;
      await glassAnswer(open[0], null, "", null, false);
    }
  } catch (e) {
    setSaveStatus("error");
  } finally {
    if (btn) btn.disabled = false;
    glassResume();
  }
}

// Ответ закрыл вопрос — у степпера могла появиться работа. Петля одна
// (glassLooping), так что два ответа подряд не запустят две сборки.
function glassResume() {
  if (glassLoopDone) {
    renderGlassPanel(null);
    // Степпер уже финишировал, вопросов и осечек больше нет → в редактор.
    // Незабранный хвост держит панель, как и в glassSteps: уведомление о
    // потере и кнопка «собрать остальное» живут только здесь.
    if (!openGlassQuestions().length && !glassFailedSlides().length &&
        !(draftPlan.rest || 0)) exitGlassMode();
    return;
  }
  // Петлю — ДО перерисовки: glassLooping ставится до первого await, и статус
  // сразу «заполняю», а не застывшее «ждёт вашего решения» на всё время шага
  // (живой прогон 2026-08-20: после ответа панель минуту врала, что ждёт).
  glassLoop();
  renderGlassPanel(null);
}

function exitGlassMode() {
  glassRunning = false;
  glassOverlayOn = false;
  glassTickStop();
  document.title = glassBaseTitle;
  byId("glassOverlay")?.classList.add("hidden");
  const badge = byId("modeBadge");
  if (badge) badge.textContent = "Конструктор";
  setupPanelTabs();      // вернуть обычную правую панель («Поля» по умолчанию)
  builtFormFor = -1;
  renderBuilderForm();
  syncImproveButton();   // сборка кончилась — точечное улучшение доступно
  syncGlassResumeBtn();  // сборка остановлена автором → в тулбаре «Продолжить»
  // Точечный refresh во время сборки идёт через pendingGoTo (jump), но слайд,
  // заполненный без jump (поздний сплайс оборванного шага, ответ, разведчик),
  // оставался в ленте скелетом «Заголовок слайда» до F5 — на выходе пересобираем
  // ленту целиком: src и подписи всех миниатюр становятся честными разом.
  thumbsDirty = true;
  loadDeck();            // перерисовать превью уже с contenteditable
  // F5 после выхода — обычный конструктор, а не перезапуск степпера
  const url = new URL(location.href);
  url.searchParams.delete("glass");
  history.replaceState(null, "", url);
}

// «Остановить сборку» (запрос Глеба 2026-08-21): ПОЛНАЯ остановка процесса и
// запросов к LLM. Три рубежа: (1) glassRunning=false + abort всех живых
// запросов — эта вкладка больше не ходит к серверу; (2) paused в плане —
// серверные гарды step/score не пускают к модели НИКОГО (вторая вкладка, F5);
// (3) флаг переживает перезагрузку — авто-возобновление по состоянию плана
// (см. init) паузу уважает. Уже улетевший в модель шаг сервер доработает
// (HTTP до провайдера не отзывается) — его результат оплачен и доклеится.
async function stopGlassBuild() {
  const ok = await confirmDialog(
    "Остановить сборку? Новых запросов к ИИ не будет, заполненные слайды " +
    "сохранятся. Вернуться можно кнопкой «Продолжить сборку» в редакторе.",
    "Остановить", "Не останавливать");
  if (!ok) return;
  glassRunning = false;
  glassAbortAll();           // петля и разведчик увидят !glassRunning и выйдут
  if (glassRetryTimer) { clearTimeout(glassRetryTimer); glassRetryTimer = null; }
  draftPlan.paused = true;   // оптимистично: кнопка «Продолжить» нужна сразу
  try {
    const r = await fetch(U(`/api/drafts/${sessionId}/glass/stop`),
                          { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
  } catch (e) {
    // Флаг на сервер не доехал (сеть): клиентские запросы уже оборваны, автор
    // выходит в редактор в любом случае; F5 в этом случае возобновит сборку.
    console.warn("glass stop:", e);
  }
  exitGlassMode();
}

// Тулбарная кнопка «Продолжить сборку»: видна только на остановленном автором
// черновике, где ещё есть что заполнять (или незабранный хвост документа).
function syncGlassResumeBtn() {
  const b = byId("glassResume");
  if (!b) return;
  const show = mode === "manual" && !glassRunning && !!(draftPlan || {}).paused &&
    (hasUnfinishedOutline() || !!(draftPlan || {}).rest);
  b.classList.toggle("hidden", !show);
}

async function resumeGlassBuild() {
  const b = byId("glassResume");
  if (b) b.disabled = true;
  try {
    const r = await fetch(U(`/api/drafts/${sessionId}/glass/resume`),
                          { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
  } catch (e) {
    if (b) b.disabled = false;
    alertDialog("Не удалось возобновить сборку — проверьте интернет и попробуйте ещё раз.");
    return;
  }
  if (b) b.disabled = false;
  draftPlan.paused = false;
  glassLoopDone = false;     // после паузы петля начинает заново
  startGlassMode();
}
byId("glassResume")?.addEventListener("click", resumeGlassBuild);

/* init */
// Корень навигации в шапке канона — абсолютный от домена (так велит контракт со
// шлюзом), а вот ссылки ВНУТРИ приложения обязаны нести префикс. Прежний тулбар
// с «a.home» снят, но «На главную» осталось на экране сборки.
const gloHome = byId("gloHome");
if (gloHome) gloHome.href = U("/");

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

// В glass-режиме табы не показываем на старте — их вернёт exitGlassMode().
if (!isGlass) setupPanelTabs(); // К§8 — правая панель с табами (сам решает по mode)
disableChatEditing(); // сразу гасим чат-контролы, чтобы не мелькали активными

// Повторно после инициализации: в chat-режиме setupChatMode() возвращает чату
// активный вид — .finally перекрывает его обратно в «в разработке» (и на ошибке init).
/* Аутлайн, который ещё не дозаполнен: слайд знает свою тему, но пуст. Ровно этот
   признак сервер считает «незавершённой сборкой» (glass.unfinished_outlines).
   Чат-режим сюда не попадает: там аутлайн доводит кнопка «Заполнить слайды». */
function hasUnfinishedOutline() {
  return mode === "manual" && !!draftPlan && Array.isArray(draftPlan.slides)
    && draftPlan.slides.some((s) => s.brief && !s.filled);
}

if (isDraft) {
  initDraftBuilder().then(initEditor)
    .then(() => {
      // «Продолжить» в списке проектов ведёт на /editor?session=…&mode=manual —
      // БЕЗ ?glass=1. Раньше это открывало мёртвый черновик: «?» на миниатюре
      // есть, слайды пустые, а степпера нет и дозаполнить нечем. Решаем по плану,
      // а не по адресу: сборка продолжается с любого входа, включая закладку.
      // Незабранный хвост открывает панель на тех же правах: сборка кончилась,
      // но предложение собрать остаток ещё в силе, и жить оно должно дольше
      // одной вкладки. Забранный хвост сервер обнуляет — панель больше не лезет.
      if (!isGlass && (hasUnfinishedOutline() || (draftPlan || {}).rest))
        isGlass = true;
      // «Остановить сборку» переживает F5 и «Продолжить» из истории: план на
      // паузе не возобновляется молча — в тулбаре ждёт «Продолжить сборку».
      if ((draftPlan || {}).paused) isGlass = false;
      if (isGlass) startGlassMode();
      else syncGlassResumeBtn();
    })
    .finally(disableChatEditing);
}
else { Promise.resolve(initEditor()).finally(disableChatEditing); }
