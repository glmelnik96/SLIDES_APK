/* Живая сетка v11 — общая для лендинга и инструмента.
   Раньше этот код жил внутри landing.html; теперь чертёж один на обе
   поверхности, и разъезжаться в мелочах им больше негде.

   Линии не подсвечиваются на месте, а расходятся в стороны от курсора —
   каждая на свою величину, поэтому ячейки раздвигаются неровно. Курсор при
   этом берётся сглаженный: точка тянется за мышью с отставанием, отсюда
   инерция вместо покадрового повторения. Хвост влияния не обрывается —
   самая дальняя линия тоже сдвинута, на пару точек, так что дышит вся сетка.

   window.lpGrid(opts):
     grid    — .lp-grid, куда рисуем (обязателен);
     stage   — на чём слушаем курсор (по умолчанию сам grid);
     alignTo — необязательный элемент, к чьим краям притирается чертёж.
   Возвращает { align } — лендингу это нужно, чтобы совместить сетку со
   строками разделов после ресайза, когда canvas не завёлся. */
(function () {
  var CELL = 24;

  window.lpGrid = function (opts) {
    var gridEl = opts.grid;
    var stage = opts.stage || gridEl;
    var alignTo = opts.alignTo || null;
    if (!gridEl) return { align: function () {} };

    var reduce = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var fine = window.matchMedia &&
      window.matchMedia('(hover: hover) and (pointer: fine)').matches;

    // Совмещение чертежа с содержимым. По вертикали хватает остатка от деления
    // на ячейку: дальше все края ложатся на линии и ни одна ячейка не режется
    // пополам. По горизонтали на линию ставится левый край текстовой колонки —
    // боковой отступ строки это половина остатка окна и кратным 24 не бывает.
    // Обе величины зависят от размеров окна, поэтому считаются здесь.
    var gy = 0, gx = 0;
    function align() {
      if (!alignTo) return;
      var g = gridEl.getBoundingClientRect(), r = alignTo.getBoundingClientRect();
      var row = alignTo.firstElementChild;
      var padL = row ? parseFloat(getComputedStyle(row).paddingLeft) || 0 : 0;
      gy = ((Math.round(r.top - g.top) % CELL) + CELL) % CELL;
      gx = ((Math.round(r.left - g.left + padL) % CELL) + CELL) % CELL;
      gridEl.style.setProperty('--lp-gy', gy + 'px');
      gridEl.style.setProperty('--lp-gx', gx + 'px');
    }
    align();

    var canvas = gridEl.querySelector('.lp-grid__canvas');
    var ctx = canvas && canvas.getContext ? canvas.getContext('2d') : null;

    if (!(ctx && stage && fine && !reduce)) {
      window.addEventListener('resize', align);
      return { align: align };
    }

    var SIG = 210;        // радиус влияния
    var AMP = 26;         // насколько расходятся ближние линии
    var TAIL = 4;         // сколько достаётся дальним
    var LIGHT = '242,245,244';
    var BASE = 0.038;     // яркость линии в покое
    var PEAK = 0.17;      // добавка под курсором
    var EASE_SHIFT = 0.032;   // с какой ленью тень догоняет курсор
    var EASE_LIGHT = 0.017;   // подсветка отстаёт ещё вдвое — она идёт следом
    var EASE_FADE = 0.026;    // разгон и затухание всего эффекта
    var W = 0, H = 0, rv = [], rh = [];
    var tx = 0, ty = 0;       // курсор
    var sx = 0, sy = 0;       // тень курсора — по ней считается раздвигание
    var hx = 0, hy = 0;       // вторая, ещё более ленивая тень — по ней светится
    var kt = 0, k = 0;        // сила эффекта: 0 вне сцены, 1 внутри
    var raf = 0, seeded = false;

    // Вес каждой линии свой, поэтому соседние расходятся на разную величину.
    // Разброс не шире 0.55…1.45: при большем линия обгоняла бы соседнюю и
    // сетка читалась бы как сломанная, а не как раздвинутая.
    function seed(arr, n) { while (arr.length < n) arr.push(0.55 + Math.random() * 0.9); }

    function resize() {
      var r = gridEl.getBoundingClientRect();
      W = Math.max(1, Math.round(r.width));
      H = Math.max(1, Math.round(r.height));
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed(rv, Math.ceil(W / CELL) + 4);
      seed(rh, Math.ceil(H / CELL) + 4);
      align();
      draw();
    }

    function draw() {
      ctx.clearRect(0, 0, W, H);
      ctx.lineWidth = 1;

      // Чертёж начинается на ячейку раньше видимого края: сдвиги gx/gy меньше
      // ячейки, и без этого запаса слева и сверху оставалась бы нерасчерченная
      // полоска шириной с остаток.
      var x0 = gx - CELL, y0 = gy - CELL;
      var xs = [], ys = [], i, v, d, f;
      for (i = 0; x0 + i * CELL <= W + CELL; i++) {
        v = x0 + i * CELL; d = v - sx;
        f = k * Math.exp(-(d * d) / (2 * SIG * SIG));
        xs.push(Math.round(v + (d < 0 ? -1 : 1) * AMP * rv[i] * f + TAIL * k * rv[i] * d / W) + 0.5);
      }
      for (i = 0; y0 + i * CELL <= H + CELL; i++) {
        v = y0 + i * CELL; d = v - sy;
        f = k * Math.exp(-(d * d) / (2 * SIG * SIG));
        ys.push(Math.round(v + (d < 0 ? -1 : 1) * AMP * rh[i] * f + TAIL * k * rh[i] * d / H) + 0.5);
      }

      // Чертёж целиком — один путь, одна обводка.
      ctx.strokeStyle = 'rgba(' + LIGHT + ',' + BASE + ')';
      ctx.beginPath();
      for (i = 0; i < xs.length; i++) { ctx.moveTo(xs[i], 0); ctx.lineTo(xs[i], H); }
      for (i = 0; i < ys.length; i++) { ctx.moveTo(0, ys[i]); ctx.lineTo(W, ys[i]); }
      ctx.stroke();

      // Подсветка. Она живёт на СВОЕЙ, более ленивой тени: пятно света идёт
      // следом за раздвиганием, а не вместе с ним. Градиент вдоль линии гасит
      // её к краям, поэтому пятно выходит круглым, а не крестом во весь экран.
      if (k < 0.01) return;
      var g, d2, fh;
      for (i = 0; i < xs.length; i++) {
        d2 = x0 + i * CELL - hx;
        fh = k * Math.exp(-(d2 * d2) / (2 * SIG * SIG));
        if (fh < 0.03) continue;
        g = ctx.createLinearGradient(0, hy - SIG, 0, hy + SIG);
        g.addColorStop(0, 'rgba(' + LIGHT + ',0)');
        g.addColorStop(0.5, 'rgba(' + LIGHT + ',' + (PEAK * fh).toFixed(3) + ')');
        g.addColorStop(1, 'rgba(' + LIGHT + ',0)');
        ctx.strokeStyle = g;
        ctx.beginPath(); ctx.moveTo(xs[i], 0); ctx.lineTo(xs[i], H); ctx.stroke();
      }
      for (i = 0; i < ys.length; i++) {
        d2 = y0 + i * CELL - hy;
        fh = k * Math.exp(-(d2 * d2) / (2 * SIG * SIG));
        if (fh < 0.03) continue;
        g = ctx.createLinearGradient(hx - SIG, 0, hx + SIG, 0);
        g.addColorStop(0, 'rgba(' + LIGHT + ',0)');
        g.addColorStop(0.5, 'rgba(' + LIGHT + ',' + (PEAK * fh).toFixed(3) + ')');
        g.addColorStop(1, 'rgba(' + LIGHT + ',0)');
        ctx.strokeStyle = g;
        ctx.beginPath(); ctx.moveTo(0, ys[i]); ctx.lineTo(W, ys[i]); ctx.stroke();
      }
    }

    // Цикл живёт, только пока есть что догонять: догнал курсор — остановился.
    function tick() {
      raf = 0;
      sx += (tx - sx) * EASE_SHIFT;
      sy += (ty - sy) * EASE_SHIFT;
      hx += (sx - hx) * EASE_LIGHT;
      hy += (sy - hy) * EASE_LIGHT;
      k += (kt - k) * EASE_FADE;
      draw();
      if (Math.abs(tx - sx) > 0.4 || Math.abs(ty - sy) > 0.4 ||
          Math.abs(sx - hx) > 0.4 || Math.abs(sy - hy) > 0.4 ||
          Math.abs(kt - k) > 0.003) {
        raf = requestAnimationFrame(tick);
      } else { sx = tx; sy = ty; hx = sx; hy = sy; k = kt; draw(); }
    }
    function wake() { if (!raf) raf = requestAnimationFrame(tick); }

    stage.addEventListener('pointermove', function (e) {
      var r = gridEl.getBoundingClientRect();
      tx = e.clientX - r.left; ty = e.clientY - r.top;
      // Первый заход — без разгона: иначе обе тени ехали бы через весь экран.
      if (!seeded) { seeded = true; sx = hx = tx; sy = hy = ty; }
      kt = 1; wake();
    });
    stage.addEventListener('pointerleave', function () { kt = 0; wake(); });

    var rt = 0;
    function relayout() { clearTimeout(rt); rt = setTimeout(resize, 150); }
    window.addEventListener('resize', relayout);

    // Окно — не единственная причина, по которой чертёж меняет размер. На
    // лендинге сетка кроет вьюпорт и расти ей некуда, но в инструменте она
    // заперта в раму ленты, а рама тянется за числом работ. Без наблюдателя
    // канва оставалась бы в размерах пустой ленты, и работы ниже первого
    // экрана лежали бы на нерасчерченном чёрном.
    if (window.ResizeObserver) new ResizeObserver(relayout).observe(gridEl);

    gridEl.classList.add('is-live');
    resize();
    return { align: align };
  };
})();
