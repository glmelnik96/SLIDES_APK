const test = require("node:test");
const assert = require("node:assert");
const { errText, SAVE_STATUS, plural, estimateLine,
  healthLine, checkedAgo, diagramClaims, gist } = require("../../webapp/static/errtext.js");

test("gist: точка внутри сокращения не режет фразу", () => {
  assert.strictEqual(
    gist("Гориз. составные бары: состав (2–4 части) по категориям."),
    "Гориз. составные бары");
});

test("gist: двоеточие заканчивает фразу", () => {
  assert.strictEqual(gist("Титульный слайд деки: огромный заголовок."),
                     "Титульный слайд деки");
});

test("gist: точка с пробелом заканчивает фразу", () => {
  assert.strictEqual(gist("Задник деки. Ставится последним."), "Задник деки");
});

test("gist: длинная фраза обрезается с многоточием", () => {
  const out = gist("а".repeat(200));
  assert.strictEqual(out.length, 80);
  assert.ok(out.endsWith("…"));
});

test("gist: незакрытая скобка не остаётся в хвосте", () => {
  assert.strictEqual(
    gist("Акцентное заявление на полотне (брендовый инверт: графитовый текст)."),
    "Акцентное заявление на полотне");
});

test("gist: пусто на пустом входе", () => {
  assert.strictEqual(gist(""), "");
  assert.strictEqual(gist(null), "");
});

test("missing_required → просьба заполнить", () => {
  assert.strictEqual(errText("missing_required", ""), "Заполните обязательное поле");
});

test("too_long парсит 'N > max'", () => {
  assert.strictEqual(errText("too_long", "45 > 40"), "Слишком длинно: 45 из 40 символов");
});

test("too_many_items парсит 'N > max'", () => {
  assert.strictEqual(errText("too_many_items", "7 > 6"), "Слишком много пунктов: 7 из 6");
});

test("unknown_slot → пусто (пользователю не показываем)", () => {
  assert.strictEqual(errText("unknown_slot", "нечто"), "");
});

test("too_long без разбираемого detail → общий текст", () => {
  assert.strictEqual(errText("too_long", "—"), "Слишком длинно");
});

test("SAVE_STATUS содержит пять состояний (+retrying, +invalid)", () => {
  assert.deepStrictEqual(Object.keys(SAVE_STATUS).sort(),
    ["error", "invalid", "retrying", "saved", "saving"]);
});

/* ── diagramClaims: зеркало семантики schema.py, проверка ДО сейва ────────── */
test("diagramClaims: валидная схема — молчит", () => {
  assert.deepStrictEqual(diagramClaims({
    kind: "flowchart",
    nodes: [{ id: "a", label: "Заявка" }, { id: "b", label: "Проверка" }],
    edges: [{ from: "a", to: "b" }],
  }), []);
});

test("diagramClaims: удалили последнюю связь блок-схемы", () => {
  const out = diagramClaims({ kind: "flowchart",
    nodes: [{ id: "a", label: "Раз" }, { id: "b", label: "Два" }], edges: [] });
  assert.strictEqual(out.length, 1);
  assert.match(out[0], /хотя бы одну связь|хотя бы одна связь/);
});

test("diagramClaims: узел без связей называется подписью, а не id", () => {
  const out = diagramClaims({ kind: "flowchart",
    nodes: [{ id: "a", label: "Заявка" }, { id: "b", label: "Проверка" },
            { id: "n7", label: "Услуга подключена" }],
    edges: [{ from: "a", to: "b" }] });
  assert.strictEqual(out.length, 1);
  assert.match(out[0], /«Услуга подключена»/);
  assert.ok(!out[0].includes("n7"), out[0]);
});

test("diagramClaims: у типов без рёбер одиночные узлы — норма", () => {
  assert.deepStrictEqual(diagramClaims({ kind: "process",
    nodes: [{ id: "a", label: "Раз" }, { id: "b", label: "Два" }] }), []);
});

test("diagramClaims: счётные капы типов", () => {
  const only = (spec) => diagramClaims(spec)[0] || "";
  const stages = (n) => Array.from({ length: n },
    (_, i) => ({ id: "n" + i, label: "Стадия " + i }));
  assert.match(only({ kind: "cycle", nodes: stages(1) }),
    /от 3 до 8 шагов — сейчас 1/);
  // Верхний кап цикла тоже счётный: с девяти стадий дуга между плашками короче
  // наконечника стрелки, кольцо рассыпается на оторванные треугольники.
  assert.match(only({ kind: "cycle", nodes: stages(9) }),
    /от 3 до 8 шагов — сейчас 9/);
  assert.strictEqual(diagramClaims({ kind: "cycle", nodes: stages(8) }).length, 0);
  assert.match(only({ kind: "matrix",
    nodes: [1, 2, 3].map((i) => ({ id: "n" + i, label: "У" + i })) }),
    /ровно 4 квадранта — сейчас 3/);
  assert.strictEqual(diagramClaims({ kind: "venn",
    nodes: [{ id: "a", label: "Раз" }, { id: "b", label: "Два" }] }).length, 0);
});

test("diagramClaims: дорожки и стороны", () => {
  const noLane = diagramClaims({ kind: "swimlanes",
    nodes: [{ id: "a", label: "Раз", lane: "Продажи" }, { id: "b", label: "Два" }] });
  assert.match(noLane[0], /«исполнитель» у узлов «Два»/);
  const oneLane = diagramClaims({ kind: "swimlanes",
    nodes: [{ id: "a", label: "Раз", lane: "Продажи" },
            { id: "b", label: "Два", lane: "Продажи" }] });
  assert.match(oneLane[0], /от 2 до 5 — сейчас 1/);
  const sides = diagramClaims({ kind: "comparison",
    nodes: [{ id: "a", label: "Раз", lane: "Мы" }, { id: "b", label: "Два", lane: "Они" },
            { id: "c", label: "Три", lane: "Третьи" }] });
  assert.match(sides[0], /ровно 2 стороны — сейчас 3/);
});

test("diagramClaims: два родителя в оргсхеме", () => {
  const out = diagramClaims({ kind: "hierarchy",
    nodes: [{ id: "a", label: "Директор" }, { id: "b", label: "Зам" },
            { id: "c", label: "Отдел" }],
    edges: [{ from: "a", to: "c" }, { from: "b", to: "c" }, { from: "a", to: "b" }] });
  assert.strictEqual(out.length, 1);
  assert.match(out[0], /один руководитель.*«Отдел»/);
});

test("diagramClaims: план-график без длительности", () => {
  const out = diagramClaims({ kind: "gantt_lite",
    nodes: [{ id: "a", label: "Аудит", value: "2" },
            { id: "b", label: "Пилот" },
            { id: "c", label: "Запуск", value: "мес" }] });
  assert.strictEqual(out.length, 1);
  assert.match(out[0], /длительность.*«Пилот», «Запуск»/);
  assert.deepStrictEqual(diagramClaims({ kind: "gantt_lite",
    nodes: [{ id: "a", label: "Аудит", value: "2 мес" },
            { id: "b", label: "Пилот", value: "1,5" }] }), []);
});

test("diagramClaims: карта и граф связей", () => {
  // центр карты не может быть подветвью
  assert.match(diagramClaims({ kind: "mindmap",
    nodes: [{ id: "c", label: "Центр" }, { id: "a", label: "Раз" },
            { id: "b", label: "Два" }],
    edges: [{ from: "a", to: "c" }] })[0], /Центр.*не может быть подветвью/);
  // карта БЕЗ связей — законный плоский веер ветвей вокруг центра
  assert.deepStrictEqual(diagramClaims({ kind: "mindmap",
    nodes: [{ id: "c", label: "Центр" }, { id: "a", label: "Раз" },
            { id: "b", label: "Два" }] }), []);
  // а вот связи есть, но не у всех узлов — уровни карты смешаются
  const mix = diagramClaims({ kind: "mindmap",
    nodes: [{ id: "c", label: "Центр" }, { id: "a", label: "Раз" },
            { id: "a1", label: "Раз-один" }, { id: "b", label: "Два" }],
    edges: [{ from: "a", to: "a1" }] });
  assert.strictEqual(mix.length, 1);
  assert.match(mix[0], /«Центр», «Два».*уровни карты смешаются/);
  // у графа связей одиночка, наоборот, выпадает из раскладки
  const net = diagramClaims({ kind: "network",
    nodes: [{ id: "a", label: "Раз" }, { id: "b", label: "Два" },
            { id: "c", label: "Три" }],
    edges: [{ from: "a", to: "b" }] });
  assert.strictEqual(net.length, 1);
  assert.match(net[0], /«Три»/);
  assert.match(diagramClaims({ kind: "network",
    nodes: [{ id: "a", label: "Раз" }, { id: "b", label: "Два" },
            { id: "c", label: "Три" }] })[0], /хотя бы одна связь/);
});

test("diagramClaims: капы волны 3", () => {
  const only = (spec) => diagramClaims(spec)[0] || "";
  const nodes = (n) => Array.from({ length: n },
    (_, i) => ({ id: "n" + i, label: "У" + i, value: "1" }));
  assert.match(only({ kind: "steps", nodes: nodes(7) }), /до 6 ступеней — сейчас 7/);
  assert.match(only({ kind: "gantt_lite", nodes: nodes(9) }), /до 8 работ — сейчас 9/);
  assert.match(only({ kind: "mindmap", nodes: nodes(2) }), /две ветви — сейчас 2/);
});

test("diagramClaims: пустая схема и незнакомый спек", () => {
  assert.match(diagramClaims({ kind: "flowchart", nodes: [] })[0], /ни одного узла/);
  assert.deepStrictEqual(diagramClaims(null), []);
  assert.deepStrictEqual(diagramClaims({}), []);
});

// Аудит 2026-08-14: «Точный перенос» укладывается в сотни мс, и карточка
// показывала «за 0:00» — как будто сборки не было. Суб-секундное округляем
// вверх до 0:01, честный ноль остаётся нулём.
test("histDur: мм:сс, суб-секундное не превращается в 0:00", () => {
  const { histDur } = require("../../webapp/static/errtext.js");
  assert.strictEqual(histDur(386), "0:01");
  assert.strictEqual(histDur(45400), "0:45");
  assert.strictEqual(histDur(125000), "2:05");
  assert.strictEqual(histDur(0), "0:00");
});

test("plural — русская форма слайдов", () => {
  const f = (n) => plural(n, "слайд", "слайда", "слайдов");
  assert.strictEqual(f(1), "слайд");
  assert.strictEqual(f(2), "слайда");
  assert.strictEqual(f(4), "слайда");
  assert.strictEqual(f(5), "слайдов");
  assert.strictEqual(f(11), "слайдов");
  assert.strictEqual(f(12), "слайдов");
  assert.strictEqual(f(21), "слайд");
  assert.strictEqual(f(25), "слайдов");
  assert.strictEqual(f(111), "слайдов");
});

test("estimateLine: null/0 → null", () => {
  assert.strictEqual(estimateLine(null, "a.md"), null);
  assert.strictEqual(estimateLine(0, "a.md"), null);
});

test("estimateLine: мелкий док — нейтрально, минимум 2 мин", () => {
  assert.deepStrictEqual(estimateLine(1, "a.md"),
    { text: "1 раздел · примерно 2 мин", warn: false });
  assert.deepStrictEqual(estimateLine(10, "d.docx"),
    { text: "10 разделов · примерно 5 мин", warn: false });
});

test("estimateLine: .pptx считает слайдами", () => {
  assert.deepStrictEqual(estimateLine(12, "deck.PPTX"),
    { text: "12 слайдов · примерно 6 мин", warn: false });
});

test("estimateLine: >20 — предупреждающий тон с припиской", () => {
  assert.deepStrictEqual(estimateLine(40, "big.md"),
    { text: "крупный документ: 40 разделов · примерно 20 мин", warn: true });
});

test("estimateLine: >100 — кап и honest-минуты по первым 100", () => {
  assert.deepStrictEqual(estimateLine(120, "huge.md"),
    { text: "крупный документ: 120 разделов · примерно 50 мин, соберём первые 100",
      warn: true });
});

test("healthLine: четыре состояния — свой тон у каждого", () => {
  assert.strictEqual(healthLine("ok").tone, "ok");
  assert.strictEqual(healthLine("fallback").tone, "warn");
  assert.strictEqual(healthLine("down").tone, "down");
  assert.strictEqual(healthLine("unknown").tone, "idle");
});

test("healthLine: не называет модели — только роли", () => {
  ["ok", "fallback", "down", "unknown"].forEach((s) => {
    assert.ok(!/kimi|minimax|moonshot/i.test(healthLine(s).text), s);
  });
});

test("healthLine: незнакомое состояние — нейтральное «проверяю»", () => {
  assert.deepStrictEqual(healthLine("бред"), healthLine("unknown"));
  assert.deepStrictEqual(healthLine(undefined), healthLine("unknown"));
});

test("checkedAgo: нет данных — пустая строка (нечего показывать)", () => {
  assert.strictEqual(checkedAgo(null), "");
  assert.strictEqual(checkedAgo(undefined), "");
  assert.strictEqual(checkedAgo(-1), "");
  assert.strictEqual(checkedAgo(Infinity), "");
});

test("checkedAgo: секунды, минуты, часы", () => {
  assert.strictEqual(checkedAgo(0), "только что");
  assert.strictEqual(checkedAgo(4), "только что");
  assert.strictEqual(checkedAgo(5), "5 с назад");
  assert.strictEqual(checkedAgo(59), "59 с назад");
  assert.strictEqual(checkedAgo(60), "1 минуту назад");
  assert.strictEqual(checkedAgo(125), "2 минуты назад");
  assert.strictEqual(checkedAgo(3599), "59 минут назад");
  assert.strictEqual(checkedAgo(3600), "1 час назад");
  assert.strictEqual(checkedAgo(7200), "2 часа назад");
});

// Фикс 6 (прод-прогон «презы путилов»): маркеры парсера «[картинка: …]»
// показывались дословно во фрагменте брифа карточки вопроса — на pptx-
// исходниках до шести повторов подряд (дека 2, слайд 17).
test("briefDisplay: маркеры картинок вырезаны, пустые строки схлопнуты", () => {
  const { briefDisplay } = require("../../webapp/static/errtext.js");
  const raw = "Слайд 16\n[картинка: без подписи]\n\n[картинка: без подписи]\n\n" +
              "[картинка: схема архитектуры]\n\nПрочитано с изображения: KTS";
  const out = briefDisplay(raw);
  assert.ok(!out.includes("[картинка"), "маркер остался: " + out);
  assert.ok(out.startsWith("Слайд 16"));
  assert.ok(out.includes("Прочитано с изображения: KTS"));
  assert.ok(!/\n{3,}/.test(out), "тройные переводы строк не схлопнуты");
});

test("briefDisplay: обычный текст не трогаем, пустой вход — пустая строка", () => {
  const { briefDisplay } = require("../../webapp/static/errtext.js");
  assert.strictEqual(briefDisplay("Задача\nРешение"), "Задача\nРешение");
  assert.strictEqual(briefDisplay(""), "");
  assert.strictEqual(briefDisplay(null), "");
});

/* ── surveyDue: когда звать на опрос ──────────────────────────────────────────
   Баннер опроса на главной. Логика вынесена отдельно от DOM: единственное, что
   тут можно сломать, — это позвать человека, который уже сходил, или замолчать
   навсегда из-за мусора в хранилище. */
const { surveyDue, SURVEY_SNOOZE_MS } = require("../../webapp/static/errtext.js");
const DAY = 24 * 60 * 60 * 1000;

test("surveyDue: отметки нет — зовём", () => {
  assert.strictEqual(surveyDue(null, Date.now()), true);
});

test("surveyDue: опрос пройден — больше не зовём никогда", () => {
  assert.strictEqual(surveyDue({ done: true }, Date.now()), false);
  assert.strictEqual(surveyDue({ done: true }, Date.now() + 365 * DAY), false);
});

test("surveyDue: отложено вчера — молчим", () => {
  const now = Date.now();
  assert.strictEqual(surveyDue({ snoozed: now - DAY }, now), false);
});

test("surveyDue: отложено дольше недели — зовём снова", () => {
  const now = Date.now();
  assert.strictEqual(surveyDue({ snoozed: now - SURVEY_SNOOZE_MS - 1 }, now), true);
});

// Хранилище переживает и битую запись, и переведённые часы. Оба случая решаем
// в пользу показа: потерять просьбу молча хуже, чем позвать второй раз.
test("surveyDue: битая отметка — зовём", () => {
  const now = Date.now();
  assert.strictEqual(surveyDue("мусор", now), true);
  assert.strictEqual(surveyDue({ snoozed: "вчера" }, now), true);
  assert.strictEqual(surveyDue({ snoozed: NaN }, now), true);
});

test("surveyDue: отметка из будущего (часы перевели) — зовём", () => {
  const now = Date.now();
  assert.strictEqual(surveyDue({ snoozed: now + 30 * DAY }, now), true);
});
