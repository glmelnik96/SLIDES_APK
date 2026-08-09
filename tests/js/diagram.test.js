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

test("layoutCycle: узлы на эллипсе, старт сверху, линки замыкаются", () => {
  const spec = { kind: "cycle", nodes: [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }] };
  const { nodes: pos, links } = D.layoutCycle(spec);
  inCanvas(pos);
  assert.ok(pos.a.y < pos.c.y);             // старт сверху, противоположный снизу
  assert.strictEqual(links.length, 4);      // замкнутый круг
  assert.strictEqual(links[3].to, "a");
  // Дуга эллиптическая (rx≠ry): окружность r=265 оставляла по 600px пустоты
  // по бокам холста 1800×720 и зажимала плашки в узкий сектор.
  links.forEach((l) => {
    assert.ok(l.arc && l.arc.rx > 0 && l.arc.ry > 0);
    assert.ok(l.arc.rx > l.arc.ry);
  });
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

test("layoutFunnel: ступень без числа не раздувает воронку обратно", () => {
  // Числа проставлены не у всех этапов (обычная история: последние ещё не
  // посчитали). Раньше пустая ступень бралась из линейного скоса «с нуля» и
  // оказывалась ШИРЕ соседней сверху — воронка разъезжалась посреди склона.
  const spec = { kind: "funnel", nodes: [
    { id: "a", value: "12000" }, { id: "b", value: "3400" },
    { id: "c", value: "1100" }, { id: "d", value: "280" },
    { id: "e" }, { id: "f" }] };
  const p = D.layoutFunnel(spec).nodes;
  inCanvas(p);
  const w = ["a", "b", "c", "d", "e", "f"].map((k) => p[k].w);
  w.slice(1).forEach((cur, i) => {
    assert.ok(cur <= w[i] + 1e-6, `ступень ${i + 2} шире предыдущей: ${w[i]} → ${cur}`);
  });

  // Дырка посреди известных величин тянется между соседями, а не скачет.
  const mid = D.layoutFunnel({ kind: "funnel", nodes: [
    { id: "a", value: "100" }, { id: "b" }, { id: "c", value: "20" }] }).nodes;
  assert.ok(mid.a.w > mid.b.w && mid.b.w > mid.c.w);
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

test("render: дорожка не заливается цветом плашки, границы держат линии", () => {
  // Полоса-зебра заливалась тем же --bg-card, что и плашки узлов, а плашка
  // лежит НА полосе: в светлой теме её контраст к своей полосе падал до 1.05:1
  // и она пропадала. Границу дорожек держат линии — они есть у каждой.
  const host = fakeHost();
  D.render(host, { kind: "swimlanes", nodes: [
    { id: "a", label: "Заявка", lane: "Клиент" },
    { id: "b", label: "Проверка", lane: "Менеджер" },
    { id: "c", label: "Настройка", lane: "Инженер" },
  ] });
  assert.ok(!/<rect[^>]*fill="var\(--bg-card\)"[^>]*fill-opacity/.test(host.innerHTML),
            "полоса дорожки снова залита цветом плашки");
  const lines = host.innerHTML.match(/<line[^>]*stroke="var\(--fg-muted\)"/g) || [];
  assert.strictEqual(lines.length, 2);   // три дорожки — две внутренние границы
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

test("render: у ступени есть линия проступи, а не только заливка", () => {
  // Подступенок — тот же --bg-card, что и плашка, в 0.45 прозрачности. В светлой
  // теме между фоном (#FFF) и плашкой (#F2F2F2) всего 13 пунктов, половина — 6:
  // контраст плашки к своему столбу падал до 1.05:1, и лестница читалась как
  // простые столбцы. Третьего тона в палитре нет — грань держит линия.
  const host = fakeHost();
  const spec = { kind: "steps", nodes: [
    { id: "a", label: "Раз" }, { id: "b", label: "Два" }, { id: "c", label: "Три" }] };
  D.render(host, spec);
  const pos = D.layoutSteps(spec).nodes;
  ["b", "c"].forEach((id) => {          // у нижней ступени столба нет — она на полу
    const y = pos[id].y + pos[id].h / 2;
    const re = new RegExp('<line[^>]*y1="' + y + '"[^>]*y2="' + y +
      '"[^>]*stroke="var\\(--fg-muted\\)"');
    assert.ok(re.test(host.innerHTML), "нет линии проступи у ступени " + id);
  });
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

test("render: подписи связей графа не заезжают на плашки узлов", () => {
  // У косой связи графа поднять метку некуда — над ней такая же плашка, как под
  // ней. Зазор в 56px хватал только стрелке, а блок метки шириной 200 ложился
  // разом на ОБЕ плашки: «при по» съедала одна, «ложительном/решении» вылезало
  // наружу обрубком — слайд читался как испорченный файл.
  // Десять узлов, а не пять: на пятерых холст раздвигает плашки на весь бюджет
  // метки, и одной раскладки хватило бы. На десяти места уже нет — подпись
  // обязана сузиться до фактического просвета и оборваться, если не влезла.
  const spec = { kind: "network", nodes: [], edges: [] };
  for (let i = 0; i < 10; i++) spec.nodes.push({ id: "n" + i, label: "Узел " + i });
  for (let i = 1; i < 10; i++) {
    spec.edges.push({ from: "n" + (i % 3 === 0 ? 0 : i - 1), to: "n" + i,
                      label: "при положительном решении" });
  }
  const host = fakeHost();
  D.render(host, spec);
  const out = D.layoutNetwork(spec);
  const texts = host.innerHTML.match(/<text[^>]*>[\s\S]*?<\/text>/g) || [];
  assert.strictEqual(texts.length, spec.edges.length, "подписи связей потерялись");
  // Плашки ЭТОЙ связи, а не все подряд: длинная связь силовой раскладки может
  // пройти над посторонним узлом — это вопрос трассировки рёбер, а не подписи.
  texts.forEach((t, i) => {
    const link = out.links[i];
    const plates = [out.nodes[link.from], out.nodes[link.to]];
    const x = Number(t.match(/x="([\d.-]+)"/)[1]);
    const y = Number(t.match(/y="([\d.-]+)"/)[1]);
    const fs = Number(t.match(/font-size="([\d.-]+)"/)[1]);
    const lines = (t.match(/<tspan[^>]*>([^<]*)<\/tspan>/g) || [])
      .map((s) => s.replace(/<[^>]*>/g, ""));
    assert.ok(lines.length && lines.join("").trim(), "подпись связи пустая");
    const w = Math.max(...lines.map((s) => s.length)) * fs * 0.6;
    const lead = Math.round(fs * 1.16);
    const box = { x0: x - w / 2, x1: x + w / 2,
                  y0: y - fs, y1: y + (lines.length - 1) * lead };
    plates.forEach((p) => {
      const over = box.x0 < p.x + p.w / 2 && box.x1 > p.x - p.w / 2 &&
                   box.y0 < p.y + p.h / 2 && box.y1 > p.y - p.h / 2;
      assert.ok(!over, `подпись «${lines.join(" ")}» легла на плашку`);
    });
  });
});

test("render: подписи ветвей карты не заезжают на плашки и не мельчают в огрызок", () => {
  // Ветвь карты идёт из края родителя в край ребёнка, между ними — просвет
  // колонки в 40px против блока метки в 200: «при положит…» съедала ветвь,
  // «…решении» — родитель. Лечение двухслойное, и проверяем ОБА слоя:
  // раскладка сужает плашки, освобождая просвет под подпись, а сама подпись
  // ограничена шириной просвета и потому физически не может лечь на плашку.
  const spec = {
    kind: "mindmap",
    nodes: [{ id: "core", label: "Платформа" },
            { id: "inf", label: "Инфраструктура" }, { id: "data", label: "Данные" },
            { id: "team", label: "Команда" }, { id: "sec", label: "Безопасность" },
            { id: "k8s", label: "Kubernetes" }, { id: "db", label: "Хранилища" }],
    edges: [{ from: "core", to: "inf" }, { from: "core", to: "data" },
            { from: "core", to: "team" }, { from: "core", to: "sec" },
            { from: "inf", to: "k8s" }, { from: "data", to: "db" }]
      .map((e) => ({ ...e, label: "при положительном решении" })),
  };
  const host = fakeHost();
  D.render(host, spec);
  const out = D.layoutMindmap(spec);
  const texts = host.innerHTML.match(/<text[^>]*>[\s\S]*?<\/text>/g) || [];
  assert.strictEqual(texts.length, spec.edges.length, "подписи ветвей потерялись");
  texts.forEach((t, i) => {
    const link = out.links[i];
    const x = Number(t.match(/x="([\d.-]+)"/)[1]);
    const y = Number(t.match(/y="([\d.-]+)"/)[1]);
    const fs = Number(t.match(/font-size="([\d.-]+)"/)[1]);
    const lines = (t.match(/<tspan[^>]*>([^<]*)<\/tspan>/g) || [])
      .map((s) => s.replace(/<[^>]*>/g, ""));
    // Просвет должен быть заложен в раскладке: подпись обязана уместиться
    // ЦЕЛИКОМ. Без запаса она честно оборвётся многоточием — тоже не ложь, но
    // читателю от «при…» на ветви никакой пользы.
    assert.strictEqual(lines.join(" "), "при положительном решении",
                       "подпись ветви усохла до огрызка");
    const w = Math.max(...lines.map((s) => s.length)) * fs * 0.6;
    const lead = Math.round(fs * 1.16);
    const box = { x0: x - w / 2, x1: x + w / 2,
                  y0: y - fs, y1: y + (lines.length - 1) * lead };
    [out.nodes[link.from], out.nodes[link.to]].forEach((p) => {
      const over = box.x0 < p.x + p.w / 2 && box.x1 > p.x - p.w / 2 &&
                   box.y0 < p.y + p.h / 2 && box.y1 > p.y - p.h / 2;
      assert.ok(!over, `подпись «${lines.join(" ")}» легла на плашку`);
    });
  });
});

test("render: подпись возвратной связи не налезает на ромб развилки", () => {
  // Возвратное ребро идёт по коридору в 56px под рядом плашек, а блок подписи из
  // двух строк требует 68 — и «положительном» упиралось в НИЖНИЙ ЛУЧ РОМБА:
  // ромб рисуется на 44px выше своей коробки раскладки, поэтому подпись,
  // честно обошедшая коробку, всё равно получала клин поверх буквы. Читалось
  // как битый символ в файле. Подпись обязана уйти под линию и остаться целой.
  const spec = {
    kind: "flowchart", direction: "right",
    nodes: [{ id: "start", label: "Заявка", shape: "start" },
            { id: "check", label: "Проверка", shape: "process" },
            { id: "ok", label: "Данные полные?", shape: "decision" },
            { id: "fix", label: "Доработка", shape: "process" },
            { id: "done", label: "Готово", shape: "end" }],
    edges: [{ from: "start", to: "check" }, { from: "check", to: "ok" },
            { from: "ok", to: "fix" }, { from: "ok", to: "done" },
            { from: "fix", to: "check", label: "при положительном решении" }],
  };
  const host = fakeHost();
  D.render(host, spec);
  const out = D.LAYOUTS.flowchart(spec);
  const texts = host.innerHTML.match(/<text[^>]*>[\s\S]*?<\/text>/g) || [];
  assert.strictEqual(texts.length, 1, "подпись возвратной связи потерялась");
  const t = texts[0];
  const x = Number(t.match(/x="([\d.-]+)"/)[1]);
  const y = Number(t.match(/y="([\d.-]+)"/)[1]);
  const fs = Number(t.match(/font-size="([\d.-]+)"/)[1]);
  const lines = (t.match(/<tspan[^>]*>([^<]*)<\/tspan>/g) || [])
    .map((s) => s.replace(/<[^>]*>/g, ""));
  assert.strictEqual(lines.join(" "), "при положительном решении",
                     "подпись усохла, хотя под линией места вдоволь");
  const w = Math.max(...lines.map((s) => s.length)) * fs * 0.6;
  const lead = Math.round(fs * 1.16);
  const box = { x0: x - w / 2, x1: x + w / 2,
                y0: y - fs, y1: y + (lines.length - 1) * lead };
  Object.keys(out.nodes).forEach((id) => {
    const p = out.nodes[id];
    // Меряем НАРИСОВАННУЮ фигуру: у ромба она выше коробки, и именно этот
    // излишек съедал букву.
    const ph = p.hDraw || p.h;
    const over = box.x0 < p.x + p.w / 2 && box.x1 > p.x - p.w / 2 &&
                 box.y0 < p.y + ph / 2 && box.y1 > p.y - ph / 2;
    assert.ok(!over, `подпись легла на фигуру «${id}»`);
  });
});

test("render: подписи связей не печатаются одна поверх другой", () => {
  // Две связи, входящие в узел с одной стороны, дают почти совпадающие середины:
  // «при положительном решении» ложилось поверх такого же, и вместо двух подписей
  // выходила нечитаемая гребёнка из наложенных букв — на глаз это битый рендер.
  const spec = {
    kind: "network",
    nodes: [{ id: "api", label: "API" }, { id: "auth", label: "Авторизация" },
            { id: "cat", label: "Каталог" }, { id: "bill", label: "Биллинг" },
            { id: "db", label: "Хранилище" }, { id: "que", label: "Очередь" },
            { id: "mon", label: "Мониторинг" }],
    edges: [{ from: "api", to: "auth" }, { from: "api", to: "cat" },
            { from: "api", to: "bill" }, { from: "auth", to: "db" },
            { from: "cat", to: "db" }, { from: "bill", to: "db" },
            { from: "bill", to: "que" }, { from: "que", to: "mon" }]
      .map((e) => ({ ...e, label: "при положительном решении" })),
  };
  const host = fakeHost();
  D.render(host, spec);
  const boxes = (host.innerHTML.match(/<text[^>]*>[\s\S]*?<\/text>/g) || [])
    .map((t) => {
      const x = Number(t.match(/x="([\d.-]+)"/)[1]);
      const y = Number(t.match(/y="([\d.-]+)"/)[1]);
      const fs = Number(t.match(/font-size="([\d.-]+)"/)[1]);
      const lines = (t.match(/<tspan[^>]*>([^<]*)<\/tspan>/g) || [])
        .map((s) => s.replace(/<[^>]*>/g, ""));
      const w = Math.max(...lines.map((s) => s.length)) * fs * 0.6;
      return { x0: x - w / 2, x1: x + w / 2, y0: y - fs,
               y1: y + (lines.length - 1) * Math.round(fs * 1.16) };
    });
  assert.strictEqual(boxes.length, spec.edges.length, "подписи связей потерялись");
  boxes.forEach((a, i) => boxes.slice(i + 1).forEach((b) => {
    const over = a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
    assert.ok(!over, "две подписи связей наложились друг на друга");
  }));
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

test("clampLabel: влезающую подпись не трогает, невлезающую режет по слову", () => {
  // 250×110 — плашка flowchart на 5 узлов: подпись помещается, кегль нашёлся.
  const ok = "Проверка данных менеджером";
  assert.strictEqual(D.clampLabel(ok, 250, 110, D.fitFont(ok, 250, 110, 28)), ok);
  // 109×110 — плашка того же flowchart на 11 узлов: не влезает и на полу кегля.
  const long = "Согласование условий с юридической службой заказчика";
  const cut = D.clampLabel(long, 109, 110, D.fitFont(long, 109, 110, 28));
  assert.notStrictEqual(cut, long);
  assert.ok(cut.endsWith("…"), cut);
  assert.ok(long.startsWith(cut.slice(0, -1)), `обрезка не по началу строки: ${cut}`);
  assert.ok(!/\s…$/.test(cut), `пробел перед многоточием: ${cut}`);
  // режем по границе слова, а не посреди
  assert.ok(long.charAt(cut.length - 1).match(/[\s]/) || cut.length - 1 === long.length,
    `обрезано посреди слова: ${cut}`);
});

test("clampLabel: одно длинное слово режется по символам, а не в ноль", () => {
  const word = "Электроэнцефалографирование";
  const cut = D.clampLabel(word, 100, 60, 16);
  assert.ok(cut.endsWith("…") && cut.length > 2, cut);
  assert.ok(word.startsWith(cut.slice(0, -1)));
  assert.strictEqual(D.clampLabel("", 100, 60, 16), "");
});

test("labelLines: слово шире строки съедает несколько строк", () => {
  assert.strictEqual(D.labelLines("аб вг", 6), 1);
  assert.strictEqual(D.labelLines("аб вг", 4), 2);
  assert.strictEqual(D.labelLines("абвгдеёжзи", 5), 2);   // рвётся overflow-wrap
  assert.strictEqual(D.labelLines("аб абвгдеёжзи", 5), 3);
});

test("NBSP: неразрывный пробел — часть слова, а не граница переноса", () => {
  // Chromium по U+00A0 не переносит: «на\u00a0комплектацию» для него один токен
  // в 15 символов. JS \s ловит NBSP, поэтому раньше движок видел «на» +
  // «комплектацию», брал крупный кегль и подпись срезалась overflow:hidden.
  const nb = "Задание на\u00a0комплектацию";
  const plain = "Задание на комплектацию";
  assert.ok(D.fitFont(nb, 250, 110) <= D.fitFont(plain, 250, 110),
    `NBSP-подпись не ужалась: ${D.fitFont(nb, 250, 110)}`);
  assert.strictEqual(D.labelLines(nb, 12), D.labelLines("Задание нкомплектацию", 12));
  // обрезка не должна подменять NBSP обычным пробелом при склейке
  const long = "Согласование условий с\u00a0юридической службой заказчика";
  const cut = D.clampLabel(long, 109, 110, D.fitFont(long, 109, 110, 28));
  assert.ok(cut.endsWith("…") && long.startsWith(cut.slice(0, -1)),
    `обрезка потеряла исходные пробелы: ${JSON.stringify(cut)}`);
});

test("render: невлезающая подпись выходит в разметку сокращённой", () => {
  const host = fakeHost();
  const long = "Согласование условий с юридической службой заказчика";
  const nodes = [];
  for (let i = 1; i <= 11; i++) nodes.push({ id: "n" + i, label: long });
  const edges = [];
  for (let i = 1; i < 11; i++) edges.push({ from: "n" + i, to: "n" + (i + 1) });
  D.render(host, { kind: "flowchart", nodes: nodes, edges: edges });
  assert.ok(!host.innerHTML.includes(long), "полная подпись всё ещё в SVG");
  assert.ok(host.innerHTML.includes("…"), "нет многоточия сокращения");
});

test("render: перенос слова живёт в обёртке, которая умеет сжиматься", () => {
  // Текст прямо во флекс-боксе — анонимный флекс-элемент: он не сжимается ниже
  // своей min-content ширины, а overflow-wrap:break-word (в отличие от anywhere)
  // intrinsic-размер не уменьшает. Длинное слово поэтому не переносилось, а
  // вылезало и молча срезалось overflow:hidden. Перенос обязан висеть на
  // внутреннем div с min-width:0.
  const host = fakeHost();
  D.render(host, { kind: "flowchart",
    nodes: [{ id: "a", label: "Электроэнцефалография" }, { id: "b", label: "Б" }],
    edges: [{ from: "a", to: "b" }] });
  const m = host.innerHTML.match(/<div style="min-width:0;[^"]*"/);
  assert.ok(m, "нет внутренней обёртки подписи");
  assert.ok(/overflow-wrap:break-word/.test(m[0]), `перенос не на обёртке: ${m[0]}`);
  assert.ok(!/display:flex;[^"]*overflow-wrap/.test(host.innerHTML),
    "перенос остался на флекс-контейнере");
});

test("render: длинная подпись дорожки не срезается молча", () => {
  // Колонка подписей swimlanes узкая и фиксированная. Подпись стояла там одной
  // строкой без переноса и без подбора кегля, под overflow:hidden — «Отдел
  // клиентского сопровождения» превращалось в «…сопровождени» с половинкой «я».
  const host = fakeHost();
  const lane = "Отдел клиентского сопровождения";
  D.render(host, { kind: "swimlanes", nodes: [
    { id: "a", label: "Приём", lane: lane },
    { id: "b", label: "Оплата", lane: "Бухгалтерия" }] });
  const i = host.innerHTML.indexOf("Отдел клиентского");
  assert.ok(i > 0, "подписи дорожки нет в разметке");
  const frag = host.innerHTML.slice(host.innerHTML.lastIndexOf("<foreignObject", i), i);
  assert.ok(/min-width:0/.test(frag) && /overflow-wrap:break-word/.test(frag),
    `подпись дорожки не переносится: ${frag}`);
});

test("render: подпись пересечения venn не наезжает на подписи кругов", () => {
  // center_label рисовался голым <text> без габарита: «Планирование маршрута
  // доставки» растягивалось на 450px при линзе в 180 и ложилось поверх подписей
  // обоих кругов — читалось месиво из наложенных букв.
  const host = fakeHost();
  D.render(host, { kind: "venn",
    nodes: [{ id: "a", label: "Отдел клиентского сопровождения" },
            { id: "b", label: "Электроэнцефалография пациента" }],
    meta: { center_label: "Планирование маршрута доставки" } });
  assert.ok(!/<text[^>]*>[^<]*Планирование/.test(host.innerHTML),
    "подпись пересечения всё ещё голый <text> без габарита");
  const i = host.innerHTML.indexOf("Планирование");
  const fo = host.innerHTML.lastIndexOf("<foreignObject", i);
  assert.ok(fo >= 0, "подпись пересечения не в foreignObject");
  const w = Number(/width="([\d.]+)"/.exec(host.innerHTML.slice(fo, i))[1]);
  // линза двух кругов: 2*(r - dx) = 2*(255-165) = 180
  assert.ok(w > 0 && w <= 180, `подпись пересечения шире линзы: ${w}`);
});

test("render: заголовок колонки comparison не переезжает разделитель", () => {
  // Схема разрешает lane до 40 символов, а заголовок рисовался голым
  // <text font-size="34"> по центру колонки: «Целевая архитектура эксплуатации
  // ЦОД 2030» разъезжалось на ~870px при колонке в 660 — закрывало пунктирный
  // разделитель посередине и лезло в чужую половину слайда.
  const host = fakeHost();
  const lane = "Целевая архитектура эксплуатации ЦОД 2030";
  D.render(host, { kind: "comparison",
    nodes: [{ id: "a", label: "Ручной разбор", lane: "Как сейчас" },
            { id: "b", label: "Авторазбор", lane: lane }] });
  assert.ok(!host.innerHTML.includes(lane),
    "заголовок колонки ушёл в разметку одной строкой");
  assert.ok(host.innerHTML.includes("Целевая архитектура"), "заголовок пропал");
  // короткий заголовок остаётся одной строкой на прежней базовой линии
  assert.ok(/y="64"[^>]*>[^<]*<tspan[^>]*>Как сейчас<\/tspan><\/text>/
    .test(host.innerHTML), "короткий заголовок колонки тронули зря");
});

test("render: длинная подпись ребра переносится и сокращается", () => {
  // Схема разрешает подписи рёбер до 60 символов, а рисовались они голым <text>
  // без переноса: «Согласование условий договора» растягивалось на пол-холста
  // поперёк соседних узлов и второй такой же подписи.
  const host = fakeHost();
  D.render(host, { kind: "flowchart",
    nodes: [{ id: "a", label: "А" }, { id: "b", label: "Б" }, { id: "c", label: "В" }],
    edges: [{ from: "a", to: "b", label: "Согласование условий договора" },
            { from: "a", to: "c", label: "да" }] });
  assert.ok(!host.innerHTML.includes("Согласование условий договора"),
    "длинная подпись ребра ушла в разметку целиком, одной строкой");
  assert.ok(host.innerHTML.includes("Согласование"), "подпись ребра пропала совсем");
  assert.ok(/>да</.test(host.innerHTML), "короткую подпись ребра тронули зря");
  D.wrapLines("аб вг де", 5).forEach(function (l) { assert.ok(l.length <= 5, l); });
  assert.deepStrictEqual(D.wrapLines("аб вг де", 5), ["аб вг", "де"]);
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

test("render: подпись связи между соседями по ряду не ложится на плашку", () => {
  // Ломаная «колено-колено» между соседями одного ряда вырождается в прямую:
  // средний сегмент — точка. Это попадало в ветку вертикального сегмента, и
  // метку уносило вбок на всю её ширину — прямо на плашку следующего узла
  // (на пяти рангах промежуток между колонками всего 75 при бюджете метки 200).
  const host = fakeHost();
  D.render(host, { kind: "flowchart", direction: "right",
    nodes: [{ id: "a", label: "Заявка" }, { id: "b", label: "Проверка" }],
    edges: [{ from: "a", to: "b", label: "при положительном решении" }] });
  const box = D.layoutFlowchart({ kind: "flowchart", direction: "right",
    nodes: [{ id: "a", label: "Заявка" }, { id: "b", label: "Проверка" }],
    edges: [{ from: "a", to: "b", label: "при положительном решении" }] }).nodes.a;
  const top = box.y - box.h / 2;
  const txt = host.innerHTML.match(/<text[^>]*y="([\d.-]+)"[^>]*>/);
  assert.ok(txt, "подпись связи не нарисована");
  assert.ok(Number(txt[1]) < top, `подпись на уровне плашки: y=${txt[1]}, верх плашки ${top}`);
  assert.ok(/<text[^>]*text-anchor="middle"/.test(host.innerHTML),
    "подпись прижата к краю сегмента, как у вертикального ребра");
});

test("render: подпись связи внутри дорожки не прячется под плашки", () => {
  // Соседей по одной дорожке соединяет прямая горизонталь. У четырёх узлов в
  // дорожке просвет между ними 97px против бюджета метки 200 — подпись пряталась
  // под обе плашки разом, наружу торчали огрызки.
  const spec = { kind: "swimlanes",
    nodes: [{ id: "a", label: "Аттестация", lane: "Инженер" },
            { id: "b", label: "Настройка", lane: "Инженер" },
            { id: "c", label: "Приёмка", lane: "Инженер" },
            { id: "d", label: "Запуск", lane: "Инженер" }],
    edges: [{ from: "a", to: "b", label: "при положительном решении" }] };
  const host = fakeHost();
  D.render(host, spec);
  const box = D.layoutSwimlanes(spec).nodes.a;
  const y = Number(host.innerHTML.match(/<text[^>]*y="([\d.-]+)"/)[1]);
  assert.ok(y < box.y - box.h / 2, `подпись на уровне плашки: y=${y}`);
});

test("render: подпись луча хаба остаётся на линии — там просвет шире метки", () => {
  const spec = { kind: "hub_spoke",
    nodes: [{ id: "h", label: "Платформа" }, { id: "a", label: "Раз" },
            { id: "b", label: "Два" }, { id: "c", label: "Три" },
            { id: "d", label: "Четыре" }],
    edges: [{ from: "h", to: "b", label: "поставляет" }] };
  const host = fakeHost();
  D.render(host, spec);
  const hub = D.layoutHubSpoke(spec).nodes.h;
  const y = Number(host.innerHTML.match(/<text[^>]*y="([\d.-]+)"/)[1]);
  assert.ok(Math.abs(y - hub.y) < 40, `луч без нужды поднят над плашкой: y=${y}`);
});

test("render: у единственного потомка оргсхемы подпись остаётся в просвете", () => {
  // У такой связи средний сегмент тоже вырожден, но ребро вертикально: сдвиг
  // «выше плашки» загнал бы метку ПОД плашку родителя — на слайде из неё
  // торчала одна вторая строка.
  const spec = { kind: "hierarchy",
    nodes: [{ id: "a", label: "Дирекция" }, { id: "b", label: "Разработка" },
            { id: "c", label: "Эксплуатация" }, { id: "d", label: "SRE" }],
    edges: [{ from: "a", to: "b" }, { from: "a", to: "c" },
            { from: "c", to: "d", label: "при положительном решении" }] };
  const host = fakeHost();
  D.render(host, spec);
  const L = D.layoutHierarchy(spec);
  // y самого <text> — базовая линия ВЕРХНЕЙ строки: блок из двух строк растёт вверх
  const y = Number(host.innerHTML.match(/<text[^>]*y="([\d.-]+)"/)[1]);
  assert.ok(y > L.nodes.c.y + L.nodes.c.h / 2, "подпись заехала под плашку родителя");
  assert.ok(y < L.nodes.d.y - L.nodes.d.h / 2, "подпись заехала на плашку потомка");
});

test("render: у полос плана прямые углы — как у всех плашек деки", () => {
  // --radius:0 в deck.css заявлен инвариантом бренда, шаблоны графиков пишут
  // rx="0" явно, скругления отвергает и филлер, и vision-QA. Полосы плана были
  // единственным скруглённым элементом движка: рядом с прямоугольными плашками
  // остальных схем это читалось как чужая вставка.
  const host = fakeHost();
  D.render(host, GANTT);
  const bars = host.innerHTML.split("data-node-id").slice(1);
  bars.forEach((chunk) => assert.ok(!/^[^>]*\brx=/.test(chunk.slice(chunk.indexOf("<rect"))),
    "полоса плана снова скруглена"));
  // у блок-схемы капсула start/end остаётся — это нотация, а не украшение
  const fc = fakeHost();
  D.render(fc, { kind: "flowchart", nodes: [{ id: "a", label: "Старт", shape: "start" }] });
  assert.ok(/rx="/.test(fc.innerHTML), "капсула терминатора блок-схемы пропала");
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

test("layout: стрелки цикла упираются в край плашки, а не внутрь неё", () => {
  // Фиксированный угловой отступ не учитывал ширину плашки: широкий узел
  // наверху круга съедал больший угол, и наконечник прятался под самой плашкой
  // (у стрелки «Формирование гипотезы → Пилот» головы просто не было).
  for (const n of [3, 4, 5, 6, 8, 12]) {
    const L = D.LAYOUTS.cycle(chain("cycle", n));
    assert.deepEqual(boxOverlap(L), [], `cycle n=${n}: плашки перекрылись`);
    L.links.forEach((l) => {
      const [q, p] = [l.points[0], l.points[l.points.length - 1]];
      const t = L.nodes[l.to], f = L.nodes[l.from];
      assert.ok(Math.abs(p[0] - t.x) > t.w / 2 || Math.abs(p[1] - t.y) > t.h / 2,
        `cycle n=${n}: ${l.from}→${l.to} — наконечник под плашкой`);
      assert.ok(Math.abs(q[0] - f.x) > f.w / 2 || Math.abs(q[1] - f.y) > f.h / 2,
        `cycle n=${n}: ${l.from}→${l.to} начинается внутри плашки`);
    });
  }
});
