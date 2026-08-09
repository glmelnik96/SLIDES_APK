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

/* Две ветки развилки, сходящиеся друг в друга (manager→reserve), стоят в одной
   колонке. Обход низом вёл линию сквозь целевую плашку и оставлял хвост под ней. */
test("layoutFlowchart: ребро внутри ранга идёт напрямую, не сквозь плашку", () => {
  const spec = {
    kind: "flowchart", direction: "right",
    nodes: [{ id: "a" }, { id: "up" }, { id: "down" }, { id: "z" }],
    edges: [{ from: "a", to: "up" }, { from: "a", to: "down" },
            { from: "up", to: "down" }, { from: "down", to: "z" }],
  };
  const { nodes: pos, links } = D.layoutFlowchart(spec);
  const l = links.find((x) => x.from === "up" && x.to === "down");
  assert.strictEqual(l.points.length, 2);                       // прямая, без обхода
  const [s, t] = l.points;
  assert.strictEqual(s[1], pos.up.y + pos.up.h / 2);            // низ источника
  assert.strictEqual(t[1], pos.down.y - pos.down.h / 2);        // верх цели
  l.points.forEach((p) => assert.ok(p[1] <= H, "линия ушла под холст"));
});

test("layoutFlowchart: настоящее обратное ребро по-прежнему идёт обходом", () => {
  const { links } = D.layoutFlowchart(FLOW);
  const back = links.find((l) => l.from === "fix" && l.to === "check");
  assert.strictEqual(back.points.length, 4);
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

/* ---- волна 3 ---- */

const GANTT = { kind: "gantt_lite", nodes: [
  { id: "a", label: "Аудит", value: "2" },
  { id: "b", label: "Проект", value: "3", level: 2 },
  { id: "c", label: "Пилот", value: "2", level: 2 },
  { id: "d", label: "Запуск", value: "3" },
] };

test("layoutGantt: полосы по строкам, длительность задаёт ширину", () => {
  const out = D.layoutGantt(GANTT);
  inCanvas(out.nodes);
  const p = out.nodes;
  // строки идут сверху вниз в порядке списка, шкала — общая
  assert.ok(p.a.y < p.b.y && p.b.y < p.c.y && p.c.y < p.d.y);
  assert.strictEqual(out.total, 7);            // d каскадом за c: 2+2 → 4, +3
  assert.ok(p.b.w > p.a.w);                    // 3 периода шире 2
  // общий level = общий левый край (x — центр полосы, ширины разные)
  const left = (q) => q.x - q.w / 2;
  assert.ok(Math.abs(left(p.b) - left(p.c)) < 8);
  assert.strictEqual(out.links.length, 0);
});

test("layoutGantt: без level работа встаёт за предыдущей", () => {
  const out = D.layoutGantt({ kind: "gantt_lite", nodes: [
    { id: "a", label: "Раз", value: "2" }, { id: "b", label: "Два", value: "2" },
  ] });
  assert.ok(out.nodes.b.x > out.nodes.a.x);
  assert.strictEqual(out.total, 4);
});

test("layoutGantt: единицы и запятая в длительности не ломают шкалу", () => {
  const out = D.layoutGantt({ kind: "gantt_lite", nodes: [
    { id: "a", label: "Раз", value: "2 мес" }, { id: "b", label: "Два", value: "1,5" },
  ] });
  inCanvas(out.nodes);
  assert.strictEqual(out.total, 3.5);
});

test("layoutSteps: ступени идут вправо и вверх", () => {
  const out = D.layoutSteps({ kind: "steps", nodes: [
    { id: "a", label: "Раз" }, { id: "b", label: "Два" }, { id: "c", label: "Три" },
  ] });
  inCanvas(out.nodes);
  const p = out.nodes;
  assert.ok(p.a.x < p.b.x && p.b.x < p.c.x);
  assert.ok(p.a.y > p.b.y && p.b.y > p.c.y);   // y растёт вниз: выше = меньше
  assert.strictEqual(out.links.length, 0);
});

const MIND = { kind: "mindmap", nodes: [
  { id: "core", label: "Центр" }, { id: "r1", label: "Право" },
  { id: "l1", label: "Лево" }, { id: "r2", label: "Ещё право" },
  { id: "k", label: "Подветвь" },
], edges: [
  { from: "core", to: "r1" }, { from: "core", to: "l1" },
  { from: "core", to: "r2" }, { from: "r1", to: "k" },
] };

test("layoutMindmap: центр посередине, ветви через одну по сторонам", () => {
  const out = D.layoutMindmap(MIND);
  inCanvas(out.nodes);
  const p = out.nodes;
  assert.strictEqual(p.core.x, W / 2);
  assert.ok(p.r1.x > p.core.x && p.r2.x > p.core.x);  // чётные ветви — вправо
  assert.ok(p.l1.x < p.core.x);                       // нечётные — влево
  assert.ok(p.k.x > p.r1.x);                          // подветвь дальше ветви
  assert.ok(out.links.every((l) => l.curve === true));
});

test("layoutMindmap: узел без связи становится ветвью центра", () => {
  const out = D.layoutMindmap({ kind: "mindmap", nodes: [
    { id: "core", label: "Центр" }, { id: "a", label: "Раз" },
    { id: "lone", label: "Один" },
  ], edges: [{ from: "core", to: "a" }] });
  inCanvas(out.nodes);
  assert.ok(out.links.some((l) => l.from === "core" && l.to === "lone"));
});

test("layoutMindmap: кольцо в данных не вешает обход", () => {
  // в собранной деке истина — HTML: data-diagram переживает правку руками,
  // и раскладка обязана пережить то, что схема бы завернула
  const out = D.layoutMindmap({ kind: "mindmap", nodes: [
    { id: "core", label: "Центр" }, { id: "a", label: "А" }, { id: "b", label: "Б" },
  ], edges: [{ from: "core", to: "a" }, { from: "a", to: "b" }, { from: "b", to: "a" }] });
  assert.deepStrictEqual(Object.keys(out.nodes).sort(), ["a", "b", "core"]);
  inCanvas(out.nodes);
});

const NET = { kind: "network", nodes: [
  { id: "api", label: "Шлюз" }, { id: "auth", label: "Аутентификация" },
  { id: "cat", label: "Каталог" }, { id: "db", label: "База" },
  { id: "mon", label: "Мониторинг" },
], edges: [
  { from: "api", to: "auth" }, { from: "api", to: "cat" },
  { from: "auth", to: "db" }, { from: "cat", to: "db" }, { from: "db", to: "mon" },
] };

test("layoutNetwork: узлы в холсте, связи стыкуются к граням", () => {
  const out = D.layoutNetwork(NET);
  inCanvas(out.nodes);
  assert.strictEqual(out.links.length, 5);
  out.links.forEach((l) => l.points.forEach((pt) => {
    assert.ok(Number.isFinite(pt[0]) && Number.isFinite(pt[1]));
  }));
});

test("layoutNetwork: раскладка детерминирована — сейв запекает SVG", () => {
  // повторный рендер поверх запечённого SVG обязан дать ТЕ ЖЕ координаты,
  // иначе схема «прыгает» при каждой перезагрузке деки
  const a = D.layoutNetwork(NET).nodes, b = D.layoutNetwork(NET).nodes;
  assert.deepStrictEqual(a, b);
});

test("layoutNetwork: связанные узлы ближе несвязанных", () => {
  const p = D.layoutNetwork(NET).nodes;
  const dist = (u, v) => Math.hypot(p[u].x - p[v].x, p[u].y - p[v].y);
  assert.ok(dist("api", "auth") < dist("api", "mon"));
});

test("LAYOUTS покрывает весь каталог типов", () => {
  assert.deepStrictEqual(Object.keys(D.LAYOUTS).sort(),
    ["comparison", "cycle", "flowchart", "funnel", "gantt_lite", "hierarchy",
     "hub_spoke", "matrix", "mindmap", "network", "process", "pyramid", "steps",
     "swimlanes", "venn"]);
});

test("CANVAS синхронизирован со схемой (1800×720)", () => {
  assert.strictEqual(W, 1800);
  assert.strictEqual(H, 720);
});

/* ---- стрелки следуют за сдвинутым узлом (гибридная раскладка) ---- */

// Сдвиг узла раньше двигал только карточку: стрелки оставались на авто-местах
// и схема разъезжалась. relink перекладывает ТОЛЬКО задетые рёбра.
function moveNode(spec, id, dx, dy) {
  const off = {}; off[id] = { dx: dx, dy: dy };
  const s = Object.assign({}, spec, { offsets: off });
  const auto = D.LAYOUTS[s.kind](s);            // геометрия до сдвигов
  const res = D.LAYOUTS[s.kind](s);
  const snap = {};
  Object.keys(res.nodes).forEach((k) => { snap[k] = Object.assign({}, res.nodes[k]); });
  D.applyOffsets(res.nodes, off);
  D.relink(res, snap);
  return { res: res, auto: auto };
}

function endsOnBox(pts, box) {                   // конец ломаной лежит на грани карточки
  const p = pts[pts.length - 1];
  const dx = Math.abs(p[0] - box.x), dy = Math.abs(p[1] - box.y);
  return dx <= box.w / 2 + 12 && dy <= box.h / 2 + 12;
}

test("relink: стрелка догоняет сдвинутый узел (flowchart)", () => {
  const { res } = moveNode(FLOW, "check", 0, 120);
  const link = res.links.find((l) => l.to === "check");
  assert.ok(endsOnBox(link.points, res.nodes.check),
    "конец стрелки должен сидеть на сдвинутой карточке");
});

test("relink: нетронутые рёбра сохраняют геометрию раскладки", () => {
  const spec = { kind: "flowchart", nodes: [
    { id: "a", label: "А" }, { id: "b", label: "Б" }, { id: "c", label: "В" },
  ], edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }] };
  const { res, auto } = moveNode(spec, "a", 0, 90);
  const untouched = res.links.find((l) => l.from === "b" && l.to === "c");
  const before = auto.links.find((l) => l.from === "b" && l.to === "c");
  assert.deepStrictEqual(untouched.points, before.points);
});

test("relink: дуга цикла становится прямой — окружности больше нет", () => {
  const spec = { kind: "cycle", nodes: [
    { id: "a", label: "А" }, { id: "b", label: "Б" }, { id: "c", label: "В" },
  ] };
  const { res } = moveNode(spec, "a", 200, 60);
  const link = res.links.find((l) => l.from === "a");
  assert.strictEqual(link.arc, undefined);
  assert.strictEqual(link.points.length, 2);
  assert.ok(endsOnBox(link.points, res.nodes.b));
});

test("relink: обратное ребро остаётся обходом (обе стыковки в одну грань)", () => {
  const spec = { kind: "flowchart", nodes: [
    { id: "a", label: "А" }, { id: "b", label: "Б" },
  ], edges: [{ from: "a", to: "b" }, { from: "b", to: "a" }] };
  const { res } = moveNode(spec, "b", 0, 80);
  const back = res.links.find((l) => l.from === "b" && l.to === "a");
  assert.strictEqual(back.points.length, 4);
  // обход идёт ниже обеих карточек, а не сквозь них
  const laneY = back.points[1][1];
  assert.ok(laneY > res.nodes.a.y + res.nodes.a.h / 2);
});

test("relink: у всех типов со связями рёбра догоняют узел", () => {
  const N4 = [{ id: "a", label: "А" }, { id: "b", label: "Б" },
              { id: "c", label: "В" }, { id: "d", label: "Г" }];
  const specs = {
    flowchart: { kind: "flowchart", nodes: N4,
      edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }, { from: "c", to: "d" }] },
    process: { kind: "process", nodes: N4 },
    cycle: { kind: "cycle", nodes: N4 },
    hierarchy: { kind: "hierarchy", nodes: N4,
      edges: [{ from: "a", to: "b" }, { from: "a", to: "c" }, { from: "c", to: "d" }] },
    hub_spoke: { kind: "hub_spoke", nodes: N4 },
    swimlanes: { kind: "swimlanes",
      nodes: N4.map((n, i) => Object.assign({ lane: i % 2 ? "Y" : "X" }, n)),
      edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }, { from: "c", to: "d" }] },
    mindmap: { kind: "mindmap", nodes: N4,
      edges: [{ from: "a", to: "b" }, { from: "a", to: "c" }, { from: "b", to: "d" }] },
    network: { kind: "network", nodes: N4,
      edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }, { from: "c", to: "d" }] },
  };
  Object.keys(specs).forEach((kind) => {
    const { res } = moveNode(specs[kind], "b", 90, 70);
    res.links.filter((l) => l.from === "b" || l.to === "b").forEach((l) => {
      const box = res.nodes[l.to === "b" ? "b" : "b"];
      const pts = l.points;
      pts.forEach((p) => assert.ok(Number.isFinite(p[0]) && Number.isFinite(p[1]),
        `${kind}: координата не число`));
      const near = l.to === "b" ? endsOnBox(pts, box)
        : endsOnBox([pts[0]], box);
      assert.ok(near, `${kind}: ребро ${l.from}→${l.to} не догнало узел`);
    });
  });
});

/* --- устойчивость движка: битые данные не должны ронять слайд ------------- */

function fakeHost() {
  return { innerHTML: "", getAttribute: () => null };
}

test("render: битые узлы и рёбра в никуда отсекаются, схема всё равно рисуется", () => {
  // В собранной деке истина — HTML: data-diagram переживает ручную правку, а
  // редактор зовёт render на промежуточных состояниях (узел удалён — ребро ещё
  // нет). Раньше такие данные роняли раскладку, и вместе с ней renderAll.
  const cases = {
    "узел без id": { kind: "process", nodes: [{ id: "a", label: "А" }, null] },
    "ребро в никуда": { kind: "flowchart",
      nodes: [{ id: "a", label: "А" }, { id: "b", label: "Б" }],
      edges: [{ from: "a", to: "b" }, { from: "a", to: "ghost" }] },
    "петля": { kind: "flowchart", nodes: [{ id: "a", label: "А" }],
      edges: [{ from: "a", to: "a" }] },
    "дубль id": { kind: "process",
      nodes: [{ id: "a", label: "А" }, { id: "a", label: "Ещё А" }] },
  };
  Object.keys(cases).forEach((name) => {
    const host = fakeHost();
    D.render(host, cases[name]);
    assert.ok(host.innerHTML.startsWith("<svg"), `${name}: вместо схемы заглушка`);
  });
});

test("render: нечего рисовать — заглушка, а не исключение", () => {
  ["", null, { kind: "process", nodes: [] }, { kind: "нет такого", nodes: [{ id: "a" }] },
   { kind: "process", nodes: [null] }].forEach((spec) => {
    const host = fakeHost();
    D.render(host, spec);
    assert.ok(host.innerHTML.includes("данные недоступны"),
      `${JSON.stringify(spec)}: ожидалась заглушка`);
  });
});

test("render: спек вызывающего не мутируется", () => {
  const spec = { kind: "flowchart",
    nodes: [{ id: "a", label: "А" }, { id: "b", label: "Б" }],
    edges: [{ from: "a", to: "b" }, { from: "a", to: "ghost" }] };
  const before = JSON.stringify(spec);
  D.render(fakeHost(), spec);
  assert.strictEqual(JSON.stringify(spec), before);
});

test("layout: битый спек — null, редактор тащит узел без магнита", () => {
  assert.strictEqual(D.layout({ kind: "process", nodes: [null] }), null);
  assert.strictEqual(D.layout(null), null);
  assert.ok(D.layout({ kind: "process",
    nodes: [{ id: "a", label: "А" }, null] }).a);
});

test("fitFont: длинная подпись ужимается под плашку, короткая — нет", () => {
  // swimlanes режет ширину под число колонок: 166×108 — реальный размер из
  // живого прогона, на котором «Развёртывание инфраструктуры» обрезалось.
  const tight = D.fitFont("Развёртывание инфраструктуры", 166, 108);
  assert.ok(tight < 28, `ожидалось уменьшение кегля, получено ${tight}`);
  assert.strictEqual(D.fitFont("Сбор данных", 300, 110), 28);
  assert.strictEqual(D.fitFont("", 40, 20), 28);
});

test("fitFont: слово шире плашки не оставляют крупным", () => {
  const word = "Электроэнцефалография";
  const fs = D.fitFont(word, 150, 100);
  assert.ok(fs * 0.6 * word.length <= 150 - 24 || fs === 16,
    `слово всё ещё шире плашки при кегле ${fs}`);
});

test("fitFont: перенос считается по словам, а не делением длины", () => {
  // 250×110 — плашка flowchart. По символам подпись «влезала» в две строки, по
  // словам их три: на этой разнице низ и обрезался в живом прогоне.
  assert.ok(D.fitFont("Проверка данных менеджером", 250, 110) <= 24);
});

test("render: кегль подписи попадает в разметку", () => {
  const host = fakeHost();
  D.render(host, { kind: "swimlanes",
    nodes: [{ id: "a", label: "Развёртывание инфраструктуры", lane: "Тех" },
            { id: "b", label: "Сбор", lane: "Продажи" }] });
  assert.ok(/font-size:\d+px/.test(host.innerHTML), "кегль не проставлен");
});

test("render: кегль подписей общий на всю схему", () => {
  const host = fakeHost();
  D.render(host, { kind: "swimlanes", nodes: [
    { id: "a", label: "Развёртывание инфраструктуры", lane: "Тех" },
    { id: "b", label: "Сбор", lane: "Продажи" },
    { id: "c", label: "Проверка договора", lane: "Юристы" },
  ] });
  // подписи дорожек рисует декор — считаем только кегли внутри карточек
  const sizes = new Set(host.innerHTML.split("data-node-id").slice(1)
    .map((chunk) => (chunk.match(/font-size:(\d+)px/) || [])[1]));
  assert.strictEqual(sizes.size, 1, `разнобой кеглей: ${[...sizes].join(", ")}`);
});

test("render: каждый тип каталога рисуется и таскается за узел", () => {
  // data-node-id — ручка drag'а редактора: тип без неё нельзя двигать руками
  const specs = {
    gantt_lite: GANTT, steps: { kind: "steps", nodes: [
      { id: "a", label: "Раз" }, { id: "b", label: "Два" }] },
    mindmap: MIND, network: NET,
  };
  Object.keys(specs).forEach((kind) => {
    const host = fakeHost();
    D.render(host, specs[kind]);
    assert.ok(host.innerHTML.startsWith("<svg"), `${kind}: заглушка вместо схемы`);
    specs[kind].nodes.forEach((n) => assert.ok(
      host.innerHTML.includes(`data-node-id="${n.id}"`),
      `${kind}: узел ${n.id} без ручки drag'а`));
  });
});

test("render: у плана-графика есть шкала периодов", () => {
  const host = fakeHost();
  D.render(host, Object.assign({ meta: { x_axis: "Месяцы" } }, GANTT));
  assert.ok(host.innerHTML.includes("Месяцы"), "подпись шкалы потерялась");
});

test("render: подпись переносится по словам, а не рвётся посреди слова", () => {
  // word-break:break-word в Chromium работает как anywhere — рвёт слово, лишь бы
  // добить текущую строку: «Задание на комплектацию» уезжало в «комплектаци» +
  // «ю» отдельной строкой, хотя слово целиком влезало следующей.
  const host = fakeHost();
  D.render(host, { kind: "flowchart",
    nodes: [{ id: "a", label: "Задание на комплектацию" },
            { id: "b", label: "Автоконтроль качества" }],
    edges: [{ from: "a", to: "b" }] });
  assert.ok(host.innerHTML.includes("overflow-wrap:break-word"),
    "подписи без переноса длинных слов — они вылезут за плашку");
  assert.ok(!/word-break\s*:/.test(host.innerHTML),
    "word-break рвёт слова там, где перенос уместился бы целиком");
});

// ── плашки не налезают друг на друга ────────────────────────────────────────
function boxOverlap(L) {
  const ns = Object.keys(L.nodes).map((id) => Object.assign({ id }, L.nodes[id]));
  const hits = [];
  for (let i = 0; i < ns.length; i++) {
    for (let j = i + 1; j < ns.length; j++) {
      const a = ns[i], b = ns[j];
      const ox = (a.w + b.w) / 2 - Math.abs(a.x - b.x);
      const oy = (a.h + b.h) / 2 - Math.abs(a.y - b.y);
      if (ox > 0 && oy > 0) hits.push(`${a.id}~${b.id}`);
    }
  }
  return hits;
}

function chain(kind, n) {
  const nodes = [], edges = [];
  for (let i = 0; i < n; i++) nodes.push({ id: "n" + i, label: "Узел " + (i + 1) });
  for (let i = 1; i < n; i++) edges.push({ from: "n" + (i - 1), to: "n" + i });
  return { version: 1, kind, direction: "right", nodes, edges };
}

test("layout: узлы графа связей не встают вплотную", () => {
  // Силовая модель считает узлы точками — плашки касались боками, и стрелка
  // между ними вырождалась в огрызок в 4px.
  for (const n of [3, 5, 8, 12]) {
    const L = D.LAYOUTS.network(chain("network", n));
    assert.deepEqual(boxOverlap(L), [], `network n=${n}: плашки перекрылись`);
  }
});

test("layout: раскладка графа связей повторяема", () => {
  // Сейв редактора запекает SVG в деку — повторный рендер обязан совпасть.
  const a = D.LAYOUTS.network(chain("network", 7));
  const b = D.LAYOUTS.network(chain("network", 7));
  assert.deepEqual(a.nodes, b.nodes);
});

test("layout: корень mindmap не залезает на первую ветвь", () => {
  // Корень был шире колонки (colW+60 против colW-40) — на глубине от трёх
  // уровней он перекрывал ветвь, и стрелка к ней рисовалась задом наперёд.
  for (const n of [4, 6, 8]) {
    const L = D.LAYOUTS.mindmap(chain("mindmap", n));
    assert.deepEqual(boxOverlap(L), [], `mindmap n=${n}: плашки перекрылись`);
    const s = L.nodes.n0, t = L.nodes.n1;
    assert.ok(Math.abs(t.x - s.x) > (s.w + t.w) / 2,
      `mindmap n=${n}: стрелка от корня идёт назад`);
  }
});

test("layout: одиннадцать лучей hub_spoke не перекрываются", () => {
  const L = D.LAYOUTS.hub_spoke(
    Object.assign(chain("hub_spoke", 12), { edges: [] }));
  assert.deepEqual(boxOverlap(L), [], "лучи внизу эллипса налезли друг на друга");
});

test("render: подпись поверх заливки берёт контрастный цвет темы", () => {
  // Хвост серой шкалы в тёмной теме почти сливается с фоном (--chart-4 #525252
  // против --bg #222), и подпись четвёртой полосы воронки читалась как тень.
  const host = fakeHost();
  D.render(host, { kind: "funnel", nodes: [
    { id: "a", label: "Лонг-лист", value: "34" },
    { id: "b", label: "Техпроверка", value: "19" },
    { id: "c", label: "Финпроверка", value: "11" },
    { id: "d", label: "Допущены к пилотам", value: "4" },
    { id: "e", label: "Контракты", value: "2", accent: true }] });
  assert.ok(host.innerHTML.includes("var(--on-chart-4)"),
    "тёмная полоса без контрастной подписи — текст сливается с заливкой");
  assert.ok(!host.innerHTML.includes("color:var(--bg)"),
    "цвет фона в роли подписи не зависит от того, насколько тёмная заливка");
});
