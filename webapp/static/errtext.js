// Русские строки автосейва и текстов ошибок валидации для конструктора.
// Отдельный модуль, чтобы чистую логику errText() юнит-тестировать через
// `node --test`. В браузере подключается ПЕРЕД editor.js и кладёт функции в
// window; в Node — экспортируется через module.exports. Меняется только текст,
// никакой DOM-логики здесь нет.
(function (root) {
  // Состояния индикатора автосейва (показываем в шапке формы).
  // К§5: retrying/error — ретрай автосейва вместо молчаливого стирания ввода.
  var SAVE_STATUS = {
    saving: "Сохранение…",
    saved: "Сохранено ✓",
    retrying: "Не сохранено — повторяю…",
    error: "Не сохранилось — проверьте интернет",
  };

  // Ч§3: единое имя rebuild-кнопки во всех состояниях (без «движка»).
  var REBUILD_LABEL = { idle: "Проверить и улучшить слайды", busy: "Запускаю…" };

  // Ч§6: примеры пустого чата в режиме СБОРКИ (не точечных правок).
  var CHAT_BUILD_EMPTY =
    "Например: «сделай презентацию о нашем продукте для инвесторов на 8 слайдов», " +
    "«добавь слайд с ключевыми цифрами», «назови презентацию Итоги Q2».";

  // Ч§3: русская плюрализация (слайд / слайда / слайдов).
  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && !(m100 >= 12 && m100 <= 14)) return few;
    return many;
  }

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
  root.REBUILD_LABEL = REBUILD_LABEL;
  root.CHAT_BUILD_EMPTY = CHAT_BUILD_EMPTY;
  root.plural = plural;
  root.errText = errText;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { SAVE_STATUS: SAVE_STATUS, REBUILD_LABEL: REBUILD_LABEL,
      CHAT_BUILD_EMPTY: CHAT_BUILD_EMPTY, plural: plural, errText: errText };
  }
})(typeof window !== "undefined" ? window : globalThis);
