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

  function init() {
    slides = Array.prototype.slice.call(document.querySelectorAll(".slide"));
    if (!slides.length) return;
    progressEl = document.querySelector(".deck-progress");

    rescale();
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
