const test = require("node:test");
const assert = require("node:assert");
const D = require("../../htmlslides/engine/diagram.js");

const W = D.CANVAS.W, H = D.CANVAS.H;

/* Сэмпл блок-схемы каталога (catalog.py) — с decision и обратным ребром. */
const FLOW = {
  kind: "flowchart", direction: "right",
  nodes: [
    { id: "start", label: "Заявка клиента", shape: "start" },
    { id: "check", label: "Проверка данных", shape: "process" },
    { id: "ok", label: "Данные полные?", shape: "decision" },
    { id: "fix", label: "Запрос уточнений", shape: "process" },
    { id: "deal", label: "Оформление договора", shape: "process", accent: true },
    { id: "done", label: "Услуга подключена", shape: "end" },
  ],
  edges: [
    { from: "start", to: "check" },
    { from: "check", to: "ok" },
    { from: "ok", to: "deal", label: "да" },
    { from: "ok", to: "fix", label: "нет" },
    { from: "fix", to: "check" },
    { from: "deal", to: "done" },
  ],
};

function inCanvas(pos) {
  Object.keys(pos).forEach((id) => {
    const p = pos[id];
    assert.ok(p.x - p.w / 2 >= -1 && p.x + p.w / 2 <= W + 1, `${id}: x за холстом`);
    assert.ok(p.y - p.h / 2 >= -1 && p.y + p.h / 2 <= H + 1, `${id}: y за холстом`);
  });
}

test("num: величины из строк с пробелами/процентами/запятой", () => {
  assert.strictEqual(D.num("12 000"), 12000);
  assert.strictEqual(D.num("45%"), 45);
  assert.strictEqual(D.num("1,5"), 1.5);
  assert.strictEqual(D.num("1.234.567"), 1234567);
  assert.strictEqual(D.num("—"), 0);
  assert.strictEqual(D.num(null), 0);
});

test("flowRanks: BFS от истока, обратное ребро не повышает ранг", () => {
  const r = D.flowRanks(FLOW.nodes, FLOW.edges);
  assert.strictEqual(r.start, 0);
  assert.strictEqual(r.check, 1);
  assert.strictEqual(r.ok, 2);
  assert.ok(r.deal > r.ok && r.fix > r.ok);
  assert.ok(r.done > r.deal);
});

test("flowRanks: чистый цикл без истоков не зависает", () => {
  const nodes = [{ id: "a" }, { id: "b" }, { id: "c" }];
  const edges = [{ from: "a", to: "b" }, { from: "b", to: "c" }, { from: "c", to: "a" }];
  const r = D.flowRanks(nodes, edges);
  assert.deepStrictEqual(Object.keys(r).sort(), ["a", "b", "c"]);
  assert.strictEqual(r.a, 0);
});

test("flowRanks: изолированный узел получает ранг", () => {
  const nodes = [{ id: "a" }, { id: "b" }, { id: "lone" }];
  const r = D.flowRanks(nodes, [{ from: "a", to: "b" }]);
  assert.ok("lone" in r);
});

test("layoutFlowchart: все узлы в холсте, ранги растут слева направо", () => {
  const { nodes: pos, links } = D.layoutFlowchart(FLOW);
  assert.strictEqual(Object.keys(pos).length, 6);
  inCanvas(pos);
  assert.ok(pos.start.x < pos.check.x && pos.check.x < pos.ok.x);
  assert.strictEqual(links.length, FLOW.edges.length);
  links.forEach((l) => assert.ok(l.points.length >= 2));
});

test("layoutFlowchart: direction down — ранги растут сверху вниз", () => {
  const spec = Object.assign({}, FLOW, { direction: "down" });
  const { nodes: pos } = D.layoutFlowchart(spec);
  inCanvas(pos);
  assert.ok(pos.start.y < pos.check.y && pos.check.y < pos.ok.y);
});

test("layoutFlowchart: метка ребра доносится до линка", () => {
  const { links } = D.layoutFlowchart(FLOW);
  const labels = links.map((l) => l.label).filter(Boolean).sort();
  assert.deepStrictEqual(labels, ["да", "нет"]);
});

test("layoutProcess: один ряд ≤6, серпантин при >6", () => {
  const six = { kind: "process", nodes: [1, 2, 3, 4, 5, 6].map((i) => ({ id: "s" + i })) };
  const p6 = D.layoutProcess(six).nodes;
  inCanvas(p6);
  const ys = new Set(Object.values(p6).map((p) => p.y));
  assert.strictEqual(ys.size, 1);           // один ряд

  const eight = { kind: "process", nodes: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => ({ id: "s" + i })) };
  const p8 = D.layoutProcess(eight).nodes;
  inCanvas(p8);
  assert.strictEqual(new Set(Object.values(p8).map((p) => p.y)).size, 2); // два ряда
  // серпантин: последний узел второго ряда левее первого узла второго ряда
  assert.ok(p8.s8.x < p8.s5.x);
});

test("layoutProcess: последовательные линки без рёбер в спеке", () => {
  const spec = { kind: "process", nodes: [{ id: "a" }, { id: "b" }, { id: "c" }] };
  const { links } = D.layoutProcess(spec);
  assert.deepStrictEqual(links.map((l) => l.from + "→" + l.to), ["a→b", "b→c"]);
});

test("layoutCycle: узлы на окружности, старт сверху, линки замыкаются", () => {
  const spec = { kind: "cycle", nodes: [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }] };
  const { nodes: pos, links } = D.layoutCycle(spec);
  inCanvas(pos);
  assert.ok(pos.a.y < pos.c.y);             // старт сверху, противоположный снизу
  assert.strictEqual(links.length, 4);      // замкнутый круг
  assert.strictEqual(links[3].to, "a");
  links.forEach((l) => assert.ok(l.arc && l.arc.r > 0));
});

test("layoutFunnel: ширины по value убывают, без value — линейное сужение", () => {
  const byVal = {
    kind: "funnel",
    nodes: [{ id: "f1", value: "12000" }, { id: "f2", value: "3400" },
            { id: "f3", value: "280" }],
  };
  const p = D.layoutFunnel(byVal).nodes;
  inCanvas(p);
  assert.ok(p.f1.w > p.f2.w && p.f2.w > p.f3.w);
  assert.ok(p.f1.y < p.f2.y && p.f2.y < p.f3.y);   // сверху вниз
  assert.ok(p.f1.wBottom <= p.f1.w);

  const plain = { kind: "funnel", nodes: [{ id: "a" }, { id: "b" }, { id: "c" }] };
  const q = D.layoutFunnel(plain).nodes;
  assert.ok(q.a.w > q.b.w && q.b.w > q.c.w);
});

test("layoutHierarchy: корень над детьми, родитель по центру потомков", () => {
  const spec = {
    kind: "hierarchy",
    nodes: [{ id: "ceo" }, { id: "dev" }, { id: "ops" }, { id: "be" }, { id: "fe" }],
    edges: [{ from: "ceo", to: "dev" }, { from: "ceo", to: "ops" },
            { from: "dev", to: "be" }, { from: "dev", to: "fe" }],
  };
  const { nodes: pos, links } = D.layoutHierarchy(spec);
  inCanvas(pos);
  assert.ok(pos.ceo.y < pos.dev.y && pos.dev.y < pos.be.y);
  assert.ok(Math.abs(pos.dev.x - (pos.be.x + pos.fe.x) / 2) < 1);
  assert.strictEqual(links.length, 4);
});

test("layoutHierarchy: узел без связей не теряется", () => {
  const spec = {
    kind: "hierarchy",
    nodes: [{ id: "root" }, { id: "kid" }, { id: "orphan" }],
    edges: [{ from: "root", to: "kid" }],
  };
  const { nodes: pos } = D.layoutHierarchy(spec);
  assert.ok("orphan" in pos);
  inCanvas(pos);
});

test("applyOffsets: сдвиг применяется, центр клампится в холст", () => {
  const pos = { a: { x: 900, y: 360, w: 200, h: 100 } };
  D.applyOffsets(pos, { a: { dx: 50, dy: -30 } });
  assert.strictEqual(pos.a.x, 950);
  assert.strictEqual(pos.a.y, 330);

  const far = { a: { x: 900, y: 360, w: 200, h: 100 } };
  D.applyOffsets(far, { a: { dx: 99999, dy: 99999 } });
  assert.strictEqual(far.a.x, W - 100);     // кламп по краю с учётом ширины
  assert.strictEqual(far.a.y, H - 50);
});

test("applyOffsets: без offsets — только кламп, позиции стабильны", () => {
  const pos = { a: { x: 900, y: 360, w: 200, h: 100 } };
  D.applyOffsets(pos, undefined);
  assert.strictEqual(pos.a.x, 900);
  assert.strictEqual(pos.a.y, 360);
});

/* ---------------- волна 2 ---------------- */

test("layoutMatrix: 4 карточки по квадрантам в порядке чтения", () => {
  const spec = { kind: "matrix", nodes: ["q1", "q2", "q3", "q4"].map((id) => ({ id })) };
  const { nodes: pos, links } = D.layoutMatrix(spec);
  inCanvas(pos);
  assert.strictEqual(links.length, 0);
  assert.ok(pos.q1.x < W / 2 && pos.q1.y < H / 2);   // верх-лево
  assert.ok(pos.q2.x > W / 2 && pos.q2.y < H / 2);   // верх-право
  assert.ok(pos.q3.x < W / 2 && pos.q3.y > H / 2);   // низ-лево
  assert.ok(pos.q4.x > W / 2 && pos.q4.y > H / 2);   // низ-право
});

test("layoutPyramid: ширины растут сверху вниз, совместимо с renderFunnel", () => {
  const spec = { kind: "pyramid", nodes: [{ id: "p1" }, { id: "p2" }, { id: "p3" }] };
  const { nodes: pos } = D.layoutPyramid(spec);
  inCanvas(pos);
  assert.ok(pos.p1.y < pos.p2.y && pos.p2.y < pos.p3.y);   // вершина сверху
  assert.ok(pos.p1.w < pos.p2.w && pos.p2.w < pos.p3.w);
  // каждый слой — трапеция, низ шире верха; стыки слоёв сходятся
  assert.ok(pos.p1.wBottom > pos.p1.w);
  assert.ok(Math.abs(pos.p1.wBottom - pos.p2.w) < 1);
});

test("layoutHubSpoke: первый узел в центре, лучи вокруг, авто-линки", () => {
  const spec = { kind: "hub_spoke",
    nodes: [{ id: "hub" }, { id: "a" }, { id: "b" }, { id: "c" }] };
  const { nodes: pos, links } = D.layoutHubSpoke(spec);
  inCanvas(pos);
  assert.strictEqual(pos.hub.x, W / 2);
  assert.strictEqual(pos.hub.y, H / 2);
  assert.deepStrictEqual(links.map((l) => l.from + "→" + l.to).sort(),
    ["hub→a", "hub→b", "hub→c"]);
  // стрелка обрезана по границам карточек: не начинается в самом центре хаба
  links.forEach((l) => {
    assert.strictEqual(l.points.length, 2);
    const [x0, y0] = l.points[0];
    assert.ok(Math.abs(x0 - W / 2) > 1 || Math.abs(y0 - H / 2) > 1);
  });
});

test("layoutComparison: две колонки по lane, элементы стопкой", () => {
  const spec = { kind: "comparison", nodes: [
    { id: "a1", lane: "До" }, { id: "a2", lane: "До" },
    { id: "b1", lane: "После" }, { id: "b2", lane: "После" },
  ] };
  const out = D.layoutComparison(spec);
  inCanvas(out.nodes);
  assert.deepStrictEqual(out.lanes, ["До", "После"]);
  assert.ok(out.nodes.a1.x < W / 2 && out.nodes.b1.x > W / 2);
  assert.strictEqual(out.nodes.a1.x, out.nodes.a2.x);       // колонка
  assert.ok(out.nodes.a1.y < out.nodes.a2.y);               // стопка
});

test("layoutVenn: 2 и 3 круга, метки оттянуты от центра относительно", () => {
  const two = { kind: "venn", nodes: [{ id: "v1" }, { id: "v2" }] };
  const p2 = D.layoutVenn(two).nodes;
  assert.ok(p2.v1.x < p2.v2.x && p2.v1.r > 0);
  assert.ok(p2.v1.labelDx < 0 && p2.v2.labelDx > 0);        // наружу
  // круги пересекаются: расстояние центров меньше суммы радиусов
  assert.ok(p2.v2.x - p2.v1.x < p2.v1.r + p2.v2.r);

  const three = { kind: "venn", nodes: [{ id: "a" }, { id: "b" }, { id: "c" }] };
  const p3 = D.layoutVenn(three).nodes;
  assert.ok(p3.a.y < p3.b.y && Math.abs(p3.b.y - p3.c.y) < 1);
});

test("layoutSwimlanes: ряды по lane, ранги по рёбрам, коллизии разведены", () => {
  const spec = { kind: "swimlanes", nodes: [
    { id: "w1", lane: "Клиент" }, { id: "w2", lane: "Инженер" },
    { id: "w3", lane: "Инженер" }, { id: "w4", lane: "Клиент" },
  ], edges: [
    { from: "w1", to: "w2" }, { from: "w2", to: "w3" }, { from: "w3", to: "w4" },
  ] };
  const out = D.layoutSwimlanes(spec);
  inCanvas(out.nodes);
  assert.deepStrictEqual(out.lanes, ["Клиент", "Инженер"]);
  assert.strictEqual(out.nodes.w1.y, out.nodes.w4.y);       // одна дорожка
  assert.strictEqual(out.nodes.w2.y, out.nodes.w3.y);
  assert.ok(out.nodes.w1.y !== out.nodes.w2.y);
  assert.ok(out.nodes.w1.x < out.nodes.w2.x && out.nodes.w2.x < out.nodes.w3.x);
  assert.strictEqual(out.links.length, 3);
});

test("layoutSwimlanes: без рёбер — порядок списка, последовательные линки", () => {
  const spec = { kind: "swimlanes", nodes: [
    { id: "a", lane: "X" }, { id: "b", lane: "Y" }, { id: "c", lane: "X" },
  ] };
  const out = D.layoutSwimlanes(spec);
  assert.ok(out.nodes.a.x < out.nodes.b.x && out.nodes.b.x < out.nodes.c.x);
  assert.deepStrictEqual(out.links.map((l) => l.from + "→" + l.to), ["a→b", "b→c"]);
});

test("layoutSwimlanes: одинаковый ранг в одной дорожке не слипается", () => {
  // ветвление: b и c оба ранга 1, обе в дорожке Y — вторую разводим вправо
  const spec = { kind: "swimlanes", nodes: [
    { id: "a", lane: "X" }, { id: "b", lane: "Y" }, { id: "c", lane: "Y" },
  ], edges: [{ from: "a", to: "b" }, { from: "a", to: "c" }] };
  const out = D.layoutSwimlanes(spec);
  assert.ok(out.nodes.b.x !== out.nodes.c.x);
});

test("LAYOUTS покрывает все типы волн 1–2", () => {
  assert.deepStrictEqual(Object.keys(D.LAYOUTS).sort(),
    ["comparison", "cycle", "flowchart", "funnel", "hierarchy", "hub_spoke",
     "matrix", "process", "pyramid", "swimlanes", "venn"]);
});

test("CANVAS синхронизирован со схемой (1800×720)", () => {
  assert.strictEqual(W, 1800);
  assert.strictEqual(H, 720);
});
