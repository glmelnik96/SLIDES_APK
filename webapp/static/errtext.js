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

  // Бриф для показа человеку: служебные пометки парсера «[картинка: …]»
  // вырезаем (pptx-исходники дают до шести подряд — фрагмент карточки вопроса
  // превращался в лесенку маркеров), осиротевшие пустые строки схлопываем.
  // Только отображение: в данных и в контексте для ИИ бриф остаётся полным.
  function briefDisplay(brief) {
    if (!brief) return "";
    return String(brief)
      .replace(/\[картинка:[^\]]*\]/g, "")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  // Длительность прогона «мм:сс» для карточек фида. Аудит 2026-08-14: «Точный
  // перенос» укладывается в сотни мс, и карточка показывала «за 0:00» — как
  // будто сборки не было. Суб-секундное округляем вверх до 0:01.
  function histDur(ms) {
    var s = Math.round(ms / 1000);
    if (s === 0 && ms > 0) s = 1;
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  // ── «пошаговая сборка» (glass), переработка 2026-08-20 ────────────────────
  // Решение по ответу /glass/step | /glass/score нового контракта:
  // action = "fill" | "score" | null. Вопросы конвейер не блокируют, поэтому
  // прежнего blocked/паузы нет: action null значит «работы сейчас нет» —
  // цикл останавливается и ждёт ответа автора или разведчика-скоринга.
  function glassStepDecision(out) {
    var done = !!(out && out.done);
    var stop = !out || done || out.action == null;
    // «Работы нет», но незаполненные слайды БЕЗ вопроса в плане остались — их
    // держит чужой шаг: другая вкладка или наш же запрос, оборванный клиентом
    // по таймауту (живой прогон 2026-08-20: сервер доработал, панель застыла
    // навсегда). Лечится перезапуском цикла по таймеру; слайды с вопросом
    // (needs_input) ждут автора, а не таймер.
    var stuck = !!(out && out.plan && (out.plan.slides || []).some(function (s) {
      return s.brief && !s.filled && s.status !== "needs_input";
    }));
    return {
      // На сцену — только что заполненный слайд; разметка сцену не дёргает,
      // иначе фоновый разведчик таскал бы автора по деке во время чтения.
      jump: out && out.action === "fill" && out.index ? out.index - 1 : null,
      done: done,
      stop: stop,
      retryLater: stop && !done && stuck,
    };
  }

  // Текст статуса панели сборки. Вопросы идут СЧЁТЧИКОМ в строке, а не паузой:
  // сборка продолжается, и текст обязан говорить об этом прямо — иначе автор
  // ждал остановки, которой больше нет. working = степпер или разведчик живы.
  function glassStatusText(s) {
    var open = s.open || 0;
    var txt;
    if (!s.total) txt = "Раскладываю документ…";
    else if (s.loopDone) txt = "Все слайды заполнены.";
    else if (open && !s.working)
      // Вся остальная работа сделана: заполнение упирается только в ответы.
      txt = "Готово " + s.filled + " из " + s.total +
        ". Сборка ждёт вашего решения: " + open + " " +
        plural(open, "вопрос", "вопроса", "вопросов") +
        " ниже — ответьте, и слайды заполнятся.";
    else {
      txt = "Заполняю слайды по одному: готово " + s.filled + " из " +
        s.total + "…";
      if (open) txt += " " + open + " " +
        plural(open, "вопрос", "вопроса", "вопросов") +
        " — разберём после заполнения, сборка не останавливается.";
    }
    if (s.failed) txt += " " + s.failed + " " +
      plural(s.failed, "слайд", "слайда", "слайдов") + " не " +
      plural(s.failed, "заполнился", "заполнились", "заполнились") +
      " — макет сменился на заглушку, карточки ниже дают переиграть.";
    if (s.notice) txt += " " + s.notice;
    return txt;
  }

  // ── экран сборки (переработка UX 2026-08-20) ──────────────────────────────
  // Подпись слайда для телеметрии и скелетов ленты: заголовок заполненного,
  // иначе первая строка брифа (тема раздела из плана). Никакого фейка: нет
  // темы — честное «Слайд N».
  function glassSlideLabel(s, n) {
    var t = (s && s.fields && s.fields.title) ||
            (s && s.content && s.content.title) || "";
    if (!t && s && s.brief) t = String(s.brief).split("\n")[0];
    t = String(t || "").trim();
    if (t.length > 48) t = t.slice(0, 47).replace(/\s+$/, "") + "…";
    return t || ("Слайд " + n);
  }

  // Кого степпер возьмёт следующим — зеркало серверного _next_index: первый
  // слайд с темой, не заполненный и без статуса (unscored/needs_input/failed
  // пропускаются, как на сервере). По нему телеметрия знает, что заполняется
  // ПРЯМО СЕЙЧАС: пока шаг в полёте, слайд в плане ещё не filled.
  function glassCurrentTarget(plan) {
    var slides = (plan && plan.slides) || [];
    for (var i = 0; i < slides.length; i++) {
      var s = slides[i];
      if (s && s.brief && !s.filled && !s.status)
        return { index: i + 1, label: glassSlideLabel(s, i + 1) };
    }
    return null;
  }

  // Таймер тикает с первой секунды — это и есть замена плашки «дольше обычного».
  function glassFillLine(target, seconds) {
    if (!target) return "";
    var t = "Заполняю слайд " + target.index + " — «" + target.label + "»…";
    if (seconds != null) t += " " + Math.max(0, Math.round(seconds)) + " с";
    return t;
  }

  // Степпер этапов: Документ → Раскладка → Заполнение N/M → [Вопросы] → Редактор.
  // Документ done всегда: экран существует только после удачного /glass/start.
  // «Вопросы» — этап по требованию: появляется, только когда ИИ реально что-то
  // спросил (open > 0). Пока идёт заполнение, он «впереди» (todo) — вопросы
  // разбираются ПОСЛЕ заполнения, а не параллельно; quest=true переносит акцент
  // на него (автозаполнение исчерпано, экран показывает карточки).
  function glassStages(s) {
    var layoutDone = s.total > 0 && !s.unscored;
    var fillDone = s.total > 0 && s.filled >= s.total;
    var st = [
      { label: "Документ", state: "done" },
      { label: "Раскладка", state: layoutDone ? "done" : "active" },
      { label: s.total ? "Заполнение " + s.filled + "/" + s.total : "Заполнение",
        state: fillDone ? "done"
          : s.quest ? "todo"
          : (s.total && (layoutDone || s.filled)) ? "active" : "todo" },
    ];
    if (s.open) st.push({
      label: "Вопросы (" + s.open + ")",
      state: s.quest ? "active" : "todo",
    });
    st.push({ label: "Редактор",
              state: fillDone && !s.open ? "active" : "todo" });
    return st;
  }

  // Тихий счётчик у степпера: вопросы копятся молча и не дёргают автора,
  // пока идёт заполнение. Пустая строка — счётчику нечего сказать.
  function glassQuestNote(n) {
    if (!n) return "";
    return "? " + n + " " + plural(n, "вопрос", "вопроса", "вопросов") +
      " — разберём после заполнения";
  }

  // Авто-переход в редактор (решение B): автозаполнение исчерпано — все слайды
  // заполнены либо остались только слайды-вопросы (needs_input дозаполняются из
  // редактора через карточки). Осечка (failed) заполнена заглушкой — не держит.
  function glassAutoExitReady(plan) {
    var slides = (plan && plan.slides) || [];
    var hasWork = false, total = 0;
    for (var i = 0; i < slides.length; i++) {
      var s = slides[i];
      if (!s || !s.brief) continue;
      total++;
      if (!s.filled && s.status !== "needs_input") hasWork = true;
    }
    return total > 0 && !hasWork;
  }

  /* ── Баннер опроса о сервисе ───────────────────────────────────────────────
     Отметка о показе лежит в localStorage браузера: своей таблицы предпочтений
     у App2 нет, а заводить миграцию и эндпоинт ради одной галочки дороже, чем
     согласиться, что отметка — на браузер, а не на человека. Плата известна:
     с другой машины позовём ещё раз. */
  var SURVEY_SNOOZE_MS = 7 * 24 * 60 * 60 * 1000;

  // Показывать ли баннер. mark — то, что удалось достать из хранилища:
  //   null            — не видел ни разу;
  //   {done:true}     — сходил по ссылке, больше не тревожим;
  //   {snoozed:<ms>}  — отложил, вернёмся через неделю.
  // Мусор в хранилище и часы из будущего решаем в пользу показа: замолчать
  // навсегда из-за битой записи хуже, чем позвать второй раз.
  function surveyDue(mark, now) {
    if (!mark || typeof mark !== "object") return true;
    if (mark.done) return false;
    var t = mark.snoozed;
    if (typeof t !== "number" || !isFinite(t) || t > now) return true;
    return now - t >= SURVEY_SNOOZE_MS;
  }

  root.SAVE_STATUS = SAVE_STATUS;
  root.glassStepDecision = glassStepDecision;
  root.glassStatusText = glassStatusText;
  root.glassSlideLabel = glassSlideLabel;
  root.glassCurrentTarget = glassCurrentTarget;
  root.glassFillLine = glassFillLine;
  root.glassStages = glassStages;
  root.glassQuestNote = glassQuestNote;
  root.glassAutoExitReady = glassAutoExitReady;
  root.histDur = histDur;
  root.briefDisplay = briefDisplay;
  root.CHAT_BUILD_EMPTY = CHAT_BUILD_EMPTY;
  root.plural = plural;
  root.errText = errText;
  root.diagramClaims = diagramClaims;
  root.gist = gist;
  root.estimateLine = estimateLine;
  root.healthLine = healthLine;
  root.checkedAgo = checkedAgo;
  root.surveyDue = surveyDue;
  root.SURVEY_SNOOZE_MS = SURVEY_SNOOZE_MS;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { SAVE_STATUS: SAVE_STATUS, histDur: histDur,
      glassStepDecision: glassStepDecision, glassStatusText: glassStatusText,
      glassSlideLabel: glassSlideLabel, glassCurrentTarget: glassCurrentTarget,
      glassFillLine: glassFillLine,
      glassStages: glassStages, glassQuestNote: glassQuestNote,
      glassAutoExitReady: glassAutoExitReady,
      CHAT_BUILD_EMPTY: CHAT_BUILD_EMPTY, plural: plural, errText: errText,
      briefDisplay: briefDisplay,
      diagramClaims: diagramClaims, estimateLine: estimateLine, gist: gist,
      healthLine: healthLine, checkedAgo: checkedAgo,
      surveyDue: surveyDue, SURVEY_SNOOZE_MS: SURVEY_SNOOZE_MS };
  }
})(typeof window !== "undefined" ? window : globalThis);
