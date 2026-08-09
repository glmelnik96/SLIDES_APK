/* Магнитное выравнивание узлов при перетаскивании (webapp/static/diagram_editor.js)
   и раскладка без отрисовки (DiagramEngine.layout), на которой магнит считается. */
const test = require("node:test");
const assert = require("node:assert");
const D = require("../../htmlslides/engine/diagram.js");
const Drag = require("../../webapp/static/diagram_editor.js");

const W = D.CANVAS.W, H = D.CANVAS.H;

const SPEC = {
  kind: "process",
  nodes: [
    { id: "a", label: "Заявка" },
    { id: "b", label: "Проверка" },
    { id: "c", label: "Договор" },
  ],
  edges: [],
};

test("layout: позиции узлов без отрисовки, сдвиги учтены", () => {
  const auto = D.layout(SPEC);
  assert.deepStrictEqual(Object.keys(auto).sort(), ["a", "b", "c"]);
  ["x", "y", "w", "h"].forEach((k) => assert.ok(typeof auto.a[k] === "number"));
  const moved = D.layout({ ...SPEC, offsets: { a: { dx: 30, dy: -20 } } });
  assert.strictEqual(moved.a.x, auto.a.x + 30);
  assert.strictEqual(moved.a.y, auto.a.y - 20);
  assert.strictEqual(moved.b.x, auto.b.x);          // соседей сдвиг не трогает
  assert.strictEqual(D.layout({ kind: "нет-такого", nodes: [{ id: "a" }] }), null);
});

const AN = (c, half) => Drag.anchors({ x: c, w: half * 2 }, "x");

test("snapAxis: рядом с авто-местом магнит возвращает узел домой", () => {
  const r = Drag.snapAxis(5, AN(100, 40), AN(900, 40), 14);
  assert.strictEqual(r.adj, -5);                    // сдвиг обнулён
  assert.strictEqual(r.at, 100);                    // направляющая по центру узла
  assert.strictEqual(Drag.snapAxis(40, AN(100, 40), AN(900, 40), 14), null);
});

test("snapAxis: тянется к ближайшей цели, дальние игнорирует", () => {
  // центр узла в авто-раскладке 100, тянем на +196 → 296; цель 300 в допуске
  const r = Drag.snapAxis(196, AN(100, 40), AN(300, 40).concat(AN(800, 40)), 14);
  assert.strictEqual(r.adj, 4);
  assert.strictEqual(r.at, 300);
  assert.strictEqual(Drag.snapAxis(196, AN(100, 40), AN(800, 40), 14), null);
});

test("snapAxis: центр не липнет к чужому краю — это не выравнивание", () => {
  // край цели ровно там, куда тянут центр (300); одноимённых опор рядом нет
  assert.strictEqual(Drag.snapAxis(200, AN(100, 50), AN(400, 100), 14), null);
});

test("snapOffset: центр к центру соседа + направляющая по оси X", () => {
  const auto = { x: 200, y: 360, w: 300, h: 150 };
  const others = [{ x: 900, y: 360, w: 300, h: 150 }];
  const r = Drag.snapOffset({ dx: 695, dy: 3 }, { auto, others });
  assert.strictEqual(r.dx, 700);                    // 200+700 = 900 — центр соседа
  assert.strictEqual(r.dy, 0);                      // по Y уже совпадали → «домой»
  const gx = r.guides.find((g) => g.axis === "x");
  assert.strictEqual(gx.at, 900);
  assert.ok(r.guides.some((g) => g.axis === "y" && g.at === 360));
});

test("snapOffset: края узлов тоже магнитят (левый край к левому)", () => {
  const auto = { x: 200, y: 100, w: 300, h: 150 };  // левый край 50
  const others = [{ x: 900, y: 600, w: 400, h: 150 }]; // левый край 700
  const r = Drag.snapOffset({ dx: 654, dy: 0 }, { auto, others, tol: 14 });
  assert.strictEqual(r.dx, 650);                    // 50+650 = 700 — края совпали
  assert.strictEqual(r.guides.find((g) => g.axis === "x").at, 700);
});

test("snapOffset: центр холста — тоже ось", () => {
  const auto = { x: 100, y: 100, w: 200, h: 100 };
  const r = Drag.snapOffset({ dx: W / 2 - 100 - 6, dy: H / 2 - 100 + 5 }, { auto, others: [] });
  assert.strictEqual(r.dx, W / 2 - 100);
  assert.strictEqual(r.dy, H / 2 - 100);
});

test("snapOffset: вне допуска — сдвиг как тянули, направляющих нет", () => {
  const auto = { x: 200, y: 200, w: 100, h: 100 };
  const r = Drag.snapOffset({ dx: 333, dy: 180 }, { auto, others: [{ x: 900, y: 700, w: 100, h: 100 }] });
  assert.strictEqual(r.dx, 333);
  assert.strictEqual(r.dy, 180);
  assert.deepStrictEqual(r.guides, []);
});

test("snapAxis: при равном расстоянии направляющая идёт по центрам", () => {
  // Боксы одного размера: центр и обе грани совпадают одновременно. Ось должна
  // объяснять выравнивание — значит центр, а не случайная грань.
  const r = Drag.snapAxis(191, AN(100, 110), AN(300, 110), 14);
  assert.strictEqual(r.at, 300);
  assert.strictEqual(r.k, "c");
});

test("anchors: у трапеции габарит по широкой грани (воронка, пирамида)", () => {
  // Верх 200, низ 400: левый край видимой фигуры — 100−200 = −100, а не 100−100.
  const a = Drag.anchors({ x: 100, w: 200, wBottom: 400 }, "x");
  assert.deepStrictEqual(a.map((p) => p.v), [100, -100, 300]);
});

// Магнит обязан работать на ЛЮБОМ типе схемы: раскладка у всех разная
// (ряды, окружность, трапеции, круги Венна, дорожки), но контракт один —
// конечные координаты, возврат «домой» и притяжение центра к центру соседа.
const KINDS = ["flowchart", "process", "cycle", "funnel", "hierarchy", "matrix",
  "pyramid", "hub_spoke", "comparison", "venn", "swimlanes"];
const THREE = [{ id: "a", label: "А" }, { id: "b", label: "Б" }, { id: "c", label: "В" }];

KINDS.forEach((kind) => {
  test(`магнит на типе «${kind}»`, () => {
    const auto = D.layout({
      kind: kind, nodes: THREE,
      edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }],
    });
    assert.ok(auto, "раскладка не собралась");
    assert.deepStrictEqual(Object.keys(auto).sort(), ["a", "b", "c"]);
    Object.keys(auto).forEach((id) => {
      ["x", "y", "w", "h"].forEach((k) =>
        assert.ok(Number.isFinite(auto[id][k]), `${id}.${k} = ${auto[id][k]}`));
    });
    const others = [auto.b, auto.c];
    const home = Drag.snapOffset({ dx: 4, dy: -3 }, { auto: auto.a, others });
    assert.strictEqual(home.dx, 0);
    assert.strictEqual(home.dy, 0);
    // Промах в 6 и 5 единиц по осям — узел должен сесть точно в центр соседа.
    const s = Drag.snapOffset(
      { dx: auto.b.x - auto.a.x + 6, dy: auto.b.y - auto.a.y - 5 }, { auto: auto.a, others });
    assert.ok(Math.abs(auto.a.x + s.dx - auto.b.x) < 1e-9, "по X не притянулся");
    assert.ok(Math.abs(auto.a.y + s.dy - auto.b.y) < 1e-9, "по Y не притянулся");
  });
});

test("pruneZero: нулевые сдвиги не хранятся (пустой offsets = авто-режим)", () => {
  const off = { a: { dx: 0, dy: 0 }, b: { dx: 0.2, dy: -0.3 }, c: { dx: 40, dy: 0 } };
  assert.deepStrictEqual(Drag.pruneZero(off), { c: { dx: 40, dy: 0 } });
});

/* ---- перенос подписей при смене типа схемы ---- */
// Примеры каталога (htmlslides/diagrams/catalog.py) в том виде, в каком их
// отдаёт /api/diagrams/catalog: смена типа кладёт в слайд именно их.
const S_PROCESS = { kind: "process", nodes: [
  { id: "s1", label: "Аудит инфраструктуры" }, { id: "s2", label: "План миграции" },
  { id: "s3", label: "Пилотный перенос" },
  { id: "s4", label: "Промышленная миграция", accent: true },
  { id: "s5", label: "Сопровождение" }] };
const S_MATRIX = { kind: "matrix", meta: { x_axis: "Стоимость", y_axis: "Эффект" },
  nodes: [{ id: "q1", label: "Быстрые победы", accent: true }, { id: "q2", label: "Стратегические" },
          { id: "q3", label: "Мелочи" }, { id: "q4", label: "Отложить" }] };
const S_FLOW = { kind: "flowchart", direction: "right",
  nodes: [{ id: "start", label: "Заявка", shape: "start" },
          { id: "check", label: "Проверка", shape: "process" },
          { id: "ok", label: "Полные?", shape: "decision" }],
  edges: [{ from: "start", to: "check" }, { from: "check", to: "ok" }] };
const clone = (o) => JSON.parse(JSON.stringify(o));
const CYCLE6 = { kind: "cycle", nodes: ["Сбор данных", "Узкие места", "Гипотеза",
  "Пилот", "Тиражирование", "Замер"].map((l, i) => ({ id: "c" + i, label: l })) };

test("смена типа переносит подписи узлов, а не подменяет их примером", () => {
  // Регрессия: цикл из шести этапов молча становился «Аудитом инфраструктуры»
  // и далее по списку — пользовательский текст исчезал без предупреждения.
  const out = Drag.carryLabels(clone(S_PROCESS), CYCLE6);
  assert.deepStrictEqual(out.nodes.map((n) => n.label),
    ["Сбор данных", "Узкие места", "Гипотеза", "Пилот", "Тиражирование", "Замер"]);
  assert.strictEqual(out.nodes.length, 6);            // тип вмещает больше пяти
  assert.strictEqual(new Set(out.nodes.map((n) => n.id)).size, 6, "id должны быть уникальны");
  // акцент примера остаётся ровно один — клон последнего узла его не тащит
  assert.strictEqual(out.nodes.filter((n) => n.accent).length, 1);
});

test("смена типа не ломает жёсткие капы (матрица — ровно 4 узла)", () => {
  const out = Drag.carryLabels(clone(S_MATRIX), CYCLE6);
  assert.strictEqual(out.nodes.length, 4);
  assert.deepStrictEqual(out.nodes.map((n) => n.label),
    ["Сбор данных", "Узкие места", "Гипотеза", "Пилот"]);
  assert.deepStrictEqual(out.nodes.map((n) => n.id), ["q1", "q2", "q3", "q4"]);
  assert.deepStrictEqual(out.meta, { x_axis: "Стоимость", y_axis: "Эффект" });
});

test("смена типа режет список до верхнего капа типа", () => {
  // Схема пускает в цикл максимум 8 стадий (дальше стрелки короче наконечника).
  // Пока NODE_RANGE обещал 12, воронка из десяти слоёв переносилась в цикл
  // целиком — и тот же редактор, который её собрал, отказывался её сохранить.
  const ten = { kind: "funnel", nodes: Array.from({ length: 10 },
    (_, i) => ({ id: "f" + i, label: "Слой " + (i + 1) })) };
  const out = Drag.carryLabels(clone(CYCLE6), ten);
  assert.strictEqual(out.nodes.length, 8);
  assert.strictEqual(out.nodes[7].label, "Слой 8");
  assert.strictEqual(new Set(out.nodes.map((n) => n.id)).size, 8);
});

test("смена типа добивает узлы до минимума типа", () => {
  // Две стороны сравнения → цикл: три узла минимум, третий берётся из примера.
  const two = { kind: "comparison", nodes: [{ id: "a", label: "Своё" }, { id: "b", label: "Облако" }] };
  const out = Drag.carryLabels(clone({ kind: "cycle", nodes: [
    { id: "c1", label: "Планирование" }, { id: "c2", label: "Внедрение" },
    { id: "c3", label: "Замер" }, { id: "c4", label: "Улучшение" }] }), two);
  assert.strictEqual(out.nodes.length, 3);
  assert.deepStrictEqual(out.nodes.map((n) => n.label), ["Своё", "Облако", "Замер"]);
});

test("у типов со структурой в рёбрах список узлов не меняется", () => {
  // Наращивание сломало бы рёбра примера: они адресуют узлы по id.
  const out = Drag.carryLabels(clone(S_FLOW), CYCLE6);
  assert.deepStrictEqual(out.nodes.map((n) => n.id), ["start", "check", "ok"]);
  assert.deepStrictEqual(out.nodes.map((n) => n.label),
    ["Сбор данных", "Узкие места", "Гипотеза"]);
  assert.deepStrictEqual(out.nodes.map((n) => n.shape), ["start", "process", "decision"]);
  assert.strictEqual(out.edges.length, 2);
});

test("узлы сверх примера не получают чужих чисел и сроков", () => {
  // Регрессия: клон последнего узла примера тащил его величину — воронка из
  // шести этапов показывала хвост одинаковых «280», которых человек не вводил,
  // и они же задавали ширину ступени. То же со стартовым периодом план-графика.
  const six = { kind: "cycle", nodes: CYCLE6.nodes };
  const funnel = { kind: "funnel", nodes: [
    { id: "f1", label: "Лиды", value: "12000" },
    { id: "f2", label: "Квалификация", value: "3400" },
    { id: "f3", label: "Сделки", value: "280" }] };
  const out = Drag.carryLabels(clone(funnel), six);
  assert.strictEqual(out.nodes.length, 6);
  assert.deepStrictEqual(out.nodes.map((n) => n.value),
    ["12000", "3400", "280", undefined, undefined, undefined]);

  const gantt = { kind: "gantt_lite", nodes: [
    { id: "g1", label: "Аудит", value: "2", level: 0 },
    { id: "g2", label: "Миграция", value: "3", level: 2 }] };
  const g = Drag.carryLabels(clone(gantt), six);
  assert.strictEqual(g.nodes.length, 6);
  assert.deepStrictEqual(g.nodes.map((n) => n.level),
    [0, 2, undefined, undefined, undefined, undefined]);
  assert.deepStrictEqual(g.nodes.map((n) => n.value),
    ["2", "3", undefined, undefined, undefined, undefined]);
});

test("без старой схемы пример остаётся примером", () => {
  assert.deepStrictEqual(Drag.carryLabels(clone(S_PROCESS), null), S_PROCESS);
  assert.deepStrictEqual(Drag.carryLabels(clone(S_PROCESS), { nodes: [] }), S_PROCESS);
});
