// Русские строки автосейва и текстов ошибок валидации для конструктора.
// Отдельный модуль, чтобы чистую логику errText() юнит-тестировать через
// `node --test`. В браузере подключается ПЕРЕД editor.js и кладёт функции в
// window; в Node — экспортируется через module.exports. Меняется только текст,
// никакой DOM-логики здесь нет.
(function (root) {
  // Три состояния индикатора автосейва (показываем в шапке формы).
  var SAVE_STATUS = {
    saving: "Сохранение…",
    saved: "Сохранено ✓",
    error: "Не сохранено",
  };

  // Разбирает detail вида "N > max" в [N, max]; иначе [null, null].
  function parseCounts(detail) {
    var m = /(-?\d+)\s*>\s*(-?\d+)/.exec(detail == null ? "" : String(detail));
    return m ? [Number(m[1]), Number(m[2])] : [null, null];
  }

  // Русский текст ошибки по коду слот-контракта. Пустая строка — «не показывать»
  // (unknown_slot и прочее внутреннее), чтобы вызывающий ничего не рисовал.
  function errText(code, detail) {
    var c = parseCounts(detail), n = c[0], max = c[1];
    if (code === "missing_required") return "Заполните обязательное поле";
    if (code === "too_long") {
      return n != null ? "Слишком длинно: " + n + " из " + max + " символов"
                       : "Слишком длинно";
    }
    if (code === "too_many_items") {
      return n != null ? "Слишком много пунктов: " + n + " из " + max
                       : "Слишком много пунктов";
    }
    return "";
  }

  root.SAVE_STATUS = SAVE_STATUS;
  root.errText = errText;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { SAVE_STATUS: SAVE_STATUS, errText: errText };
  }
})(typeof window !== "undefined" ? window : globalThis);
