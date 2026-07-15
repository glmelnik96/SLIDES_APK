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

  /* Точный перенос: вписать блок .exact-fit в .exact-zone. Текст НЕ сокращаем —
     масштабируем весь блок (transform:scale) и центрируем по вертикали, чтобы
     контент не «прилипал» к верху с пустотой снизу. Разрежённый (короткий) контент
     можно УВЕЛИЧИТЬ до _EXACT_MAX_UP (заполняет зону, но не раздувается), длинный —
     ужать (нижний предел 0.35: ниже нечитаемо — оставляем как есть, текст цел, но
     может подрезаться зоной). */
  var _EXACT_MAX_UP = 1.8;
  function autofitExact() {
    var zones = document.querySelectorAll(".exact-zone");
    for (var i = 0; i < zones.length; i++) {
      var zone = zones[i];
      var fit = zone.querySelector(".exact-fit");
      if (!fit) continue;
      fit.style.transform = "none";
      var needH = fit.scrollHeight, needW = fit.scrollWidth;
      if (!needH || !needW) continue;
      var zh = zone.clientHeight, zw = zone.clientWidth;
      var scale = Math.min(zh / needH, zw / needW);
      if (scale > _EXACT_MAX_UP) scale = _EXACT_MAX_UP;
      if (scale < 0.35) scale = 0.35;
      var ty = (zh - needH * scale) / 2;             // центр по вертикали
      if (ty < 0) ty = 0;
      fit.style.transform = "translateY(" + ty + "px) scale(" + scale + ")";
    }
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
  }

  function init() {
    slides = Array.prototype.slice.call(document.querySelectorAll(".slide"));
    if (!slides.length) return;
    progressEl = document.querySelector(".deck-progress");

    /* Инвариант: активен РОВНО один слайд. goTo() снимает .is-active только с
       предыдущего current, поэтому любой «застрявший» маркер в исходном HTML
       (редактор сохраняет живой DOM вместе с .is-active того слайда, что был на
       экране) остался бы виден поверх остальных — тот слайд «дублировался» бы на
       все. Сбрасываем маркер со всех слайдов ДО первого goTo. */
    for (var s = 0; s < slides.length; s++) slides[s].classList.remove("is-active");

    rescale();
    autofitExact();
    autofitStats();
    window.addEventListener("load", function () { autofitExact(); autofitStats(); });   // пересчёт после подгрузки шрифтов/картинок
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
