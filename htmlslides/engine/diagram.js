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
      var points;
      if (down) {
        points = forward
          ? [[s.x, s.y + s.h / 2], [s.x, (s.y + t.y) / 2], [t.x, (s.y + t.y) / 2], [t.x, t.y - t.h / 2]]
          : backRoute(s, t, true);
      } else {
        points = forward
          ? [[s.x + s.w / 2, s.y], [(s.x + t.x) / 2, s.y], [(s.x + t.x) / 2, t.y], [t.x - t.w / 2, t.y]]
          : backRoute(s, t, false);
      }
      return { from: e.from, to: e.to, points: points, label: e.label || "", style: e.style || "solid" };
    });
    return { nodes: pos, links: links };
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
    var r = Math.min(H / 2 - 70, 265);
    var pos = {}, links = [];
    nodes.forEach(function (node, i) {
      var a = -Math.PI / 2 + (2 * Math.PI * i) / n;   // старт сверху, по часовой
      pos[node.id] = {
        x: cx + r * Math.cos(a), y: cy + r * Math.sin(a),
        w: Math.min(300, 2 * Math.PI * r / n - 40), h: 96,
        angle: a,
      };
    });
    for (var i = 0; i < n; i++) {
      var from = nodes[i].id, to = nodes[(i + 1) % n].id;
      var a1 = pos[from].angle + (Math.PI / n) * 0.42;
      var a2 = pos[to].angle - (Math.PI / n) * 0.42;
      links.push({
        from: from, to: to, label: "", style: "solid",
        arc: { cx: cx, cy: cy, r: r, a1: a1, a2: a2 },
        points: [[cx + r * Math.cos(a1), cy + r * Math.sin(a1)],
                 [cx + r * Math.cos(a2), cy + r * Math.sin(a2)]],
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

  var LAYOUTS = {
    flowchart: layoutFlowchart,
    process: layoutProcess,
    cycle: layoutCycle,
    funnel: layoutFunnel,
    hierarchy: layoutHierarchy,
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

  /* ---------------- отрисовка ---------------- */

  var uid = 0;   // инстанс-уникальные id маркеров (два слайда = дубли id — нельзя)

  function nodeFill(n) { return n.accent ? "var(--accent)" : "var(--bg-card)"; }
  function nodeText(n) { return n.accent ? "var(--cl-graphite)" : "var(--fg-body)"; }

  function labelFO(p, n, color, fontSize) {
    var pad = 10;
    return '<foreignObject x="' + (p.x - p.w / 2 + pad) + '" y="' + (p.y - p.h / 2 + pad) +
      '" width="' + (p.w - 2 * pad) + '" height="' + (p.h - 2 * pad) + '">' +
      '<div xmlns="http://www.w3.org/1999/xhtml" style="width:100%;height:100%;display:flex;' +
      'align-items:center;justify-content:center;text-align:center;overflow:hidden;' +
      'font-size:' + (fontSize || 28) + 'px;line-height:1.15;letter-spacing:-.3px;' +
      'color:' + color + ';">' + esc(n.label) + "</div></foreignObject>";
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

  function linkPath(link, markerId) {
    var attrs = 'fill="none" stroke="var(--fg-muted)" stroke-width="3"' +
      (link.style === "dashed" ? ' stroke-dasharray="10 8"' : "") +
      ' marker-end="url(#' + markerId + ')"';
    var d;
    if (link.arc) {
      var a = link.arc;
      var large = (a.a2 - a.a1) % (2 * Math.PI) > Math.PI ? 1 : 0;
      d = "M" + (a.cx + a.r * Math.cos(a.a1)) + " " + (a.cy + a.r * Math.sin(a.a1)) +
        " A" + a.r + " " + a.r + " 0 " + large + " 1 " +
        (a.cx + a.r * Math.cos(a.a2)) + " " + (a.cy + a.r * Math.sin(a.a2));
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
      if (Math.abs(p0[0] - p1[0]) < 1) {   // вертикальный сегмент: метку вбок
        lx += 14; ly += 12; anchor = "start";
      }
      out += '<text x="' + lx + '" y="' + ly + '" text-anchor="' + anchor + '" font-size="24" ' +
        'fill="var(--fg-muted)">' + esc(link.label) + "</text>";
    }
    return out;
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
        labelFO({ x: p.x, y: p.y, w: Math.min(wTop, wBot) + 40, h: p.h }, n, "var(--bg)") +
        (n.value ? '<text x="' + (p.x + 640) + '" y="' + p.y +
          '" dominant-baseline="middle" font-size="30" font-weight="500" ' +
          'fill="var(--fg-body)">' + esc(n.value) + "</text>" : "") +
        "</g>");
    });
    return parts.join("");
  }

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
    if (!spec || !LAYOUTS[spec.kind] || !Array.isArray(spec.nodes) || !spec.nodes.length) {
      host.innerHTML = '<div style="font-size:28px;color:var(--fg-muted);padding:24px 0;">' +
        "Схема: данные недоступны</div>";
      return;
    }
    var result = LAYOUTS[spec.kind](spec);
    applyOffsets(result.nodes, spec.offsets);

    var markerId = "dgm-arrow-" + (++uid);
    var parts = ['<svg class="diagram-svg m-enter" viewBox="0 0 ' + W + " " + H +
      '" fill="none" xmlns="http://www.w3.org/2000/svg" role="img">'];
    parts.push('<defs><marker id="' + markerId + '" viewBox="0 0 10 10" refX="8.5" refY="5" ' +
      'markerWidth="7" markerHeight="7" orient="auto-start-reverse">' +
      '<path d="M0 0L10 5L0 10z" fill="var(--fg-muted)"/></marker></defs>');
    result.links.forEach(function (link) { parts.push(linkPath(link, markerId)); });
    if (spec.kind === "funnel") {
      parts.push(renderFunnel(spec, result.nodes));
    } else {
      spec.nodes.forEach(function (n) {
        var p = result.nodes[n.id];
        parts.push('<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
          shapePath(n, p) + labelFO(p, n, nodeText(n)) + "</g>");
      });
    }
    parts.push("</svg>");
    host.innerHTML = parts.join("");
  }

  function renderAll(root) {
    var scope = root || document;
    var hosts = scope.querySelectorAll(".diagram-host");
    for (var i = 0; i < hosts.length; i++) render(hosts[i]);
  }

  var api = {
    render: render, renderAll: renderAll,
    LAYOUTS: LAYOUTS, applyOffsets: applyOffsets, num: num,
    layoutFlowchart: layoutFlowchart, layoutProcess: layoutProcess,
    layoutCycle: layoutCycle, layoutFunnel: layoutFunnel,
    layoutHierarchy: layoutHierarchy, flowRanks: flowRanks,
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
