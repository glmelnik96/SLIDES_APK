/* Drag узлов диаграммы в превью редактора (гибридная раскладка).
 *
 * Единственный жест на слайде-схеме — перетаскивание узла [data-node-id]:
 * дельта в координатах холста (1800×720) пишется в spec.offsets[id],
 * DiagramEngine из iframe перерисовывает вживую, на отпускание — onCommit(spec)
 * (редактор дебаунс-сейвит offsets через PUT /fields). Правка текста узлов —
 * в боковой панели, не на слайде (решение владельца).
 *
 * Слушатели висят на .diagram-host (переживает host.innerHTML = "" при живой
 * перерисовке), pointer capture — тоже на host: узел под пальцем пересоздаётся
 * каждый кадр, а жест продолжает жить.
 */
(function (global) {
  "use strict";

  var CANVAS_W = 1800, CANVAS_H = 720;
  var DRAG_THRESHOLD = 4; // px экрана: меньше — это клик, не перетаскивание

  function attach(host, opts) {
    if (host.__dgmDrag) return;      // повторный load кадра — не дублируем
    host.__dgmDrag = true;
    host.style.touchAction = "none"; // жест целиком наш, без скролла страницы
    host.style.cursor = "grab";
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
      drag = {
        spec: spec, id: id,
        baseDx: base.dx || 0, baseDy: base.dy || 0,
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
      var ddx = (e.clientX - drag.x0) * drag.kx;
      var ddy = (e.clientY - drag.y0) * drag.ky;
      if (!drag.moved &&
          Math.abs(e.clientX - drag.x0) < DRAG_THRESHOLD &&
          Math.abs(e.clientY - drag.y0) < DRAG_THRESHOLD) return;
      drag.moved = true;
      host.style.cursor = "grabbing";
      // Кламп не нужен: applyOffsets движка держит центр узла в холсте,
      // а серверная схема клампит саму дельту при сейве.
      drag.spec.offsets[drag.id] = { dx: drag.baseDx + ddx, dy: drag.baseDy + ddy };
      opts.engine.render(host, drag.spec);
    });

    function finish() {
      if (!drag) return;
      var d = drag;
      drag = null;
      host.style.cursor = "grab";
      if (d.moved) opts.onCommit(d.spec);
    }
    host.addEventListener("pointerup", finish);
    host.addEventListener("pointercancel", finish);
  }

  global.DiagramDrag = { attach: attach };
})(typeof window !== "undefined" ? window : this);
