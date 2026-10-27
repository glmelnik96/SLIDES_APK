/* Cloud.ru deck — deck.js: автоскейл, навигация, deep-link, прогресс. Без зависимостей. */
(function () {
  "use strict";

  var slides = [];
  var current = -1;
  var progressEl = null;

  function clamp(n) {
    return Math.max(0, Math.min(slides.length - 1, n));
  }

  /* E3: счётчик больших цифр. При активации слайда «большие числа» (.js-count)
     считаются от малого к финалу. Финальная строка — ТОЧНО исходная (сервер).
     Скриншот на 900мс ловит финал; reduced-motion → финал мгновенно. */
  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Разобрать строку на: префикс, число (нормализованное), суффикс.
     Поддержка: рус. десятичная запятая, пробелы-разделители тысяч, ведущий минус.
     Примеры: "99,982%"→{num:99.982, suf:"%"}, "130+"→{num:130, suf:"+"},
     "152-ФЗ"→{num:152, suf:"-ФЗ"}, "9"→{num:9}, " 72"→{num:72}. */
  function parseNumber(text) {
    var m = String(text).match(
      /^(\s*-?)((?:\d[\d\s\u00a0]*)(?:[.,]\d+)?)(.*)$/);
    if (!m) return null;
    var digits = m[2].replace(/[\s\u00a0]/g, "");        // убрать разделители тысяч
    var decimals = 0;
    var dot = digits.search(/[.,]/);
    if (dot !== -1) decimals = digits.length - dot - 1;
    var value = parseFloat(digits.replace(",", "."));
    if (!isFinite(value)) return null;
    return {
      prefix: m[1],
      suffix: m[3],
      value: value,
      decimals: decimals,
      grouped: /[\s\u00a0]/.test(m[2].trim())             // были ли пробелы тысяч
    };
  }

  /* Отрисовать число с тем же числом знаков и (если надо) пробелами тысяч. */
  function formatNumber(v, p) {
    var s = v.toFixed(p.decimals);
    if (p.decimals) s = s.replace(".", ",");
    if (p.grouped) {
      var parts = s.split(",");
      parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
      s = parts.join(",");
    }
    return p.prefix + s + p.suffix;
  }

  function animateCount(el) {
    try {
      var original = el.getAttribute("data-count-final");
      if (original === null) {
        original = el.textContent;
        el.setAttribute("data-count-final", original);
      }
      if (reduceMotion) { el.textContent = original; return; }
      var p = parseNumber(original);
      if (!p) { el.textContent = original; return; }      // нет числа — не трогаем
      var dur = 600, start = null, target = p.value;
      function frame(ts) {
        if (start === null) start = ts;
        var t = Math.min(1, (ts - start) / dur);
        var eased = 1 - Math.pow(1 - t, 3);                // easeOutCubic
        el.textContent = formatNumber(target * eased, p);
        if (t < 1) requestAnimationFrame(frame);
        else el.textContent = original;                    // ТОЧНО исходная строка
      }
      requestAnimationFrame(frame);
    } catch (e) {
      var fin = el.getAttribute("data-count-final");
      if (fin !== null) el.textContent = fin;              // никогда не падаем
    }
  }

  function runCounters(slide) {
    var nodes = slide.querySelectorAll(".js-count");
    for (var i = 0; i < nodes.length; i++) animateCount(nodes[i]);
  }

  /* Точка входа: показать слайд n (0-based). Ставит .is-active -> CSS играет входы. */
  function goTo(n) {
    n = clamp(n);
    if (n === current) return;
    if (current >= 0) slides[current].classList.remove("is-active");
    current = n;
    slides[current].classList.add("is-active");
    runCounters(slides[current]);                          /* E3: счётчик цифр */
    if (history.replaceState) history.replaceState(null, "", "#" + (current + 1));
    if (progressEl) {
      var pct = slides.length > 1 ? (current / (slides.length - 1)) * 100 : 100;
      progressEl.style.width = pct + "%";
    }
  }

  function next() { goTo(current + 1); }
  function prev() { goTo(current - 1); }

  /* Автоскейл: вписать 1920x1080 в окно по меньшей стороне (letterbox). */
  function rescale() {
    var stage = document.querySelector(".deck-stage");
    if (!stage) return;
    var w = window.innerWidth / 1920;
    var h = window.innerHeight / 1080;
    var s = Math.min(w, h);
    if (!isFinite(s) || s <= 0) return; /* окно ещё без размера (скрытый webview) — не затирать масштаб */
    stage.style.setProperty("--deck-scale", String(s));
  }

  function fromHash() {
    var m = (location.hash || "").match(/^#(\d+)$/);
    return m ? parseInt(m[1], 10) - 1 : 0;
  }

  /* stats-row: «цифры-герои» в ряду (.sr-value) при длинных значениях-диапазонах
     («−30–45%», «15→65%») вылезали за свою ячейку и наезжали на соседнюю. Считаем,
     во сколько раз надо ужать, чтобы КАЖДОЕ значение влезло в свою ячейку, берём
     самый жёсткий коэффициент и применяем его КО ВСЕМ значениям ряда — так все
     цифры одного размера (как у дизайнера), без коллизий, при любом ответе модели.
     Пол читаемости — 42% базового кегля; короткие значения не трогаем. */
  function autofitStats() {
    var rows = document.querySelectorAll(".sr-row");
    for (var r = 0; r < rows.length; r++) {
      var vals = rows[r].querySelectorAll(".sr-value");
      var ratio = 1, i;
      for (i = 0; i < vals.length; i++) vals[i].style.fontSize = "";  // сброс к базе
      for (i = 0; i < vals.length; i++) {
        var cell = vals[i].parentElement;                             // .sr-cell
        if (!cell || !cell.clientWidth || !vals[i].scrollWidth) continue;
        ratio = Math.min(ratio, cell.clientWidth / vals[i].scrollWidth);
      }
      if (ratio < 1) {
        ratio = Math.max(ratio * 0.97, 0.42);
        for (i = 0; i < vals.length; i++) {
          var base = parseFloat(getComputedStyle(vals[i]).fontSize) || 104;
          vals[i].style.fontSize = (base * ratio) + "px";
        }
      }
    }
    autofitStatsHeight();
  }

  /* Тот же слайд по вертикали: блок рядов начинается на y=328, слайд кончается
     на 1080 — бюджет ~692px. Два ряда по 4 метрики с пояснениями на слотовом
     капе (label 40, caption 160 симв.) занимают 887px, то есть 200px уезжают
     ЗА нижний край, а .slide{overflow:hidden} срезает их молча: человек видит
     ряд без половины пояснений и не понимает, что текст вообще был.
     Ужимаем подписи и пояснения всех ячеек блока до кегля, при котором ряды
     влезают (цифру-героя не трогаем — она и есть смысл слайда).
     Правило владельца продукта (2026-10-27): минимальный кегль ЛЮБОГО текста
     16px, ниже не ужимаем (подпись и пояснение упираются в один пол). */
  var _SR_MIN_FS = 16, _SR_BOTTOM = 60, _SR_SLACK = 40;
  function autofitStatsHeight() {
    var blocks = document.querySelectorAll(".sr-rows");
    for (var b = 0; b < blocks.length; b++) {
      var box = blocks[b];
      var slide = box.closest ? box.closest(".slide") : null;
      if (!slide || !slide.clientHeight) continue;
      var labels = box.querySelectorAll(".sr-label");
      var caps = box.querySelectorAll(".sr-caption");
      var i, fs;
      for (i = 0; i < labels.length; i++) labels[i].style.fontSize = "";   // сброс к базе
      for (i = 0; i < caps.length; i++) caps[i].style.fontSize = "";
      box.style.top = "";                 // сброс сдвига: пересчёт идемпотентен
      var top0 = box.offsetTop;
      var budget = slide.clientHeight - _SR_BOTTOM - top0;
      for (fs = 30; fs > _SR_MIN_FS; fs--) {
        if (box.offsetHeight <= budget) break;
        for (i = 0; i < labels.length; i++) labels[i].style.fontSize = (fs - 1) + "px";
        for (i = 0; i < caps.length; i++) caps[i].style.fontSize = Math.max(_SR_MIN_FS, (fs - 1) * 22 / 30) + "px";
      }
      /* Остаток бюджета делим пополам. Верх блока задан константой в CSS под
         ДВА ряда по четыре метрики, а типичный слайд — один ряд из трёх: он
         занимал ~250px из 690, и нижние 45% слайда оставались пустыми (прод-замер,
         дека «Юнит-экономика», слайд 41). Двигаем блок, а не переписываем CSS:
         ужим выше меряет ту же коробку, и фиксированная высота сломала бы его. */
      var free = budget - box.offsetHeight;
      if (free > _SR_SLACK) box.style.top = (top0 + free / 2) + "px";
    }
  }

  /* Единое автоужатие кегля для «похожих фреймов» (правило владельца продукта,
     2026-10-27). Текст, который во фрейм не влез, молча срезался overflow:hidden —
     БЕЗ всякого признака, что часть текста не показана (2 ряда по 4 плашки на
     слотовом капе теряли 84px, 3×5 — 325px). Правило: кегль базовый (30), ужимаем
     ТОЛЬКО если текст не помещается во фрейм (с учётом его паддинга-отступа), и
     РАВНОМЕРНО во всех похожих фреймах одного слайда — до кегля, при котором
     влезает САМЫЙ плотный фрейм (у дизайнера все плашки слайда одного кегля).
     Пол читаемости 16px: ниже не ужимаем, пусть лучше модель режет текст.
     Покрытие — все шаблоны с текстовыми фреймами/строками:
       grid-2x2, frames-grid, cards-6, two-col-cards, three-col,
       service-table (строки 179px), timeline (строки 132px). */
  var _CELL_MIN_FS = 16;
  var _FIT_GRIDS = [
    { grid: ".g22-grid", cell: ".g22-cell", text: ".g22-text" },
    { grid: ".fg-grid",  cell: ".fg-frame", text: ".fg-text" },
    { grid: ".c6-grid",  cell: ".c6-cell",  text: ".c6-text" },
    { grid: ".tcc-grid", cell: ".tcc-body", text: ".tcc-text" },
    { grid: ".tcl-grid", cell: ".tcl-col",  text: ".tcl-text" },
    { grid: ".svc-rows", cell: ".svc-row",  text: ".svc-benefit" },
    { grid: ".tl-list",  cell: ".tl-row",   text: ".tl-text" }
  ];
  function autofitCells() {
    for (var k = 0; k < _FIT_GRIDS.length; k++) {
      var grids = document.querySelectorAll(_FIT_GRIDS[k].grid);
      for (var g = 0; g < grids.length; g++) {
        var cells = grids[g].querySelectorAll(_FIT_GRIDS[k].cell);
        var texts = grids[g].querySelectorAll(_FIT_GRIDS[k].text);
        if (!texts.length) continue;
        var i, fs;
        for (i = 0; i < texts.length; i++) texts[i].style.fontSize = "";   // сброс к базе
        for (fs = 30; fs > _CELL_MIN_FS; fs--) {
          var fits = true;
          for (i = 0; i < cells.length; i++) {
            if (cells[i].scrollHeight > cells[i].clientHeight + 1) { fits = false; break; }
          }
          if (fits) break;
          for (i = 0; i < texts.length; i++) texts[i].style.fontSize = (fs - 1) + "px";
        }
      }
    }
    autofitServiceName();
  }

  /* service-table: имя сервиса (.svc-name, база 54) течёт сверху панели, а блок
     строк (.svc-rows) прибит на top:182 — имя на слотовом капе (40 симв.)
     занимает 4 строки и НАЕЗЖАЕТ на «01» (стресс-замер 2026-10-27). Ужимаем
     кегль имени, пока его низ не поднимется над блоком строк; пол тот же 16px. */
  function autofitServiceName() {
    var panels = document.querySelectorAll(".svc-panel");
    for (var p = 0; p < panels.length; p++) {
      var name = panels[p].querySelector(".svc-name");
      var rows = panels[p].querySelector(".svc-rows");
      if (!name || !rows || !rows.offsetTop) continue;
      name.style.fontSize = "";                                        // сброс к базе
      var base = parseFloat(getComputedStyle(name).fontSize) || 54;
      for (var fs = base; fs > _CELL_MIN_FS; fs--) {
        if (name.offsetTop + name.offsetHeight <= rows.offsetTop) break;
        name.style.fontSize = (fs - 1) + "px";
      }
    }
  }

  function init() {
    slides = Array.prototype.slice.call(document.querySelectorAll(".slide"));
    if (!slides.length) return;
    progressEl = document.querySelector(".deck-progress");

    /* К§6: превью в редакторе (?editor=1) — режим покоя. Входы/лупы/графики
       показываем в финальном видимом состоянии (motion.css .no-motion), чтобы
       повторяющиеся сейвы не перезапускали спектакль в iframe. */
    if (/[?&]editor=1/.test(location.search) && document.body) {
      document.body.classList.add("no-motion");
    }

    /* Инвариант: активен РОВНО один слайд. goTo() снимает .is-active только с
       предыдущего current, поэтому любой «застрявший» маркер в исходном HTML
       (редактор сохраняет живой DOM вместе с .is-active того слайда, что был на
       экране) остался бы виден поверх остальных — тот слайд «дублировался» бы на
       все. Сбрасываем маркер со всех слайдов ДО первого goTo. */
    for (var s = 0; s < slides.length; s++) slides[s].classList.remove("is-active");

    rescale();
    autofitStats();
    autofitCells();
    window.addEventListener("load", function () {   // пересчёт после подгрузки шрифтов/картинок
      autofitStats(); autofitCells();
    });
    window.addEventListener("resize", rescale);
    document.addEventListener("visibilitychange", rescale);

    goTo(fromHash());
    window.addEventListener("hashchange", function () { goTo(fromHash()); });

    document.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight" || e.key === " " || e.key === "PageDown") { e.preventDefault(); next(); }
      else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); prev(); }
      else if (e.key === "Home") { e.preventDefault(); goTo(0); }
      else if (e.key === "End") { e.preventDefault(); goTo(slides.length - 1); }
    });

    /* Свайп. */
    var touchX = null;
    document.addEventListener("touchstart", function (e) {
      touchX = e.changedTouches[0].clientX;
    }, { passive: true });
    document.addEventListener("touchend", function (e) {
      if (touchX === null) return;
      var dx = e.changedTouches[0].clientX - touchX;
      touchX = null;
      if (Math.abs(dx) < 40) return;
      if (dx < 0) next(); else prev();
    }, { passive: true });

    /* Клик по краям (правая треть — вперёд, левая — назад). */
    document.addEventListener("click", function (e) {
      if (e.target.closest("a,button,input,textarea,select")) return;
      var x = e.clientX / window.innerWidth;
      if (x > 0.66) next();
      else if (x < 0.33) prev();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.deck = { goTo: goTo, next: next, prev: prev };
})();
