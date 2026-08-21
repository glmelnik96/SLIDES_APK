// Группировка каталога макетов для пикера конструктора: простыня из 22 плиток
// без групп не сканируется глазами. Отдельный модуль без DOM — чистая функция
// тестируется через `node --test`; в браузере подключается ПЕРЕД editor.js и
// кладёт PickerGroups в window (паттерн errtext.js).
(function (root) {
  // Порядок групп = порядок в пикере. Макеты, не приписанные ни к одной группе
  // (будущие), падают в «Другие макеты» — каталог расширяем без правок здесь.
  var GROUPS = [
    ["Обложки и финал", ["cover", "cover-image", "contacts"]],
    ["Цифры и графики", ["kpi", "stats-row", "bar-chart", "donut-chart",
      "line-chart", "stacked-bar", "kpi-rings"]],
    ["Сравнение и структура", ["before-after", "timeline", "diagram"]],
    ["Текст и карточки", ["statement", "statement-green", "quote",
      "service-table", "two-col-cards", "three-col", "grid-2x2",
      "frames-grid", "blank"]],
  ];

  // catalog: [{id, …}] (уже без hidden) → [{label, items}] без пустых групп.
  function groupTemplates(catalog) {
    var by = {};
    (catalog || []).forEach(function (t) { by[t.id] = t; });
    var used = {};
    var out = [];
    GROUPS.forEach(function (g) {
      var items = g[1].map(function (id) { used[id] = true; return by[id]; })
        .filter(Boolean);
      if (items.length) out.push({ label: g[0], items: items });
    });
    var rest = (catalog || []).filter(function (t) { return !used[t.id]; });
    if (rest.length) out.push({ label: "Другие макеты", items: rest });
    return out;
  }

  root.PickerGroups = { groupTemplates: groupTemplates };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { groupTemplates: groupTemplates };
  }
})(typeof window !== "undefined" ? window : globalThis);
