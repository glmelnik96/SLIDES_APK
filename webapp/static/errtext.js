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
    // Схема не прошла контракт: интернет ни при чём, чинить надо саму схему —
    // перечень претензий печатается блоком над формой.
    invalid: "Не сохранено — схема не сходится",
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

  // ── Претензии к схеме до сейва (зеркало htmlslides/diagrams/schema.py) ──
  // Сервер отвергает невалидный спек ЦЕЛИКОМ: панель успешно правится, а план
  // не меняется. Проверка на клиенте ловит это до запроса и говорит, что именно
  // сломано, пока правка на глазах. Тексты — про смысл схемы, а не про поля.
  // Форма (капы длин, дубли id, петли) здесь не проверяется: её держат maxLength
  // и сборщик панели, повторять их — растить второе место правды.
  var DGM_COUNT = {
    cycle: { min: 3, max: 8, text: "В цикле от 3 до 8 шагов" },
    funnel: { min: 2, text: "В воронке должно быть минимум 2 слоя" },
    pyramid: { min: 3, text: "В пирамиде должно быть минимум 3 уровня" },
    hub_spoke: { min: 3, text: "Хабу нужен центр и минимум два луча" },
    matrix: { min: 4, max: 4, text: "У матрицы ровно 4 квадранта" },
    venn: { min: 2, max: 3, text: "У диаграммы Венна 2 или 3 множества" },
    gantt_lite: { min: 2, max: 8, text: "В плане-графике от 2 до 8 работ" },
    steps: { min: 2, max: 6, text: "В лестнице от 2 до 6 ступеней" },
    mindmap: { min: 3, text: "Карте нужен центр и минимум две ветви" },
    network: { min: 3, text: "В графе связей минимум 3 узла" },
  };
  var DGM_EDGE_KINDS = ["flowchart", "hierarchy", "swimlanes", "network", "mindmap"];
  var DGM_LANE_FIELD = { comparison: "сторона", swimlanes: "исполнитель" };
  // Типы, чьи рёбра обязаны складываться в дерево: второе ребро к тому же узлу
  // раскладка молча теряет — связь, заданная автором, просто не появится.
  var DGM_TREE_FIELD = {
    hierarchy: "В оргсхеме у узла один руководитель, а у ",
    mindmap: "В карте у ветви одна родительская ветвь, а у ",
  };

  function diagramClaims(spec) {
    if (!spec || !spec.kind) return [];
    var nodes = spec.nodes || [], edges = spec.edges || [], out = [];
    var label = function (id) {
      for (var i = 0; i < nodes.length; i++) {
        if (nodes[i].id === id) return nodes[i].label || id;
      }
      return id;
    };
    // Узлы называем подписями, а не id: пользователь видел «Заявка», а не «n3».
    var names = function (ids) {
      var head = ids.slice(0, 3).map(function (i) { return "«" + label(i) + "»"; });
      return head.join(", ") + (ids.length > 3 ? " и ещё " + (ids.length - 3) : "");
    };
    if (!nodes.length) return ["В схеме не осталось ни одного узла с текстом"];

    var cnt = DGM_COUNT[spec.kind];
    if (cnt && (nodes.length < cnt.min || (cnt.max && nodes.length > cnt.max))) {
      out.push(cnt.text + " — сейчас " + nodes.length);
    }
    if (spec.kind === "flowchart" && nodes.length >= 2 && !edges.length) {
      out.push("В блок-схеме нужна хотя бы одна связь между узлами");
    }
    if (spec.kind === "gantt_lite") {
      // Длительность — единственное, что задаёт ширину полосы: без числа работа
      // не «нулевая», её просто нечем нарисовать.
      var noDur = nodes.filter(function (n) {
        var m = /-?\d+(?:[.,]\d+)?/.exec(String(n.value == null ? "" : n.value));
        var v = m ? Number(m[0].replace(",", ".")) : 0;
        return !(v > 0 && v <= 12);
      }).map(function (n) { return n.id; });
      if (noDur.length) {
        out.push("Укажите длительность (от 1 до 12 периодов) у работ " +
                 names(noDur));
      }
    }
    var laneField = DGM_LANE_FIELD[spec.kind];
    if (laneField) {
      var blank = nodes.filter(function (n) { return !(n.lane || "").trim(); });
      var lanes = [];
      nodes.forEach(function (n) {
        if (lanes.indexOf(n.lane) < 0) lanes.push(n.lane);
      });
      if (blank.length) {
        out.push("Заполните «" + laneField + "» у узлов " +
                 names(blank.map(function (n) { return n.id; })));
      } else if (spec.kind === "comparison" && lanes.length !== 2) {
        out.push("В сравнении ровно 2 стороны — сейчас " + lanes.length);
      } else if (spec.kind === "swimlanes" && (lanes.length < 2 || lanes.length > 5)) {
        out.push("Дорожек должно быть от 2 до 5 — сейчас " + lanes.length);
      }
    }
    if (spec.kind === "network" && !edges.length) {
      out.push("В графе связей нужна хотя бы одна связь — без связей это " +
               "россыпь плашек");
    }
    if (spec.kind === "mindmap" && edges.some(function (e) {
      return e.to === nodes[0].id;
    })) {
      out.push("Центр карты «" + (nodes[0].label || nodes[0].id) +
               "» не может быть подветвью — связь ведёт в него");
    }
    var treeText = DGM_TREE_FIELD[spec.kind];
    if (treeText) {
      var parent = {}, twice = [], ring = [];
      edges.forEach(function (e) {
        if (parent[e.to]) { if (twice.indexOf(e.to) < 0) twice.push(e.to); }
        else parent[e.to] = e.from;
      });
      if (twice.length) out.push(treeText + names(twice) + " их несколько");
      Object.keys(parent).forEach(function (id) {
        var hops = 0, cur = id;
        while (parent[cur]) {
          cur = parent[cur];
          if (++hops > 12) { ring.push(id); return; }
        }
      });
      if (ring.length) out.push("Связи замкнулись в кольцо вокруг " + names(ring));
    }
    // Узел без единой связи там, где раскладку задают именно рёбра: он не
    // «ещё не подключён», а сядет в нулевой ранг рядом со стартом. У карты то же
    // самое выглядит иначе: узел уходит ветвью к центру, и двухуровневая карта
    // схлопывается в плоский веер — визуально неотличимо от задуманного.
    if (edges.length && DGM_EDGE_KINDS.indexOf(spec.kind) >= 0) {
      var linked = {};
      edges.forEach(function (e) { linked[e.from] = 1; linked[e.to] = 1; });
      var lone = nodes.filter(function (n) { return !linked[n.id]; })
        .map(function (n) { return n.id; });
      if (lone.length) {
        out.push("Ни одна связь не ведёт к " + names(lone) +
                 (spec.kind === "mindmap"
                   ? " — такая ветвь уйдёт к центру и уровни карты смешаются"
                   : " — на схеме такой узел выпадает из потока"));
      }
    }
    return out;
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

  // Минуты на «Проверить и улучшить слайды». Это ХВОСТ сборки (сборка HTML →
  // линтер → вычитка внешнего вида → круг автоправок): ни разбора документа, ни
  // планирования, ни заполнения. Значит, он заведомо не дольше полной сборки
  // такого же числа слайдов, — берём ту же ставку 30 с/слайд, что и estimateLine.
  // Раньше здесь стояло «примерно n–2n мин», то есть в 2–4 раза БОЛЬШЕ, чем
  // страница обещает за сборку с нуля; замер: 1 слайд ≈ 50 с, 8 слайдов ≈ 2,5 мин
  // (вычитка идёт волнами по QA_WORKERS=8 параллельно).
  function rebuildEstimate(count) {
    return Math.max(1, Math.round(count * 0.5));
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

  // Короткое «что это» для чипа и карточки макета: первая фраза intent'а, который
  // целиком — абзац на 300 символов для промпта. Точку признаём концом фразы
  // только вместе с пробелом: иначе «Гориз. составные бары» обрезалось в «Гориз».
  function gist(intent, max) {
    var text = String(intent || "").trim();
    if (!text) return "";
    var limit = max || 80;
    var end = text.length;
    var re = /:|\.(?=\s|$)/g, m;
    while ((m = re.exec(text)) !== null) {
      if (m.index >= 8) { end = m.index; break; }  // «Гориз.» — сокращение, не фраза
    }
    var out = text.slice(0, end).trim();
    // Обрыв внутри скобки читается как опечатка — отрезаем незакрытый хвост.
    if (out.split("(").length > out.split(")").length)
      out = out.slice(0, out.lastIndexOf("(")).trim();
    return out.length > limit ? out.slice(0, limit - 1).trim() + "…" : out;
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
  root.diagramClaims = diagramClaims;
  root.gist = gist;
  root.estimateLine = estimateLine;
  root.rebuildEstimate = rebuildEstimate;
  root.healthLine = healthLine;
  root.checkedAgo = checkedAgo;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { SAVE_STATUS: SAVE_STATUS, REBUILD_LABEL: REBUILD_LABEL,
      CHAT_BUILD_EMPTY: CHAT_BUILD_EMPTY, plural: plural, errText: errText,
      diagramClaims: diagramClaims, estimateLine: estimateLine, gist: gist,
      rebuildEstimate: rebuildEstimate,
      healthLine: healthLine, checkedAgo: checkedAgo };
  }
})(typeof window !== "undefined" ? window : globalThis);
