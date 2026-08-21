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

  // Правка текста прямо на слайде → патч значения слота (ручной режим должен
  // принимать правки с обеих сторон: и в форме, и на слайде, без ухода слайда
  // в свободный режим). Возвращает новое значение слота или null, если правку
  // нельзя однозначно положить в поля:
  //   text  — всегда мапится (весь слот = один текст);
  //   list  — ищем ЕДИНСТВЕННЫЙ пункт-подполе с исходным текстом; 0 или 2+
  //           совпадений → неоднозначно (например, два одинаковых заголовка);
  //   group — то же по полям одного объекта;
  //   image и прочее — не текст, не мапится.
  function applySlotEdit(spec, value, origText, newText) {
    if (!spec) return null;
    var was = String(origText === null || origText === undefined ? "" : origText).trim();
    var now = String(newText === null || newText === undefined ? "" : newText).trim();
    if (spec.kind === "text") return now;
    if (spec.kind !== "list" && spec.kind !== "group") return null;
    var items = spec.kind === "group"
      ? [value] : Array.isArray(value) ? value : [];
    var hitItem = -1, hitSub = null, hits = 0;
    items.forEach(function (item, i) {
      if (!item || typeof item !== "object") return;
      Object.keys(item).forEach(function (sub) {
        var v = item[sub];
        if (String(v === null || v === undefined ? "" : v).trim() === was && was !== "") {
          hits += 1; hitItem = i; hitSub = sub;
        }
      });
    });
    if (hits !== 1) return null;
    var patched = items.map(function (item, i) {
      if (i !== hitItem) return item;
      var copy = {};
      Object.keys(item).forEach(function (k) { copy[k] = item[k]; });
      copy[hitSub] = now;
      return copy;
    });
    return spec.kind === "group" ? patched[0] : patched;
  }

  root.remapLists = remapLists;
  root.remapItem = remapItem;
  root.applySlotEdit = applySlotEdit;
  if (typeof module !== "undefined" && module.exports)
    module.exports = { remapLists: remapLists, remapItem: remapItem, roleOf: roleOf,
                       applySlotEdit: applySlotEdit };
})(typeof window !== "undefined" ? window : globalThis);
