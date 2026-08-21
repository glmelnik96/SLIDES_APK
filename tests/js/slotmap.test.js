/* Перенос пунктов между макетами при смене макета (webapp/static/slotmap.js).
   Регрессия: «Две карточки» → «Сетка плашек» молча теряла весь текст карточек,
   и слайд показывал вместо него пример-рыбу. */
const test = require("node:test");
const assert = require("node:assert");
const { remapLists, remapItem, applySlotEdit } =
  require("../../webapp/static/slotmap.js");

const CARDS = {
  id: "two-col-cards",
  slots: {
    title: { kind: "text" },
    cards: { kind: "list", max_items: 2,
             item_slots: { heading: { kind: "text" }, body: { kind: "text" } } },
  },
};
const GRID = {
  id: "grid-2x2",
  slots: {
    title: { kind: "text" },
    row1: { kind: "list", max_items: 2,
            item_slots: { heading: { kind: "text" }, text: { kind: "text" } } },
    row2: { kind: "list", max_items: 2,
            item_slots: { heading: { kind: "text" }, text: { kind: "text" } } },
  },
};
const BARS = {
  id: "bar-chart",
  slots: {
    bars: { kind: "list", max_items: 6,
            item_slots: { label: { kind: "text" }, value: { kind: "text" },
                          display: { kind: "text" } } },
  },
};
const TIMELINE = {
  id: "timeline",
  slots: { items: { kind: "list", max_items: 5,
                    item_slots: { text: { kind: "text" } } } },
};

test("карточки → сетка: body уезжает в text, заголовки на месте", () => {
  const out = remapLists(CARDS, GRID, {
    title: "Сравнение",
    cards: [{ heading: "Своя", body: "Полный контроль" },
            { heading: "Коробка", body: "Запуск за 5 месяцев" }],
  });
  assert.strictEqual(out.title, "Сравнение");
  assert.deepStrictEqual(out.row1, [
    { heading: "Своя", text: "Полный контроль" },
    { heading: "Коробка", text: "Запуск за 5 месяцев" },
  ]);
  assert.deepStrictEqual(out.cards, [   // исходный слот цел — возврат восстановит
    { heading: "Своя", body: "Полный контроль" },
    { heading: "Коробка", body: "Запуск за 5 месяцев" },
  ]);
});

test("поток переливается в следующий список по вместимости", () => {
  const items = [1, 2, 3].map((n) => ({ heading: "H" + n, body: "B" + n }));
  const out = remapLists(CARDS, GRID, { cards: items });
  assert.strictEqual(out.row1.length, 2);
  assert.strictEqual(out.row2.length, 1);
  assert.strictEqual(out.row2[0].heading, "H3");
});

test("одноимённый слот не трогаем — уезжает как есть", () => {
  const SAME = { id: "cards-6", slots: { cards: { kind: "list", max_items: 6,
    item_slots: { heading: { kind: "text" }, text: { kind: "text" } } } } };
  const content = { cards: [{ heading: "H", body: "B" }] };
  assert.strictEqual(remapLists(CARDS, SAME, content), content);
});

test("уже заполненный целевой слот не перезаписываем (возврат A→B→A)", () => {
  const out = remapLists(CARDS, GRID, {
    cards: [{ heading: "новое", body: "новое" }],
    row1: [{ heading: "старое", text: "старое" }],
  });
  assert.deepStrictEqual(out.row1, [{ heading: "старое", text: "старое" }]);
});

test("числовой слот не подменяем текстом, текстовый добирает что есть", () => {
  const out = remapLists(CARDS, BARS, {
    cards: [{ heading: "Склад", body: "Очень длинное описание" }],
  });
  assert.strictEqual(out.bars[0].label, "Склад");
  assert.strictEqual(out.bars[0].value, "");        // цифру из текста не выдумываем
  assert.strictEqual(out.bars[0].display, "");
});

test("график → таймлайн: подпись переходит в текст пункта", () => {
  const out = remapLists(BARS, TIMELINE, {
    bars: [{ label: "Запуск", value: "40", display: "40%" }],
  });
  assert.strictEqual(out.items[0].text, "Запуск");
});

test("пустые пункты не переносим", () => {
  const content = { cards: [{ heading: "", body: "" }] };
  assert.strictEqual(remapLists(CARDS, GRID, content), content);
});

test("remapItem: одноимённый слот важнее роли", () => {
  const out = remapItem({ text: "тело", heading: "шапка" },
                        { heading: { kind: "text" }, text: { kind: "text" } });
  assert.deepStrictEqual(out, { heading: "шапка", text: "тело" });
});

/* applySlotEdit: правка текста прямо на слайде уезжает в поля плана, а не в
   свободный режим — ручная сборка двусторонняя (форма справа + сам слайд). */
const LIST = { kind: "list",
               item_slots: { heading: { kind: "text" }, body: { kind: "text" } } };

test("applySlotEdit: text-слот — правка ложится целиком", () => {
  assert.strictEqual(
    applySlotEdit({ kind: "text" }, "Старый заголовок", "Старый заголовок", "Новый"),
    "Новый");
});

test("applySlotEdit: text-слот с рыбой — правка перекрывает пустое значение", () => {
  assert.strictEqual(
    applySlotEdit({ kind: "text" }, "", "Пример-рыба на слайде", "Свой текст"),
    "Свой текст");
});

test("applySlotEdit: text-слот опустошили — пустая строка (не null)", () => {
  assert.strictEqual(applySlotEdit({ kind: "text" }, "Было", "Было", "  "), "");
});

test("applySlotEdit: list — правится единственный совпавший пункт", () => {
  const items = [{ heading: "Своя", body: "Контроль" },
                 { heading: "Коробка", body: "Запуск" }];
  const out = applySlotEdit(LIST, items, "Запуск", "Запуск за 5 месяцев");
  assert.deepStrictEqual(out[1], { heading: "Коробка", body: "Запуск за 5 месяцев" });
  assert.deepStrictEqual(out[0], items[0]);
  assert.deepStrictEqual(items[1], { heading: "Коробка", body: "Запуск" }); // вход цел
});

test("applySlotEdit: list — два одинаковых текста = неоднозначно, null", () => {
  const items = [{ heading: "Итог" }, { heading: "Итог" }];
  assert.strictEqual(applySlotEdit(LIST, items, "Итог", "Другое"), null);
});

test("applySlotEdit: list — текст не найден (рыба пустого слота) → null", () => {
  assert.strictEqual(applySlotEdit(LIST, [], "Пример-рыба", "Своё"), null);
  assert.strictEqual(applySlotEdit(LIST, undefined, "Пример-рыба", "Своё"), null);
});

test("applySlotEdit: сравнение терпимо к обрамляющим пробелам", () => {
  const out = applySlotEdit(LIST, [{ body: "Текст" }], "  Текст\n", " Новый ");
  assert.deepStrictEqual(out, [{ body: "Новый" }]);
});

test("applySlotEdit: group — правится поле объекта", () => {
  const spec = { kind: "group",
                 item_slots: { name: { kind: "text" }, phone: { kind: "text" } } };
  const out = applySlotEdit(spec, { name: "Иван", phone: "111" }, "111", "222");
  assert.deepStrictEqual(out, { name: "Иван", phone: "222" });
});

test("applySlotEdit: image и неизвестный kind не мапятся", () => {
  assert.strictEqual(applySlotEdit({ kind: "image" }, "data:...", "x", "y"), null);
  assert.strictEqual(applySlotEdit(null, "x", "x", "y"), null);
});
