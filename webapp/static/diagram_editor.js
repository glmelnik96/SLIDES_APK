/* Drag узлов диаграммы в превью редактора (гибридная раскладка).
 *
 * Единственный жест на слайде-схеме — перетаскивание узла [data-node-id]:
 * дельта в координатах холста (1800×720) пишется в spec.offsets[id],
 * DiagramEngine из iframe перерисовывает вживую, на отпускание — onCommit(spec)
 * (редактор дебаунс-сейвит offsets через PUT /fields). Правка текста узлов —
 * в боковой панели, не на слайде (решение владельца).
 *
 * Магнитное выравнивание: узел притягивается к осям и краям соседей, к центру
 * холста и к своему авто-месту (возврат «как было»); совпавшая ось подсвечивается
 * пунктирной направляющей. Alt при перетаскивании отключает магнит.
 *
 * Слушатели висят на .diagram-host (переживает host.innerHTML = "" при живой
 * перерисовке), pointer capture — тоже на host: узел под пальцем пересоздаётся
 * каждый кадр, а жест продолжает жить.
 */
(function (global) {
  "use strict";

  var CANVAS_W = 1800, CANVAS_H = 720;
  var DRAG_THRESHOLD = 4; // px экрана: меньше — это клик, не перетаскивание
  var SNAP = 14;          // единиц холста (~7 px на экране): радиус магнита

  /* Опоры узла по оси: центр и два края. Вид опоры («c»/«min»/«max») важен —
     магнитим только одноимённые (центр к центру, левый край к левому). Совпадение
     центра с чужим краем — визуальный шум, а не выравнивание. */
  function anchors(p, axis) {
    var c = axis === "x" ? p.x : p.y;
    // Трапеции (воронка, пирамида) шире по нижней грани: габарит по широкой
    // стороне, иначе край-магнит целился бы в невидимую линию.
    var size = axis === "y" ? p.h
      : (p.wBottom == null ? p.w : Math.max(p.w, p.wBottom));
    var half = size / 2;
    return [{ k: "c", v: c }, { k: "min", v: c - half }, { k: "max", v: c + half }];
  }

  /* Ближайший магнит по одной оси. want — желаемый сдвиг узла, from — его опоры
     в авто-раскладке, to — опоры целей. Возвращает {adj, at} (adj — доводка
     сдвига, at — координата направляющей) или null, если целей в допуске нет. */
  function snapAxis(want, from, to, tol) {
    var best = null;
    // «Домой»: рядом с авто-местом магнит возвращает узел ровно на него.
    if (Math.abs(want) <= tol) best = { adj: -want, at: from[0].v, k: "c" };
    for (var i = 0; i < from.length; i++) {
      for (var j = 0; j < to.length; j++) {
        if (from[i].k !== to[j].k) continue;
        var d = to[j].v - (from[i].v + want);
        if (Math.abs(d) > tol) continue;
        var cand = { adj: d, at: to[j].v, k: from[i].k };
        if (!best || better(cand, best)) best = cand;
      }
    }
    return best;
  }

  /* Кто победил при равном расстоянии. У узлов одного размера центр и края
     совпадают одновременно — тогда направляющую ведём по центрам: она
     объясняет выравнивание, а линия по краю выглядит случайной. Допуск EPS —
     от плавающей точки: без него «равные» доводки решал порядок перебора. */
  function better(a, b) {
    var da = Math.abs(a.adj), db = Math.abs(b.adj);
    if (da < db - 1e-6) return true;
    if (db < da - 1e-6) return false;
    return a.k === "c" && b.k !== "c";
  }

  /* want: {dx,dy} — сдвиг «как тянет мышь». ctx: {auto, others, tol}, где auto —
     позиция узла в авто-раскладке {x,y,w,h}, others — позиции остальных узлов
     (как они нарисованы, со своими сдвигами). */
  function snapOffset(want, ctx) {
    var tol = ctx.tol == null ? SNAP : ctx.tol;
    var a = ctx.auto;
    // Центр холста — тоже ось (только для центра узла).
    var xs = [{ k: "c", v: CANVAS_W / 2 }], ys = [{ k: "c", v: CANVAS_H / 2 }];
    (ctx.others || []).forEach(function (p) {
      xs = xs.concat(anchors(p, "x"));
      ys = ys.concat(anchors(p, "y"));
    });
    var bx = snapAxis(want.dx, anchors(a, "x"), xs, tol);
    var by = snapAxis(want.dy, anchors(a, "y"), ys, tol);
    var guides = [];
    if (bx) guides.push({ axis: "x", at: bx.at });
    if (by) guides.push({ axis: "y", at: by.at });
    return {
      dx: want.dx + (bx ? bx.adj : 0),
      dy: want.dy + (by ? by.adj : 0),
      guides: guides,
    };
  }

  /* Нулевые сдвиги не храним: пустой offsets = чистый авто-режим (и панель тогда
     не показывает «Сбросить раскладку»). */
  function pruneZero(offsets) {
    Object.keys(offsets || {}).forEach(function (id) {
      var o = offsets[id];
      if (Math.abs(o.dx || 0) < 0.5 && Math.abs(o.dy || 0) < 0.5) delete offsets[id];
    });
    return offsets;
  }

  function drawGuides(host, guides) {
    if (!guides || !guides.length) return;
    var svg = host.querySelector("svg.diagram-svg");
    if (!svg) return;
    var s = "";
    guides.forEach(function (g) {
      var p = g.axis === "x" ? [g.at, 0, g.at, CANVAS_H] : [0, g.at, CANVAS_W, g.at];
      s += '<line class="dgm-guide" x1="' + p[0] + '" y1="' + p[1] + '" x2="' + p[2] +
        '" y2="' + p[3] + '" stroke="var(--accent)" stroke-width="2" ' +
        'stroke-dasharray="12 10" opacity="0.85" pointer-events="none"/>';
    });
    svg.insertAdjacentHTML("beforeend", s);
  }

  function attach(host, opts) {
    if (host.__dgmDrag) return;      // повторный load кадра — не дублируем
    host.__dgmDrag = true;
    host.style.touchAction = "none"; // жест целиком наш, без скролла страницы
    host.style.cursor = "grab";
    // Подсказка там, где жест: курсор-«рука» говорит «можно тянуть», тултип — что
    // именно произойдёт. Правка текста живёт в панели, здесь про неё не врём.
    host.title = "Перетащите узел, чтобы сдвинуть. Узел выравнивается по соседям " +
      "и центру, Alt — без выравнивания. Текст узлов правится в панели справа.";
    var drag = null;

    host.addEventListener("pointerdown", function (e) {
      var g = e.target && e.target.closest && e.target.closest("[data-node-id]");
      var svg = e.target && e.target.closest && e.target.closest("svg.diagram-svg");
      if (!g || !svg) return;
      var spec = opts.getSpec();
      if (!spec) return;
      var box = svg.getBoundingClientRect();
      if (!box.width || !box.height) return;
      var id = g.getAttribute("data-node-id");
      var base = (spec.offsets && spec.offsets[id]) || { dx: 0, dy: 0 };
      if (!spec.offsets) spec.offsets = {};
      // Магнит считаем по раскладке движка, а не по DOM: позиции соседей берём
      // как нарисованы (со сдвигами), авто-место тянущегося узла — из раскладки
      // без сдвигов вообще.
      // Дека, собранная до появления layout() (движок запечён в её HTML),
      // просто перетаскивается без магнита.
      var lay = opts.engine.layout;
      var drawn = lay ? (lay(spec) || {}) : {};
      var autoAll = lay
        ? (lay(Object.assign({}, spec, { offsets: {} })) || {}) : {};
      var others = Object.keys(drawn)
        .filter(function (k) { return k !== id; })
        .map(function (k) { return drawn[k]; });
      drag = {
        spec: spec, id: id,
        baseDx: base.dx || 0, baseDy: base.dy || 0,
        auto: autoAll[id], others: others,
        x0: e.clientX, y0: e.clientY,
        // масштаб: экранные px → единицы viewBox (дека масштабируется transform'ом,
        // getBoundingClientRect уже учитывает его)
        kx: CANVAS_W / box.width, ky: CANVAS_H / box.height,
        moved: false,
      };
      if (host.setPointerCapture) {
        try { host.setPointerCapture(e.pointerId); } catch (_) {}
      }
      e.preventDefault();
    });

    host.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var want = {
        dx: drag.baseDx + (e.clientX - drag.x0) * drag.kx,
        dy: drag.baseDy + (e.clientY - drag.y0) * drag.ky,
      };
      if (!drag.moved &&
          Math.abs(e.clientX - drag.x0) < DRAG_THRESHOLD &&
          Math.abs(e.clientY - drag.y0) < DRAG_THRESHOLD) return;
      drag.moved = true;
      host.style.cursor = "grabbing";
      // Alt — «без магнита»: точная ручная доводка, когда выравнивание мешает.
      var res = (e.altKey || !drag.auto)
        ? { dx: want.dx, dy: want.dy, guides: [] }
        : snapOffset(want, { auto: drag.auto, others: drag.others });
      // Кламп не нужен: applyOffsets движка держит центр узла в холсте,
      // а серверная схема клампит саму дельту при сейве.
      drag.spec.offsets[drag.id] = { dx: res.dx, dy: res.dy };
      opts.engine.render(host, drag.spec);
      drawGuides(host, res.guides);
    });

    function finish() {
      if (!drag) return;
      var d = drag;
      drag = null;
      host.style.cursor = "grab";
      if (!d.moved) return;
      pruneZero(d.spec.offsets);
      opts.engine.render(host, d.spec);   // финальный кадр — уже без направляющих
      opts.onCommit(d.spec);
    }
    host.addEventListener("pointerup", finish);
    host.addEventListener("pointercancel", finish);
  }

  var api = {
    attach: attach, snapOffset: snapOffset, snapAxis: snapAxis,
    anchors: anchors, pruneZero: pruneZero, SNAP: SNAP,
  };
  global.DiagramDrag = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
