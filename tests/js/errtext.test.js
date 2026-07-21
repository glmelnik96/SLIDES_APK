const test = require("node:test");
const assert = require("node:assert");
const { errText, SAVE_STATUS, REBUILD_LABEL, plural } = require("../../webapp/static/errtext.js");

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

test("SAVE_STATUS содержит четыре состояния (+retrying)", () => {
  assert.deepStrictEqual(Object.keys(SAVE_STATUS).sort(),
    ["error", "retrying", "saved", "saving"]);
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
