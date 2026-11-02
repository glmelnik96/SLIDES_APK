const test = require("node:test");
const assert = require("node:assert/strict");
const { placement, GAP } = require("../../webapp/static/tooltip.js");

// Вьюпорт 1440×900 — расчетный стол редактора (три колонки 264|768|264).
const VP = { width: 1440, height: 900 };
// Кирпич 36×24 — иконочная кнопка ряда «Действия со слайдами» (борд 4892:5392).
const BTN = (x, y) => ({ top: y, bottom: y + 24, left: x, width: 36 });
// Подсказка «Добавлю пустой слайд после текущего» в 264 укладывается в строку.
const TIP = { width: 264, height: 44 };

test("по умолчанию подсказка встает под триггером с отбивкой 8", () => {
  const p = placement(BTN(0, 200), TIP, VP);
  assert.equal(p.side, "below");
  assert.equal(p.top, 224 + GAP);
});

test("снизу не хватает места — уходит наверх сама", () => {
  // Кнопка у нижней кромки: 900 − 880 = 20 < 44 + 8 + 8.
  const p = placement(BTN(0, 856), TIP, VP);
  assert.equal(p.side, "above");
  assert.equal(p.top, 856 - 44 - GAP);
});

test("force=above поднимает подсказку, даже когда снизу есть место", () => {
  // Это случай «Сменить макет слайда» (§4): под кнопкой поле «Заголовок»,
  // место формально свободно, но занято смыслом.
  const p = placement(BTN(1100, 300), TIP, VP, "above");
  assert.equal(p.side, "above");
  assert.equal(p.top, 300 - 44 - GAP);
});

test("просьбу поднять игнорируем, если сверху обрежется", () => {
  // Кнопка у верхней кромки окна: обрезанная подсказка хуже перекрытого поля.
  const p = placement(BTN(1100, 12), TIP, VP, "above");
  assert.equal(p.side, "below");
  assert.equal(p.top, 36 + GAP);
});

test("подсказка центруется по триггеру", () => {
  const p = placement(BTN(600, 200), TIP, VP);
  assert.equal(p.left, 600 + 18 - 132);
});

test("у правого края подсказка прижимается к вьюпорту, а не уезжает", () => {
  // Кнопка правого рельса: центровка дала бы left 1404 − 132 = 1272,
  // то есть правый край на 1536 — на 96 за кадром.
  const p = placement(BTN(1386, 200), TIP, VP);
  assert.equal(p.left, VP.width - TIP.width - 8);
  assert.ok(p.left + TIP.width <= VP.width);
});

test("у левого края подсказка прижимается тем же зазором", () => {
  const p = placement(BTN(0, 200), TIP, VP);
  assert.equal(p.left, 8);
});

test("высокая подсказка в коротком окне не уходит в минус", () => {
  // Узкое окно (например, развернутый стол на 860): места нет ни снизу, ни
  // сверху — выбираем «снизу», чтобы верх подсказки остался на экране.
  const p = placement(BTN(0, 150), { width: 264, height: 300 },
                      { width: 1440, height: 400 });
  assert.equal(p.side, "below");
  assert.ok(p.top > 0);
});
