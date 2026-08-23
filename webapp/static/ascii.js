/* ============================================================================
   ASCII-графика канона v11. Два независимых сюжета в одном файле:

   • СТЫК ЭТАЖЕЙ — светлый этаж не обрывается в тёмный по линейке, а осыпается
     в него зерном. Граница остаётся границей во всю ширину, но перестаёт быть
     чертой, проведённой по линейке.
   • ПРОГРЕСС СБОРКИ — каркас деки, который заполняется знаками слева направо,
     пока идёт заполнение слайдов. Не «крутилка»: видно, ЧТО собирается.

   ФАЙЛ НАМЕРЕННО САМОДОСТАТОЧЕН И ПЕРЕНОСИМ. Он не знает ни одного селектора
   конкретного приложения и не заводит ни одного своего цвета: и палитра, и
   размеры читаются из CSS в момент отрисовки. Поэтому один и тот же файл
   кладётся в /images, /slides и /creatives без правок, а перекраска этажа или
   смена акцента подхватываются сами.

   Значения по умолчанию (зерно 8, полоса 48, матрица Байер 8×8) выбраны
   владельцем на макете и менялись подбором — не «круглые числа наугад».
   ========================================================================== */
(function (root) {
  'use strict';

  /* ===== Общее ============================================================ */

  // Матрица Байера порядка n. Классическое рекурсивное построение: каждый
  // следующий порядок — четыре копии предыдущего со сдвигом 0/2/3/1.
  // Упорядоченная матрица, а не случайный шум: шум даёт грязную кашу, у
  // матрицы осыпь ложится ровным зерном и читается фактурой, а не помехой.
  function bayer(n) {
    var m = [[0, 2], [3, 1]], i, j;
    while (m.length < n) {
      var s = m.length, r = [];
      for (i = 0; i < s * 2; i++) r.push(new Array(s * 2));
      for (i = 0; i < s; i++) for (j = 0; j < s; j++) {
        var v = m[i][j] * 4;
        r[i][j] = v; r[i][j + s] = v + 2; r[i + s][j] = v + 3; r[i + s][j + s] = v + 1;
      }
      m = r;
    }
    var d = n * n, out = [];
    for (i = 0; i < n; i++) { out.push([]); for (j = 0; j < n; j++) out[i].push(m[i][j] / d); }
    return out;
  }
  var B8 = bayer(8);

  function clamp01(v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }

  // Полотно под плотность точек: размеры в CSS-пикселях, но с учётом DPR —
  // иначе зерно 8px на ретине превращается в мыло. Потолок 2 намеренный:
  // выше него растёт только счёт пикселей, а глазу разницы нет.
  function fit(cv, cssW, cssH) {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = Math.max(1, Math.round(cssW * dpr));
    cv.height = Math.max(1, Math.round(cssH * dpr));
    var ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  function cssVar(el, name, fallback) {
    var v = getComputedStyle(el).getPropertyValue(name).trim();
    return v || fallback;
  }

  function num(v, fallback) {
    var n = parseFloat(v);
    return isFinite(n) ? n : fallback;
  }

  function reducedMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  /* ===== Сюжет А: стык этажей =============================================
     Разметка: <canvas data-ascii-seam> между двумя полосами. Цвета берутся с
     соседей — фон элемента сверху и фон элемента снизу, — поэтому модулю не
     нужно знать ни имён классов, ни палитры. Переопределить можно атрибутами
     data-from / data-to, если соседи прозрачные.
     ====================================================================== */

  var SEAM_SEL = '[data-ascii-seam]';

  function bgOf(el) {
    if (!el) return '';
    var c = getComputedStyle(el).backgroundColor;
    // rgba(…, 0) и 'transparent' — не цвет: осыпаться не во что.
    if (!c || c === 'transparent' || /,\s*0\s*\)$/.test(c)) return '';
    return c;
  }

  function drawSeam(cv) {
    var w = cv.clientWidth;
    if (!w) return;                       // ещё не в раскладке — вернёмся позже
    var cell = num(cv.dataset.cell, 8);
    var h = num(cv.dataset.band, 48);

    var from = cv.dataset.from || bgOf(cv.previousElementSibling) || '#D9DEDB';
    var to   = cv.dataset.to   || bgOf(cv.nextElementSibling)     || '#0A0C0B';

    cv.style.height = h + 'px';
    var ctx = fit(cv, w, h);
    ctx.fillStyle = from;
    ctx.fillRect(0, 0, w, h);

    for (var y = 0; y < h; y += cell) {
      // Доля пути от света к чернилам — ЛИНЕЙНАЯ, и это решение, а не
      // недоделка. Сглаженная (smoothstep) сжимает распад к середине: полоса
      // заявлена в 48, а осыпается в 24, и высота полосы почти ничего не
      // меняет. Линейная тратит всю полосу целиком, а мягкость краёв даёт
      // сама матрица: у t≈0 порог проходят считаные ячейки из 64, и край
      // получается разрежённым сам собой.
      // Доля берётся по ВЕРХНЕЙ кромке ячейки, а не по её центру. Разница не
      // косметическая: полоса в 48 при зерне 8 — это всего шесть рядов, и
      // сдвиг на полряда меняет каждый из них заметно. По верхней кромке
      // первый ряд выходит чистым камнем, и осыпь начинается ПОД границей,
      // а не на ней. Именно этот вариант утверждён на макете.
      var t = y / h;
      var gy = Math.floor(y / cell);
      for (var x = 0; x < w; x += cell) {
        var gx = Math.floor(x / cell);
        if (t > B8[gy % 8][gx % 8]) {
          ctx.fillStyle = to;
          ctx.fillRect(x, y, cell, cell);
        }
      }
    }
  }

  function mountSeams(scope) {
    var list = (scope || document).querySelectorAll(SEAM_SEL);
    for (var i = 0; i < list.length; i++) drawSeam(list[i]);
  }

  // Ширина стыка равна ширине окна, поэтому перерисовывать надо на каждом
  // изменении размера. Полотно рисуется единожды и не анимируется, так что
  // дребезг тут дешёвый, но всё равно придержан кадром.
  var seamPend = false;
  function seamResize() {
    if (seamPend) return;
    seamPend = true;
    requestAnimationFrame(function () { seamPend = false; mountSeams(); });
  }

  /* ===== Сюжет Г: прогресс сборки =========================================
     Каркас слайда, набранный знаками, заполняется фронтом слева направо.
     Фронт — не украшение: он отвечает на вопрос «идёт ли работа» в тот же
     миг, что и число отвечает на «сколько осталось».

     Цифры процентов набираются ТЕКСТОМ, а не этими же блоками. Блочные цифры
     3×5 пробовали: в растре число перестаёт читаться с первого взгляда, а
     читают его именно так. Блоки остаются картинке хода.
     ====================================================================== */

  var G_COLS = 44, G_ROWS = 20;            // каркас деки 16:9 знаками
  var RAMP = [' ', '\u00B7', '\u2592', '\u2593', '\u2588'];

  // Раскладка слайда: заголовок сверху, столбец текста, блок картинки,
  // подпись. Каждая зона несёт свой «вес» — плотность, до которой она дойдёт
  // в готовом виде. Заголовок плотнее текста, как и на настоящем слайде.
  function zone(c, r) {
    if (r < 2) return 0;                          // поле
    if (r < 5) return c > 2 && c < 30 ? 1 : 0;    // заголовок
    if (r < 7) return 0;                          // воздух
    if (r < 16) {
      if (c > 2 && c < 20) return 0.62;           // левый столбец текста
      if (c > 23 && c < 41) return 0.95;          // правый блок — картинка
      return 0;
    }
    if (r < 18) return c > 2 && c < 14 ? 0.45 : 0;  // подпись
    return 0;
  }

  function Progress(cv) {
    this.cv = cv;
    this.prog = 0;
    this.live = false;
    this.raf = 0;
    this.t = 0;
    var self = this;
    this._tick = function (ts) {
      self.t = ts || 0;
      self.draw();
      if (self.live) self.raf = requestAnimationFrame(self._tick);
    };
  }

  Progress.prototype.draw = function () {
    var cv = this.cv, w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    var ctx = fit(cv, w, h);

    var text  = cssVar(cv, '--lp-text', '#F2F5F4');
    var muted = cssVar(cv, '--lp-muted', '#97A29E');
    var key   = cssVar(cv, '--lp-key', '#3FB67C');

    // Полотно ПРОЗРАЧНОЕ: подложку даёт панель, на которой оно лежит. Своей
    // заливкой оно бы завело вторую поверхность внутри первой — ровно ту
    // «карточку в карточке», от которой канон и уходит.
    ctx.clearRect(0, 0, w, h);

    var cw = w / G_COLS, ch = h / G_ROWS;
    ctx.font = '700 ' + Math.round(ch * 0.82) + 'px ' +
               cssVar(cv, '--font-mono', '"SB Sans Cond Mono", ui-monospace, monospace');
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    // Фронт идёт слева направо и обгоняет долю на несколько столбцов: так
    // видно, что дека именно СОБИРАЕТСЯ, а не плавно проявляется целиком.
    var front = this.prog * (G_COLS + 8) - 4;
    var breathe = this.live && !reducedMotion();

    for (var r = 0; r < G_ROWS; r++) {
      for (var c = 0; c < G_COLS; c++) {
        var wgt = zone(c, r);
        if (wgt === 0) continue;
        var lead = front - c;
        var ready = clamp01(lead / 6);
        // Место, до которого фронт ещё не дошёл, метится самым редким знаком, а
        // не оставляется пустым. Пустое место обходилось дорого: полотно шириной
        // в каркас не имело видимых границ, и на первых процентах от деки
        // оставался комок знаков у левого края, а справа — ничто. Каркас в такой
        // подаче не читался ни каркасом, ни даже фигурой, с которой можно
        // выровнять число над ним. С меткой видно ВСЮ деку с первой секунды, и
        // фронт заполняет заявленное место, а не выращивает его из ничего.
        if (ready <= 0) {
          ctx.fillStyle = muted;
          ctx.globalAlpha = 0.22;         // тень намерения, а не второй слой
          ctx.fillText(RAMP[1], (c + 0.5) * cw, (r + 0.5) * ch);
          continue;
        }

        var lvl = ready * wgt;
        var edge = lead > 0 && lead < 6;              // кромка фронта дышит
        if (edge && breathe)
          lvl *= 0.55 + 0.45 * (Math.sin(this.t * 0.006 + c * 0.5) * 0.5 + 0.5);

        var idx = Math.min(RAMP.length - 1, Math.round(lvl * (RAMP.length - 1)));
        if (idx === 0) continue;
        ctx.fillStyle = edge ? key : (wgt === 1 ? text : muted);
        ctx.globalAlpha = edge ? 0.9 : (wgt === 1 ? 1 : 0.8);
        ctx.fillText(RAMP[idx], (c + 0.5) * cw, (r + 0.5) * ch);
      }
    }
    ctx.globalAlpha = 1;
  };

  // Доля готовности, 0..1. Пересчёт кадра синхронный и дешёвый (сорок на
  // двадцать знаков), поэтому статичное обновление рисуется сразу.
  Progress.prototype.set = function (frac) {
    this.prog = clamp01(frac);
    if (!this.live) this.draw();
    return this;
  };

  Progress.prototype.start = function () {
    if (this.live) return this;
    // При выключенной анимации крутить кадры незачем: дышать нечему, а картинка
    // от кадра к кадру не меняется. Рисуем один раз и на этом останавливаемся —
    // прогресс всё равно перерисуется на следующем set().
    if (reducedMotion()) { this.draw(); return this; }
    this.live = true;
    this.raf = requestAnimationFrame(this._tick);
    return this;
  };

  Progress.prototype.stop = function () {
    this.live = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.draw();                       // застыть на честном кадре, не на пустом
    return this;
  };

  /* ===== Публичный вход =================================================== */

  root.CloudAscii = {
    seam: mountSeams,
    seamOne: drawSeam,
    progress: function (cv) { return cv ? new Progress(cv) : null; }
  };

  // Стык — чистое оформление и живёт без единой строки в коде приложения.
  // Шрифты в стыке не участвуют, ждать их незачем; прогресс перерисовывается
  // приложением сам, поэтому подмена шрифта его догонит.
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', function () { mountSeams(); });
  else mountSeams();
  window.addEventListener('resize', seamResize, { passive: true });
})(window);
