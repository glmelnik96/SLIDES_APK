const test = require("node:test");
const assert = require("node:assert");
const { errText, SAVE_STATUS, REBUILD_LABEL, plural, estimateLine,
  healthLine, checkedAgo } = require("../../webapp/static/errtext.js");

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
