// Ховер-подсказки редактора (MD 2026-11-02, §5). Иконочные кнопки без подписи
// («+», «дублировать», «отменить») без подсказки нечитаемы: aria-label слышит
// только скринридер, а глазами кнопка остается ребусом.
//
// Почему свой компонент, а не атрибут title. Нативный title нельзя вызвать с
// клавиатуры (focus-visible его не показывает), задержку и позицию он не отдает,
// и на части платформ рисуется системным шрифтом мимо макета. Все четыре пункта
// §5 требуют нашей отрисовки.
//
// Отдельный модуль без DOM в чистой части — решение о стороне показа
// (`placement`) тестируется через `node --test`; в браузере файл подключается
// ПЕРЕД editor.js и кладет Tooltip в window (паттерн errtext.js/picker_groups.js).
(function (root) {
  var WIDTH = 264;      // §5: верхняя граница ширины, текст переносится
  var GAP = 8;          // отступ от триггера
  var DELAY_IN = 400;   // §5: появление с задержкой, скрытие — сразу
  var MARGIN = 8;       // минимальный зазор до края вьюпорта

  /* Сторона и координаты подсказки. Чистая функция: на вход — прямоугольник
     триггера, размер подсказки и вьюпорт, на выход — готовые top/left.

     Снизу по умолчанию, сверху — когда снизу не хватает места ИЛИ вызывающий
     попросил явно (`force`). Явная просьба нужна кнопке «Сменить макет»: под ней
     поле «Заголовок», и подсказка его перекрывала (§4) — место снизу формально
     есть, но занято смыслом.

     Горизонтально подсказка центрируется по триггеру и затем ПРИЖИМАЕТСЯ к
     вьюпорту. Без прижима подсказка кнопки в правом рельсе (264 шириной, у самого
     края окна) уезжала за кадр правым краем. */
  function placement(trigger, tip, viewport, force) {
    var roomBelow = viewport.height - trigger.bottom;
    var above = force === "above" || roomBelow < tip.height + GAP + MARGIN;
    // Сверху тоже может не хватить (кнопка у верхней кромки окна) — тогда,
    // несмотря на просьбу, показываем снизу: обрезанная подсказка хуже
    // перекрытого поля.
    if (above && trigger.top < tip.height + GAP + MARGIN) above = false;
    var top = above ? trigger.top - tip.height - GAP : trigger.bottom + GAP;
    var left = trigger.left + trigger.width / 2 - tip.width / 2;
    var max = viewport.width - tip.width - MARGIN;
    if (left > max) left = max;
    if (left < MARGIN) left = MARGIN;
    return { top: top, left: left, side: above ? "above" : "below" };
  }

  /* ---- DOM-часть: в node она не выполняется ---- */

  function mount(doc, win) {
    var node = null;       // единственный узел подсказки на весь документ
    var shown = null;      // триггер, для которого подсказка сейчас видна
    var timer = null;      // отложенное появление
    var seq = 0;           // счетчик для уникальных id (aria-describedby)

    function el() {
      if (node) return node;
      node = doc.createElement("div");
      node.className = "tip";
      node.setAttribute("role", "tooltip");
      node.hidden = true;
      doc.body.appendChild(node);
      return node;
    }

    function hide() {
      clearTimeout(timer);
      timer = null;
      if (!shown) return;
      // aria-describedby снимаем вместе с показом: висящая ссылка на скрытый
      // узел заставляет скринридер читать подсказку как часть имени кнопки.
      shown.removeAttribute("aria-describedby");
      shown = null;
      if (node) node.hidden = true;
    }

    function show(trigger) {
      var text = trigger.getAttribute("data-tip");
      if (!text) return;
      hide();                        // §5: одновременно видна только одна
      var tip = el();
      tip.textContent = text;
      if (!tip.id) tip.id = "tip-" + (++seq);
      tip.hidden = false;
      // Замер ПОСЛЕ показа и установки текста: у скрытого узла высота нулевая,
      // а она решает, встанет подсказка снизу или сверху.
      var r = tip.getBoundingClientRect();
      var t = trigger.getBoundingClientRect();
      var p = placement(
        { top: t.top, bottom: t.bottom, left: t.left, width: t.width },
        { width: r.width || WIDTH, height: r.height },
        { width: win.innerWidth, height: win.innerHeight },
        trigger.getAttribute("data-tip-place")
      );
      tip.style.top = p.top + "px";
      tip.style.left = p.left + "px";
      tip.dataset.side = p.side;
      // Подсказка ДОПОЛНЯЕТ aria-label, а не заменяет его (§5): у кнопки
      // остается короткое имя, describedby дает подробность.
      trigger.setAttribute("aria-describedby", tip.id);
      shown = trigger;
    }

    function schedule(trigger) {
      clearTimeout(timer);
      timer = setTimeout(function () { show(trigger); }, DELAY_IN);
    }

    function triggerOf(target) {
      return target && target.closest ? target.closest("[data-tip]") : null;
    }

    // Делегирование на документ: кнопки ленты и формы пересобираются через
    // innerHTML на каждом слайде, и слушатели на самих кнопках пришлось бы
    // навешивать заново после каждой перерисовки.
    doc.addEventListener("mouseover", function (e) {
      var t = triggerOf(e.target);
      if (t) schedule(t); else if (shown && !triggerOf(e.target)) hide();
    });
    doc.addEventListener("mouseout", function (e) {
      var t = triggerOf(e.target);
      // Уход внутрь того же триггера (с кнопки на ее svg) — не уход.
      if (t && e.relatedTarget && t.contains(e.relatedTarget)) return;
      if (t) hide();
    });
    // focus-visible, а не focus: §5 требует подсказку при клавиатурной
    // навигации, но после клика мышью она была бы вторым показом подряд.
    doc.addEventListener("focusin", function (e) {
      var t = triggerOf(e.target);
      if (!t) return;
      var vis = true;
      try { vis = t.matches(":focus-visible"); } catch (_) { vis = true; }
      if (vis) schedule(t);
    });
    doc.addEventListener("focusout", function (e) {
      if (triggerOf(e.target)) hide();
    });
    doc.addEventListener("keydown", function (e) {
      if (e.key === "Escape") hide();
    });
    // Прокрутка рельса и ресайз окна оставляли подсказку висеть на старом
    // месте: она позиционируется абсолютно по замеру в момент показа.
    win.addEventListener("scroll", hide, true);
    win.addEventListener("resize", hide);

    return { show: show, hide: hide, isShown: function () { return shown; } };
  }

  root.Tooltip = { placement: placement, mount: mount, WIDTH: WIDTH, GAP: GAP,
                   DELAY_IN: DELAY_IN };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { placement: placement, mount: mount, WIDTH: WIDTH,
                       GAP: GAP, DELAY_IN: DELAY_IN };
  }
})(typeof window !== "undefined" ? window : globalThis);
