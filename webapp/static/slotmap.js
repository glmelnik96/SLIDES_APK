/* Перенос содержимого между макетами при смене макета слайда.

   У макетов разные имена под одно и то же: «body» в карточках, «text» в сетке,
   «caption» в статистике. Одноимённого переноса мало — смена макета молча теряла
   весь текст пунктов, а слайд подставлял вместо него пример-рыбу. Числовые слоты
   держим отдельной ролью, чтобы заголовок карточки не уехал в значение оси. */
(function (root) {
  "use strict";

  var SUB_ROLE = {
    heading: "head", label: "head", title: "head",
    text: "body", body: "body", caption: "body", desc: "body", benefit: "body",
    value: "num", display: "num", num: "num", unit: "num",
    v1: "num", v2: "num", v3: "num", v4: "num",
  };
  function roleOf(name) { return SUB_ROLE[name] || "body"; }

  function filled(item) {
    return !!item && Object.keys(item).some(function (k) {
      return String(item[k] === null || item[k] === undefined ? "" : item[k]).trim();
    });
  }

  // Один пункт в под-слоты нового макета: сначала одноимённый слот, потом любой
  // свободный той же роли, потом любой текстовый (числовые целями не подменяем).
  function remapItem(item, toSlots) {
    var src = {};
    Object.keys(item || {}).forEach(function (k) { src[k] = item[k]; });
    function take(pred) {
      var key = Object.keys(src).find(function (k) {
        var v = src[k];
        return String(v === null || v === undefined ? "" : v).trim() && pred(k);
      });
      if (key === undefined) return null;
      var v = src[key];
      delete src[key];
      return v;
    }
    var out = {};
    Object.keys(toSlots || {}).forEach(function (name) {
      var v = take(function (k) { return k === name; });
      if (v === null) v = take(function (k) { return roleOf(k) === roleOf(name); });
      if (v === null && roleOf(name) !== "num")
        v = take(function (k) { return roleOf(k) !== "num"; });
      out[name] = v === null ? "" : v;
    });
    return out;
  }

  // Пункты старых списков сплошным потоком раскладываем по спискам нового макета:
  // каждый берёт столько, сколько вмещает. Одноимённые слоты не трогаем — они
  // уезжают как есть, и возврат к прежнему макету восстанавливает исходный текст.
  function remapLists(fromTpl, toTpl, content) {
    if (!fromTpl || !toTpl || !content) return content;
    var toSlots = toTpl.slots || {};
    var targets = Object.keys(toSlots).filter(function (n) {
      return toSlots[n].kind === "list";
    });
    if (!targets.length) return content;
    var pool = [];
    var fromSlots = fromTpl.slots || {};
    Object.keys(fromSlots).forEach(function (name) {
      if (fromSlots[name].kind !== "list" || toSlots[name]) return;
      (Array.isArray(content[name]) ? content[name] : []).forEach(function (it) {
        if (filled(it)) pool.push(it);
      });
    });
    if (!pool.length) return content;
    var out = {};
    Object.keys(content).forEach(function (k) { out[k] = content[k]; });
    var at = 0;
    targets.forEach(function (name) {
      var has = Array.isArray(out[name]) && out[name].some(filled);
      if (has || at >= pool.length) return;
      var chunk = pool.slice(at, at + (toSlots[name].max_items || pool.length));
      at += chunk.length;
      out[name] = chunk.map(function (it) {
        return remapItem(it, toSlots[name].item_slots || {});
      });
    });
    return out;
  }

  root.remapLists = remapLists;
  root.remapItem = remapItem;
  if (typeof module !== "undefined" && module.exports)
    module.exports = { remapLists: remapLists, remapItem: remapItem, roleOf: roleOf };
})(typeof window !== "undefined" ? window : globalThis);
