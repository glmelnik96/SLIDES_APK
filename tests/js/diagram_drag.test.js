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

test("pruneZero: нулевые сдвиги не хранятся (пустой offsets = авто-режим)", () => {
  const off = { a: { dx: 0, dy: 0 }, b: { dx: 0.2, dy: -0.3 }, c: { dx: 40, dy: 0 } };
  assert.deepStrictEqual(Drag.pruneZero(off), { c: { dx: 40, dy: 0 } });
});
