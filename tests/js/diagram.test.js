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
  // По макету 146:982: ортогональные «колени» вместо дуг, стрелки на обоих
  // концах. Каждый сегмент колена строго горизонтален или вертикален.
  // Толщину линии цикл БОЛЬШЕ НЕ ЗАДАЁТ: раньше он перебивал общую тройку
  // своей двойкой «по макету», и на одной деке стрелки выходили разной
  // толщины. Теперь двойка — общий EDGE_STROKE движка, а не свойство ребра,
  // поэтому здесь проверяем, что цикл её НЕ переопределяет.
  links.forEach((l) => {
    assert.strictEqual(l.arc, undefined);
    assert.ok(l.both, `${l.from}→${l.to}: нет флага двусторонней стрелки`);
    assert.strictEqual(l.width, undefined);
    assert.strictEqual(l.points.length, 3);   // ромб n=4: всегда колено
    for (let i = 1; i < l.points.length; i++) {
      const [p, q] = [l.points[i - 1], l.points[i]];
      assert.ok(Math.abs(p[0] - q[0]) < 1 || Math.abs(p[1] - q[1]) < 1,
        `${l.from}→${l.to}: сегмент ${i} не ортогонален`);
    }
  });
  // Угол колена — снаружи: у верхней плашки «a» стрелка к правой «b» уходит
  // вбок на её же высоте (y угла = y полюсной плашки, x — боковой).
  const ab = links.find((l) => l.from === "a" && l.to === "b");
  assert.ok(Math.abs(ab.points[1][1] - pos.a.y) < 1);
  assert.ok(Math.abs(ab.points[1][0] - pos.b.x) < 1);
});

test("layoutFunnel: прямоугольники лесенкой, колонка не по центру холста", () => {
  // Ширина НЕ пропорциональна величине, и это сознательно: на живых данных
  // 12 000 / 3 400 / 1 100 / 280 пропорция дала бы последним ступеням полоски
  // по 26 px, в которые не влезает ни подпись, ни число. Величину несёт число
  // в правой колонке, ступенька — только порядок. Поэтому ширины идут ровным
  // линейным сходом от 1136 к 544 (замер макета) при любых value.
  const spec = { kind: "funnel", nodes: [
    { id: "f1", value: "12 000" }, { id: "f2", value: "3 400" },
    { id: "f3", value: "1 100" }, { id: "f4", value: "280" }] };
  const p = D.layoutFunnel(spec).nodes;
  inCanvas(p);
  const ids = ["f1", "f2", "f3", "f4"];
  assert.strictEqual(Math.round(p.f1.w), 1136);
  assert.strictEqual(Math.round(p.f4.w), 544);
  // Сход ровный: шаг ширины одинаков на всех ступенях.
  const d = p.f1.w - p.f2.w;
  ids.slice(1).forEach((id, i) => {
    assert.ok(Math.abs((p[ids[i]].w - p[id].w) - d) < 0.5, `рваный сход на ${id}`);
    assert.ok(p[id].y > p[ids[i]].y, "ступени идут не сверху вниз");
  });
  // Трапеций больше нет — у ступени одна ширина.
  ids.forEach((id) => assert.strictEqual(p[id].wBottom, undefined));
  // Колонка смещена влево: справа отрезана полоса под числа.
  ids.forEach((id) => assert.strictEqual(p[id].x, 629));
});

test("layoutFunnel: ступень без числа ничем не отличается от остальных", () => {
  // Раньше пустая ступень бралась из линейного скоса «с нуля» и оказывалась
  // ШИРЕ соседней сверху — воронка разъезжалась посреди склона. Теперь value
  // на геометрию не влияет вовсе, так что случай «числа посчитали не у всех»
  // перестал быть особым: проверяем, что ряд всё так же монотонен.
  const spec = { kind: "funnel", nodes: [
    { id: "a", value: "12000" }, { id: "b", value: "3400" },
    { id: "c", value: "1100" }, { id: "d", value: "280" },
    { id: "e" }, { id: "f" }] };
  const p = D.layoutFunnel(spec).nodes;
  inCanvas(p);
  const w = ["a", "b", "c", "d", "e", "f"].map((k) => p[k].w);
  w.slice(1).forEach((cur, i) => {
    assert.ok(cur < w[i], `ступень ${i + 2} не уже предыдущей: ${w[i]} → ${cur}`);
  });
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

test("layoutMatrix: 4 квадранта 880×309 делят холст, сверху полоса под подпись оси", () => {
  const spec = { kind: "matrix", nodes: ["q1", "q2", "q3", "q4"].map((id) => ({ id })) };
  const { nodes: pos, links } = D.layoutMatrix(spec);
  inCanvas(pos);
  assert.strictEqual(links.length, 0);
  assert.ok(pos.q1.x < W / 2 && pos.q1.y < H / 2);   // верх-лево
  assert.ok(pos.q2.x > W / 2 && pos.q2.y < H / 2);   // верх-право
  assert.ok(pos.q3.x < W / 2 && pos.q3.y > H / 2);   // низ-лево
  assert.ok(pos.q4.x > W / 2 && pos.q4.y > H / 2);   // низ-право
  // Замер макета: квадрант — четверть зоны, а не плашка внутри неё.
  ["q1", "q2", "q3", "q4"].forEach((id) => {
    assert.strictEqual(pos[id].w, 880);
    assert.strictEqual(pos[id].h, 309);
  });
  // По ширине сетка занимает холст без остатка, зазор между колонками 40.
  assert.strictEqual(pos.q1.x - pos.q1.w / 2, 0);
  assert.strictEqual(pos.q2.x + pos.q2.w / 2, W);
  assert.strictEqual((pos.q2.x - pos.q2.w / 2) - (pos.q1.x + pos.q1.w / 2), 40);
  // По высоте прижата к низу: снизу вплотную, сверху остаётся 61 под «Эффект».
  assert.strictEqual(pos.q3.y + pos.q3.h / 2, H);
  assert.strictEqual(pos.q1.y - pos.q1.h / 2, 61);
  assert.strictEqual((pos.q3.y - pos.q3.h / 2) - (pos.q1.y + pos.q1.h / 2), 41);
});

test("render матрицы: подписи по внешним углам, оси в зазорах", () => {
  const spec = {
    kind: "matrix",
    meta: { x_axis: "Усилия", y_axis: "Эффект" },
    nodes: [
      { id: "q1", label: "Быстрые победы", accent: true },
      { id: "q2", label: "Стратегические проекты" },
      { id: "q3", label: "Фоновые задачи" },
      { id: "q4", label: "Неблагодарная работа" },
    ],
  };
  const host = fakeHost();
  D.render(host, spec);
  const svg = host.innerHTML;
  // Четыре угла: верх-лево, верх-право, низ-лево, низ-право — каждый свой.
  const corners = [...svg.matchAll(/align-items:(\w[\w-]*);justify-content:(\w[\w-]*)/g)]
    .map((m) => m[1] + "/" + m[2]);
  assert.deepStrictEqual(corners, [
    "flex-start/flex-start", "flex-start/flex-end",
    "flex-end/flex-start", "flex-end/flex-end",
  ]);
  // Зелёный ровно один — «Быстрые победы», и текст на нём графитовый.
  assert.strictEqual((svg.match(/var\(--accent\)/g) || []).length, 1);
  assert.ok(svg.includes("var(--cl-graphite)"));
  // Кегль подписи квадранта 30 (а не общие для схем 24).
  assert.ok(svg.includes("font-size:30px"), "кегль квадранта не 30");
  // Оси стоят в зазорах: вертикальная на 900, горизонтальная на 390.5.
  assert.ok(svg.includes('<line x1="900" y1="720" x2="900" y2="61"'), "вертикальной оси нет");
  assert.ok(svg.includes('<line x1="0" y1="390.5" x2="1700" y2="390.5"'), "горизонтальной оси нет");
  // Подпись вертикальной оси — над острием, то есть выше верха квадрантов.
  assert.ok(/<text x="900" y="41" text-anchor="middle"[^>]*>Эффект</.test(svg));
  assert.ok(/<text x="1800" y="398.5" text-anchor="end"[^>]*>Усилия</.test(svg));
});

test("layoutPyramid: ярусы-полосы равной высоты по оси фигуры", () => {
  // Раскладка отдаёт не плашки, а ПОЛЯ ПОДПИСЕЙ внутри одного треугольника
  // (его рисует декор). Отсюда и проверки: ряды равной высоты сверху вниз,
  // все по оси, поля не шире контура на своей высоте.
  const spec = { kind: "pyramid", nodes: [{ id: "p1" }, { id: "p2" }, { id: "p3" }] };
  const { nodes: pos, links } = D.layoutPyramid(spec);
  inCanvas(pos);
  assert.strictEqual(links.length, 0);
  assert.ok(pos.p1.y < pos.p2.y && pos.p2.y < pos.p3.y);   // вершина сверху
  ["p1", "p2", "p3"].forEach((id) => assert.strictEqual(pos[id].x, W / 2));
  assert.ok(Math.abs(pos.p1.h - pos.p3.h) < 0.5, "полосы разной высоты");
  // Трапеций нет: у поля подписи одна ширина, и она не расширяется бесконечно.
  assert.strictEqual(pos.p1.wBottom, undefined);
  assert.ok(pos.p3.w <= 583 && pos.p1.w <= pos.p3.w);
  // Верхнее поле не вылезает за контур: ширина треугольника на нижней кромке
  // первой полосы — 1754·(y+35)/725, поле обязано быть уже.
  const edge = 1754 * ((pos.p1.y + pos.p1.h / 2) + 35) / 725;
  assert.ok(pos.p1.w <= edge, `поле ${pos.p1.w} шире контура ${edge}`);
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

test("layoutVenn: прямоугольники внахлёст, метки — в своих полосах", () => {
  const two = { kind: "venn", nodes: [{ id: "v1" }, { id: "v2" }] };
  const p2 = D.layoutVenn(two).nodes;
  // стопка идёт вправо-вниз и занимает зону целиком по ширине
  assert.ok(p2.v1.x < p2.v2.x && p2.v1.y < p2.v2.y);
  assert.strictEqual(p2.v1.x - p2.v1.w / 2, 0);
  assert.strictEqual(p2.v2.x + p2.v2.w / 2, W);
  // соседи действительно пересекаются
  assert.ok(p2.v2.x - p2.v2.w / 2 < p2.v1.x + p2.v1.w / 2);
  assert.ok(p2.v1.labelDx < 0 && p2.v2.labelDx > 0);        // наружу, в свою полосу

  const three = { kind: "venn", nodes: [{ id: "a" }, { id: "b" }, { id: "c" }] };
  const p3 = D.layoutVenn(three).nodes;
  assert.ok(p3.a.y < p3.b.y && p3.b.y < p3.c.y);
  // Общая часть всех трёх обязана быть НЕвырожденной: иначе подпись
  // пересечения рисовать негде (на половинном нахлёсте она схлопывалась в
  // линию нулевой ширины).
  const x0 = Math.max(...["a", "b", "c"].map((k) => p3[k].x - p3[k].w / 2));
  const x1 = Math.min(...["a", "b", "c"].map((k) => p3[k].x + p3[k].w / 2));
  assert.ok(x1 - x0 > 200, `тройное пересечение вырождено: ${x1 - x0}`);
  // у среднего своей полосы нет — его подпись уходит вверх, а не вбок
  assert.strictEqual(p3.b.labelDx, 0);
  assert.ok(p3.b.labelDy < 0);
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
  assert.strictEqual(lines.length, 4);   // три дорожки — четыре границы, включая крайние
  // Подпись дорожки прижата к верхней линии (+10), а не центрирована в полосе.
  assert.ok(/<foreignObject x="0" y="10"/.test(host.innerHTML), "подпись не у верхней линии");
  assert.ok(/align-items:flex-start/.test(host.innerHTML));
});

test("layoutSwimlanes: плашки по замерам, коленья коннекторов из двух отрезков", () => {
  const spec = { kind: "swimlanes", nodes: [
    { id: "a", label: "Заявка", lane: "Клиент" },
    { id: "b", label: "Проверка", lane: "Менеджер" },
    { id: "c", label: "Квота", lane: "Инженер" },
  ], edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }] };
  const out = D.layoutSwimlanes(spec);
  // Поле под подпись дорожки в начале полосы — плашки туда не заходят.
  assert.ok(out.nodes.a.x - out.nodes.a.w / 2 >= 240, "плашка залезла на подпись дорожки");
  ["a", "b", "c"].forEach((id) => {
    assert.strictEqual(out.nodes[id].w, 300);
    assert.strictEqual(out.nodes[id].h, 100);
  });
  // Колено — ровно три точки (два отрезка), вертикаль выходит из центра источника.
  out.links.forEach((l) => {
    assert.strictEqual(l.points.length, 3, "коннектор не из двух отрезков");
    assert.strictEqual(l.points[0][0], l.points[1][0]);        // первый отрезок вертикальный
    assert.strictEqual(l.points[1][1], l.points[2][1]);        // второй горизонтальный
  });
  assert.strictEqual(out.links[0].points[0][0], out.nodes.a.x);
  // Старт — на нижней кромке источника, финиш — не доходя до левого края приёмника.
  assert.strictEqual(out.links[0].points[0][1], out.nodes.a.y + out.nodes.a.h / 2);
  assert.ok(out.links[0].points[2][0] < out.nodes.b.x - out.nodes.b.w / 2);
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

test("layoutSteps: столбики на общем основании, растут вправо", () => {
  // Была лесенка из одинаковых плашек, висящих на разной высоте. Стали
  // столбики с ОБЩИМ низом: рост несёт высота, её видно и можно сравнить.
  const out = D.layoutSteps({ kind: "steps", nodes: [
    { id: "a", label: "Раз" }, { id: "b", label: "Два" }, { id: "c", label: "Три" },
  ] });
  inCanvas(out.nodes);
  const p = out.nodes;
  assert.ok(p.a.x < p.b.x && p.b.x < p.c.x);
  ["a", "b", "c"].forEach((id) =>
    assert.strictEqual(p[id].y + p[id].h / 2, H, "столбик " + id + " не на основании"));
  assert.ok(p.a.h < p.b.h && p.b.h < p.c.h);           // прибавка на ступень
  assert.strictEqual(p.b.h - p.a.h, p.c.h - p.b.h);    // прибавка одинаковая
  assert.strictEqual(out.links.length, 0);
  // Колонки делят ширину без остатка, между столбиками остаётся зазор.
  assert.strictEqual(p.a.x - p.a.w / 2, 0);
  assert.ok(Math.abs(p.c.x + p.c.w / 2 - W) < 10);
  assert.ok(p.b.x - p.b.w / 2 > p.a.x + p.a.w / 2);
});

test("render лестницы: подпись в верхнем углу столбика, рейка на всю высоту", () => {
  const host = fakeHost();
  const spec = { kind: "steps", nodes: [
    { id: "a", label: "Ручные регламенты", value: "Уровень 1" },
    { id: "b", label: "Автоматизация", value: "Уровень 2" },
    { id: "c", label: "Единая платформа", value: "Уровень 3", accent: true }] };
  D.render(host, spec);
  const html = host.innerHTML;
  const pos = D.layoutSteps(spec).nodes;
  // Рейка у левого края каждой колонки, от кромки до кромки зоны.
  ["a", "b", "c"].forEach((id) => {
    const x = pos[id].x - pos[id].w / 2;
    const re = new RegExp('<line x1="' + x + '" y1="0" x2="' + x + '" y2="' + H + '"');
    assert.ok(re.test(html), "нет рейки у колонки " + id);
  });
  // Подступенков не осталось: столбик стоит на основании, достраивать нечего.
  assert.ok(!/fill-opacity="0\.45"/.test(html), "вернулся подступенок");
  // Подпись прижата к верху столбика, а не центрирована в нём.
  const top = pos.a.y - pos.a.h / 2;
  assert.ok(html.includes('<foreignObject x="32" y="' + (top + 22) + '"'),
            "подпись не в верхнем левом углу столбика");
  assert.ok(html.includes("font-size:36px"), "кегль подписи ступени не 36");
  // Пометки «Уровень N» — общая строка над полем, у всех одна высота.
  const caps = [...html.matchAll(/<text x="(\d+)" y="(\d+)" font-size="24" font-weight="700"/g)];
  assert.strictEqual(caps.length, 3);
  assert.strictEqual(new Set(caps.map((m) => m[2])).size, 1, "пометки пошли лесенкой");
  // Акцентная ступень набрана Semibold — единственное небазовое начертание.
  assert.strictEqual((html.match(/font-weight:600/g) || []).length, 1);
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
  /* Ветвь — ломаная из прямых углов, а не безье: правило схем запрещает кривые.
     Форм две, и обе законны: прямой отрезок, когда ребёнок стоит напротив грани
     родителя (так все ветви первого уровня выходят из высокого центра), и
     колено из четырёх точек во всех остальных случаях. */
  out.links.forEach((l) => {
    assert.strictEqual(l.curve, undefined);
    assert.ok(l.points.length === 2 || l.points.length === 4,
      "ветвь карты — прямая или колено");
    l.points.slice(1).forEach((p, i) => {
      const q = l.points[i];
      assert.ok(p[0] === q[0] || p[1] === q[1], "сегмент ветви наискосок");
    });
  });
  // Ветви первого уровня стоят напротив высокого центра — значит прямые.
  ["r1", "l1", "r2"].forEach((id) => {
    const l = out.links.find((v) => v.from === "core" && v.to === id);
    assert.strictEqual(l.points.length, 2, `ветвь ${id} должна быть прямой`);
  });
  // Единственная подветвь наследует высоту родителя — тоже прямая.
  assert.strictEqual(out.links.find((l) => l.to === "k").points.length, 2);
  // А вот две подветви развести по высоте без колена нельзя.
  const forked = D.layoutMindmap({ kind: "mindmap", nodes: [
    { id: "core", label: "Центр" }, { id: "a", label: "Ветвь" },
    { id: "x", label: "Раз" }, { id: "y", label: "Два" },
  ], edges: [{ from: "core", to: "a" }, { from: "a", to: "x" },
             { from: "a", to: "y" }] });
  assert.strictEqual(forked.links.find((l) => l.to === "x").points.length, 4);
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

test("render: подписи веток развилки не накрываются плашками следующей колонки", () => {
  // Прод-замер (дека «Выбор по RTO и RPO», слайд 5): у прямого ребра метка стоит
  // СБОКУ от вертикального коридора с бюджетом 200px, а коридор проходит между
  // ЦЕНТРАМИ колонок — до плашки соседа остаётся ~86px. Плашки рисуются ПОСЛЕ
  // рёбер, и «около нуля»/«минуты/часы» превращались в «около н…» без следа
  // обрезки: текст цел, но накрыт. Метка обязана съехать вдоль сегмента в щель
  // между рядами (там просвет — вся колонка) или ужаться до фактического
  // просвета — и не пересекать ни одну нарисованную фигуру.
  const spec = {
    kind: "flowchart", direction: "right",
    nodes: [{ id: "start", label: "Выбор решения", shape: "start" },
            { id: "rpo", label: "RPO: сколько данных можно потерять?", shape: "decision" },
            { id: "rto", label: "RTO: сколько можно не работать?", shape: "decision" },
            { id: "sync", label: "Синхронный резерв" },
            { id: "repl", label: "Репликация или backup" },
            { id: "dr", label: "DR или горячий резерв" },
            { id: "bkp", label: "Backup достаточен" },
            { id: "cls", label: "Класс защиты сервиса", shape: "end", accent: true }],
    edges: [{ from: "start", to: "rpo" }, { from: "start", to: "rto" },
            { from: "rpo", to: "sync", label: "около нуля" },
            { from: "rpo", to: "repl", label: "минуты/часы" },
            { from: "rto", to: "dr", label: "минуты" },
            { from: "rto", to: "bkp", label: "часы–сутки" },
            { from: "sync", to: "cls" }, { from: "repl", to: "cls" },
            { from: "dr", to: "cls" }, { from: "bkp", to: "cls" }],
  };
  const host = fakeHost();
  D.render(host, spec);
  const out = D.LAYOUTS.flowchart(spec);
  const texts = host.innerHTML.match(/<text[^>]*>[\s\S]*?<\/text>/g) || [];
  assert.strictEqual(texts.length, 4, "подписи веток потерялись");
  texts.forEach((t) => {
    const x = Number(t.match(/x="([\d.-]+)"/)[1]);
    const y = Number(t.match(/y="([\d.-]+)"/)[1]);
    const fs = Number(t.match(/font-size="([\d.-]+)"/)[1]);
    const anchor = (t.match(/text-anchor="(\w+)"/) || [])[1];
    const lines = (t.match(/<tspan[^>]*>([^<]*)<\/tspan>/g) || [])
      .map((s) => s.replace(/<[^>]*>/g, ""));
    // Щель между рядами вмещает подпись целиком — огрызок с «…» был бы
    // регрессией не хуже накрытой плашкой метки.
    assert.ok(!lines.join("").includes("…"),
              `подпись «${lines.join(" ")}» усохла до огрызка`);
    const w = Math.max(...lines.map((s) => s.length)) * fs * 0.6;
    const lead = Math.round(fs * 1.16);
    const x0 = anchor === "start" ? x : x - w / 2;
    const box = { x0: x0, x1: x0 + w,
                  y0: y - (lines.length - 1) * lead - fs, y1: y };
    Object.keys(out.nodes).forEach((id) => {
      const p = out.nodes[id];
      const ph = p.hDraw || p.h;      // ромб рисуется выше своей коробки
      const over = box.x0 < p.x + p.w / 2 && box.x1 > p.x - p.w / 2 &&
                   box.y0 < p.y + ph / 2 && box.y1 > p.y - ph / 2;
      assert.ok(!over, `подпись «${lines.join(" ")}» накрыта фигурой «${id}»`);
    });
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
    ["cycle", "flowchart", "funnel", "gantt_lite", "hierarchy",
     "hub_spoke", "matrix", "mindmap", "network", "process", "pyramid", "steps",
     "swimlanes", "venn"]);
});

/* --- правила схем из шапки diagram.js ------------------------------------ */

/* По какой оси тип обязан держать равный шаг между рангами. Пустая строка —
   тип раскладывается не сеткой, и это осознанно: кольцо, радиус, круги, ось
   времени. Новая раскладка обязана попасть в этот список — иначе тест упадёт
   на сверке с LAYOUTS и заставит объявить поведение сетки явно. */
const GRID_AXES = {
  flowchart: "xy", process: "xy", steps: "xy", swimlanes: "xy",
  funnel: "xy", pyramid: "xy", matrix: "xy",
  hierarchy: "y",            // колонки — слоты листьев, ряды равномерны
  mindmap: "x",              // наоборот: колонки равномерны, ряды — слоты
  cycle: "",                 // кольцо: равный шаг по углу, не по координате
  hub_spoke: "x",            // два ряда подписей по равномерным колонкам
  venn: "",                  // прямоугольники внахлёст: шаг есть, но ряда нет
  network: "xy",             // колонка — ранг в потоке, ряд — порядок в ранге
  gantt_lite: "",            // x — это время
};

function gridSpec(kind, n) {
  const nodes = "abcdefghijkl".split("").slice(0, n)
    .map((id, i) => ({ id, label: "Узел " + (i + 1) }));
  if (kind === "swimlanes") nodes.forEach((v, i) => { v.lane = ["X", "Y", "Z"][i % 3]; });
  if (kind === "gantt_lite") nodes.forEach((v, i) => { v.start = i + 1; v.end = i + 2; });
  return { version: 1, kind, direction: "right", nodes,
           edges: nodes.slice(1).map((v, i) => ({ from: nodes[i].id, to: v.id })) };
}

function ranks(nodes, axis) {
  const vals = [...new Set(Object.values(nodes).map((n) => Math.round(n[axis] * 100) / 100))];
  return vals.sort((a, b) => a - b);
}

test("сетка: ранги узлов стоят на равном шаге", () => {
  assert.deepStrictEqual(Object.keys(GRID_AXES).sort(), Object.keys(D.LAYOUTS).sort(),
    "новая раскладка не объявила, по какой оси держит сетку");
  Object.keys(GRID_AXES).forEach((kind) => {
    GRID_AXES[kind].split("").forEach((axis) => {
      for (const n of [2, 3, 4, 5, 6, 8, 10, 12]) {
        const r = ranks(D.LAYOUTS[kind](gridSpec(kind, n)).nodes, axis);
        if (r.length < 3) continue;          // один ранг или два — шага ещё нет
        const d = r[1] - r[0];
        r.slice(1).forEach((v, i) => assert.ok(Math.abs((v - r[i]) - d) < 0.5,
          `${kind} n=${n}: шаг по ${axis} рваный — ${r.join(" ")}`));
      }
    });
  });
});

test("стрелки: только прямые отрезки и прямые углы", () => {
  // Кривых в линиях нет вовсе: ни curve/arc в раскладке, ни наклонного колена.
  Object.keys(D.LAYOUTS).forEach((kind) => {
    for (const n of [3, 4, 6, 8]) {
      (D.LAYOUTS[kind](gridSpec(kind, n)).links || []).forEach((l) => {
        assert.strictEqual(l.curve, undefined, `${kind}: безье в связи`);
        assert.strictEqual(l.arc, undefined, `${kind}: дуга в связи`);
        const p = l.points || [];
        if (p.length <= 2) return;           // одиночный отрезок может идти косо
        for (let i = 1; i < p.length; i++) {
          const dx = Math.abs(p[i][0] - p[i - 1][0]), dy = Math.abs(p[i][1] - p[i - 1][1]);
          assert.ok(dx < 0.5 || dy < 0.5,
            `${kind} n=${n}: колено с косым сегментом — ${JSON.stringify(p)}`);
        }
      });
    }
  });
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
  assert.ok(tight < 24, `ожидалось уменьшение кегля, получено ${tight}`);
  // 24 — потолок кегля подписи узла (NODE_FS, замер по макетам Figma).
  assert.strictEqual(D.fitFont("Сбор данных", 300, 110), 24);
  assert.strictEqual(D.fitFont("", 40, 20), 24);
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

test("render: подпись пересечения venn не наезжает на подписи множеств", () => {
  // center_label рисовался голым <text> без габарита: «Планирование маршрута
  // доставки» растягивалось на 450px при общей части в 180 и ложилось поверх
  // подписей обоих множеств — читалось месиво из наложенных букв.
  const spec = { kind: "venn",
    nodes: [{ id: "a", label: "Отдел клиентского сопровождения" },
            { id: "b", label: "Электроэнцефалография пациента" }],
    meta: { center_label: "Планирование маршрута доставки" } };
  const host = fakeHost();
  D.render(host, spec);
  assert.ok(!/<text[^>]*>[^<]*Планирование/.test(host.innerHTML),
    "подпись пересечения всё ещё голый <text> без габарита");
  const i = host.innerHTML.indexOf("Планирование");
  const fo = host.innerHTML.lastIndexOf("<foreignObject", i);
  assert.ok(fo >= 0, "подпись пересечения не в foreignObject");
  const w = Number(/width="([\d.]+)"/.exec(host.innerHTML.slice(fo, i))[1]);
  const p = D.layoutVenn(spec).nodes;
  const over = p.a.x + p.a.w / 2 - (p.b.x - p.b.w / 2);   // общая часть пары
  assert.ok(w > 0 && w <= over, `подпись пересечения шире общей части: ${w}`);
});

test("render Венна: палитры графиков нет — множества не упорядочены", () => {
  // Исключение из «ряды красит --chart-N»: шкала кодирует ПОРЯДОК, а множества
  // Венна не упорядочены. Практика: залитое первое множество занимает половину
  // зоны, и --chart-1 делал пол-схемы сплошным зелёным полем при правиле «один
  // акцент на слайд», а серый хвост шкалы в контурах остальных на тёмном фоне
  // не читался. Держим макет: нейтральная заливка + зелёный контур.
  const host = fakeHost();
  D.render(host, { kind: "venn", nodes: [
    { id: "a", label: "Скорость разработки" },
    { id: "b", label: "Надежность эксплуатации" }] });
  const html = host.innerHTML;
  assert.ok(!/var\(--chart-\d\)/.test(html), "Венн взял палитру графиков");
  assert.ok(!/var\(--on-chart-\d\)/.test(html), "подпись Венна по палитре графиков");
  // Залиты ОБА множества, и одним тоном. Контурный прямоугольник был бы дырой
  // в композиции — сквозь него видно фон зоны, и множество читается пустым;
  // а две РАЗНЫЕ заливки внахлёст дали бы в общей части третий цвет, то есть
  // сущность, которой в данных нет. Отличаются множества только обводкой.
  assert.strictEqual((html.match(/fill="var\(--bg-card\)"/g) || []).length, 2,
                     "залито не два множества");
  // Без заливки остаётся РОВНО одна рамка — пунктир общей части: сквозь неё
  // обязаны просвечивать оба множества, иначе это не пересечение, а третья
  // плашка поверх них.
  const hollow = html.match(/<rect[^>]*fill="none"[^>]*>/g) || [];
  assert.strictEqual(hollow.length, 1, "пустых рамок не одна");
  assert.ok(/stroke-dasharray/.test(hollow[0]), "пустая рамка — не пунктир пересечения");
  assert.ok(/fill="var\(--bg-card\)" stroke="var\(--accent\)"/.test(html),
            "у пересекающего множества нет зелёной обводки");
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
  // Луч — короткая вертикальная стрелка между хабом и подписью ряда. Рядом с
  // ней нет ни одной плашки (в этой схеме их нет вовсе), значит и уводить
  // метку некуда: она обязана стоять на своём отрезке.
  const spec = { kind: "hub_spoke",
    nodes: [{ id: "h", label: "Платформа" }, { id: "a", label: "Раз" },
            { id: "b", label: "Два" }, { id: "c", label: "Три" },
            { id: "d", label: "Четыре" }],
    edges: [{ from: "h", to: "b", label: "поставляет" }] };
  const host = fakeHost();
  D.render(host, spec);
  const seg = D.layoutHubSpoke(spec).links[0].points;
  const lo = Math.min(seg[0][1], seg[1][1]), hi = Math.max(seg[0][1], seg[1][1]);
  const y = Number(host.innerHTML.match(/<text[^>]*y="([\d.-]+)"/)[1]);
  assert.ok(y >= lo - 14 && y <= hi + 14, `метка ушла с отрезка: y=${y}`);
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

test("render: толщина связи ОДНА на весь движок", () => {
  // Цикл задавал ребру width:2 «по макету», остальные типы рисовались тройкой
  // по умолчанию. На одной деке это давало стрелки разной толщины от слайда к
  // слайду. Теперь толщина — свойство движка (EDGE_STROKE), а не ребра, и ни
  // один тип её не переопределяет.
  const seen = new Set();
  Object.keys(D.LAYOUTS).forEach((kind) => {
    const host = fakeHost();
    D.render(host, { kind, nodes: [
      { id: "a", label: "Старт", lane: "X", start: 1, end: 2 },
      { id: "b", label: "Шаг", lane: "Y", start: 2, end: 3 },
      { id: "c", label: "Финиш", lane: "X", start: 3, end: 4 }],
      edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }] });
    // <defs> отрезаем: внутри маркера штрих 1 намеренный — markerUnits по
    // умолчанию strokeWidth, то есть голова масштабируется толщиной линии.
    const body = host.innerHTML.replace(/<defs>.*?<\/defs>/g, "");
    for (const m of body.matchAll(/<path [^>]*stroke-width="([\d.]+)"/g)) {
      seen.add(m[1]);
    }
  });
  assert.deepStrictEqual([...seen], ["2"],
    "толщина стрелок разошлась по типам: " + [...seen].join(", "));
});

test("render: точечная подложка стоит не у всех типов", () => {
  // Сетка читается как пустое поле, по которому плавает фигура, и смысл у неё
  // есть ровно там, где схема зону не заполняет. У матрицы, Венна, плана и
  // лестницы плашки занимают всю зону — точки лезут в щели между ними и в
  // просветы заливок, и подложка превращается в шум поверх геометрии.
  const WITHOUT = ["matrix", "venn", "gantt_lite", "steps"];
  Object.keys(D.LAYOUTS).forEach((kind) => {
    const host = fakeHost();
    D.render(host, { kind, nodes: [
      { id: "a", label: "Первый", lane: "X", start: 1, end: 2 },
      { id: "b", label: "Второй", lane: "Y", start: 2, end: 3 }] });
    const has = /class="dgm-grid"/.test(host.innerHTML);
    assert.strictEqual(has, WITHOUT.indexOf(kind) === -1,
      kind + (has ? ": подложка лишняя" : ": подложка пропала"));
  });
});

test("layoutNetwork: колена разных узлов не сливаются в одну линию", () => {
  // Все колена одного просвета вставали на его середину, и огрызки разных
  // связей встречались в одной точке. На выборке каталога это рисовало
  // соединение, которого в данных нет: «Каталог услуг → Базы данных» шёл
  // вправо и вверх, «Биллинг → Очередь событий» — вверх и вправо, а читалось
  // как сплошная стрелка «Каталог услуг → Очередь событий».
  const spec = { kind: "network", nodes: [
    { id: "api", label: "API" }, { id: "cat", label: "Каталог" },
    { id: "bill", label: "Биллинг" }, { id: "db", label: "БД" },
    { id: "que", label: "Очередь" }],
    edges: [{ from: "api", to: "cat" }, { from: "api", to: "bill" },
            { from: "cat", to: "db" }, { from: "bill", to: "db" },
            { from: "bill", to: "que" }] };
  const { links } = D.layoutNetwork(spec);
  // Вертикальная полоса колена — своя у каждого ИСТОЧНИКА: веер из одного узла
  // по общему стволу двусмысленности не создаёт (видно, что всё идёт от него),
  // а стволы разных узлов обязаны разойтись.
  const lane = {};
  links.filter((l) => l.points.length === 4).forEach((l) => {
    (lane[l.from] = lane[l.from] || new Set()).add(l.points[1][0]);
  });
  Object.keys(lane).forEach((from) => assert.strictEqual(lane[from].size, 1,
    from + ": веер из одного узла разъехался по разным полосам"));
  const xs = Object.keys(lane).map((k) => [...lane[k]][0]);
  assert.strictEqual(new Set(xs).size, xs.length,
    "колена разных узлов встали на одну полосу");
});

test("render: у плашек прямые углы — во всех типах схем", () => {
  // --radius:0 в deck.css заявлен инвариантом бренда, шаблоны графиков пишут
  // rx="0" явно, скругления отвергает и филлер, и vision-QA. Полосы плана были
  // единственным скруглённым элементом движка: рядом с прямоугольными плашками
  // остальных схем это читалось как чужая вставка.
  const host = fakeHost();
  D.render(host, GANTT);
  const bars = host.innerHTML.split("data-node-id").slice(1);
  bars.forEach((chunk) => assert.ok(!/^[^>]*\brx=/.test(chunk.slice(chunk.indexOf("<rect"))),
    "полоса плана снова скруглена"));
  // Терминаторы блок-схемы были капсулами (rx = h/2). В макетах схем капсул нет
  // ни у одного типа — проверяем, что скругление не вернулось ни в одну плашку.
  Object.keys(D.LAYOUTS).forEach((kind) => {
    const fc = fakeHost();
    D.render(fc, { kind, nodes: [
      { id: "a", label: "Старт", shape: "start", lane: "X", start: 1, end: 2 },
      { id: "b", label: "Финиш", shape: "end", lane: "Y", start: 2, end: 3 }] });
    fc.innerHTML.split("data-node-id").slice(1).forEach((chunk) =>
      assert.ok(!/\brx="(?!0")/.test(chunk), `${kind}: плашка снова скруглена`));
  });
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

/* Шкалу --chart-N после сверки с макетами держит один план-график: воронка и
   пирамида с неё сняты (см. renderFunnel). Оба теста ниже переехали на Гант —
   поведение проверяется то же самое, просто на единственном его носителе. */
const ganttRows = (rows) => ({
  kind: "gantt_lite",
  nodes: rows.map((r, i) => Object.assign(
    { id: "g" + i, start: 1, end: 6 }, r)),
});

test("render: ряды красит палитра графиков, зелёный в ряду ровно один", () => {
  // --chart-N кодирует РЯД: у воронки, пирамиды и плана-графика узлы и есть
  // ряд. Инвариант палитры — --accent и --chart-1 это ОДИН зелёный, поэтому
  // при проставленном акценте остальные узлы начинают шкалу с серого
  // (--chart-2): двух зелёных на схеме не бывает.
  const html = (spec) => { const h = fakeHost(); D.render(h, spec); return h.innerHTML; };
  const specs = [
    ganttRows([{ label: "Аудит" }, { label: "Пилот" }, { label: "Запуск", accent: true },
               { label: "Сопровождение" }]),
    { kind: "funnel", nodes: [{ id: "a", label: "Лиды" }, { id: "b", label: "Пилоты" },
                              { id: "c", label: "Сделки", accent: true }] },
  ];
  specs.forEach((spec) => {
    const h = html(spec);
    assert.ok(/var\(--chart-\d\)/.test(h), spec.kind + ": палитра графиков не вернулась");
    assert.ok(/var\(--on-chart-\d\)/.test(h), spec.kind + ": подпись не по палитре");
    // Зелёный ровно один — акцентный; шкала у остальных стартует с серого.
    assert.strictEqual((h.match(/var\(--accent\)/g) || []).length, 1,
                       spec.kind + ": зелёных не один");
    assert.ok(!/var\(--chart-1\)/.test(h),
              spec.kind + ": при акценте шкала обязана начинаться с --chart-2");
  });
  // Без акцента шкала идёт с самого начала — первый узел зелёный (--chart-1),
  // а отдельного var(--accent) в разметке нет: это тот же цвет под другим именем.
  const plain = html({ kind: "funnel", nodes: [
    { id: "a", label: "Лиды" }, { id: "b", label: "Сделки" }] });
  assert.ok(/var\(--chart-1\)/.test(plain), "без акцента шкала не с начала");
  assert.strictEqual((plain.match(/var\(--accent\)/g) || []).length, 0);
});

test("render плана-графика: полосы по шкале, зелёная ровно одна", () => {
  const greens = (html) => (html.match(/fill="var\(--accent\)"/g) || []).length;

  const host = fakeHost();
  D.render(host, ganttRows([{ label: "Лиды" }, { label: "Квалификация" },
    { label: "Пилоты" }, { label: "Сделки", accent: true }]));
  const html = host.innerHTML;
  assert.strictEqual(greens(html), 1, "зелёная полоса должна быть одна");
  // Три неакцентные работы идут по шкале подряд, начиная с серого --chart-2.
  assert.deepStrictEqual(
    [...html.matchAll(/<rect [^>]*fill="var\(--chart-(\d)\)"/g)].map((m) => m[1]),
    ["2", "3", "4"], "полосы не по порядку шкалы");
  // Подпись внутри полосы лежит поверх заливки — цвет диктует --on-chart-N.
  assert.ok(/var\(--on-chart-\d\)/.test(html), "подпись в полосе не по палитре");

  // Без акцента зелёного-как-accent нет: шкала просто идёт с --chart-1, то есть
  // с того же зелёного. Отдельного «героя» схема не назначает.
  const plain = fakeHost();
  D.render(plain, ganttRows([{ label: "Лиды" }, { label: "Сделки" }]));
  assert.strictEqual(greens(plain.innerHTML), 0);
  assert.ok(/var\(--chart-1\)/.test(plain.innerHTML));
});

test("render воронки: ступени по шкале, геометрия макетная", () => {
  // Цвет ступеней вернулся к шкале --chart-N, но геометрия осталась из макета:
  // прямоугольники вместо трапеций и числа отдельной правой колонкой.
  const host = fakeHost();
  D.render(host, { kind: "funnel", nodes: [
    { id: "a", label: "Лонг-лист", value: "12 000" },
    { id: "b", label: "Техпроверка", value: "3 400" },
    { id: "c", label: "Финпроверка", value: "1 100" },
    { id: "d", label: "Контракты", value: "280", accent: true }] });
  const html = host.innerHTML;
  assert.deepStrictEqual(
    [...html.matchAll(/<rect [^>]*fill="var\(--chart-(\d)\)"/g)].map((m) => m[1]),
    ["2", "3", "4"], "ступени не по порядку шкалы");
  assert.strictEqual((html.match(/fill="var\(--accent\)"/g) || []).length, 1);
  // Подмеса-тонирования больше нет: цвет ступени задаёт шкала целиком.
  assert.ok(!/fill="var\(--fg-body\)" opacity=/.test(html), "вернулся подмес тона");
  // Ни одной косой кромки: трапеций у воронки больше нет.
  assert.ok(!html.includes("<polygon"), "в воронке осталась трапеция");
  // Числа — одной правой колонкой с выключкой вправо.
  assert.strictEqual((html.match(/text-anchor="end"/g) || []).length, 4);
  assert.strictEqual((html.match(/x="1765"/g) || []).length, 4);
});

test("render пирамиды: ярусы по шкале, силуэт треугольника и насечки", () => {
  // Пирамида — ОДНА однотонная фигура: ярус задаётся насечкой, а не цветом.
  // Шкалу --chart-N здесь пробовали, каждому ярусу свой тон, и это сломало
  // фигуру дважды: однотонный силуэт макета превратился в стопку трапеций, а
  // полосы рисовались от первой насечки, а не от вершины, — остриё просто не
  // выводилось, и верх выходил срезанным. Тест держит оба свойства.
  const host = fakeHost();
  D.render(host, { kind: "pyramid", nodes: [
    { id: "p1", label: "Видение" }, { id: "p2", label: "Стратегические цели" },
    { id: "p3", label: "Ключевые инициативы" },
    { id: "p4", label: "Операционные задачи" }] });
  const html = host.innerHTML;
  assert.ok(!/var\(--chart-\d\)/.test(html), "пирамида взяла палитру графиков");
  // Фигура ровно одна, и она треугольник: три вершины, остриё на оси.
  const polys = [...html.matchAll(/<polygon points="([^"]+)" fill="([^"]+)"\/>/g)];
  assert.strictEqual(polys.length, 1, "пирамида не одной фигурой");
  assert.strictEqual(polys[0][2], "var(--bg-card)", "заливка пирамиды не нейтральная");
  const pts = polys[0][1].split(" ").map((s) => s.split(",").map(Number));
  assert.strictEqual(pts.length, 3, "силуэт не треугольник");
  assert.strictEqual(pts[0][0], 900, "остриё не на оси зоны");
  // Вписана в область паттерна: вершина на верхней кромке зоны, основание на
  // нижней. Раньше основание стояло на 750 при холсте 720 и фигуру срезало.
  assert.strictEqual(pts[0][1], 0, "вершина не на верхней кромке зоны");
  assert.strictEqual(pts[1][1], 720, "основание не на нижней кромке зоны");
  assert.strictEqual(pts[2][1], 720, "основание не на нижней кромке зоны");
  // Разделителей на один больше, чем ярусов, и все они короче основания.
  const cuts = [...html.matchAll(/<line x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)"/g)];
  assert.strictEqual(cuts.length, 5);
  cuts.forEach((m) => assert.ok(Number(m[3]) - Number(m[1]) < 1754,
    "разделитель шире треугольника"));
  // Подписи крупнее общей двадцатьчетвёрки — это замер макета.
  assert.ok(html.includes("font-size:32px"), "кегль подписей яруса не 32");
});

test("render пирамиды: акцентный ярус зелёный, и только он", () => {
  // Единственное исключение из однотонности — ярус, помеченный accent. Это не
  // градация ряда, а указатель «вот этот уровень», и работает он только пока
  // остальные ярусы нейтральны.
  const host = fakeHost();
  D.render(host, { kind: "pyramid", nodes: [
    { id: "p1", label: "Видение" },
    { id: "p2", label: "Цели", accent: true },
    { id: "p3", label: "Задачи" }] });
  const html = host.innerHTML;
  const polys = [...html.matchAll(/<polygon points="([^"]+)" fill="([^"]+)"\/>/g)];
  assert.strictEqual(polys.length, 2, "кроме силуэта и акцента есть ещё полосы");
  assert.strictEqual(polys[0][2], "var(--bg-card)");
  assert.strictEqual(polys[1][2], "var(--accent)");
  assert.strictEqual((html.match(/var\(--accent\)/g) || []).length, 1,
                     "зелёного на схеме больше одного");
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

function bbox(pos) {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const id of Object.keys(pos)) {
    const p = pos[id];
    x0 = Math.min(x0, p.x - p.w / 2); x1 = Math.max(x1, p.x + p.w / 2);
    y0 = Math.min(y0, p.y - p.h / 2); y1 = Math.max(y1, p.y + p.h / 2);
  }
  return { x0, x1, y0, y1 };
}

test("layout: схема стоит по центру холста, а не жмётся к краю", () => {
  // Раскладки считают позиции от «идеальной» геометрии (корень карты — в
  // середине, ветви расходятся в обе стороны). На НЕсимметричных данных схема
  // уезжала к краю: карта с ОДНОЙ ветвью оставляла 745px пустоты слева и 45
  // справа — слайд выглядел сломанным, хотя каждый узел стоял «правильно».
  const one = { version: 1, kind: "mindmap", direction: "right",
    nodes: [{ id: "a", label: "Тема" }, { id: "b", label: "Ветвь" },
            { id: "c", label: "Лист" }],
    edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }] };
  const raw = bbox(D.LAYOUTS.mindmap(one).nodes);
  assert.ok(raw.x0 - (W - raw.x1) > 300, "сэмпл должен быть несимметричным");

  for (const kind of ["mindmap", "cycle", "flowchart", "hierarchy", "network",
                      "process", "hub_spoke", "venn"]) {
    const spec = kind === "mindmap" ? one : chain(kind, 5);
    const b = bbox(D.placed(spec).nodes);
    assert.ok(Math.abs(b.x0 - (W - b.x1)) <= 2,
      `${kind}: поля по X ${Math.round(b.x0)}/${Math.round(W - b.x1)}`);
    assert.ok(Math.abs(b.y0 - (H - b.y1)) <= 2,
      `${kind}: поля по Y ${Math.round(b.y0)}/${Math.round(H - b.y1)}`);
  }
});

test("layout: схему с фоновой разметкой не центрируем", () => {
  // Оси матрицы, дорожки, сетка Ганта и ступени нарисованы в АБСОЛЮТНЫХ
  // координатах холста: сдвинь узлы — и подпись дорожки уедет от своей полосы.
  // Хаб тоже тут: его панель — такая же фоновая разметка во всю зону, а ряды
  // лучей привязаны к её краям. Воронка и пирамида добавились после сверки с
  // макетами: у воронки колонка ступеней стоит не по центру холста (справа
  // отрезана полоса под числа), у пирамиды узлы — это поля подписей внутри
  // треугольника с абсолютными вершинами.
  for (const kind of ["matrix", "swimlanes", "gantt_lite", "steps", "hub_spoke",
                      "funnel", "pyramid"]) {
    const spec = chain(kind, 4);
    assert.deepEqual(D.placed(spec).nodes, D.LAYOUTS[kind](spec).nodes, kind);
  }
});

test("layout: граф связей читается слева направо", () => {
  // Силовая модель симметрична — направление рёбер на неё не влияло, и цепочка
  // a→b→c с равным успехом ложилась справа налево: граф читался задом наперёд.
  const fwd = D.placed(chain("network", 5)).nodes;
  for (let i = 1; i < 5; i++) {
    assert.ok(fwd["n" + i].x > fwd["n" + (i - 1)].x,
      `n${i - 1}→n${i}: поток идёт назад`);
  }
  // Те же узлы, рёбра объявлены с конца — раскладка обязана совпасть.
  const back = chain("network", 5);
  back.edges = back.edges.map((e) => ({ from: e.to, to: e.from })).reverse();
  const pos = D.placed(back).nodes;
  for (let i = 1; i < 5; i++) {
    assert.ok(pos["n" + i].x < pos["n" + (i - 1)].x,
      `n${i}→n${i - 1}: поток идёт назад`);
  }
});
