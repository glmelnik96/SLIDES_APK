const test = require("node:test");
const assert = require("node:assert");
const { errText, SAVE_STATUS, REBUILD_LABEL, plural, estimateLine, rebuildEstimate,
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

test("REBUILD_LABEL — одно имя кнопки в двух состояниях", () => {
  assert.strictEqual(REBUILD_LABEL.idle, "Проверить и улучшить слайды");
  assert.strictEqual(REBUILD_LABEL.busy, "Запускаю…");
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

test("rebuildEstimate: улучшение не дольше сборки того же с нуля", () => {
  // Улучшение — только хвост сборки (без разбора/планирования/заполнения),
  // поэтому обещать за него больше, чем за полную сборку, нельзя. Диалог когда-то
  // просил «n–2n мин» — вчетверо больше замера (8 слайдов ≈ 2,5 мин).
  for (const n of [1, 3, 8, 20, 40]) {
    const full = parseInt(estimateLine(n, "d.md").text.match(/(\d+) мин/)[1], 10);
    assert.ok(rebuildEstimate(n) <= full, `n=${n}`);
  }
  assert.strictEqual(rebuildEstimate(1), 1);   // не «0 минут»
  assert.strictEqual(rebuildEstimate(8), 4);
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
