/* diagram.js — детерминированный SVG-рендер диаграмм по DiagramSpec.
 *
 * Контракт: слайд-шаблон diagram.html кладёт JSON спека в data-diagram у
 * .diagram-host; движок на DOMContentLoaded раскладывает узлы (авто-layout по
 * kind), применяет ручные offsets (гибридный режим) и рисует SVG на токенах
 * темы (--bg-card, --accent, --fg-…, --chart-…). Никаких внешних библиотек.
 *
 * Идемпотентность ОБЯЗАТЕЛЬНА: сейв редактора запекает сгенерированный SVG в
 * deck.html, при следующей загрузке render() стирает host и рисует заново —
 * иначе дубли. Запечённый SVG заодно служит фолбэком, если JS не исполнился.
 *
 * Layout-функции ЧИСТЫЕ (spec → позиции) и экспортируются в node --test
 * (tests/js/diagram.test.js) — как errtext.js.
 *
 * Виртуальный холст 1800×720 = рабочая зона .diagram-host (deck.css);
 * числа синхронизированы с htmlslides/diagrams/schema.py (CANVAS_W/H).
 */
(function (global) {
  "use strict";

  var W = 1800, H = 720;

  /* Безопасно достать число из строки величины ("12 000", "45%", "1,5"). */
  function num(v) {
    var m = String(v == null ? "" : v).match(/-?\d[\d\s\u00a0\u202f.,]*/);
    if (!m) return 0;
    var s = m[0].replace(/[\s\u00a0\u202f]/g, "");
    var hadComma = s.indexOf(",") !== -1;
    s = s.replace(/,/g, ".");
    var parts = s.split(".");
    if (parts.length > 2) {
      /* несколько точек = тысячные (1.234.567); при запятой-десятичной
       * последняя точка остаётся десятичной — зеркалит assembler._num */
      s = hadComma
        ? parts.slice(0, -1).join("") + "." + parts[parts.length - 1]
        : parts.join("");
    }
    var f = parseFloat(s);
    return isNaN(f) ? 0 : f;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ---------------- layouts (чистые функции) ----------------
   * Каждая возвращает {nodes: {id: {x, y, w, h}}, links: [{from, to, points,
   * label, style}]}; x/y — ЦЕНТР узла. links.points — ломаная для <path>. */

  /* Ранги BFS от истоков (узлы без входящих). Обратные рёбра циклов не
   * повышают ранг (посещённые не перекладываем) — раскладка устойчива к
   * циклам «на доработку». Возвращает {id: rank}. */
  function flowRanks(nodes, edges) {
    var incoming = {}, adj = {};
    nodes.forEach(function (n) { incoming[n.id] = 0; adj[n.id] = []; });
    edges.forEach(function (e) {
      if (adj[e.from]) adj[e.from].push(e.to);
      if (e.to in incoming) incoming[e.to] += 1;
    });
    var rank = {}, queue = [];
    nodes.forEach(function (n) {
      if (!incoming[n.id]) { rank[n.id] = 0; queue.push(n.id); }
    });
    if (!queue.length && nodes.length) {          // чистый цикл — стартуем с первого
      rank[nodes[0].id] = 0; queue.push(nodes[0].id);
    }
    while (queue.length) {
      var id = queue.shift();
      adj[id].forEach(function (to) {
        if (!(to in rank)) { rank[to] = rank[id] + 1; queue.push(to); }
      });
      if (!queue.length) {                        // изолированные/недостижимые
        for (var i = 0; i < nodes.length; i++) {
          if (!(nodes[i].id in rank)) { rank[nodes[i].id] = 0; queue.push(nodes[i].id); break; }
        }
      }
    }
    return rank;
  }

  function layoutFlowchart(spec) {
    var nodes = spec.nodes, edges = spec.edges || [];
    var down = spec.direction === "down";
    var rank = flowRanks(nodes, edges);
    var maxRank = 0;
    nodes.forEach(function (n) { maxRank = Math.max(maxRank, rank[n.id]); });

    var byRank = [];
    nodes.forEach(function (n, i) {
      var r = rank[n.id];
      (byRank[r] = byRank[r] || []).push({ id: n.id, idx: i });
    });

    /* Ось рангов и поперечная ось зависят от direction. */
    var mainLen = down ? H : W, crossLen = down ? W : H;
    var nodeMain = Math.min(down ? 110 : 300, (mainLen - 60 * maxRank) / (maxRank + 1));
    var pos = {};
    byRank.forEach(function (bucket, r) {
      /* Порядок в ранге — барицентр предшественников (стабильно по idx). */
      bucket.forEach(function (b) {
        var preds = edges.filter(function (e) { return e.to === b.id && (e.from in pos); });
        b.bary = preds.length
          ? preds.reduce(function (s, e) { return s + (down ? pos[e.from].x : pos[e.from].y); }, 0) / preds.length
          : b.idx * 1000;
      });
      bucket.sort(function (a, b2) { return a.bary - b2.bary || a.idx - b2.idx; });
      var pitch = crossLen / bucket.length;
      var main = maxRank ? r * (mainLen - nodeMain) / maxRank + nodeMain / 2 : mainLen / 2;
      bucket.forEach(function (b, i) {
        var cross = i * pitch + pitch / 2;
        var w = down ? Math.min(300, pitch - 24) : nodeMain;
        var h = down ? nodeMain : Math.min(110, pitch - 24);
        pos[b.id] = { x: down ? cross : main, y: down ? main : cross, w: w, h: h };
      });
    });

    var links = edges.map(function (e) {
      var s = pos[e.from], t = pos[e.to];
      var forward = rank[e.to] > rank[e.from];
      var same = rank[e.to] === rank[e.from];
      var points;
      if (down) {
        points = forward
          ? [[s.x, s.y + s.h / 2], [s.x, (s.y + t.y) / 2], [t.x, (s.y + t.y) / 2], [t.x, t.y - t.h / 2]]
          : same ? sameRankRoute(s, t, true) : backRoute(s, t, true);
      } else {
        points = forward
          ? [[s.x + s.w / 2, s.y], [(s.x + t.x) / 2, s.y], [(s.x + t.x) / 2, t.y], [t.x - t.w / 2, t.y]]
          : same ? sameRankRoute(s, t, false) : backRoute(s, t, false);
      }
      return { from: e.from, to: e.to, points: points, label: e.label || "", style: e.style || "solid" };
    });
    return { nodes: pos, links: links };
  }

  /* Соседи одного ранга (две ветки развилки, сходящиеся друг в друга) стоят в
     одной колонке, поэтому это НЕ обратное ребро: обход низом прокладывал линию
     сквозь целевую плашку и оставлял торчащий хвост под ней. Соединяем встречные
     грани напрямую. */
  function sameRankRoute(s, t, down) {
    if (down) {
      var right = t.x >= s.x;
      return [[s.x + (right ? s.w / 2 : -s.w / 2), s.y],
              [t.x + (right ? -t.w / 2 : t.w / 2), t.y]];
    }
    var below = t.y >= s.y;
    return [[s.x, s.y + (below ? s.h / 2 : -s.h / 2)],
            [t.x, t.y + (below ? -t.h / 2 : t.h / 2)]];
  }

  /* Обратное ребро (цикл «на доработку»): обводим низом/краем рабочей зоны. */
  function backRoute(s, t, down) {
    if (down) {
      var lane = Math.min(W - 20, Math.max(s.x + s.w / 2, t.x + t.w / 2) + 60);
      return [[s.x + s.w / 2, s.y], [lane, s.y], [lane, t.y], [t.x + t.w / 2, t.y]];
    }
    var laneY = Math.min(H - 16, Math.max(s.y + s.h / 2, t.y + t.h / 2) + 56);
    return [[s.x, s.y + s.h / 2], [s.x, laneY], [t.x, laneY], [t.x, t.y + t.h / 2]];
  }

  function layoutProcess(spec) {
    var nodes = spec.nodes;
    var rows = nodes.length > 6 ? 2 : 1;
    var perRow = Math.ceil(nodes.length / rows);
    var gap = 72;
    var w = Math.min(320, (W - gap * (perRow - 1)) / perRow);
    var h = Math.min(150, rows === 1 ? 170 : H / 2 - 90);
    var pos = {}, links = [];
    nodes.forEach(function (n, i) {
      var row = Math.floor(i / perRow), col = i % perRow;
      if (row === 1) col = perRow - 1 - col;       // серпантин: вторая строка справа налево
      var rowW = (row === rows - 1 && nodes.length % perRow)
        ? nodes.length - perRow * row : perRow;
      var x0 = (W - (w * perRow + gap * (perRow - 1))) / 2;
      pos[n.id] = {
        x: x0 + col * (w + gap) + w / 2,
        y: rows === 1 ? H / 2 : (row === 0 ? H * 0.3 : H * 0.72),
        w: w, h: h,
      };
      void rowW;
    });
    for (var i = 1; i < nodes.length; i++) {
      var s = pos[nodes[i - 1].id], t = pos[nodes[i].id];
      var points = (Math.abs(s.y - t.y) < 1)
        ? [[s.x + (s.x < t.x ? s.w / 2 : -s.w / 2), s.y], [t.x + (s.x < t.x ? -t.w / 2 : t.w / 2), t.y]]
        : [[s.x, s.y + s.h / 2], [s.x, (s.y + t.y) / 2], [t.x, (s.y + t.y) / 2], [t.x, t.y - t.h / 2]];
      links.push({ from: nodes[i - 1].id, to: nodes[i].id, points: points, label: "", style: "solid" });
    }
    return { nodes: pos, links: links };
  }

  function layoutCycle(spec) {
    var nodes = spec.nodes, n = nodes.length;
    var cx = W / 2, cy = H / 2;
    /* Эллипс, а не окружность: холст 1800×720, и круг r=265 оставлял по 600px
       пустоты слева и справа, зажимая плашки в узкий сектор — от шести этапов
       на подписи оставалось по 200px, а стрелки между ними сходили на нет. */
    var ry = Math.min(H / 2 - 70, 265), rx = Math.min(W / 2 - 320, 640);
    var perim = Math.PI * (3 * (rx + ry) -
      Math.sqrt((3 * rx + ry) * (rx + 3 * ry)));   // приближение Рамануджана
    var pos = {}, links = [];
    function at(a) { return [cx + rx * Math.cos(a), cy + ry * Math.sin(a)]; }
    nodes.forEach(function (node, i) {
      var a = -Math.PI / 2 + (2 * Math.PI * i) / n;   // старт сверху, по часовой
      var p = at(a);
      pos[node.id] = {
        // От девяти этапов плашки жмутся на боках эллипса — иначе стрелка между
        // ними снова сжимается в царапину.
        x: p[0], y: p[1], w: Math.min(300, perim / n - 40),
        h: n > 8 ? 74 : 96, angle: a,
      };
    });
    /* Где дуга выходит из плашки — считаем по самой плашке, а не фиксированным
       0.42·π/n: широкий узел наверху занимает больший угол, чем такой же сбоку,
       и дуга упиралась ВНУТРЬ плашки. Наконечник рисуется до узлов и закрывался
       ими — у стрелки «Формирование гипотезы → Пилот» просто не было головы.
       Шаг перебора мелкий и фиксированный: раскладка обязана быть повторяемой. */
    function edgeAngle(p, sign) {
      var lim = (Math.PI / n) * 0.92, step = Math.PI / 720;
      for (var d = step; d < lim; d += step) {
        var q = at(p.angle + sign * d);
        if (Math.abs(q[0] - p.x) > p.w / 2 + 16 ||
            Math.abs(q[1] - p.y) > p.h / 2 + 16) return p.angle + sign * d;
      }
      return p.angle + sign * lim;
    }
    for (var i = 0; i < n; i++) {
      var from = nodes[i].id, to = nodes[(i + 1) % n].id;
      var a1 = edgeAngle(pos[from], 1);
      var a2 = edgeAngle(pos[to], -1);
      links.push({
        from: from, to: to, label: "", style: "solid",
        arc: { cx: cx, cy: cy, rx: rx, ry: ry, a1: a1, a2: a2 },
        points: [at(a1), at(a2)],
      });
    }
    return { nodes: pos, links: links };
  }

  function layoutFunnel(spec) {
    var nodes = spec.nodes, n = nodes.length;
    var gap = 10;
    var layerH = Math.min(150, (H - gap * (n - 1)) / n);
    var total = layerH * n + gap * (n - 1);
    var top = (H - total) / 2;
    var wMax = 1150, wMin = 340;
    var values = nodes.map(function (nd) { return num(nd.value); });
    var vMax = Math.max.apply(null, values.concat([0]));
    var widths = nodes.map(function (nd, i) {
      if (vMax > 0 && values[i] > 0) return wMin + (values[i] / vMax) * (wMax - wMin);
      return wMax - (i * (wMax - wMin)) / Math.max(1, n - 1);   // линейное сужение
    });
    var pos = {};
    nodes.forEach(function (nd, i) {
      pos[nd.id] = {
        x: W / 2, y: top + i * (layerH + gap) + layerH / 2,
        w: widths[i], h: layerH,
        wBottom: i + 1 < n ? widths[i + 1] : widths[i] * 0.72,
      };
    });
    return { nodes: pos, links: [] };
  }

  function layoutHierarchy(spec) {
    var nodes = spec.nodes, edges = spec.edges || [];
    var children = {}, hasParent = {};
    nodes.forEach(function (n) { children[n.id] = []; });
    edges.forEach(function (e) {
      if (children[e.from]) children[e.from].push(e.to);
      hasParent[e.to] = true;
    });
    var roots = nodes.filter(function (n) { return !hasParent[n.id]; }).map(function (n) { return n.id; });
    if (!roots.length && nodes.length) roots = [nodes[0].id];

    var depth = {}, maxDepth = 0, leaves = {};
    (function walk(ids, d) {
      ids.forEach(function (id) {
        if (id in depth) return;                   // защита от мусорных данных
        depth[id] = d; maxDepth = Math.max(maxDepth, d);
        walk(children[id], d + 1);
      });
    })(roots, 0);
    (function countLeaves(ids) {
      ids.forEach(function (id) {
        var kids = children[id].filter(function (k) { return depth[k] === depth[id] + 1; });
        countLeaves(kids);
        leaves[id] = kids.length
          ? kids.reduce(function (s, k) { return s + leaves[k]; }, 0) : 1;
      });
    })(roots);

    var totalLeaves = roots.reduce(function (s, r) { return s + leaves[r]; }, 0) || 1;
    var slotW = W / totalLeaves;
    var levelH = H / (maxDepth + 1);
    var w = Math.min(280, slotW - 20), h = Math.min(100, levelH - 40);
    var pos = {}, cursor = { x: 0 };
    (function place(ids) {
      ids.forEach(function (id) {
        var kids = children[id].filter(function (k) { return depth[k] === depth[id] + 1; });
        var x;
        if (kids.length) {
          place(kids);
          x = kids.reduce(function (s, k) { return s + pos[k].x; }, 0) / kids.length;
        } else {
          x = cursor.x + (leaves[id] * slotW) / 2;
          cursor.x += leaves[id] * slotW;
        }
        pos[id] = { x: x, y: depth[id] * levelH + levelH / 2, w: w, h: h };
      });
    })(roots);
    nodes.forEach(function (n) {                    // недостижимые — вниз в ряд
      if (!(n.id in pos)) {
        pos[n.id] = { x: cursor.x + slotW / 2, y: maxDepth * levelH + levelH / 2, w: w, h: h };
        cursor.x += slotW;
      }
    });

    var links = edges.map(function (e) {
      var s = pos[e.from], t = pos[e.to];
      var midY = (s.y + s.h / 2 + t.y - t.h / 2) / 2;
      return {
        from: e.from, to: e.to, label: e.label || "", style: e.style || "solid",
        points: [[s.x, s.y + s.h / 2], [s.x, midY], [t.x, midY], [t.x, t.y - t.h / 2]],
      };
    });
    return { nodes: pos, links: links };
  }

  /* ---------------- волна 2 ---------------- */

  /* Дорожки в порядке первого появления — зеркалит schema._lanes. */
  function laneList(nodes) {
    var lanes = [];
    nodes.forEach(function (n) {
      var l = n.lane || "";
      if (lanes.indexOf(l) === -1) lanes.push(l);
    });
    return lanes;
  }

  /* Отрезать сегмент центр→центр по границам карточек узлов (+8px зазор),
   * чтобы стрелка была видна, а не пряталась под карточкой. */
  function trimSegment(s, t) {
    var dx = t.x - s.x, dy = t.y - s.y;
    function cut(box) {
      var tx = dx ? (box.w / 2 + 8) / Math.abs(dx) : Infinity;
      var ty = dy ? (box.h / 2 + 8) / Math.abs(dy) : Infinity;
      return Math.min(tx, ty, 0.49);
    }
    var t0 = cut(s), t1 = 1 - cut(t);
    return [[s.x + dx * t0, s.y + dy * t0], [s.x + dx * t1, s.y + dy * t1]];
  }

  /* Матрица 2×2: ровно 4 карточки по квадрантам (порядок: верх-лево,
   * верх-право, низ-лево, низ-право); оси рисует decorMatrix. */
  function layoutMatrix(spec) {
    var pos = {};
    var cxs = [W * 0.26, W * 0.74], cys = [H * 0.26, H * 0.74];
    spec.nodes.forEach(function (n, i) {
      pos[n.id] = { x: cxs[i % 2], y: cys[i < 2 ? 0 : 1], w: 560, h: 220 };
    });
    return { nodes: pos, links: [] };
  }

  /* Пирамида: слои сверху вниз, вершина первой; ширины растут линейно.
   * Структура pos совместима с renderFunnel (w = верх, wBottom = низ). */
  function layoutPyramid(spec) {
    var nodes = spec.nodes, n = nodes.length;
    var gap = 8;
    var layerH = Math.min(150, (H - gap * (n - 1)) / n);
    var total = layerH * n + gap * (n - 1);
    var top = (H - total) / 2;
    var wTop = 240, wBase = 1150;
    function wAt(t) { return wTop + (wBase - wTop) * t; }
    var pos = {};
    nodes.forEach(function (nd, i) {
      pos[nd.id] = {
        x: W / 2, y: top + i * (layerH + gap) + layerH / 2,
        w: wAt(i / n), h: layerH, wBottom: wAt((i + 1) / n),
      };
    });
    return { nodes: pos, links: [] };
  }

  /* Хаб и лучи: первый узел в центре, остальные по эллипсу вокруг; рёбра
   * опциональны (без них — центр соединяется с каждым лучом). */
  function layoutHubSpoke(spec) {
    var nodes = spec.nodes;
    var hub = nodes[0], spokes = nodes.slice(1), m = spokes.length || 1;
    var pos = {};
    pos[hub.id] = { x: W / 2, y: H / 2, w: 400, h: 150 };
    /* От одиннадцати лучей шаг по эллипсу становится меньше карточки, и соседи
       внизу налезали друг на друга: раздвигаем эллипс и ужимаем плашки. */
    var wide = m > 10;
    var rx = wide ? 660 : 630, ry = wide ? 272 : 255;
    var sw = wide ? 250 : 310, sh = wide ? 86 : 104;
    spokes.forEach(function (n, i) {
      var a = -Math.PI / 2 + (2 * Math.PI * i) / m;
      pos[n.id] = { x: W / 2 + rx * Math.cos(a), y: H / 2 + ry * Math.sin(a), w: sw, h: sh };
    });
    var edges = (spec.edges && spec.edges.length) ? spec.edges
      : spokes.map(function (n) { return { from: hub.id, to: n.id }; });
    var links = edges.map(function (e) {
      return { from: e.from, to: e.to, label: e.label || "", style: e.style || "solid",
               points: trimSegment(pos[e.from], pos[e.to]) };
    });
    return { nodes: pos, links: links };
  }

  /* Сравнение сторон: две колонки карточек, заголовки колонок — decorComparison. */
  function layoutComparison(spec) {
    var lanes = laneList(spec.nodes);
    var cols = [W * 0.27, W * 0.73];
    var gap = 18, topY = 120;
    var pos = {};
    lanes.forEach(function (lane, li) {
      var items = spec.nodes.filter(function (n) { return (n.lane || "") === lane; });
      var itemH = Math.min(130,
        (H - topY - 20 - gap * (items.length - 1)) / Math.max(1, items.length));
      items.forEach(function (n, i) {
        pos[n.id] = { x: cols[Math.min(li, 1)], y: topY + itemH / 2 + i * (itemH + gap),
                      w: 660, h: itemH };
      });
    });
    return { nodes: pos, links: [], lanes: lanes };
  }

  /* Венн: 2–3 полупрозрачных круга; метка оттянута от общего центра наружу
   * (labelDx/labelDy — ОТНОСИТЕЛЬНЫЕ, переживают drag-offsets). */
  function layoutVenn(spec) {
    var nodes = spec.nodes, n = nodes.length;
    var r = n === 3 ? 235 : 255;
    var centers = n === 3
      ? [[W / 2, H / 2 - 115], [W / 2 - 195, H / 2 + 115], [W / 2 + 195, H / 2 + 115]]
      : [[W / 2 - 165, H / 2], [W / 2 + 165, H / 2]];
    var cx0 = 0, cy0 = 0, used = Math.min(n, centers.length);
    centers.slice(0, used).forEach(function (c) { cx0 += c[0] / used; cy0 += c[1] / used; });
    var pos = {};
    nodes.forEach(function (nd, i) {
      var c = centers[i] || centers[centers.length - 1];
      var dx = c[0] - cx0, dy = c[1] - cy0;
      var len = Math.sqrt(dx * dx + dy * dy) || 1;
      pos[nd.id] = {
        x: c[0], y: c[1], w: 2 * r, h: 2 * r, r: r,
        labelDx: (dx / len) * r * 0.45, labelDy: (dy / len) * r * 0.45,
      };
    });
    return { nodes: pos, links: [] };
  }

  /* Дорожки процесса: ряды по исполнителям (lane), колонки по рангам шагов.
   * Коллизии (ранг, дорожка) разводим вправо, чтобы шаги не слипались. */
  function layoutSwimlanes(spec) {
    var nodes = spec.nodes, edges = spec.edges || [];
    var lanes = laneList(nodes);
    var laneH = H / lanes.length;
    var labelW = 230;
    var rank = {};
    if (edges.length) {
      rank = flowRanks(nodes, edges);
    } else {
      nodes.forEach(function (n, i) { rank[n.id] = i; });
    }
    var used = {};
    nodes.forEach(function (n) {
      var li = Math.max(0, lanes.indexOf(n.lane || ""));
      var r = rank[n.id];
      while (used[r + ":" + li]) r += 1;
      used[r + ":" + li] = true;
      rank[n.id] = r;
    });
    var maxRank = 0;
    nodes.forEach(function (n) { maxRank = Math.max(maxRank, rank[n.id]); });
    var colW = (W - labelW - 20) / (maxRank + 1);
    var w = Math.min(290, colW - 28), h = Math.min(108, laneH - 32);
    var pos = {};
    nodes.forEach(function (n) {
      var li = Math.max(0, lanes.indexOf(n.lane || ""));
      pos[n.id] = { x: labelW + rank[n.id] * colW + colW / 2,
                    y: li * laneH + laneH / 2, w: w, h: h };
    });
    var linkDefs = edges.length ? edges
      : nodes.slice(1).map(function (n, i) { return { from: nodes[i].id, to: n.id }; });
    var links = linkDefs.map(function (e) {
      var s = pos[e.from], t = pos[e.to];
      var points;
      if (Math.abs(s.y - t.y) < 1) {
        points = s.x < t.x
          ? [[s.x + s.w / 2, s.y], [t.x - t.w / 2, t.y]]
          : backRoute(s, t, false);
      } else if (t.x > s.x) {
        var midX = (s.x + s.w / 2 + t.x - t.w / 2) / 2;
        points = [[s.x + s.w / 2, s.y], [midX, s.y], [midX, t.y], [t.x - t.w / 2, t.y]];
      } else {
        points = backRoute(s, t, false);
      }
      return { from: e.from, to: e.to, points: points,
               label: e.label || "", style: e.style || "solid" };
    });
    return { nodes: pos, links: links, lanes: lanes, laneH: laneH, labelW: labelW };
  }

  /* ---------------- волна 3 ---------------- */

  /* План-график: строка = работа, полоса по шкале периодов. value —
     длительность, level — стартовый период; без level работа встаёт сразу за
     предыдущей (каскад). Линейку и сетку рисует decorGantt. */
  var GANTT_TOP = 86;                       // полоса линейки периодов сверху

  function layoutGantt(spec) {
    var nodes = spec.nodes, n = nodes.length;
    var rowH = (H - GANTT_TOP) / n;
    var barH = Math.min(84, rowH - 16);
    var starts = [], durs = [], cursor = 0, total = 0;
    nodes.forEach(function (nd, i) {
      var d = Math.max(1, num(nd.value) || 1);
      var s = (nd.level == null) ? cursor : Math.max(0, +nd.level || 0);
      starts[i] = s; durs[i] = d; cursor = s + d;
      total = Math.max(total, s + d);
    });
    var unit = W / Math.max(1, total);
    var pos = {};
    nodes.forEach(function (nd, i) {
      pos[nd.id] = {
        x: (starts[i] + durs[i] / 2) * unit,
        y: GANTT_TOP + i * rowH + rowH / 2,
        w: Math.max(56, durs[i] * unit - 14), h: barH,
      };
    });
    return { nodes: pos, links: [], unit: unit, total: total };
  }

  /* Лестница: ступени слева направо и снизу вверх. */
  function layoutSteps(spec) {
    var nodes = spec.nodes, n = nodes.length;
    var gap = 26;
    var w = Math.min(360, (W - gap * (n - 1)) / n);
    var h = Math.min(150, (H - 60) / Math.max(2, n));
    var rise = n > 1 ? (H - h - 46) / (n - 1) : 0;
    var x0 = (W - (w * n + gap * (n - 1))) / 2;
    var pos = {};
    nodes.forEach(function (nd, i) {
      pos[nd.id] = { x: x0 + i * (w + gap) + w / 2,
                     y: H - h / 2 - i * rise, w: w, h: h };
    });
    return { nodes: pos, links: [] };
  }

  /* Ментальная карта: первый узел — центр, ветви расходятся вправо и влево
     через одну. Дерево строим обходом в ширину от центра: второй родитель и
     кольцо («b→a» при «a→b») в дерево просто не попадают — раскладка обязана
     пережить данные, которые схема бы завернула (в собранной деке истина —
     HTML). Узел, до которого обход не дошёл, становится ветвью центра. */
  function layoutMindmap(spec) {
    var nodes = spec.nodes, root = nodes[0];
    var edges = (spec.edges && spec.edges.length) ? spec.edges
      : nodes.slice(1).map(function (n) { return { from: root.id, to: n.id }; });
    var adj = {}, children = {};
    nodes.forEach(function (n) { adj[n.id] = []; children[n.id] = []; });
    edges.forEach(function (e) { if (adj[e.from]) adj[e.from].push(e.to); });

    var depth = {}, queue = [root.id];
    depth[root.id] = 0;
    while (queue.length) {
      var id = queue.shift();
      adj[id].forEach(function (to) {
        if (to in depth) return;
        depth[to] = depth[id] + 1;
        children[id].push(to);
        queue.push(to);
      });
    }
    var maxDepth = 0;
    nodes.forEach(function (n) {
      if (!(n.id in depth)) { depth[n.id] = 1; children[root.id].push(n.id); }
      maxDepth = Math.max(maxDepth, depth[n.id]);
    });

    function leaves(id) {
      var kids = children[id];
      if (!kids.length) return 1;
      return kids.reduce(function (s, k) { return s + leaves(k); }, 0);
    }
    /* 200 — запас под половину самой широкой плашки: считать колонку по чистому
       расстоянию до края значило бы вывесить крайний уровень за холст. */
    var colW = Math.min(420, (W / 2 - 200) / Math.max(1, maxDepth));
    var w = Math.min(320, colW - 40), h = 86;
    var pos = {};
    /* Ширина корня — по той же мерке, что у ветвей: прежние colW+60 при глубине
       от трёх уровней (colW < 320) залезали на первую ветвь на 10px, и стрелка
       от корня уходила назад, рисуясь задом наперёд. Корень выделен высотой. */
    pos[root.id] = { x: W / 2, y: H / 2, w: Math.min(380, colW - 40), h: 130 };
    [1, -1].forEach(function (dir) {
      /* Ветви центра расходятся через одну: чётные — вправо, нечётные — влево. */
      var branches = children[root.id].filter(function (id, i) {
        return (i % 2 ? -1 : 1) === dir;
      });
      var total = branches.reduce(function (s, id) { return s + leaves(id); }, 0) || 1;
      var slot = H / total, cursor = { y: 0 };
      (function place(ids) {
        ids.forEach(function (id) {
          var kids = children[id], y;
          if (kids.length) {
            place(kids);
            y = kids.reduce(function (s, k) { return s + pos[k].y; }, 0) / kids.length;
          } else {
            y = cursor.y + slot / 2; cursor.y += slot;
          }
          pos[id] = { x: W / 2 + dir * depth[id] * colW, y: y, w: w, h: h };
        });
      })(branches);
    });

    var byPair = {};
    (spec.edges || []).forEach(function (e) { byPair[e.from + ">" + e.to] = e; });
    var links = [];
    Object.keys(children).forEach(function (pid) {
      children[pid].forEach(function (cid) {
        var s = pos[pid], t = pos[cid], e = byPair[pid + ">" + cid] || {};
        var dir = t.x >= s.x ? 1 : -1;
        links.push({ from: pid, to: cid, label: e.label || "",
                     style: e.style || "solid", curve: true,
                     points: [[s.x + dir * s.w / 2, s.y],
                              [t.x - dir * t.w / 2, t.y]] });
      });
    });
    return { nodes: pos, links: links };
  }

  /* Развести перекрывшиеся плашки по оси наименьшего проникновения и вернуть их
     в холст. Порядок обхода фиксирован (ключи в порядке вставки), случайности
     нет — повторный рендер обязан дать ТЕ ЖЕ координаты. */
  function separateBoxes(pos, gap) {
    var ids = Object.keys(pos), i, j;
    for (var pass = 0; pass < 80; pass++) {
      var moved = false;
      for (i = 0; i < ids.length; i++) {
        for (j = i + 1; j < ids.length; j++) {
          var a = pos[ids[i]], b = pos[ids[j]];
          var ox = (a.w + b.w) / 2 + gap - Math.abs(a.x - b.x);
          var oy = (a.h + b.h) / 2 + gap - Math.abs(a.y - b.y);
          if (ox <= 0 || oy <= 0) continue;
          moved = true;
          /* Проникновение сравниваем в долях размера: холст широкий, и «кто
             меньше в пикселях» всегда толкал бы по вертикали. */
          if (ox / (a.w + b.w) < oy / (a.h + b.h)) {
            var sx = ((a.x <= b.x ? -1 : 1) * ox) / 2;
            a.x += sx; b.x -= sx;
          } else {
            var sy = ((a.y <= b.y ? -1 : 1) * oy) / 2;
            a.y += sy; b.y -= sy;
          }
        }
      }
      for (i = 0; i < ids.length; i++) {
        var p = pos[ids[i]];
        p.x = Math.max(p.w / 2 + 4, Math.min(W - p.w / 2 - 4, p.x));
        p.y = Math.max(p.h / 2 + 4, Math.min(H - p.h / 2 - 4, p.y));
      }
      if (!moved) break;
    }
    /* Разведение толкает облако к краям, а клампы прибивают его к низу: граф
       съезжал в нижнюю половину, оставляя пустую верхнюю. Возвращаем композицию
       в центр по фактическим габаритам плашек. */
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    ids.forEach(function (id) {
      var p = pos[id];
      minX = Math.min(minX, p.x - p.w / 2); maxX = Math.max(maxX, p.x + p.w / 2);
      minY = Math.min(minY, p.y - p.h / 2); maxY = Math.max(maxY, p.y + p.h / 2);
    });
    var shiftX = (W - (minX + maxX)) / 2, shiftY = (H - (minY + maxY)) / 2;
    ids.forEach(function (id) { pos[id].x += shiftX; pos[id].y += shiftY; });
  }

  /* Граф связей: старт по эллипсу + фиксированное число итераций
     Фрухтермана—Рейнгольда. Ни случайности, ни времени: сейв редактора запекает
     SVG в деку, и повторный рендер обязан дать ТЕ ЖЕ координаты. */
  function layoutNetwork(spec) {
    var nodes = spec.nodes, edges = spec.edges || [], n = nodes.length;
    var w = Math.min(280, Math.max(160, W / (n + 1))), h = 92;
    var idx = {}, px = [], py = [];
    nodes.forEach(function (nd, i) {
      idx[nd.id] = i;
      var a = -Math.PI / 2 + (2 * Math.PI * i) / n;
      px[i] = W / 2 + W * 0.33 * Math.cos(a);
      py[i] = H / 2 + H * 0.34 * Math.sin(a);
    });
    var k = Math.sqrt((W * H) / n) * 0.5, iters = 240;
    var dx = [], dy = [], i, j;
    for (var it = 0; it < iters; it++) {
      for (i = 0; i < n; i++) { dx[i] = 0; dy[i] = 0; }
      for (i = 0; i < n; i++) {
        for (j = i + 1; j < n; j++) {
          /* Вертикаль весит больше: холст 1800×720, и «круглое» отталкивание
             сбивало узлы в столбики по краям вместо ленты. */
          var ax = px[i] - px[j], ay = (py[i] - py[j]) * 1.6;
          var d = Math.max(1, Math.sqrt(ax * ax + ay * ay));
          var f = (k * k) / (d * d);
          dx[i] += ax * f; dy[i] += ay * f;
          dx[j] -= ax * f; dy[j] -= ay * f;
        }
      }
      edges.forEach(function (e) {
        var a = idx[e.from], b = idx[e.to];
        if (a === undefined || b === undefined) return;
        var ex = px[a] - px[b], ey = py[a] - py[b];
        var d2 = Math.max(1, Math.sqrt(ex * ex + ey * ey));
        var pull = (d2 * d2) / k;
        dx[a] -= (ex / d2) * pull; dy[a] -= (ey / d2) * pull;
        dx[b] += (ex / d2) * pull; dy[b] += (ey / d2) * pull;
      });
      var temp = 26 * (1 - it / iters) + 1;
      for (i = 0; i < n; i++) {
        var len = Math.max(0.01, Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]));
        var step = Math.min(len, temp);
        px[i] = Math.max(w / 2 + 4, Math.min(W - w / 2 - 4, px[i] + (dx[i] / len) * step));
        py[i] = Math.max(h / 2 + 4, Math.min(H - h / 2 - 4, py[i] + (dy[i] / len) * step));
      }
    }
    /* Итерации оставляют облако где придётся: узлы липнут к рамке клампа, а
       полканваса пустует. Вписываем облако в холст ОДНИМ масштабом по обеим
       осям — разные растянули бы граф и соврали про расстояния. */
    var minX = Math.min.apply(null, px), maxX = Math.max.apply(null, px);
    var minY = Math.min.apply(null, py), maxY = Math.max.apply(null, py);
    var pad = 24;
    var s = Math.min(2, (W - w - 2 * pad) / Math.max(1, maxX - minX),
                     (H - h - 2 * pad) / Math.max(1, maxY - minY));
    var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    var pos = {};
    nodes.forEach(function (nd, i2) {
      pos[nd.id] = { x: W / 2 + (px[i2] - cx) * s, y: H / 2 + (py[i2] - cy) * s,
                     w: w, h: h };
    });
    /* Силовая модель считает узлы точками, поэтому плашки вставали вплотную:
       две карточки касались боками, а стрелка между ними вырождалась в огрызок
       в 4px. Разводим прямоугольники ПОСЛЕ вписывания — до него общий масштаб
       снова съел бы зазор. Зазор в 56px, а не впритык: стрелке нужна длина,
       иначе она читается царапиной между плашками. */
    separateBoxes(pos, 56);
    var links = edges.filter(function (e) { return pos[e.from] && pos[e.to]; })
      .map(function (e) {
        return { from: e.from, to: e.to, label: e.label || "",
                 style: e.style || "solid",
                 points: trimSegment(pos[e.from], pos[e.to]) };
      });
    return { nodes: pos, links: links };
  }

  var LAYOUTS = {
    flowchart: layoutFlowchart,
    process: layoutProcess,
    cycle: layoutCycle,
    funnel: layoutFunnel,
    hierarchy: layoutHierarchy,
    matrix: layoutMatrix,
    pyramid: layoutPyramid,
    hub_spoke: layoutHubSpoke,
    comparison: layoutComparison,
    venn: layoutVenn,
    swimlanes: layoutSwimlanes,
    gantt_lite: layoutGantt,
    steps: layoutSteps,
    mindmap: layoutMindmap,
    network: layoutNetwork,
  };

  /* Гибрид: ручные сдвиги поверх авто-раскладки + кламп центра в холст. */
  function applyOffsets(pos, offsets) {
    Object.keys(pos).forEach(function (id) {
      var o = (offsets || {})[id];
      var p = pos[id];
      if (o) { p.x += (+o.dx || 0); p.y += (+o.dy || 0); }
      p.x = Math.max(p.w / 2, Math.min(W - p.w / 2, p.x));
      p.y = Math.max(p.h / 2, Math.min(H - p.h / 2, p.y));
    });
    return pos;
  }

  /* Снимок авто-позиций ДО сдвигов: по нему видно, какие узлы двигали руками,
     и на какой грани карточки сидел конец стрелки. */
  function snapshot(pos) {
    var out = {};
    Object.keys(pos).forEach(function (id) {
      var p = pos[id];
      out[id] = { x: p.x, y: p.y, w: p.w, h: p.h };
    });
    return out;
  }

  function shifted(a, b) {
    return Math.abs(a.x - b.x) > 0.5 || Math.abs(a.y - b.y) > 0.5;
  }

  /* Ближайшая грань карточки к точке — так узнаём, куда стрелка была
     пристыкована в авто-раскладке. */
  function sideOf(pt, box) {
    var d = { l: Math.abs(pt[0] - (box.x - box.w / 2)),
              r: Math.abs(pt[0] - (box.x + box.w / 2)),
              t: Math.abs(pt[1] - (box.y - box.h / 2)),
              b: Math.abs(pt[1] - (box.y + box.h / 2)) };
    var best = "l";
    ["r", "t", "b"].forEach(function (k) { if (d[k] < d[best]) best = k; });
    return best;
  }

  /* Проложить стрелку заново после ручного сдвига узла, сохранив её характер:
     прямая остаётся прямой, «колено» — коленом на тех же гранях, обратное ребро
     (стыковка обоими концами в одну грань) — обходом. Дуга цикла становится
     прямой: окружности, вокруг которой она шла, больше нет. */
  function reroute(link, sAuto, tAuto, s, t) {
    var pts = link.points || [];
    if (link.curve) {                     // ветвь карты: кривая остаётся кривой
      var dir = t.x >= s.x ? 1 : -1;
      link.points = [[s.x + dir * s.w / 2, s.y], [t.x - dir * t.w / 2, t.y]];
      return;
    }
    if (link.arc || pts.length < 4) {
      delete link.arc;
      link.points = trimSegment(s, t);
      return;
    }
    var a = sideOf(pts[0], sAuto), b = sideOf(pts[pts.length - 1], tAuto);
    var vertical = a === "t" || a === "b";
    if (a === b) { link.points = backRoute(s, t, !vertical); return; }
    var sx = a === "l" ? s.x - s.w / 2 : a === "r" ? s.x + s.w / 2 : s.x;
    var sy = a === "t" ? s.y - s.h / 2 : a === "b" ? s.y + s.h / 2 : s.y;
    var tx = b === "l" ? t.x - t.w / 2 : b === "r" ? t.x + t.w / 2 : t.x;
    var ty = b === "t" ? t.y - t.h / 2 : b === "b" ? t.y + t.h / 2 : t.y;
    var mid = vertical ? (sy + ty) / 2 : (sx + tx) / 2;
    link.points = vertical
      ? [[sx, sy], [sx, mid], [tx, mid], [tx, ty]]
      : [[sx, sy], [mid, sy], [mid, ty], [tx, ty]];
  }

  /* Стрелки строятся внутри раскладки по авто-координатам, а сдвиги
     прикладываются после — без этого прохода линии оставались бы на месте, и
     схема разъезжалась. Нетронутые рёбра НЕ пересчитываем: их геометрию знает
     раскладка, и она точнее общего правила. */
  function relink(result, auto) {
    (result.links || []).forEach(function (l) {
      var s = result.nodes[l.from], t = result.nodes[l.to];
      var a = auto[l.from], b = auto[l.to];
      if (!s || !t || !a || !b) return;
      if (!shifted(s, a) && !shifted(t, b)) return;
      reroute(l, a, b, s, t);
    });
    return result;
  }

  /* ---------------- отрисовка ---------------- */

  var uid = 0;   // инстанс-уникальные id маркеров (два слайда = дубли id — нельзя)

  function nodeFill(n) { return n.accent ? "var(--accent)" : "var(--bg-card)"; }
  function nodeText(n) { return n.accent ? "var(--cl-graphite)" : "var(--fg-body)"; }

  /* Кегль подписи под размер плашки. Без этого длинный label в узкой плашке
   * (swimlanes/comparison режут ширину под число колонок) молча обрезался
   * overflow:hidden — на слайде оставалось «звёртывание фраструктур». Меряем
   * без DOM: раскладки чистые и гоняются в node --test, а рендер обязан быть
   * детерминированным (сейв запекает SVG). 0.6 кегля — ширина символа Inter
   * по кириллице с запасом (замер canvas.measureText дал 0.56–0.60 на живых
   * подписях), 1.16 — межстрочный интервал плюс поле. */
  /* Границы переноса, а НЕ весь \s: неразрывный пробел (U+00A0, его ставит
   * типографика ассемблера — «на\u00a0комплектацию») и узкий неразрывный
   * (U+202F) Chromium не рвёт, для него это ОДНО слово. JS-класс \s их ловит,
   * поэтому split(/\s+/) считал два коротких слова там, где браузер видит одно
   * длинное: кегль брался крупный, обрезка не срабатывала, и подпись молча
   * срезалась overflow:hidden («Задание на комплектацию» → «…комплектацис»). */
  var BREAK_RE = /[^\S\u00a0\u202f]+/;
  var BREAK_KEEP_RE = /([^\S\u00a0\u202f]+)/;

  /* fitFont/clampLabel меряют ПЛАШКУ (габарит минус поля по 12px). Подписи в
   * точном прямоугольнике — колонка дорожек swimlanes — знают свой чистый размер,
   * им нужны те же расчёты без вычитания полей: отсюда пара *Box. */
  function fitFontBox(label, inner, box, base) {
    var text = String(label == null ? "" : label).trim();
    var max = base || 28;
    if (!text) return max;
    var words = text.split(BREAK_RE);
    var longest = 0;
    words.forEach(function (word) { longest = Math.max(longest, word.length); });
    for (var fs = max; fs > 15; fs -= 2) {
      var perLine = Math.max(1, Math.floor(inner / (fs * 0.6)));
      if (longest > perLine) continue;            // слово шире строки — не влезет
      /* Перенос по словам, а не делением длины на ширину: «Проверка данных
       * менеджером» — 26 символов и 14 на строку, то есть «две строки» по
       * счёту, но три по словам. Именно на этой разнице подпись и обрезалась. */
      var lines = 1, cur = 0;
      for (var i = 0; i < words.length; i++) {
        var len = words[i].length;
        if (!cur) cur = len;
        else if (cur + 1 + len <= perLine) cur += 1 + len;
        else { lines += 1; cur = len; }
      }
      if (lines * fs * 1.16 <= box) return fs;
    }
    return 16;
  }

  function fitFont(label, w, h, base) {
    return fitFontBox(label, Math.max(24, w - 24), Math.max(24, h - 24), base);
  }

  /* Сколько строк займёт подпись при ширине строки perLine (в символах). Слово
   * длиннее строки Chromium РВЁТ (overflow-wrap), то есть оно съедает несколько
   * строк, а не переносится целиком — fitFont такой случай просто пропускает
   * (ищет кегль поменьше), а здесь его надо посчитать честно: на полу кегля
   * выбора уже нет. */
  function labelLines(text, perLine) {
    var words = String(text == null ? "" : text).trim().split(BREAK_RE);
    var lines = 0, cur = 0, i, len;
    for (i = 0; i < words.length; i++) {
      len = words[i].length;
      if (!len) continue;
      if (len > perLine) {
        if (cur) lines += 1;
        lines += Math.ceil(len / perLine) - 1;
        cur = len % perLine || perLine;
      } else if (!cur) cur = len;
      else if (cur + 1 + len <= perLine) cur += 1 + len;
      else { lines += 1; cur = len; }
    }
    return lines + (cur ? 1 : 0);
  }

  /* Подпись, которая не влезает даже на минимальном кегле (16 — ниже нечитаемо),
   * до сих пор просто срезалась overflow:hidden. Обрезка шла ПО ЦЕНТРУ плашки:
   * сверху и снизу оставались половинки строк — на слайде это выглядит не как
   * «текст сократили», а как испорченные данные. Режем сами, по границе слова,
   * и ставим многоточие: «сокращено» читается однозначно. Функция чистая (текст
   * + габарит + кегль), поэтому запечённый в деку SVG воспроизводится байт в
   * байт. Когда fitFont нашёл кегль (fs > 16), обрезка не срабатывает по
   * построению: он и выбирал его по условию «строки помещаются». */
  function clampLabelBox(label, inner, box, fs) {
    var text = String(label == null ? "" : label).trim();
    var perLine = Math.max(1, Math.floor(inner / (fs * 0.6)));
    var maxLines = Math.max(1, Math.floor(box / (fs * 1.16)));
    if (!text || labelLines(text, perLine) <= maxLines) return text;
    /* Сплит с захватом разделителей: склеивать обратно через " " нельзя — так
     * NBSP из типографики подменялся обычным пробелом, и обрезанная подпись
     * начинала переноситься не там, где мерил движок. */
    var parts = text.split(BREAK_KEEP_RE), count = (parts.length + 1) / 2;
    for (var n = count - 1; n > 0; n--) {
      var cut = parts.slice(0, 2 * n - 1).join("") + "…";
      if (labelLines(cut, perLine) <= maxLines) return cut;
    }
    return text.slice(0, Math.max(1, perLine * maxLines - 1)) + "…";
  }

  function clampLabel(label, w, h, fs) {
    return clampLabelBox(label, Math.max(24, w - 24), Math.max(24, h - 24), fs);
  }

  /* Разбить подпись на строки по границам слов — для SVG <text>, который сам не
   * переносится (подписи рёбер). Тот же жадный алгоритм, по которому мерят
   * fitFont/labelLines, поэтому число строк совпадает с расчётным. Слово длиннее
   * строки режем: на этом месте Chromium в foreignObject делает то же самое. */
  function wrapLines(text, perLine) {
    var parts = String(text == null ? "" : text).trim().split(BREAK_KEEP_RE);
    var lines = [], cur = "", i, word, sep;
    for (i = 0; i < parts.length; i += 2) {
      word = parts[i];
      if (!word) continue;
      sep = i ? parts[i - 1] : "";
      while (word.length > perLine) {
        if (cur) { lines.push(cur); cur = ""; }
        lines.push(word.slice(0, perLine));
        word = word.slice(perLine);
      }
      if (!cur) cur = word;
      else if (cur.length + sep.length + word.length <= perLine) cur += sep + word;
      else { lines.push(cur); cur = word; }
    }
    if (cur) lines.push(cur);
    return lines;
  }

  var FO_ALIGN = { left: ["flex-start", "left"], right: ["flex-end", "right"] };

  function labelFO(p, n, color, fontSize, align) {
    var pad = 10;
    var a = FO_ALIGN[align] || ["center", "center"];
    var fs = fitFont(n.label, p.w, p.h, fontSize);
    return '<foreignObject x="' + (p.x - p.w / 2 + pad) + '" y="' + (p.y - p.h / 2 + pad) +
      '" width="' + (p.w - 2 * pad) + '" height="' + (p.h - 2 * pad) + '">' +
      '<div xmlns="http://www.w3.org/1999/xhtml" style="width:100%;height:100%;display:flex;' +
      'align-items:center;justify-content:' + a[0] + ';text-align:' + a[1] + ';overflow:hidden;' +
      'font-size:' + fs + 'px;line-height:1.15;' +
      /* overflow-wrap, а НЕ word-break:break-word: последний в Chromium работает
         как anywhere — рвёт слово, лишь бы добить текущую строку, и «Задание на
         комплектацию» превращалось в «комплектаци» + «ю» отдельной строкой,
         хотя слово целиком влезало следующей. overflow-wrap сначала переносит
         слово и режет только то, что не помещается в строку вообще. */
      'letter-spacing:-.3px;' +
      'color:' + color + ';"><div style="min-width:0;max-width:100%;' +
      /* Внутренняя обёртка с min-width:0 обязательна. Текст прямо во флекс-боксе
         становится анонимным флекс-элементом, а тот не сжимается уже своей
         min-content ширины — для overflow-wrap:break-word это ЦЕЛОЕ длинное
         слово (break-word, в отличие от anywhere, intrinsic-размер не уменьшает).
         Слово шире плашки поэтому не переносилось, а вылезало и молча срезалось
         overflow:hidden: «Задание на комплектацию» → «…комплектацис». */
      'overflow-wrap:break-word;">' + esc(clampLabel(n.label, p.w, p.h, fs)) +
      "</div></div></foreignObject>";
  }

  function shapePath(n, p) {
    var x = p.x, y = p.y, w = p.w, h = p.h;
    var fill = nodeFill(n);
    if (n.shape === "decision") {
      var dh = Math.min(h + 44, h * 1.7);
      return '<polygon points="' + x + "," + (y - dh / 2) + " " + (x + w / 2) + "," + y + " " +
        x + "," + (y + dh / 2) + " " + (x - w / 2) + "," + y + '" fill="' + fill + '"/>';
    }
    if (n.shape === "start" || n.shape === "end") {
      return '<rect x="' + (x - w / 2) + '" y="' + (y - h / 2) + '" width="' + w +
        '" height="' + h + '" rx="' + h / 2 + '" fill="' + fill + '"/>';
    }
    if (n.shape === "io") {
      var k = 26;
      return '<polygon points="' + (x - w / 2 + k) + "," + (y - h / 2) + " " + (x + w / 2) + "," + (y - h / 2) +
        " " + (x + w / 2 - k) + "," + (y + h / 2) + " " + (x - w / 2) + "," + (y + h / 2) +
        '" fill="' + fill + '"/>';
    }
    return '<rect x="' + (x - w / 2) + '" y="' + (y - h / 2) + '" width="' + w +
      '" height="' + h + '" fill="' + fill + '"/>';
  }

  /* Бюджет подписи ребра: 200px — примерно половина промежутка между колонками
   * узлов, дальше подпись уже заезжает на соседей. Две строки по 26px — всё, что
   * помещается между рядами, не задевая плашки. 24 — потолок кегля, не константа:
   * подбор идёт вниз тем же fitFontBox, что и у подписей узлов. Без подбора
   * «Автоматизированная» (18 символов при 13 на строку) рвалось посреди слова —
   * SVG не расставляет переносов, и «Автоматизиров/анная» читается как брак. */
  var EDGE_FS = 24, EDGE_W = 200, EDGE_H = 2 * EDGE_FS * 1.16;

  function linkPath(link, markerId) {
    var attrs = 'fill="none" stroke="var(--fg-muted)" stroke-width="3"' +
      (link.style === "dashed" ? ' stroke-dasharray="10 8"' : "") +
      ' marker-end="url(#' + markerId + ')"';
    var d;
    if (link.arc) {
      var a = link.arc;
      var large = (a.a2 - a.a1) % (2 * Math.PI) > Math.PI ? 1 : 0;
      var arx = a.rx == null ? a.r : a.rx, ary = a.ry == null ? a.r : a.ry;
      d = "M" + (a.cx + arx * Math.cos(a.a1)) + " " + (a.cy + ary * Math.sin(a.a1)) +
        " A" + arx + " " + ary + " 0 " + large + " 1 " +
        (a.cx + arx * Math.cos(a.a2)) + " " + (a.cy + ary * Math.sin(a.a2));
    } else if (link.curve && link.points.length === 2) {
      /* Ветвь ментальной карты: горизонтальные касательные у обоих концов —
         ломаная «коленом» читалась бы как оргсхема, а не как карта. */
      var q0 = link.points[0], q1 = link.points[1], mx = (q0[0] + q1[0]) / 2;
      d = "M" + q0[0] + " " + q0[1] + " C" + mx + " " + q0[1] + " " + mx + " " +
        q1[1] + " " + q1[0] + " " + q1[1];
    } else {
      d = link.points.map(function (pt, i) { return (i ? "L" : "M") + pt[0] + " " + pt[1]; }).join("");
    }
    var out = '<path d="' + d + '" ' + attrs + "/>";
    if (link.label) {
      /* Метка — на СРЕДНЕМ сегменте ломаной: первый сегмент у всех рёбер из
       * одного узла общий (метки «да»/«нет» развилки слипались в кашу),
       * а средний вертикальный/горизонтальный — у каждого ребра свой. */
      var pts = link.points;
      var p0 = pts[1] || pts[0], p1 = pts[2] || pts[1] || p0;
      var lx = (p0[0] + p1[0]) / 2, ly = (p0[1] + p1[1]) / 2 - 12;
      var anchor = "middle";
      if (pts.length === 2) {              // прямой сегмент (hub_spoke): середина
        lx = (pts[0][0] + pts[1][0]) / 2; ly = (pts[0][1] + pts[1][1]) / 2 - 12;
      } else if (Math.abs(p0[0] - p1[0]) < 1) { // вертикальный сегмент: метку вбок
        lx += 14; ly += 12; anchor = "start";
      }
      /* SVG <text> НЕ переносится и ничем не ограничен по ширине. Схема
       * разрешает подпись ребра до 60 символов, и «Согласование условий
       * договора» растягивалось одной строкой на пол-холста — поперёк соседних
       * узлов и поверх второй такой же подписи. Даём подписи бюджет (две строки
       * по ~13 символов — половина шага сетки узлов), сокращаем лишнее тем же
       * clampLabelBox, что и подписи узлов, и раскладываем на tspan'ы. Блок
       * растёт ВВЕРХ: нижняя строка остаётся на прежней базовой линии, то есть
       * короткие «да»/«нет» стоят там же, где стояли. */
      var efs = fitFontBox(link.label, EDGE_W, EDGE_H, EDGE_FS);
      var lead = Math.round(efs * 1.16);
      var elines = wrapLines(clampLabelBox(link.label, EDGE_W, EDGE_H, efs),
                             Math.max(1, Math.floor(EDGE_W / (efs * 0.6))));
      out += '<text x="' + lx + '" y="' + (ly - (elines.length - 1) * lead) +
        '" text-anchor="' + anchor + '" font-size="' + efs + '" ' +
        'fill="var(--fg-muted)">' +
        elines.map(function (line, i) {
          return '<tspan x="' + lx + '"' + (i ? ' dy="' + lead + '"' : "") +
            ">" + esc(line) + "</tspan>";
        }).join("") + "</text>";
    }
    return out;
  }

  /* Цвет подписи, лежащей ПОВЕРХ заливки графика. Хвост серой шкалы в тёмной
     теме почти сливается с фоном (--chart-4 #525252 против --bg #222 — контраст
     2:1), и «Допущены к пилотам» на четвёртой полосе воронки читалось как тень.
     Пары «заливка → подпись» живут в теме, здесь только выбор индекса. */
  function onChart(i, accent) {
    return "var(--on-chart-" + (accent ? 1 : Math.min(i, 5) + 1) + ")";
  }

  function renderFunnel(spec, pos) {
    var parts = [];
    spec.nodes.forEach(function (n, i) {
      var p = pos[n.id];
      var wTop = p.w, wBot = p.wBottom == null ? p.w : p.wBottom;
      var fill = n.accent ? "var(--accent)" : "var(--chart-" + (Math.min(i, 5) + 1) + ")";
      parts.push('<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
        '<polygon points="' +
        (p.x - wTop / 2) + "," + (p.y - p.h / 2) + " " + (p.x + wTop / 2) + "," + (p.y - p.h / 2) + " " +
        (p.x + wBot / 2) + "," + (p.y + p.h / 2) + " " + (p.x - wBot / 2) + "," + (p.y + p.h / 2) +
        '" fill="' + fill + '"/>' +
        labelFO({ x: p.x, y: p.y, w: Math.min(wTop, wBot) + 40, h: p.h }, n,
                onChart(i, n.accent)) +
        (n.value ? '<text x="' + (p.x + 640) + '" y="' + p.y +
          '" dominant-baseline="middle" font-size="30" font-weight="500" ' +
          'fill="var(--fg-body)">' + esc(n.value) + "</text>" : "") +
        "</g>");
    });
    return parts.join("");
  }

  function renderPyramid(spec, pos) {
    /* Слои-трапеции: структура pos как у funnel (w = верх, wBottom = низ). */
    return renderFunnel(spec, pos);
  }

  function renderVenn(spec, pos) {
    var parts = [];
    spec.nodes.forEach(function (n, i) {
      var p = pos[n.id];
      var fill = n.accent ? "var(--accent)" : "var(--chart-" + (Math.min(i, 5) + 1) + ")";
      parts.push('<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
        '<circle cx="' + p.x + '" cy="' + p.y + '" r="' + p.r + '" fill="' + fill +
        '" fill-opacity="0.45"/>' +
        labelFO({ x: p.x + (p.labelDx || 0), y: p.y + (p.labelDy || 0),
                  w: p.r * 1.1, h: 130 }, n, "var(--fg-body)") +
        "</g>");
    });
    var cl = spec.meta && spec.meta.center_label;
    if (cl) {
      var cx = 0, cy = 0, n = spec.nodes.length;
      spec.nodes.forEach(function (nd) { cx += pos[nd.id].x / n; cy += pos[nd.id].y / n; });
      /* Габарит подписи — сама линза пересечения, а не «сколько текста пришло».
         Голый <text> тянулся во всю длину: «Планирование маршрута доставки» на
         450px при линзе в 180 ложилось поверх подписей обоих кругов, и слайд
         читался как месиво наложенных букв. Радиус минус расстояние до центра —
         это и есть половина линзы, работает и для двух кругов, и для трёх. */
      var half = Infinity;
      spec.nodes.forEach(function (nd) {
        var p = pos[nd.id], dx = p.x - cx, dy = p.y - cy;
        half = Math.min(half, (p.r || 0) - Math.sqrt(dx * dx + dy * dy));
      });
      var cw = Math.max(60, 2 * half), ch = cw;
      var cfs = fitFontBox(cl, cw, ch, 26);
      parts.push('<foreignObject x="' + (cx - cw / 2) + '" y="' + (cy - ch / 2) +
        '" width="' + cw + '" height="' + ch + '">' +
        '<div xmlns="http://www.w3.org/1999/xhtml" style="width:100%;height:100%;' +
        'display:flex;align-items:center;justify-content:center;text-align:center;' +
        'overflow:hidden;font-size:' + cfs + 'px;line-height:1.15;font-weight:500;' +
        'color:var(--fg-body);"><div style="min-width:0;max-width:100%;' +
        'overflow-wrap:break-word;">' + esc(clampLabelBox(cl, cw, ch, cfs)) +
        "</div></div></foreignObject>");
    }
    return parts.join("");
  }

  function renderGantt(spec, pos) {
    var parts = [];
    spec.nodes.forEach(function (n, i) {
      var p = pos[n.id];
      var fill = n.accent ? "var(--accent)" : "var(--chart-" + (Math.min(i, 5) + 1) + ")";
      /* Подпись живёт внутри полосы, пока та достаточно широка; короткая
         работа (один период на длинной шкале) уводит подпись наружу — иначе
         текст молча резался бы по границе полосы. */
      var box, color, align;
      if (p.w >= 300) {
        box = { x: p.x, y: p.y, w: p.w, h: p.h };
        color = onChart(i, n.accent); align = "center";
      } else if (W - (p.x + p.w / 2) >= 280) {
        var lw = Math.min(420, W - (p.x + p.w / 2) - 16);
        box = { x: p.x + p.w / 2 + 16 + lw / 2, y: p.y, w: lw, h: p.h };
        color = "var(--fg-body)"; align = "left";
      } else {
        var rw = Math.min(420, p.x - p.w / 2 - 16);
        box = { x: p.x - p.w / 2 - 16 - rw / 2, y: p.y, w: rw, h: p.h };
        color = "var(--fg-body)"; align = "right";
      }
      parts.push('<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
        '<rect x="' + (p.x - p.w / 2) + '" y="' + (p.y - p.h / 2) + '" width="' + p.w +
        /* Без rx: прямые углы — инвариант бренда (--radius:0 в deck.css), и
           скругления в деке запрещены явно — их отвергает и филлер, и
           vision-QA. Полосы плана были единственным скруглённым элементом во
           всём движке: рядом с прямоугольными плашками остальных схем это
           читалось как чужая вставка. У флоучарта rx остаётся — там капсула
           start/end это нотация, а не украшение. */
        '" height="' + p.h + '" fill="' + fill + '"/>' +
        labelFO(box, n, color, 26, align) + "</g>");
    });
    return parts.join("");
  }

  function renderSteps(spec, pos) {
    var parts = [], fs = 28;
    spec.nodes.forEach(function (n) {
      var p = pos[n.id];
      fs = Math.min(fs, fitFont(n.label, p.w, p.h));
    });
    spec.nodes.forEach(function (n) {
      var p = pos[n.id];
      var g = '<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
        '<rect x="' + (p.x - p.w / 2) + '" y="' + (p.y - p.h / 2) + '" width="' + p.w +
        '" height="' + p.h + '" fill="' + nodeFill(n) + '"/>' +
        labelFO(p, n, nodeText(n), fs);
      if (n.value) {                        // пометка ступени — над карточкой
        g += '<text x="' + p.x + '" y="' + (p.y - p.h / 2 - 14) + '" text-anchor="middle" ' +
          'font-size="24" fill="var(--fg-muted)">' + esc(n.value) + "</text>";
      }
      parts.push(g + "</g>");
    });
    return parts.join("");
  }

  /* ---- декор фона (оси, разделители, дорожки) — рисуется ПОД узлами ---- */

  function decorMatrix(spec, layout, markerId) {
    var m = spec.meta || {};
    var out = '<line x1="40" y1="' + H / 2 + '" x2="' + (W - 40) + '" y2="' + H / 2 +
      '" stroke="var(--fg-muted)" stroke-width="2.5" marker-end="url(#' + markerId + ')"/>' +
      '<line x1="' + W / 2 + '" y1="' + (H - 16) + '" x2="' + W / 2 + '" y2="26" ' +
      'stroke="var(--fg-muted)" stroke-width="2.5" marker-end="url(#' + markerId + ')"/>';
    if (m.x_axis) {
      out += '<text x="' + (W - 44) + '" y="' + (H / 2 + 40) + '" text-anchor="end" ' +
        'font-size="26" fill="var(--fg-muted)">' + esc(m.x_axis) + "</text>";
    }
    if (m.y_axis) {
      out += '<text x="' + (W / 2 + 18) + '" y="44" font-size="26" ' +
        'fill="var(--fg-muted)">' + esc(m.y_axis) + "</text>";
    }
    return out;
  }

  /* Бюджет заголовка колонки: ширина карточки (660, см. layoutComparison) минус
   * поля — заголовок жирный, а 0.6 кегля на символ выведены по обычному
   * начертанию, так что запас нужен. Две строки — всё, что помещается над
   * карточками. */
  var CMP_W = 620, CMP_H = 2 * 34 * 1.16;

  function decorComparison(spec, layout) {
    var out = '<line x1="' + W / 2 + '" y1="20" x2="' + W / 2 + '" y2="' + (H - 20) +
      '" stroke="var(--fg-muted)" stroke-width="2" stroke-dasharray="10 8" opacity="0.6"/>';
    var cols = [W * 0.27, W * 0.73];
    (layout.lanes || []).slice(0, 2).forEach(function (lane, i) {
      if (!lane) return;
      /* Заголовок колонки рисовался голым <text> без габарита, а схема разрешает
       * lane до 40 символов: «Целевая архитектура эксплуатации ЦОД 2030» на 34
       * кегле занимает ~870px при колонке в 660 — заголовок наезжал на пунктирный
       * разделитель и лез в чужую половину. Бюджет — ширина карточки минус поля;
       * дальше тот же контракт, что у подписей узлов и рёбер. Блок из двух строк
       * расходится от прежней базовой линии симметрично: одна строка стоит там
       * же, где стояла, две — не достают до карточек (их верх на y=120). */
      var lfs = fitFontBox(lane, CMP_W, CMP_H, 34);
      var lead = Math.round(lfs * 1.16);
      var rows = wrapLines(clampLabelBox(lane, CMP_W, CMP_H, lfs),
                           Math.max(1, Math.floor(CMP_W / (lfs * 0.6))));
      out += '<text x="' + cols[i] + '" y="' + (64 - (rows.length - 1) * lead / 2) +
        '" text-anchor="middle" font-size="' + lfs + '" ' +
        'font-weight="500" fill="var(--fg-body)">' +
        rows.map(function (row, k) {
          return '<tspan x="' + cols[i] + '"' + (k ? ' dy="' + lead + '"' : "") +
            ">" + esc(row) + "</tspan>";
        }).join("") + "</text>";
    });
    return out;
  }

  function decorSwimlanes(spec, layout) {
    var lanes = layout.lanes || [], laneH = layout.laneH || H, labelW = layout.labelW || 0;
    var out = "";
    lanes.forEach(function (lane, i) {
      /* Полосу-зебру под чётной дорожкой убрали. Она заливалась тем же
         --bg-card, что и плашки узлов, — а плашка лежит НА полосе, и граница
         между ними держалась на одной этой прозрачности. В светлой теме между
         фоном (#FFF) и плашкой (#F2F2F2) всего 13 пунктов, половина — 6: плашка
         в затенённой дорожке падала до контраста 1.05:1 к своей же полосе и
         пропадала — «Проверка договора» читалась как текст без плашки, пока
         соседние дорожки показывали плашки нормально. Третьего тона в палитре
         нет: любая заливка в этом промежутке сталкивается с плашкой. Границы
         дорожек и без неё держат линии ниже — они есть у каждой дорожки; сама
         же зебра при трёх дорожках подсвечивала ровно одну среднюю, читаясь
         как выделение, которого в данных нет. */
      if (i) {
        out += '<line x1="0" y1="' + (i * laneH) + '" x2="' + W + '" y2="' + (i * laneH) +
          '" stroke="var(--fg-muted)" stroke-width="1.5" opacity="0.35"/>';
      }
      /* Колонка подписей узкая и фиксированная, поэтому здесь тот же контракт,
         что и у подписей узлов: подбор кегля, перенос на обёртке с min-width:0 и
         явная обрезка с многоточием. Без него «Отдел клиентского сопровождения»
         молча срезалось overflow:hidden до «…сопровождени» с половинкой буквы. */
      var lw = Math.max(24, labelW - 40), lh = Math.max(24, laneH - 20);
      var lfs = fitFontBox(lane, lw, lh, 26);
      out += '<foreignObject x="16" y="' + (i * laneH + 10) + '" width="' + lw +
        '" height="' + lh + '">' +
        '<div xmlns="http://www.w3.org/1999/xhtml" style="width:100%;height:100%;' +
        'display:flex;align-items:center;overflow:hidden;font-size:' + lfs + 'px;' +
        'line-height:1.15;color:var(--fg-muted);">' +
        '<div style="min-width:0;max-width:100%;overflow-wrap:break-word;">' +
        esc(clampLabelBox(lane, lw, lh, lfs)) + "</div></div></foreignObject>";
    });
    return out;
  }

  function decorGantt(spec, layout) {
    var total = layout.total || 1, unit = layout.unit || W;
    var out = "";
    var ax = spec.meta && spec.meta.x_axis;
    if (ax) {
      out += '<text x="4" y="30" font-size="26" fill="var(--fg-muted)">' +
        esc(ax) + "</text>";
    }
    for (var i = 0; i <= total; i++) {
      var x = Math.min(W - 1, i * unit);
      out += '<line x1="' + x + '" y1="' + (GANTT_TOP - 8) + '" x2="' + x + '" y2="' + H +
        '" stroke="var(--fg-muted)" stroke-width="1.5" opacity="' +
        (i ? "0.22" : "0.4") + '"/>';
      /* Номера периодов только пока они читаются: на длинной шкале подписи
         слипаются в серую полосу — сетка без номеров честнее. */
      if (total <= 16 && i < total) {
        out += '<text x="' + (i * unit + unit / 2) + '" y="' + (GANTT_TOP - 22) +
          '" text-anchor="middle" font-size="24" fill="var(--fg-muted)">' +
          (i + 1) + "</text>";
      }
    }
    return out;
  }

  function decorSteps(spec, layout) {
    var pos = layout.nodes, out = "";
    /* Подступенки считаются по ФАКТИЧЕСКИМ позициям (декор рисуется после
       applyOffsets) — иначе сдвинутая руками ступень висела бы над чужим
       столбом. */
    spec.nodes.forEach(function (n) {
      var p = pos[n.id];
      if (!p) return;
      var top = p.y + p.h / 2;
      if (top >= H - 2) return;
      out += '<rect x="' + (p.x - p.w / 2) + '" y="' + top + '" width="' + p.w +
        '" height="' + (H - top) + '" fill="var(--bg-card)" fill-opacity="0.45"/>';
      /* Линия проступи. Подступенок — тот же --bg-card, что и плашка ступени,
         только в 0.45 прозрачности, поэтому граница между ними держится ровно на
         разнице этих двух заливок. В тёмной теме между фоном (#222) и плашкой
         (#383838) 22 пункта, и грань видно; в светлой между #FFF и #F2F2F2 их 13,
         половина — 6, и контраст плашки к подступенку падает до 1.05:1 — ступень
         сливалась со своим столбом, лестница читалась как простые столбцы. Внутри
         палитры это не лечится: между фоном и плашкой светлой темы просто нет
         места для третьего тона. Поэтому грань рисуем линией — тем же приёмом,
         каким у дорожек отбиты полосы (там она есть, а здесь её не было). */
      out += '<line x1="' + (p.x - p.w / 2) + '" y1="' + top + '" x2="' +
        (p.x + p.w / 2) + '" y2="' + top +
        '" stroke="var(--fg-muted)" stroke-width="1.5" opacity="0.35"/>';
    });
    return out + '<line x1="0" y1="' + (H - 1) + '" x2="' + W + '" y2="' + (H - 1) +
      '" stroke="var(--fg-muted)" stroke-width="2" opacity="0.4"/>';
  }

  var DECOR = {
    matrix: decorMatrix,
    comparison: decorComparison,
    swimlanes: decorSwimlanes,
    gantt_lite: decorGantt,
    steps: decorSteps,
  };

  var NODE_RENDERERS = {
    funnel: renderFunnel,
    pyramid: renderPyramid,
    venn: renderVenn,
    gantt_lite: renderGantt,
    steps: renderSteps,
  };

  function render(host, specArg) {
    var spec = specArg;
    if (!spec) {
      try {
        spec = JSON.parse(host.getAttribute("data-diagram") || "null");
      } catch (e) {
        spec = null;
      }
    }
    host.innerHTML = "";   // идемпотентность: запечённый сейвом SVG стираем
    spec = sanitize(spec);
    if (!spec) {
      host.innerHTML = PLACEHOLDER;
      return;
    }
    try {
      host.innerHTML = build(spec);
    } catch (e) {
      // Раскладки рассчитаны на спек, прошедший схему, но в собранной деке
      // истина — HTML: data-diagram мог пережить ручную правку, а редактор зовёт
      // render на промежуточных состояниях. Один такой слайд не должен уносить
      // с собой ни свою страницу, ни соседние (renderAll идёт циклом).
      host.innerHTML = PLACEHOLDER;
    }
  }

  var PLACEHOLDER = '<div style="font-size:28px;color:var(--fg-muted);' +
    'padding:24px 0;">Схема: данные недоступны</div>';

  /* Отсечь то, на чём раскладки спотыкаются: узлы без id и рёбра в никуда.
     Схема такое не пропускает, но в собранной деке истина — HTML, а в редакторе
     render зовут на промежуточных состояниях (узел уже удалён, ребро ещё нет).
     Лучше нарисовать схему без одной стрелки, чем плашку «данные недоступны».
     Возвращает копию (исходный спек — чужой объект) или null, если рисовать
     нечего. */
  function sanitize(spec) {
    if (!spec || !LAYOUTS[spec.kind] || !Array.isArray(spec.nodes)) return null;
    var ids = {};
    var nodes = spec.nodes.filter(function (n) {
      if (!n || typeof n.id !== "string" || !n.id || ids[n.id]) return false;
      ids[n.id] = true;
      return true;
    });
    if (!nodes.length) return null;
    var edges = (Array.isArray(spec.edges) ? spec.edges : []).filter(function (e) {
      return e && ids[e.from] && ids[e.to] && e.from !== e.to;
    });
    var out = {};
    Object.keys(spec).forEach(function (k) { out[k] = spec[k]; });
    out.nodes = nodes;
    out.edges = edges;
    return out;
  }

  function build(spec) {
    var result = LAYOUTS[spec.kind](spec);
    var auto = snapshot(result.nodes);
    applyOffsets(result.nodes, spec.offsets);
    relink(result, auto);

    var markerId = "dgm-arrow-" + (++uid);
    var parts = ['<svg class="diagram-svg m-enter" viewBox="0 0 ' + W + " " + H +
      '" fill="none" xmlns="http://www.w3.org/2000/svg" role="img">'];
    parts.push('<defs><marker id="' + markerId + '" viewBox="0 0 10 10" refX="8.5" refY="5" ' +
      'markerWidth="7" markerHeight="7" orient="auto-start-reverse">' +
      '<path d="M0 0L10 5L0 10z" fill="var(--fg-muted)"/></marker></defs>');
    var decor = DECOR[spec.kind];
    if (decor) parts.push(decor(spec, result, markerId));
    result.links.forEach(function (link) { parts.push(linkPath(link, markerId)); });
    var custom = NODE_RENDERERS[spec.kind];
    if (custom) {
      parts.push(custom(spec, result.nodes));
    } else {
      /* Кегль общий на всю схему — по самой тесной плашке. Подбор в каждой
       * плашке отдельно давал «Сбор данных» 28-м рядом с «Коммерческие условия»
       * 20-м: подписи перестали резаться, но разнобой читался как небрежность. */
      var fs = 28;
      spec.nodes.forEach(function (n) {
        var p = result.nodes[n.id];
        fs = Math.min(fs, fitFont(n.label, p.w, p.h));
      });
      spec.nodes.forEach(function (n) {
        var p = result.nodes[n.id];
        parts.push('<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
          shapePath(n, p) + labelFO(p, n, nodeText(n), fs) + "</g>");
      });
    }
    parts.push("</svg>");
    return parts.join("");
  }

  /* Позиции узлов без отрисовки (x,y — центр, w,h — габарит, уже со сдвигами):
     редактор считает по ним магнитное выравнивание при перетаскивании. */
  function computeLayout(spec) {
    var clean = sanitize(spec);
    if (!clean) return null;
    try {
      return applyOffsets(LAYOUTS[clean.kind](clean).nodes, clean.offsets);
    } catch (e) {
      return null;   // нет раскладки — редактор просто тащит узел без магнита
    }
  }

  function renderAll(root) {
    var scope = root || document;
    var hosts = scope.querySelectorAll(".diagram-host");
    for (var i = 0; i < hosts.length; i++) render(hosts[i]);
  }

  var api = {
    render: render, renderAll: renderAll, layout: computeLayout,
    LAYOUTS: LAYOUTS, applyOffsets: applyOffsets, num: num,
    relink: relink, reroute: reroute, sideOf: sideOf,
    layoutFlowchart: layoutFlowchart, layoutProcess: layoutProcess,
    layoutCycle: layoutCycle, layoutFunnel: layoutFunnel,
    layoutHierarchy: layoutHierarchy, flowRanks: flowRanks,
    layoutMatrix: layoutMatrix, layoutPyramid: layoutPyramid,
    layoutHubSpoke: layoutHubSpoke, layoutComparison: layoutComparison,
    layoutVenn: layoutVenn, layoutSwimlanes: layoutSwimlanes,
    layoutGantt: layoutGantt, layoutSteps: layoutSteps,
    layoutMindmap: layoutMindmap, layoutNetwork: layoutNetwork,
    laneList: laneList, trimSegment: trimSegment, fitFont: fitFont,
    clampLabel: clampLabel, labelLines: labelLines, wrapLines: wrapLines,
    CANVAS: { W: W, H: H },
  };

  global.DiagramEngine = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () { renderAll(); });
    } else {
      renderAll();
    }
  }
})(typeof window !== "undefined" ? window : this);
