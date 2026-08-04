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

  // Оценка времени сборки по числу разделов исходника (~30 с на слайд, замер
  // 2026-07: 40 слайдов ≈ 20 мин). Возвращает {text, warn} или null (нет данных).
  // >20 разделов — предупреждающий тон; >100 — движок соберёт первые 100
  // (MAX_DECK_SLIDES), минуты честно считаем по капу. Для .pptx единица —
  // «слайд», у остальных форматов — «раздел».
  function estimateLine(count, filename) {
    if (count == null || count <= 0) return null;
    var pptx = /\.pptx$/i.test(filename || "");
    var unit = pptx ? plural(count, "слайд", "слайда", "слайдов")
                    : plural(count, "раздел", "раздела", "разделов");
    var minutes = Math.max(2, Math.round(Math.min(count, 100) * 0.5));
    var text = count + " " + unit + " · примерно " + minutes + " мин";
    if (count > 100) text += ", соберём первые 100";
    var warn = count > 20;
    if (warn) text = "крупный документ: " + text;
    return { text: text, warn: warn };
  }

  // Доступность сервиса ИИ (GET /api/models/health) → тон индикатора и текст.
  // Роли, а не имена моделей: имя — деталь реализации, менялось уже дважды.
  // При "down" кнопку сборки НЕ блокируем — состояние могло измениться за секунды,
  // а запрет был бы решением за пользователя по данным минутной давности.
  var MODEL_HEALTH = {
    ok: { tone: "ok", text: "Сервис ИИ работает штатно" },
    fallback: { tone: "warn",
      text: "Основная модель не отвечает. Соберём на резервной — может быть медленнее" },
    down: { tone: "down",
      text: "Сервис ИИ недоступен: не отвечают обе модели. Сборка сейчас не пройдёт" },
    unknown: { tone: "idle", text: "Проверяю доступность сервиса ИИ…" },
  };
  function healthLine(state) {
    return MODEL_HEALTH[state] || MODEL_HEALTH.unknown;
  }

  // Возраст проверки доступности. Проба идёт только по запросу (загрузка страницы
  // и возврат во вкладку), поэтому свежесть данных пользователь обязан видеть —
  // иначе зелёный индикатор часовой давности читается как «сейчас всё хорошо».
  function checkedAgo(sec) {
    if (sec == null || !isFinite(sec) || sec < 0) return "";
    if (sec < 5) return "только что";
    if (sec < 60) return sec + " с назад";
    var min = Math.floor(sec / 60);
    if (min < 60) return min + " " + plural(min, "минуту", "минуты", "минут") + " назад";
    var h = Math.floor(min / 60);
    return h + " " + plural(h, "час", "часа", "часов") + " назад";
  }

  root.SAVE_STATUS = SAVE_STATUS;
  root.REBUILD_LABEL = REBUILD_LABEL;
  root.CHAT_BUILD_EMPTY = CHAT_BUILD_EMPTY;
  root.plural = plural;
  root.errText = errText;
  root.estimateLine = estimateLine;
  root.healthLine = healthLine;
  root.checkedAgo = checkedAgo;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { SAVE_STATUS: SAVE_STATUS, REBUILD_LABEL: REBUILD_LABEL,
      CHAT_BUILD_EMPTY: CHAT_BUILD_EMPTY, plural: plural, errText: errText,
      estimateLine: estimateLine, healthLine: healthLine,
      checkedAgo: checkedAgo };
  }
})(typeof window !== "undefined" ? window : globalThis);
