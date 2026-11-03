/* Экран 05 · Просмотр готовой деки (борд 4823:2002).

   Отдельный файл, а не режим editor.js, и причина — вес. editor.js это пять с
   лишним тысяч строк: планы черновика, пикер макетов, чат-агент, отмена,
   редактор схем, автосохранение, проверка на пример-текст перед экспортом.
   Пока «Посмотреть» вело в /editor, всё это грузилось и поднималось ради
   пролистывания двенадцати слайдов. Здесь нужно ровно четыре вещи: показать
   деку, листать её, отдать файл и увести в редактор.

   Второй довод — правки. Редактор делает слайды редактируемыми
   (contenteditable) и сохраняет правку по уходу из блока. Просмотр обязан
   открываться в режиме «не трону»: человек пришёл смотреть и вправе кликать
   по тексту, не рискуя его изменить. Отсюда и &editor=1 у адреса деки —
   флаг покоя (см. GET /api/jobs/{sid}/deck), а не «режим редактора». */
"use strict";

const PREFIX = window.__APP_PREFIX__ || "";
const U = (p) => PREFIX + p;
const byId = (id) => document.getElementById(id);

const sessionId = new URLSearchParams(location.search).get("session") || "";
const frame = byId("deck");

let slides = [];        // .slide из документа деки
let current = 0;

// Единый cache-bust на страницу: деку и все кадры ленты берём одним и тем же
// снимком, иначе лента могла бы показать слайд, которого на канве уже нет.
const deckT = Date.now();

/* ---- адреса и выходы ------------------------------------------------------
   Дека на просмотре не меняется, поэтому и возвращаться после правки некуда:
   «Редактировать» ведёт в редактор той же сессии обычной ссылкой, а не через
   history. Открыть деку без session нечем — тогда показываем это прямо, а не
   пустым чёрным кадром. */
byId("backHome").href = U("/");
byId("toEditor").href = U(`/editor?session=${encodeURIComponent(sessionId)}`);
byId("html").href = U(`/api/jobs/${sessionId}/deck?download=1`);

/* ---- шапка ----------------------------------------------------------------
   Дату сборки пишем по-русски, без года: год в подписи к работе, живущей
   сутки, — лишнее слово. «Собрана», а не «создана»: создают черновик, а здесь
   уже файл. */
const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                "августа", "сентября", "октября", "ноября", "декабря"];
function builtLine(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `Собрана ${d.getDate()} ${MONTHS[d.getMonth()]}, ${hh}:${mm}`;
}

/* Имя деки берём из <title> загруженного документа — ровно тем же способом,
   что и редактор (syncDeckTitle). Второй источник имени (поле в ответе API)
   разошёлся бы с первым после любой правки деки в редакторе, и просмотр
   показывал бы старое название поверх нового содержимого.
   «Презентация» — заглушка сборщика, а не имя: узел остаётся пустым и
   схлопывается (.stage__title:empty), выдуманная подпись хуже отсутствующей. */
function syncDeckTitle(doc) {
  let name = ((doc && doc.title) || "").trim();
  if (name === "Презентация") name = "";
  byId("deckTitle").textContent = name;
}

/* ---- листание -------------------------------------------------------------
   Слайд меняет сама дека (win.deck.goTo) — своего способа показать слайд у
   страницы нет и быть не должно: тогда просмотр и редактор разошлись бы в том,
   что считается «текущим слайдом». */
function goTo(i) {
  if (!slides.length) return;
  current = Math.max(0, Math.min(slides.length - 1, i));
  const win = frame.contentWindow;
  if (win && win.deck && win.deck.goTo) win.deck.goTo(current);
  [...document.querySelectorAll(".film")].forEach((f, idx) => {
    const on = idx === current;
    f.classList.toggle("active", on);
    f.setAttribute("aria-selected", on ? "true" : "false");
    f.tabIndex = on ? 0 : -1;       // ряд — один таб-стоп, внутри стрелки
    if (on) f.scrollIntoView({ block: "nearest", inline: "nearest" });
  });
  syncPager();
}

// Края не зацикливаем, краевой кирпич гаснет — как в редакторе: «следующий»
// после последнего слайда не бывает, и подсунуть вместо него первый значит
// соврать про конец презентации.
function syncPager() {
  const nav = byId("pager");
  const n = slides.length;
  nav.classList.toggle("hidden", n < 2);
  if (n < 2) return;
  byId("pagerNum").textContent = `${current + 1} / ${n}`;
  byId("prevSlide").disabled = current <= 0;
  byId("nextSlide").disabled = current >= n - 1;
}

byId("prevSlide").addEventListener("click", () => goTo(current - 1));
byId("nextSlide").addEventListener("click", () => goTo(current + 1));

// Стрелки с самой страницы. Внутри кадра деки свой обработчик (deck.js), этот
// нужен, когда фокус на ленте или на кирпичах. Модификаторы не трогаем —
// Cmd+← это «назад» браузера, и перехватывать его нельзя.
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

/* ---- лента кадров ---------------------------------------------------------
   Кадр — настоящий слайд в iframe, как и миниатюры рельса в редакторе: второго
   способа отрисовать слайд в сервисе нет, и заводить его нельзя. Каждый кадр
   грузит ОДИН слайд (&slide=N), а не всю деку: двенадцать полных дек в
   двенадцати iframe — это двенадцать прогонов deck.js на страницу.

   Кнопка, а не div: кадр — орган, по которому кликают, и клавиатуре он обязан
   отвечать сам, без наших обработчиков клавиш. */
function buildFilmstrip() {
  const box = byId("filmstrip");
  box.textContent = "";
  slides.forEach((s, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "film";
    b.dataset.index = i;
    b.setAttribute("role", "tab");
    // Подписи на кадре нет, поэтому имя целиком живёт здесь. Название слайда
    // добавляем, если оно есть: «Слайд 3» и «Слайд 3 · Выручка» — разный ответ
    // на вопрос «куда я попаду», и второй лучше.
    const t = (s.querySelector("h1,h2,h3,[data-slot=title]")?.textContent || "").trim();
    b.setAttribute("aria-label", t ? `Слайд ${i + 1} · ${t}` : `Слайд ${i + 1}`);
    const ifr = document.createElement("iframe");
    ifr.loading = "lazy";
    ifr.tabIndex = -1;
    ifr.setAttribute("aria-hidden", "true");
    ifr.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1&slide=${i + 1}`);
    b.appendChild(ifr);
    b.addEventListener("click", () => goTo(Number(b.dataset.index)));
    box.appendChild(b);
  });
}

/* ---- полоска хвоста -------------------------------------------------------
   Текст обеих строк — в errtext.js (tailLine/tailButton): там он проверяется
   юнит-тестами, а здесь осталась только проводка.

   Кнопка заводит ВТОРУЮ деку и уводит в неё новой вкладкой: первая остаётся
   как есть. Открываем ссылкой, которую жмёт человек, а не window.open после
   await — тот браузер режет как всплывающее окно. */
function renderTail(tail) {
  const strip = byId("tailStrip");
  const line = (window.tailLine || (() => ""))(tail);
  const label = (window.tailButton || (() => ""))(tail);
  if (!line || !label) return;        // хвоста нет — полоски нет
  byId("tailText").textContent = line;
  const btn = byId("tailGo");
  btn.textContent = label;
  strip.classList.remove("hidden");

  let busy = false;
  btn.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    btn.disabled = true;
    btn.textContent = "Раскладываю…";
    try {
      const r = await fetch(U(`/api/drafts/${sessionId}/glass/rest`),
                            { method: "POST" });
      if (!r.ok) {
        let detail = "";
        try { detail = JSON.parse(await r.text()).detail; } catch (e) { detail = ""; }
        throw new Error(detail || "не удалось собрать оставшиеся разделы");
      }
      const out = await r.json();
      // Полоска сделала свою работу: хвост забран, предлагать его второй раз
      // нельзя. На её месте — ссылка во вторую деку.
      btn.remove();
      const a = document.createElement("a");
      a.className = "btn-link";
      a.href = U(`/editor?session=${out.session_id}&mode=manual&glass=1`);
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "открыть вторую деку в новой вкладке";
      byId("tailText").textContent = "Вторая дека готова к сборке: ";
      byId("tailText").appendChild(a);
    } catch (e) {
      busy = false;
      btn.disabled = false;
      btn.textContent = label;
      byId("tailText").textContent = (e && e.message) || String(e);
    }
  });
}

/* ---- меню «Скачать» -------------------------------------------------------
   Разметка и цикл те же, что в редакторе (POST ставит рендер → опрос {state}
   раз в полторы секунды → готовый файл уезжает скрытым <a download>), но без
   двух редакторских шагов: сохранения деки перед экспортом и проверки на
   пример-текст. Оба про НЕСОХРАНЁННЫЕ правки, которых на просмотре не бывает.

   Меню не закрывается по клику внутри: строка «Готовлю PPTX…» — это отчёт о
   работе, и закрыть меню значило бы его спрятать. */
const EXPORT_LABEL = {
  pptx: { idle: "PowerPoint (.pptx)", busy: "Готовлю PPTX… ~10–20 сек" },
  png: { idle: "Картинки PNG (.zip)", busy: "Готовлю PNG… ~10–20 сек" },
};

function triggerDownload(fmt) {
  const a = document.createElement("a");
  a.href = U(`/api/jobs/${sessionId}/export/${fmt}/file`);
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
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
    btn.textContent = L.idle;
  };

  async function poll() {
    let r;
    try {
      r = await fetch(U(`/api/jobs/${sessionId}/export/${fmt}`));
    } catch (e) { return; }           // моргнула сеть — продолжаем опрос
    if (!r.ok) { toIdle("Ошибка — повторить"); return; }
    const { state } = await r.json();
    if (state === "ready") {
      // Качаем только по завершению СВОЕГО рендера: опрос мог застать готовым
      // файл, сделанный в прошлый заход, и тот уехал бы без спроса.
      const wasBusy = btn.classList.contains("is-busy");
      toReady();
      if (wasBusy) triggerDownload(fmt);
    } else if (state === "error") toIdle("Ошибка — повторить");
  }

  btn.addEventListener("click", async () => {
    if (btn.classList.contains("is-ready")) { triggerDownload(fmt); return; }
    btn.disabled = true;
    btn.classList.add("is-busy");
    btn.textContent = L.busy;
    let r;
    try {
      r = await fetch(U(`/api/jobs/${sessionId}/export/${fmt}`), { method: "POST" });
    } catch (e) { toIdle("Ошибка — повторить"); return; }
    if (!r.ok) { toIdle("Ошибка — повторить"); return; }
    poller = setInterval(poll, 1500);
    poll();
  });
}
document.querySelectorAll("[data-fmt]").forEach(setupExport);

(function initDownloadMenu() {
  const wrap = byId("dl"), btn = byId("dlBtn"), menu = byId("dlMenu");

  // Клик по самой деке до документа страницы не доходит: слайд живёт в iframe,
  // и события мыши остаются внутри него. Половина экрана под меню — это именно
  // дека, так что без этого меню не закрывалось бы по клику по слайду.
  function frameDoc() {
    try { return frame.contentDocument; } catch (e) { return null; }
  }
  function onFrameDown() { close(); }
  function close() {
    menu.classList.add("hidden");
    btn.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onOutside, true);
    document.removeEventListener("keydown", onEsc, true);
    const d = frameDoc();
    if (d) d.removeEventListener("pointerdown", onFrameDown, true);
  }
  function onOutside(e) { if (!wrap.contains(e.target)) close(); }
  function onEsc(e) {
    if (e.key !== "Escape") return;
    close();
    btn.focus();
  }
  function open() {
    menu.classList.remove("hidden");
    btn.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onOutside, true);
    document.addEventListener("keydown", onEsc, true);
    const d = frameDoc();
    if (d) d.addEventListener("pointerdown", onFrameDown, true);
  }
  btn.addEventListener("click", () => {
    if (menu.classList.contains("hidden")) open(); else close();
  });
  byId("html").addEventListener("click", close);
})();

/* ---- загрузка -------------------------------------------------------------
   Кадр и строка работы грузятся параллельно и независимо: имя деки живёт в
   кадре, дата сборки — в ответе API, и ждать одно ради другого незачем. */
frame.addEventListener("load", () => {
  let doc = null;
  try { doc = frame.contentDocument; } catch (e) { doc = null; }
  if (!doc) return;
  slides = [...doc.querySelectorAll(".slide")];
  syncDeckTitle(doc);
  buildFilmstrip();
  goTo(0);
});

async function boot() {
  if (!sessionId) {
    byId("deckMeta").textContent = "Не указана презентация";
    return;
  }
  frame.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1`);
  let info;
  try {
    const r = await fetch(U(`/api/jobs/${sessionId}/view`));
    if (!r.ok) return;                // деку уже грузим — строка не критична
    info = await r.json();
  } catch (e) { return; }
  byId("deckMeta").textContent = builtLine(info.finished_at);
  renderTail(info.tail);
}
boot();
