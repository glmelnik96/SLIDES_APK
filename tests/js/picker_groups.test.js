const test = require("node:test");
const assert = require("node:assert/strict");
const { groupTemplates } = require("../../webapp/static/picker_groups.js");

// Полный видимый каталог (23 макета, порядок как в library.json — см.
// webapp/templates_api.py; hidden cards-6 в пикер не попадает).
const IDS = ["cover", "cover-image", "statement", "statement-green", "contacts",
  "kpi", "stats-row", "bar-chart", "donut-chart", "line-chart", "stacked-bar",
  "kpi-rings", "before-after", "service-table", "quote", "timeline",
  "two-col-cards", "three-col", "grid-2x2", "frames-grid", "section-hatch",
  "blank", "diagram"];

test("23 макета раскладываются в 4 группы без потерь и дублей", () => {
  const groups = groupTemplates(IDS.map((id) => ({ id })));
  assert.deepEqual(groups.map((g) => g.label), ["Титулы, разделы и финал",
    "Цифры и графики", "Сравнение и структура", "Текст и карточки"]);
  const flat = groups.flatMap((g) => g.items.map((t) => t.id));
  assert.equal(flat.length, IDS.length);
  assert.deepEqual([...flat].sort(), [...IDS].sort());
  // Титулы в порядке роли (cover-image основной, statement тезисный, cover
  // запасной), следом разделитель секций и финальный слайд.
  assert.deepEqual(groups[0].items.map((t) => t.id),
    ["cover-image", "statement", "statement-green", "cover", "section-hatch",
     "contacts"]);
});

test("неизвестный макет попадает в «Другие макеты», пустых групп нет", () => {
  const groups = groupTemplates([{ id: "cover" }, { id: "brand-new" }]);
  assert.deepEqual(groups.map((g) => g.label),
    ["Титулы, разделы и финал", "Другие макеты"]);
  assert.deepEqual(groups[1].items.map((t) => t.id), ["brand-new"]);
});

test("пустой/отсутствующий каталог → пусто", () => {
  assert.deepEqual(groupTemplates([]), []);
  assert.deepEqual(groupTemplates(null), []);
});
