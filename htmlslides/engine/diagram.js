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
 *
 * ПРАВИЛА СХЕМ (обязательны для каждой новой layout-функции):
 *
 * 1. Блоки выравниваются по сетке. Узлы одного ранга стоят на одной линии: у
 *    колонки общий x, у ряда общий y, шаг между рангами одинаковый. «На глаз»
 *    и индивидуальные координаты под конкретный узел запрещены — проверять
 *    выравнивание надо равенством координат в tests/js/diagram.test.js, а не
 *    просмотром картинки. По какой оси тип держит шаг — объявлено в GRID_AXES
 *    того же теста; у кольца, радиуса, кругов и оси времени сетки нет по сути
 *    типа, и такой тип объявляется там пустой строкой, а не умалчивается.
 *
 * 2. Стрелки — только прямые отрезки и ломаные с ПРЯМЫМИ углами. Дуг, безье и
 *    любых скруглений в линиях нет: ни в layout (никаких arc/curve в link), ни
 *    в drawLink (в d= допустимы только M и L). Колено строится как
 *    [[sx,sy],[mid,sy],[mid,ty],[tx,ty]] по горизонтали или симметрично по
 *    вертикали — см. reroute(), он же единственный пересчёт после ручного
 *    сдвига узла.
 */
(function (global) {
  "use strict";

  var W = 1800, H = 720;

  /* Поле подписи внутри габарита узла (labelFO). Вынесено из labelFO, потому что
     раскладке хаба надо посчитать габарит ОТ желаемой ширины текста, то есть
     знать это поле заранее. */
  var LABEL_PAD = 10;

  /* Подложка зоны схемы — точечная сетка. Шаг, диаметр и фаза сняты пиксельным
   * замером с нативного рендера макетов, не на глаз. В Figma это растровая
   * картинка, растянутая под 1800×780, поэтому измеренный шаг нецелый (29.44) —
   * берём исходные 30. Ядро точки 4 px → r = 2. Фаза — смещение центра ПЕРВОЙ
   * точки от левого верхнего угла холста (замер слайд (61.5, 251.5) при зоне,
   * начинающейся в (60, 240)).
   *
   * Сетка стоит НЕ у всех типов. Раньше она была общей — я счёл расхождение в
   * макетах следом работы. Это была ошибка: сетка читается как пустое поле, по
   * которому фигура плавает, и смысл у неё есть ровно там, где фигура зону не
   * заполняет (треугольник пирамиды, ступени воронки, цепочка процесса). У
   * матрицы, Венна, плана-графика и лестницы плашки занимают всю зону, точки
   * лезут в щели между ними и в просветы заливок — получается не подложка, а
   * шум поверх геометрии. Поэтому список исключений ниже, а не общее правило. */
  var GRID_STEP = 30, GRID_DOT = 2, GRID_PHASE = [1.5, 11.5];
  var NO_GRID = { matrix: 1, venn: 1, gantt_lite: 1, steps: 1 };

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

  /* Высота нарисованного ромба развилки: выше коробки, иначе подпись не влезает
     в узкую середину. Одна формула на раскладку и на отрисовку. */
  function diamondH(h) { return Math.min(h + 44, h * 1.7); }

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
        /* Ромб рисуется ВЫШЕ своей коробки — иначе подпись не влезала бы в узкую
           середину. Храним нарисованную высоту рядом с коробкой: раскладка и
           якоря рёбер живут по h, а всё, что обходит фигуру глазами (подписи
           связей), обязано мерить hDraw. */
        if (nodes[b.idx].shape === "decision") pos[b.id].hDraw = diamondH(h);
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
    /* Замер с макета: плашка 300×120, зазор 75 — пять штук ровно закрывают всю
       ширину зоны (5·300 + 4·75 = 1800). Прежние 320×150 с зазором 72 в эту
       арифметику не складывались, и ряд то не доставал до края, то упирался. */
    var gap = 75;
    var w = Math.min(300, (W - gap * (perRow - 1)) / perRow);
    var h = Math.min(120, rows === 1 ? 170 : H / 2 - 90);
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
    var ry = Math.min(H / 2 - 70, 265), rx = Math.min(W / 2 - 240, 640);
    var perim = Math.PI * (3 * (rx + ry) -
      Math.sqrt((3 * rx + ry) * (rx + 3 * ry)));   // приближение Рамануджана
    var pos = {};
    nodes.forEach(function (node, i) {
      var a = -Math.PI / 2 + (2 * Math.PI * i) / n;   // старт сверху, по часовой
      pos[node.id] = {
        // От девяти этапов плашки жмутся на боках эллипса — иначе стрелка между
        // ними снова сжимается в царапину. 390×84 — пропорции плашки из макета
        // (416×80 на кадре 1920), пересчитанные на холст 1800.
        x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a),
        w: Math.min(390, perim / n - 40), h: n > 8 ? 74 : 84, angle: a,
      };
    });
    /* Связи — по макету (Slide decks, 146:982): ортогональное «колено» вместо
       дуги, угол СНАРУЖИ эллипса — x угла от бокового узла, y от верхнего или
       нижнего. Стрелки на ОБОИХ концах, как нарисовано в макете. */
    var PAD = 10;    // зазор между наконечником и краем плашки
    var ROOM = 34;   // PAD + место под сам наконечник и кусочек линии
    function elbow(A, B) {
      var sx = B.x > A.x ? 1 : -1, sy = B.y > A.y ? 1 : -1;
      if (Math.abs(A.x - B.x) < 1)        // соседи в одной колонке: вертикаль
        return [[A.x, A.y + sy * (A.h / 2 + PAD)],
                [B.x, B.y - sy * (B.h / 2 + PAD)]];
      if (Math.abs(A.y - B.y) < 1)        // в одном ряду: горизонталь
        return [[A.x + sx * (A.w / 2 + PAD), A.y],
                [B.x - sx * (B.w / 2 + PAD), B.y]];
      var aV = Math.abs(A.y - cy) >= Math.abs(B.y - cy);  // кто ближе к полюсу
      var v = aV ? A : B, s = aV ? B : A;
      /* Колену не хватает места (от девяти этапов соседние плашки почти в одном
         ряду) — честная прямая между краями, как у графа связей. */
      if (Math.abs(v.x - s.x) < v.w / 2 + ROOM ||
          Math.abs(v.y - s.y) < s.h / 2 + ROOM) return trimSegment(A, B);
      return aV
        ? [[A.x + sx * (A.w / 2 + PAD), A.y], [B.x, A.y],
           [B.x, B.y - sy * (B.h / 2 + PAD)]]
        : [[A.x, A.y + sy * (A.h / 2 + PAD)], [A.x, B.y],
           [B.x - sx * (B.w / 2 + PAD), B.y]];
    }
    var links = [];
    for (var i = 0; i < n; i++) {
      var A = pos[nodes[i].id], B = pos[nodes[(i + 1) % n].id];
      links.push({
        from: nodes[i].id, to: nodes[(i + 1) % n].id, label: "", style: "solid",
        both: true, points: elbow(A, B),
      });
    }
    return { nodes: pos, links: links };
  }

  /* Воронка. Трапеций больше нет: в макете это четыре отдельных ПРЯМОУГОЛЬНИКА
   * равной высоты, сходящихся лесенкой, — ни одной косой кромки (правило схем),
   * и подпись стоит в прямом поле, а не в сужающемся клине.
   *
   * Два решения, которые выглядят как потеря, но ими и являются сознательно:
   *
   * 1) Ширина больше НЕ пропорциональна числу. В макете 12 000 / 3 400 / 1 100 /
   *    280 нарисованы ширинами 1136 / 930 / 736 / 544 — ровный линейный сход по
   *    −197 на ступень. Пропорция дала бы 1136 / 322 / 104 / 26: последние две
   *    ступени схлопнулись бы в полоски, в которые не влезает ни подпись, ни
   *    число. Прежний код это и ловил — упирался в пол wMin, то есть всё равно
   *    рисовал ширину, которой в данных нет. Честнее не притворяться: величину
   *    несёт ЧИСЛО в правой колонке, а ступенька — только порядок.
   * 2) Колонка ступеней центрирована не по холсту, а по x = 629: справа
   *    отрезана полоса под числа, выключенные вправо по x = 1765.
   *
   * Замеры: плашка 1136…544 × 142, шаг 153, колонка чисел кеглем 40. */
  var FUNNEL_CX = 629, FUNNEL_H = 142, FUNNEL_STEP = 153;
  var FUNNEL_WMAX = 1136, FUNNEL_WMIN = 544;
  var FUNNEL_NUM_X = 1765, FUNNEL_NUM_FS = 40, FUNNEL_NUM_DY = 43;

  function layoutFunnel(spec) {
    var nodes = spec.nodes, n = nodes.length;
    var step = Math.min(FUNNEL_STEP, H / Math.max(1, n));
    var h = Math.max(40, Math.min(FUNNEL_H, step - 11));
    var top = (H - (h + (n - 1) * step)) / 2;
    var pos = {};
    nodes.forEach(function (nd, i) {
      var w = n > 1
        ? FUNNEL_WMAX - (i * (FUNNEL_WMAX - FUNNEL_WMIN)) / (n - 1)
        : FUNNEL_WMAX;
      pos[nd.id] = { x: FUNNEL_CX, y: top + i * step + h / 2, w: w, h: h };
    });
    return { nodes: pos, links: [] };
  }

  /* Линейка-выноска: по ВЕРХНЕЙ кромке ступени от её левого края до правого
     края колонки чисел. Она и есть связь «ступень → число»; стрелок в макете
     нет вовсе. Живёт в декоре, потому что уходит далеко за плашку — рисовать
     её внутри узла значило бы, что при перетаскивании ступени мышью линейка
     уехала бы вместе с ней за край холста. */
  function decorFunnel(spec, result) {
    return spec.nodes.map(function (nd) {
      var p = result.nodes[nd.id];
      if (!p) return "";
      var y = p.y - p.h / 2;
      return '<line x1="' + (p.x - p.w / 2) + '" y1="' + y + '" x2="' + FUNNEL_NUM_X +
        '" y2="' + y + '" stroke="var(--fg-muted)" stroke-width="1" opacity="0.5"/>';
    }).join("");
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
   * верх-право, низ-лево, низ-право); оси рисует decorMatrix.
   *
   * Квадранты больше не доли холста (было 560×220 на 26%/74%), а замер макета:
   * 880×309 с зазорами 40 по x и 41 по y. Числа подобраны так, что сетка 2×2
   * занимает холст ЦЕЛИКОМ (920 + 880 = 1800 по ширине, 411 + 309 = 720 по
   * высоте) — то есть квадрант это и есть четверть зоны, а не плашка внутри
   * неё. Прежние 560×220 оставляли по краям пустые поля, и матрица выглядела
   * как четыре подписи в воздухе, а не как поделённое поле.
   *
   * Зазоры 40/41 держим ПОСТОЯННЫМИ: в них стоят оси (см. decorMatrix), и ось
   * обязана проходить между квадрантами, а не по ним.
   *
   * По ширине сетка занимает холст без остатка, а по высоте прижата к НИЗУ:
   * сверху остаётся полоса 61 px, и это не промах замера, а место под подпись
   * вертикальной оси — она стоит над острием стрелки, то есть выше верхней
   * кромки квадрантов. Прижми сетку к верху, и подпись «Эффект» легла бы на
   * плашку. */
  var MTX_W = 880, MTX_H = 309, MTX_GAP_X = 40, MTX_GAP_Y = 41;
  var MTX_FS = 30, MTX_PAD = 32, MTX_AXIS_FS = 24;
  var MTX_TOP = H - (2 * MTX_H + MTX_GAP_Y);   // 61

  function layoutMatrix(spec) {
    var pos = {};
    var cxs = [MTX_W / 2, MTX_W + MTX_GAP_X + MTX_W / 2];
    var cys = [MTX_TOP + MTX_H / 2, MTX_TOP + MTX_H + MTX_GAP_Y + MTX_H / 2];
    spec.nodes.forEach(function (n, i) {
      pos[n.id] = { x: cxs[i % 2], y: cys[i < 2 ? 0 : 1], w: MTX_W, h: MTX_H };
    });
    return { nodes: pos, links: [] };
  }

  /* Подпись квадранта прижата к его ВНЕШНЕМУ углу (верх-лево → левый верхний и
     так далее), а не центрирована. Это не оформление: внешний угол каждого
     квадранта — тот, что дальше всего от перекрестья осей, то есть подпись
     стоит там, где квадрант «самый свой», и четыре подписи расходятся от
     центра, а не сбиваются к нему в кучу. Центрированные подписи в макете
     читались бы как один блок текста с крестом поверх.
     Кегль 30 — замер; отступ от угла 32 (в макете 31–35 вручную). */
  function renderMatrix(spec, pos) {
    return spec.nodes.map(function (n, i) {
      var p = pos[n.id];
      if (!p) return "";
      var left = i % 2 === 0, top = i < 2;
      var x = p.x - p.w / 2, y = p.y - p.h / 2;
      var fs = fitFont(n.label, p.w - 2 * MTX_PAD, p.h - 2 * MTX_PAD, MTX_FS);
      return '<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
        '<rect x="' + x + '" y="' + y + '" width="' + p.w + '" height="' + p.h +
          '" fill="' + (n.accent ? "var(--accent)" : "var(--bg-card)") + '"/>' +
        '<foreignObject x="' + (x + MTX_PAD) + '" y="' + (y + MTX_PAD) +
          '" width="' + (p.w - 2 * MTX_PAD) + '" height="' + (p.h - 2 * MTX_PAD) + '">' +
        '<div xmlns="http://www.w3.org/1999/xhtml" style="width:100%;height:100%;' +
        'display:flex;align-items:' + (top ? "flex-start" : "flex-end") +
        ';justify-content:' + (left ? "flex-start" : "flex-end") +
        ';text-align:' + (left ? "left" : "right") + ';overflow:hidden;' +
        'font-size:' + fs + 'px;line-height:1.15;letter-spacing:-.3px;color:' +
        nodeText(n) + ';"><div style="min-width:0;max-width:100%;' +
        'overflow-wrap:break-word;">' + esc(n.label || "") +
        "</div></div></foreignObject></g>";
    }).join("");
  }

  /* Пирамида. Раньше это была стопка трапеций-плашек с зазором 8 и своей
   * заливкой у каждой — то есть пирамида «из кирпичей». В макете фигура одна:
   * сплошной треугольник, а ярусы отбиты белыми линиями в 1 px, вставленными
   * внутрь контура. Разница не косметическая: стопка плашек заявляет, что
   * ярусы — отдельные сущности, а монолит с насечками — что это ОДНО целое,
   * поделённое на уровни. Второе и есть смысл пирамиды.
   *
   * Замеры макета: апекс (900, 25), основание 1754 на y = 750, высота 725.
   * Над первым разделителем остаётся неподписанная шапка 99, под последним —
   * сливер 24; между ними n равных полос (при n = 4 выходит 150.75 — макет
   * мерил 150/152/150/151, руками).
   *
   * Основание уходит на 30 ниже холста СОЗНАТЕЛЬНО: так в макете, а фигура там
   * сплошная и непрозрачная — за этими 30 px нет ничего, кроме фона зоны, и ни
   * одна подпись туда не попадает (поля ярусов подрезаны холстом, см. ниже).
   * Поднять основание до 720 значило бы сплющить треугольник на 4% и разойтись
   * с макетом по ВСЕМ разделителям сразу, а не только по последнему.
   *
   * Шапку и сливер держим ПОСТОЯННЫМИ, а не долей высоты: от них зависит, как
   * выглядит кончик фигуры, и он обязан быть одинаковым и на трёх уровнях, и
   * на шести — иначе при добавлении яруса меняется вся фигура, а не разметка.
   *
   * Раскладка отдаёт только поля подписей (x по оси, w по ширине полосы), сам
   * треугольник и разделители рисует decorPyramid. */
  /* Вершина на верхней кромке зоны, основание — на нижней: фигура вписана В
     ОБЛАСТЬ ПАТТЕРНА, а не положена поверх неё. Раньше основание стояло на 750
     при холсте 720 — нижние 30 px треугольника уезжали за viewBox, и фигура
     выглядела обрезанной снизу, хотя в макете у неё есть основание. В макете
     высота треугольника ровно 720 (замер: вершина на зонной 26, основание на
     746) — ту же высоту и берём, просто поднимаем на 26, потому что у нас
     подложка кончается вместе с зоной, а в Figma уходит ниже, под колонтитул. */
  var PYR_APEX_Y = 0, PYR_BASE_Y = 720, PYR_BASE_W = 1754;
  var PYR_CAP = 99, PYR_SLIVER = 24, PYR_INSET = 32;
  var PYR_FS = 32, PYR_LABEL_W = 583;

  /* Ширина треугольника на высоте y (вне фигуры — 0). */
  function pyrWidthAt(y) {
    var t = (y - PYR_APEX_Y) / (PYR_BASE_Y - PYR_APEX_Y);
    return Math.max(0, Math.min(1, t)) * PYR_BASE_W;
  }

  /* Границы ярусов по y: n + 1 значение, сверху вниз. Верхняя отступает от
     вершины на PYR_CAP, нижняя не доходит до основания на PYR_SLIVER — у
     фигуры обязаны остаться видимый остриё и видимая подошва, иначе верхний
     разделитель читается как срез пирамиды, а нижний сливается с основанием.
     Замер макета в зонных координатах: разделители на 122, 273, 425, 575, 725
     при вершине 26 и основании 746, то есть шапка 96 и сливер 21 — наши 99 и
     24 попадают в тот же замер. */
  function pyrCuts(n) {
    var top = PYR_APEX_Y + PYR_CAP, bottom = Math.min(H, PYR_BASE_Y - PYR_SLIVER);
    var step = (bottom - top) / Math.max(1, n), cuts = [];
    for (var i = 0; i <= n; i++) cuts.push(top + i * step);
    return cuts;
  }

  function layoutPyramid(spec) {
    var nodes = spec.nodes, cuts = pyrCuts(nodes.length);
    var pos = {};
    nodes.forEach(function (nd, i) {
      var a = cuts[i], b = cuts[i + 1];
      /* Ширина 583 (замер) — но не шире полосы по её НИЖНЕЙ, самой широкой
         кромке: у верхних ярусов фигура ещё узкая, и подпись, набранная на
         полную 583, вылезла бы за контур обоими концами. */
      pos[nd.id] = { x: W / 2, y: (a + b) / 2, h: b - a,
                     w: Math.max(200, Math.min(PYR_LABEL_W, pyrWidthAt(b) - 24)) };
    });
    return { nodes: pos, links: [] };
  }

  /* Треугольник ЦЕЛИКОМ, одной заливкой, и белые разделители поверх. Шкалу
     --chart-N здесь пробовали — каждый ярус своим тоном — и это оказалось
     ошибкой сразу по двум причинам. Во-первых, в макете пирамида однотонная:
     ярус задаётся насечкой, а не цветом, и ступенчатая заливка превращает
     фигуру из пирамиды в стопку трапеций. Во-вторых, полосы рисовались от
     первого разделителя, а не от вершины, — остриё просто не выводилось, и
     верх фигуры выходил срезанным.

     Исключение одно: ярус, помеченный accent, получает --accent. Это не
     градация ряда, а указатель «вот этот уровень», и он работает только пока
     остальные ярусы нейтральны. */
  function decorPyramid(spec) {
    var nodes = spec.nodes, cuts = pyrCuts(nodes.length), cx = W / 2;
    var half = PYR_BASE_W / 2, out = "";
    out += '<polygon points="' + cx + "," + PYR_APEX_Y + " " +
      (cx + half) + "," + PYR_BASE_Y + " " + (cx - half) + "," + PYR_BASE_Y +
      '" fill="var(--bg-card)"/>';
    nodes.forEach(function (n, i) {
      if (!n.accent) return;
      /* Крайние ярусы заливаем до самой кромки силуэта (остриё и подошва), а
         не до разделителя: иначе у акцентного верхнего яруса над заливкой
         торчал бы тёмный кончик, а у нижнего под ней — тёмная полоска. */
      var a = i === 0 ? PYR_APEX_Y : cuts[i];
      var b = i === nodes.length - 1 ? PYR_BASE_Y : cuts[i + 1];
      var wa = pyrWidthAt(a) / 2, wb = pyrWidthAt(b) / 2;
      out += '<polygon points="' + (cx - wa) + "," + a + " " + (cx + wa) + "," + a +
        " " + (cx + wb) + "," + b + " " + (cx - wb) + "," + b +
        '" fill="var(--accent)"/>';
    });
    /* Разделитель уже треугольника на PYR_INSET с каждой стороны: доведённый
       до самой кромки он читался бы как разрез фигуры, а вставленный внутрь —
       как насечка на ней. Цвет --fg (в тёмной теме белый, как в макете; в
       светлой графит — иначе белым по #F2F2F2 насечки просто не было бы). */
    cuts.forEach(function (y) {
      var half = Math.max(0, pyrWidthAt(y) / 2 - PYR_INSET);
      if (half <= 0) return;
      out += '<line x1="' + (cx - half) + '" y1="' + y + '" x2="' + (cx + half) +
        '" y2="' + y + '" stroke="var(--fg)" stroke-width="1"/>';
    });
    return out;
  }

  /* Хаб и лучи. Раньше лучи стояли по эллипсу вокруг плашки-центра — так эту
   * схему рисуют все генераторы, и ровно поэтому она выбивалась из набора: в
   * макетах Figma нет ни эллипса, ни плашки у центра. Там композиция строго
   * прямоугольная: тёмная панель во всю зону, ИМЯ хаба набрано крупным зелёным
   * текстом по центру (без подложки — это заголовок, а не узел), а лучи стоят
   * двумя рядами подписей, сверху и снизу, и соединены с хабом короткими
   * ВЕРТИКАЛЬНЫМИ стрелками. Ни одной косой линии — то самое правило схем.
   *
   * Числа: ряды подписей на 105 от верхнего и нижнего края (симметрично),
   * высота подписи 80; хаб — блок 1560×140 по центру; стрелки от ±110 от
   * центра до самой границы подписи (замер с макета: 250→145 и 470→575).
   * Колонки распределены равномерно по ширине зоны — centre = W*(i+½)/k. В
   * макете они стоят чуть теснее (шаг 492 вместо 600), но там их расставили
   * руками по длине конкретной надписи «Cloud.ru Evolution»; равномерный шаг
   * — правило, которое держится при любом числе лучей и ложится на сетку. */
  /* Имя хаба тянется ОТ крайней левой стрелки ДО крайней правой: стрелка обязана
     выходить из слова, а не из воздуха рядом с ним. Поэтому кегль здесь не
     константа, а следствие — длинное имя набирается мельче, короткое крупнее
     (см. hubFont). Потолок задаёт высота блока: между концами стрелок
     (H/2 ± HUB_GAP) всего 220 px, и 200 — всё, что можно занять, не налезая на
     их хвосты. Короткое имя упрётся в него и до стрелок чуть-чуть не дотянет —
     это физический предел, а не недоработка подгонки. */
  var HUB_BOX_H = 200, HUB_FS_MIN = 40;
  var HUB_SPOKE_FS = 28;     // подписи лучей: без плашки, поэтому крупнее NODE_FS
  var HUB_ROW_Y = 105, HUB_ROW_H = 80, HUB_GAP = 110;

  function layoutHubSpoke(spec) {
    var nodes = spec.nodes;
    var hub = nodes[0], spokes = nodes.slice(1), m = spokes.length;
    var pos = {};
    /* Верхний ряд получает лишний луч при нечётном числе: так ряды отличаются
       максимум на один, а не на половину. Колонки общие для обоих рядов —
       короткий ряд центрируется внутри той же сетки (сдвиг на полколонки),
       иначе при 5 лучах низ прижимался бы влево дырой справа. */
    var top = Math.ceil(m / 2), bottom = m - top, k = Math.max(1, top);
    function colX(i, count) { return (W * (i + (k - count) / 2 + 0.5)) / k; }
    /* Ширина блока хаба = пролёт между крайними колонками ПЛЮС поля labelFO:
       строке должен достаться ровно пролёт, тогда её концы придутся на стрелки.
       На одной колонке пролёта нет (обе стрелки в центре), выравнивать не к
       чему — тогда даём имени всю панель и упираемся в потолок по высоте. */
    pos[hub.id] = { x: W / 2, y: H / 2, h: HUB_BOX_H,
                    w: (k > 1 ? colX(k - 1, k) - colX(0, k) : 1540) + 2 * LABEL_PAD };
    spokes.forEach(function (n, i) {
      var up = i < top;
      var idx = up ? i : i - top;
      pos[n.id] = { x: colX(idx, up ? top : bottom),
                    y: up ? HUB_ROW_Y : H - HUB_ROW_Y,
                    w: W / k - 40, h: HUB_ROW_H, hubUp: up };
    });
    /* Стрелка всегда идёт ОТ хаба К лучу (голова у подписи) и строго по
       вертикали — поэтому точки считаем сами, а не trimSegment'ом: тот ведёт
       линию по прямой между центрами, то есть наискосок. Заданные рёбра
       уважаем только как список связей; геометрию задаёт ряд. */
    var edges = (spec.edges && spec.edges.length) ? spec.edges
      : spokes.map(function (n) { return { from: hub.id, to: n.id }; });
    var links = edges.map(function (e) {
      var s = pos[e.from], t = pos[e.to];
      var spoke = t.hubUp == null ? s : t;
      var up = spoke.hubUp !== false;
      var x = spoke.x;
      var points = up
        ? [[x, H / 2 - HUB_GAP], [x, spoke.y + spoke.h / 2]]
        : [[x, H / 2 + HUB_GAP], [x, spoke.y - spoke.h / 2]];
      return { from: e.from, to: e.to, label: e.label || "", style: e.style || "solid",
               points: points };
    });
    return { nodes: pos, links: links };
  }

  /* Венн. Круги убраны: в макетах пересечение множеств нарисовано
   * ПРЯМОУГОЛЬНИКАМИ, наезжающими друг на друга со сдвигом вправо-вниз. Это не
   * вкусовщина — круг это кривая, а правило схем (см. шапку) кривых не
   * допускает; заодно подписи получают прямые поля вместо линзы, в которую
   * текст никогда толком не влезал.
   *
   * Числа сняты с макета для двух множеств: 1200×600, сдвиг 600 вправо и ~55
   * вниз. Отсюда правило: по горизонтали соседи перекрываются ровно наполовину,
   * то есть wr + (n−1)·wr/2 = W. Для трёх множеств половина не годится —
   * тройная общая часть вырождается в линию нулевой ширины, и «пересечение
   * всех» рисовать негде; там перекрытие две трети, общая часть выходит 360 px.
   * По вертикали шаг фиксированный (60), высота 600 подобрана так, чтобы стопка
   * из трёх ровно уложилась в зону: 600 + 2·60 = 720 = H. */
  var VENN_H = 600, VENN_VSTEP = 60;
  /* Подписи множеств в макете набраны 36-м — крупнее плашечных 24: плашки тут
     нет, подпись стоит на пустом поле и работает заголовком области. */
  var VENN_FS = 36;

  function layoutVenn(spec) {
    var nodes = spec.nodes, n = nodes.length;
    var f = n >= 3 ? 2 / 3 : 1 / 2;                    // доля перекрытия соседей
    var wr = n > 1 ? W / (1 + (n - 1) * (1 - f)) : W;
    var step = wr * (1 - f);
    var top = (H - (VENN_H + (n - 1) * VENN_VSTEP)) / 2;
    var pos = {};
    nodes.forEach(function (nd, i) {
      var cx = i * step + wr / 2, cy = top + i * VENN_VSTEP + VENN_H / 2;
      /* Подпись стоит в СОБСТВЕННОЙ полосе множества — там, куда не залез
         сосед. Иначе «Скорость разработки» легла бы на общую часть, и слайд
         утверждал бы то, чего в данных нет. У среднего из трёх своей полосы
         нет вовсе (его обступили с обеих сторон) — его подпись уходит под
         верхний край прямоугольника, туда, где пусто и где её не спутать с
         подписью пересечения. Смещения ОТНОСИТЕЛЬНЫЕ: узел можно утащить
         мышью, подпись поедет вместе с ним. */
      var own = n === 1 ? [0, W]
        : i === 0 ? [0, step]
        : i === n - 1 ? [(i - 1) * step + wr, W]
        : [(i - 1) * step + wr, (i + 1) * step];
      var has = own[1] - own[0] > 80;
      pos[nd.id] = {
        x: cx, y: cy, w: wr, h: VENN_H,
        labelDx: has ? (own[0] + own[1]) / 2 - cx : 0,
        /* Все подписи — на ОДНОЙ горизонтали по центру зоны (так и в макете).
           По центру своего прямоугольника они вставали лесенкой вслед за
           сдвигом стопки, и схема читалась как съехавшая, хотя всё на месте. */
        labelDy: has ? H / 2 - cy : -(VENN_H / 2 - 80),
        labelW: (has ? own[1] - own[0] : wr) - 80,
      };
    });
    return { nodes: pos, links: [] };
  }

  /* Дорожки процесса: ряды по исполнителям (lane), колонки по рангам шагов.
   * Коллизии (ранг, дорожка) разводим вправо, чтобы шаги не слипались.
   *
   * Замеры макета: плашка 300×100 (радиус 0), подпись дорожки 24 px у левого
   * края ВНУТРИ полосы со сдвигом +10 вниз от её верхней линии, первая плашка
   * начинается на x = 240 — то есть колонка подписей не отдельная колонка, а
   * зарезервированное поле в начале каждой дорожки.
   *
   * Высота дорожки в макете 220 при трёх дорожках (сверху остаётся 60 px
   * пустых). Мы вместо этого делим зону поровну: H / n. Фиксированные 220 либо
   * не влезают (с четырёх дорожек это 880 при зоне 720), либо оставляют
   * четверть зоны пустой (при двух — 280 px), а число дорожек задаёт автор, и
   * схема обязана собираться при любом. На трёх дорожках наша полоса выходит
   * 240 против 220 макета — выше на 8%, структура та же. */
  var SWIM_LEFT = 240, SWIM_W = 300, SWIM_NODE_H = 100;
  var SWIM_LABEL_W = 200, SWIM_LABEL_DY = 10, SWIM_TIP_GAP = 10;

  function layoutSwimlanes(spec) {
    var nodes = spec.nodes, edges = spec.edges || [];
    var lanes = laneList(nodes);
    var laneH = H / lanes.length;
    var labelW = SWIM_LEFT;
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
    var colW = (W - labelW) / (maxRank + 1);
    var w = Math.min(SWIM_W, colW - 24), h = Math.min(SWIM_NODE_H, laneH - 40);
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
          ? [[s.x + s.w / 2, s.y], [t.x - t.w / 2 - SWIM_TIP_GAP, t.y]]
          : backRoute(s, t, false);
      } else if (t.x > s.x) {
        /* Колено из ДВУХ отрезков, а не из трёх: вниз (или вверх) из середины
           кромки источника до высоты приёмника, затем вправо в его левый край.
           Было колено через середину промежутка — три отрезка и два угла, и
           вертикальный участок висел в пустоте между колонками, не привязанный
           ни к одной плашке. В макете все четыре коннектора — ровно два
           отрезка, и вертикаль выходит из центра плашки-источника: так видно,
           КТО передаёт работу, а не просто что стрелка идёт в соседний ряд. */
        var edgeY = t.y > s.y ? s.y + s.h / 2 : s.y - s.h / 2;
        points = [[s.x, edgeY], [s.x, t.y], [t.x - t.w / 2 - SWIM_TIP_GAP, t.y]];
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
     предыдущей (каскад). Линейку и сетку рисует decorGantt.
     Замеры макета: линии шкалы от y = 100 до низа холста, первая полоса на
     y = 180, высота полосы 83, шаг 114, зазор ~31. */
  var GANTT_TOP = 100;                      // верх линий шкалы
  var GANTT_BAR_TOP = 180;                  // верх первой полосы
  var GANTT_BAR_H = 83, GANTT_GAP = 31;
  var GANTT_FS = 24, GANTT_NUM_Y = 57;

  function layoutGantt(spec) {
    var nodes = spec.nodes, n = nodes.length;
    /* Каскад прижат и к верхней, и к НИЖНЕЙ кромке зоны: в макете первая
       полоса начинается на 180, последняя заканчивается ровно на 720. Поэтому
       шаг считается из размаха (а не берётся константой 114): при пяти работах
       он сам выходит 114.25, при восьми сжимается, и график всегда занимает
       поле целиком, а не обрывается на середине.
       Высота полосы уступает зазору: сперва режем полосу (сохраняя просвет 31,
       без которого соседние работы слипаются в сплошную заливку), и только
       когда резать уже нечего — на очень длинных списках — упираемся в пол.
       Шаг при этом ограничен сверху: три работы на 540 px дали бы просвет
       в полторы полосы, и каскад рассыпался бы на отдельные плашки; в таком
       случае стопку центрируем по полю. */
    var span = H - GANTT_BAR_TOP;
    var barH = Math.max(24, Math.min(GANTT_BAR_H, (span - (n - 1) * GANTT_GAP) / n));
    var pitch = n > 1
      ? Math.min(barH + 2 * GANTT_GAP, (span - barH) / (n - 1)) : 0;
    var top = GANTT_BAR_TOP + (span - ((n - 1) * pitch + barH)) / 2;
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
        y: top + i * pitch + barH / 2,
        /* Ширина РОВНО по периодам, без отъёма 14 px на просвет. Полоса в
           плане-графике — это отрезок времени, и её край обязан лежать на
           линии шкалы: с отъёмом работа «первый-второй месяц» кончалась за
           7 px до линии второго, то есть врала на четверть колонки при
           двухмесячной работе. Соседние полосы и так разведены по строкам. */
        w: durs[i] * unit, h: barH,
      };
    });
    return { nodes: pos, links: [], unit: unit, total: total };
  }

  /* Лестница. Была лесенка из одинаковых плашек, каждая со своим y: ступень
   * висела в воздухе, под ней рисовался полупрозрачный «подступенок», и высота
   * ничего не значила — весь рост читался только по сдвигу вверх.
   * В макете это СТОЛБИКИ С ОБЩИМ ОСНОВАНИЕМ: все прижаты низом к кромке зоны
   * и отличаются ровно высотой (180 / 330 / 480 / 630 — прибавка 150 на
   * ступень). Разница смысловая: у столбика высота — величина, её видно и
   * можно сравнить взглядом; у висящей плашки сравнивать нечего, кроме
   * положения. Заодно исчезает подступенок — отдельная фигура, которую
   * приходилось вводить, чтобы связать плашку с основанием.
   *
   * Прибавку считаем из размаха, а не берём 150 константой: при четырёх
   * ступенях она сама выходит 150, при шести — ужимается, и самый высокий
   * столбик всегда упирается в верх поля, а не вылезает за него.
   * Низ первой ступени не нулевой (STEP_H0 = 180): столбик высотой 0 — это не
   * «мало», это «нет», а первый уровень зрелости существует. */
  var STEP_GAP = 9, STEP_H0 = 180, STEP_RISE = 150;
  /* STEP_LABEL_H — положение подписи (верх столбика + 28, бокс 80, текст по его
     центру), STEP_LABEL_BOX — БЮДЖЕТ высоты, который получает подборщик кегля.
     Они разные нарочно: в бокс 80 две строки по 36 не влезают, и fitFont ронял
     кегль до 24, набирая «Автоматизация отчётности» одной мелкой строкой —
     вместо двух крупных, как в макете. Подпись ступени и должна быть набрана
     36-м: это единственное содержимое широкой колонки, и мельчить его ради
     того, чтобы уложиться в одну строку, значит экономить на главном. */
  var STEP_FS = 36, STEP_PAD = 32, STEP_LABEL_DY = 28;
  var STEP_LABEL_H = 80, STEP_LABEL_BOX = 112;
  var STEP_CAP_FS = 24, STEP_CAP_Y = 18;

  function layoutSteps(spec) {
    var nodes = spec.nodes, n = nodes.length;
    var pitch = W / n;                       // 452 при четырёх — замер макета
    var w = pitch - STEP_GAP;
    var rise = n > 1 ? Math.min(STEP_RISE, (H - STEP_H0) / (n - 1)) : 0;
    var pos = {};
    nodes.forEach(function (nd, i) {
      var h = STEP_H0 + i * rise;
      pos[nd.id] = { x: i * pitch + w / 2, y: H - h / 2, w: w, h: h };
    });
    return { nodes: pos, links: [], pitch: pitch };
  }

  /* Ментальная карта: первый узел — центр, ветви расходятся вправо и влево
     через одну. Дерево строим обходом в ширину от центра: второй родитель и
     кольцо («b→a» при «a→b») в дерево просто не попадают — раскладка обязана
     пережить данные, которые схема бы завернула (в собранной деке истина —
     HTML). Узел, до которого обход не дошёл, становится ветвью центра.

     Замеры с макета: центр 336×335 (крупный зелёный блок, подпись набрана
     заголовочным кеглем в две строки), ветвь — плашка высотой 70. */
  var MIND_ROOT_W = 336, MIND_ROOT_H = 335, MIND_ROOT_FS = 40;
  var MIND_H = 70, MIND_ROW = 180;

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
    /* Просвет между колонками = colW - w. Без подписей хватает 40px на стрелку,
       но подписанная ветвь требует места под БЛОК текста: в просвете 40 от «при
       положительном решении» оставалось «при…», хотя на холсте было куда
       подвинуться. Место закладываем в РАСКЛАДКУ (как у графа), сужая плашки —
       ниже 200px не опускаемся, иначе спасённая подпись ветви съела бы подписи
       самих узлов. Сужаем только когда подписи есть: карта без них должна
       остаться с прежними широкими плашками. */
    var reserve = (spec.edges || []).some(function (e) { return e.label; })
      ? EDGE_W : 40;
    /* Пол ширины: на глубокой карте колонка сама по себе уже 175px, и вычесть из
       неё 200 значило бы получить отрицательную плашку. Пол не выше прежней
       ширины без подписей — иначе плашки соседних колонок сомкнулись бы. */
    var minW = Math.min(200, colW - 40);
    var w = Math.max(Math.min(320, colW - reserve), minW), h = MIND_H;
    var pos = {};
    /* Корень — крупный зелёный блок почти в полвысоты зоны (замер с макета:
       336×335), а не плашка чуть выше ветви. Ширину приходится зажимать: она
       съедает просвет до первой колонки, а колонки стоят на жёстком шаге colW
       (от него зависит сетка). Два потолка: 2·colW − w − 80 — ширина, при
       которой между корнем и ветвью остаётся 40 с каждой стороны; colW −
       reserve — прежняя мерка под подпись ветви (у подписанной карты просвет
       нужен не меньше, чем был). Floor minW не даёт уйти в ноль на глубокой
       карте и сам по себе не шире первого потолка, поэтому шаг колонок
       остаётся ровным и сетка цела. */
    var rootW = Math.max(minW,
      Math.min(MIND_ROOT_W, colW - reserve, 2 * colW - w - 80));
    [1, -1].forEach(function (dir) {
      /* Ветви центра расходятся через одну: чётные — вправо, нечётные — влево. */
      var branches = children[root.id].filter(function (id, i) {
        return (i % 2 ? -1 : 1) === dir;
      });
      var total = branches.reduce(function (s, id) { return s + leaves(id); }, 0) || 1;
      /* Шаг рядов ограничен сверху (MIND_ROW), а стопка центрируется по зоне.
         Раньше ряды растягивались на всю высоту: при двух ветвях они уезжали к
         самым краям, и даже крупный центр не доставал до них гранью — каждая
         стрелка получала лишнее колено на ровном месте. В макете ряды стоят
         тесно, напротив центра, и стрелки идут прямо. */
      var slot = Math.min(H / total, MIND_ROW);
      var cursor = { y: (H - total * slot) / 2 };
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
    /* Высота центра — не только замер, но и обязательство: гранью он обязан
       доставать до КАЖДОЙ ветви первого уровня, иначе прямая стрелка снова
       превратится в колено. Считаем по самой далёкой ветви (симметрично, центр
       стоит на H/2), пол — замеренные 335, потолок — зона минус воздух. */
    var reach = 0;
    children[root.id].forEach(function (id) {
      if (pos[id]) reach = Math.max(reach, Math.abs(pos[id].y - H / 2));
    });
    pos[root.id] = { x: W / 2, y: H / 2, w: rootW,
                     h: Math.min(H - 40, Math.max(MIND_ROOT_H, 2 * reach + 24)) };

    var byPair = {};
    (spec.edges || []).forEach(function (e) { byPair[e.from + ">" + e.to] = e; });
    var links = [];
    Object.keys(children).forEach(function (pid) {
      children[pid].forEach(function (cid) {
        var s = pos[pid], t = pos[cid], e = byPair[pid + ">" + cid] || {};
        var dir = t.x >= s.x ? 1 : -1;
        /* Ветвь карты — лежачее колено, как у оргсхемы: правило схем (см. шапку)
           запрещает кривые в линиях. Прежде тут было безье с горизонтальными
           касательными. Колено на тех же концах: из боковой грани родителя в
           боковую грань ребёнка, поворот на середине просвета между колонками. */
        var bx = s.x + dir * s.w / 2, ex = t.x - dir * t.w / 2;
        var mx = (bx + ex) / 2;
        /* Выход из родителя — не обязательно по его середине: если ребёнок
           стоит НАПРОТИВ грани (а у высокого корня напротив оказываются все
           ветви первого уровня), стрелка идёт одним прямым отрезком с высоты
           ребёнка — ровно как в макете. Колено остаётся для тех, до кого от
           грани по прямой не дотянуться. 12 — отступ от угла, чтобы линия не
           выходила из самой кромки плашки. */
        var sy = Math.max(s.y - s.h / 2 + 12, Math.min(s.y + s.h / 2 - 12, t.y));
        /* Подписанная ветвь колено оставляет себе даже когда могла бы пойти
           прямо: блок подписи живёт на СРЕДНЕМ, вертикальном сегменте — там
           над ним и под ним пусто. На прямой между двумя плашками одной высоты
           ему остался бы только просвет колонок, и «при положительном решении»
           полезло бы на соседнюю плашку. В макете подписей у ветвей нет. */
        links.push({ from: pid, to: cid, label: e.label || "",
                     style: e.style || "solid",
                     labelGap: Math.abs(ex - bx),
                     points: (sy === t.y && !e.label)
                       ? [[bx, t.y], [ex, t.y]]
                       : [[bx, sy], [mx, sy], [mx, t.y], [ex, t.y]] });
      });
    });
    return { nodes: pos, links: links };
  }

  /* Граф связей. Силовая модель (Фрухтерман—Рейнгольд) убрана целиком. Она
   * давала «облако»: узлы вставали где придётся, связи шли косыми отрезками, а
   * плашки приходилось потом растаскивать отдельным проходом. В макетах граф
   * нарисован ПО СЕТКЕ — плашки стоят колонками и рядами, связи идут
   * ортогональными коленами; косая линия тут не органика, а прямое нарушение
   * правила схем (прямые отрезки и прямые углы).
   *
   * Колонка = ранг узла в потоке (тот же flowRanks, что у блок-схемы), ряд =
   * порядок внутри ранга. Отсюда сразу три свойства, которых у силовой модели
   * не было: поток читается слева направо без всякого отражения постфактум,
   * плашки не могут перекрыться (сетка), а раскладка тривиально повторяема —
   * сейв редактора запекает SVG, и повторный рендер обязан совпасть.
   *
   * Замер с макета: плашка 286×80, шаг рядов 220. */
  var NET_W = 286, NET_H = 80, NET_ROW = 220;

  function layoutNetwork(spec) {
    var nodes = spec.nodes, edges = spec.edges || [];
    var rank = flowRanks(nodes, edges);
    var byRank = [];
    nodes.forEach(function (n) {
      var r = rank[n.id] || 0;
      (byRank[r] = byRank[r] || []).push(n);
    });
    /* Больше шести плашек в колонку не влезает (80 на плашку плюс воздух).
       Лишние уходят в СЛЕДУЮЩУЮ колонку того же ранга — так граф без рёбер
       (все узлы ранга 0) раскладывается решёткой, а не слипается в столбик
       высотой в три холста. */
    var rowsLimit = Math.max(1, Math.floor((H - 40) / (NET_H + 20)));
    var cols = [];
    byRank.forEach(function (bucket) {
      if (!bucket) return;
      for (var s = 0; s < bucket.length; s += rowsLimit) {
        cols.push(bucket.slice(s, s + rowsLimit));
      }
    });
    var k = Math.max(1, cols.length), colW = W / k;
    /* Просвет между колонками закладываем в РАСКЛАДКУ, а не подгоняем метку
       постфактум: подписи связей живут именно там, и у графа поднять их
       некуда — сверху такая же плашка, как снизу. */
    var labeled = edges.some(function (e) { return e.label; });
    var w = Math.max(110, Math.min(NET_W, colW - (labeled ? EDGE_W : 60)));
    var maxRow = 0;
    cols.forEach(function (c) { maxRow = Math.max(maxRow, c.length - 1); });
    var step = maxRow ? Math.min(NET_ROW, (H - NET_H - 40) / maxRow) : 0;
    var top = (H - maxRow * step) / 2;
    var pos = {};
    cols.forEach(function (c, ci) {
      c.forEach(function (n, ri) {
        pos[n.id] = { x: colW * (ci + 0.5), y: top + ri * step, w: w, h: NET_H };
      });
    });
    var live = edges.filter(function (e) { return pos[e.from] && pos[e.to]; });
    /* Вертикальная «полоса» колена — СВОЯ у каждого источника в просвете.
       Раньше все колена одного просвета вставали на его середину, и отрезки
       разных связей сливались в одну линию. На выборке графа это рисовало
       соединение, которого в данных нет: «Каталог услуг → Базы данных» шёл
       вправо до середины и вверх, а «Биллинг → Очередь событий» — вверх до
       того же ряда и вправо; два огрызка встречались в одной точке и читались
       как сплошная стрелка «Каталог услуг → Очередь событий».

       Полоса на ИСТОЧНИК, а не на ребро: веер из одного узла по общему стволу
       двусмысленности не создаёт (видно, что всё идёт от него) и именно так
       нарисован в макете, а вот стволы разных узлов обязаны разойтись. */
    var lanes = {};
    live.forEach(function (e) {
      var s = pos[e.from], t = pos[e.to];
      if (Math.abs(s.y - t.y) < 1 || t.x <= s.x) return;   // прямая или обход — полоса не нужна
      var key = Math.round(s.x) + "|" + Math.round(t.x);
      var g = lanes[key] || (lanes[key] = { order: [] });
      if (g.order.indexOf(e.from) === -1) g.order.push(e.from);
    });
    var links = live.map(function (e) {
        /* Та же трассировка, что у дорожек: сосед справа по ряду — прямая,
           сосед правее другим рядом — колено «вбок-вниз-вбок», связь назад —
           обход по краю зоны. Ни одного косого сегмента. */
        var s = pos[e.from], t = pos[e.to], points;
        if (Math.abs(s.y - t.y) < 1) {
          points = s.x < t.x
            ? [[s.x + s.w / 2, s.y], [t.x - t.w / 2, t.y]]
            : backRoute(s, t, false);
        } else if (t.x > s.x) {
          var x0 = s.x + s.w / 2, x1 = t.x - t.w / 2;
          var g = lanes[Math.round(s.x) + "|" + Math.round(t.x)];
          var j = g ? g.order.indexOf(e.from) : 0, m = g ? g.order.length : 1;
          var midX = x0 + ((x1 - x0) * (j + 1)) / (m + 1);
          points = [[x0, s.y], [midX, s.y], [midX, t.y], [x1, t.y]];
        } else {
          points = backRoute(s, t, false);
        }
        return { from: e.from, to: e.to, label: e.label || "",
                 style: e.style || "solid", points: points };
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
     (стыковка обоими концами в одну грань) — обходом. */
  function reroute(link, sAuto, tAuto, s, t) {
    var pts = link.points || [];
    if (pts.length < 4) {
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
   * (swimlanes режут ширину под число дорожек) молча обрезался
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

  /* Потолок кегля подписи узла. 28 было крупнее макета: в Figma подписи плашек
   * набраны 24-м, и при 28 текст занимал плашку почти целиком — воздуха вокруг
   * слова не оставалось, а длинные подписи уезжали на 16-й через fitFont, то
   * есть разброс кеглей по деке был больше, чем нужно. 24 — и потолок, и то,
   * что реально получает большинство плашек. */
  var NODE_FS = 24;

  /* fitFont/clampLabel меряют ПЛАШКУ (габарит минус поля по 12px). Подписи в
   * точном прямоугольнике — колонка дорожек swimlanes — знают свой чистый размер,
   * им нужны те же расчёты без вычитания полей: отсюда пара *Box. */
  function fitFontBox(label, inner, box, base) {
    var text = String(label == null ? "" : label).trim();
    var max = base || NODE_FS;
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

  /* Ширина строки в em'ах по таблице метрик SB Sans Display. Приближение «0.6
   * кегля на символ» (им живёт fitFont) годится, когда надо лишь понять, влезет
   * подпись в плашку или нет, но непригодно, когда ширину строки надо ПОПАСТЬ в
   * заданную: у «Cloud.ru Evolution» реальные 8.84 em против 10.8 по среднему —
   * промах в 18%, то есть 200 px на имени хаба.
   *
   * Таблица снята canvas.measureText по живому шрифту деки и свёрнута в корзины
   * по сотым em. Сумма ширин символов сходится с измерением строки целиком с
   * точностью 0.1% на контрольных фразах — кернинга, который стоило бы
   * учитывать, в шрифте нет. Меряем так, а не канвасом в рантайме, потому что
   * раскладки гоняются в node --test без DOM, а SVG запекается в сейв: один и
   * тот же spec обязан дать один и тот же кегль где угодно. */
  var GLYPH_EM = (function () {
    var rows = {
      26: " ", 27: "ij", 29: "I/", 31: ".:", 32: ",;()", 33: "l",
      34: "!'", 38: "fr", 42: "1", 43: "t-", 45: "J", 47: "sгт«»",
      48: "з", 49: "z", 50: "cvxyсух", 51: "э", 53: "7ь?", 54: "akав",
      55: "eечя\"", 56: "235к", 57: "л", 58: "ouГоп", 59: "hnбдн", 60: "bdgpqийрцъ",
      61: "TZТ", 62: "04689FL+", 63: "Y", 64: "SЗ", 65: "EЕ", 66: "Ь",
      67: "PР", 68: "VXБХ", 69: "ф", 70: "ABАВУЯмы", 71: "CRСЧ–", 72: "Э",
      74: "KК", 75: "DGДЪ", 76: "ж", 77: "Л", 79: "Uwю", 80: "ш",
      81: "HНПЦ", 82: "NИЙщ", 83: "OQО", 88: "m", 89: "%", 90: "Ы",
      91: "—", 93: "WФ", 94: "MМ", 100: "Ж", 104: "Ш", 105: "Щ",
      108: "Ю", 124: "№"
    };
    var map = {}, k, s, i;
    for (k in rows) {
      if (!Object.prototype.hasOwnProperty.call(rows, k)) continue;
      s = rows[k];
      for (i = 0; i < s.length; i++) map[s.charAt(i)] = Number(k) / 100;
    }
    return map;
  })();
  var GLYPH_EM_FALLBACK = 0.6;   // буква вне таблицы (греческая, диакритика)

  /* Неразрывные пробелы (U+00A0 от типографики ассемблера, U+202F) в таблице не
     лежат, но рисуются шириной обычного пробела — без этой ветки каждый
     «Cloud.ru Evolution» прибавлял бы 0.6 em вместо 0.26. */
  var SPACE_RE = /[\s\u00a0\u202f]/;

  function textEm(text) {
    var s = String(text == null ? "" : text).trim(), sum = 0, i, ch, w;
    for (i = 0; i < s.length; i++) {
      ch = s.charAt(i);
      w = GLYPH_EM[ch];
      if (w === undefined) w = SPACE_RE.test(ch) ? GLYPH_EM[" "] : GLYPH_EM_FALLBACK;
      sum += w;
    }
    return sum;
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

  /* weight — необязательный: нужен там, где подпись работает заголовком, а не
     подписью плашки (центр ментальной карты). По умолчанию вес не пишем вовсе,
     чтобы не менять ни байта в SVG у всех остальных схем: редактор запекает
     разметку в deck.html, и лишний атрибут раздул бы диффы на ровном месте. */
  function labelFO(p, n, color, fontSize, align, weight) {
    var fs = fitFont(n.label, p.w, p.h, fontSize);
    return labelFOAt(p, clampLabel(n.label, p.w, p.h, fs), color, fs, align, weight);
  }

  /* Тот же вывод, но кегль И текст ОКОНЧАТЕЛЬНЫЕ — без fitFont и без clampLabel.
     Нужен там, где размер посчитан точной метрикой (имя хаба): обе эти функции
     меряют по среднему 0.6 em на символ, то есть завышают ширину. fitFont сбил
     бы честные 135 до 107, а clampLabel на этом же завышении решил бы, что
     «Cloud.ru Evolution» не влезает, и обрезал его до «Cloud.ru…». */
  function labelFOAt(p, text, color, fs, align, weight) {
    var pad = LABEL_PAD;
    var a = FO_ALIGN[align] || ["center", "center"];
    return '<foreignObject x="' + (p.x - p.w / 2 + pad) + '" y="' + (p.y - p.h / 2 + pad) +
      '" width="' + (p.w - 2 * pad) + '" height="' + (p.h - 2 * pad) + '">' +
      '<div xmlns="http://www.w3.org/1999/xhtml" style="width:100%;height:100%;display:flex;' +
      'align-items:center;justify-content:' + a[0] + ';text-align:' + a[1] + ';overflow:hidden;' +
      'font-size:' + fs + 'px;line-height:1.15;' +
      (weight ? "font-weight:" + weight + ";" : "") +
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
      'overflow-wrap:break-word;">' + esc(text) +
      "</div></div></foreignObject>";
  }

  function shapePath(n, p) {
    var x = p.x, y = p.y, w = p.w, h = p.h;
    var fill = nodeFill(n);
    if (n.shape === "decision") {
      var dh = diamondH(h);
      return '<polygon points="' + x + "," + (y - dh / 2) + " " + (x + w / 2) + "," + y + " " +
        x + "," + (y + dh / 2) + " " + (x - w / 2) + "," + y + '" fill="' + fill + '"/>';
    }
    /* start/end рисовались капсулой (rx = h/2). Макеты схем в Figma капсул не
     * знают: у всех плашек, включая начало и конец блок-схемы, прямые углы —
     * тот же --radius:0, что и у остального слайда. Остались ромб развилки и
     * параллелограмм ввода-вывода: это не скругление, а разные фигуры. */
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

  /* Просвет от линии y до ближайшей плашки вверх (dir −1) или вниз (dir +1),
     считая только те, что попадают в горизонтальный диапазон [x0, x1]. Высоту
     берём НАРИСОВАННУЮ (hDraw): ромб развилки выступает за свою коробку. */
  function clearance(boxes, x0, x1, y, dir) {
    var room = dir < 0 ? y : H - y;
    Object.keys(boxes).forEach(function (id) {
      var p = boxes[id], ph = p.hDraw || p.h;
      if (x1 <= p.x - p.w / 2 || x0 >= p.x + p.w / 2) return;
      var edge = dir < 0 ? p.y + ph / 2 : p.y - ph / 2;
      if (dir < 0 ? edge < y : edge > y) room = Math.min(room, Math.abs(y - edge));
    });
    return room;
  }

  /* Просвет ВПРАВО от x до ближайшей плашки, пересекающей полосу [y0, y1].
     Пара к clearance: та меряет вертикаль для меток над/под линией, эта —
     горизонталь для меток сбоку от вертикального коридора. */
  function hClearance(boxes, x, y0, y1) {
    var room = W - x;
    Object.keys(boxes).forEach(function (id) {
      var p = boxes[id], ph = p.hDraw || p.h;
      if (y1 <= p.y - ph / 2 || y0 >= p.y + ph / 2) return;
      var left = p.x - p.w / 2;
      if (left >= x) room = Math.min(room, left - x);
      else if (p.x + p.w / 2 > x) room = 0;      // плашка прямо на коридоре
    });
    return room;
  }

  /* Толщина связи ОДНА на весь движок. Раньше по умолчанию рисовалась тройка,
     а цикл перебивал её двойкой «по макету» — в итоге линии цикла были заметно
     тоньше линий блок-схемы и графа, хотя это один и тот же элемент одного
     набора. Замер макета — 2; наконечник масштабируется сам (markerUnits по
     умолчанию strokeWidth), так что отдельной правки стрелок не требуется. */
  var EDGE_STROKE = 2;

  function linkPath(link, markerId, boxes, taken) {
    /* both: стрелки на обоих концах, marker-start живёт за счёт
       orient="auto-start-reverse" в определении маркера. */
    var attrs = 'fill="none" stroke="var(--fg-muted)" stroke-width="' +
      EDGE_STROKE + '"' +
      (link.style === "dashed" ? ' stroke-dasharray="10 8"' : "") +
      (link.both ? ' marker-start="url(#' + markerId + ')"' : "") +
      ' marker-end="url(#' + markerId + ')"';
    /* Только M и L: правило схем (см. шапку) запрещает дуги и безье в линиях.
       Ветки A (дуга цикла) и C (ветвь ментальной карты) отсюда убраны — обе
       раскладки теперь отдают ломаную с прямыми углами. */
    var d = link.points.map(function (pt, i) {
      return (i ? "L" : "M") + pt[0] + " " + pt[1];
    }).join("");
    var out = '<path d="' + d + '" ' + attrs + "/>";
    if (link.label) {
      /* Метка — на СРЕДНЕМ сегменте ломаной: первый сегмент у всех рёбер из
       * одного узла общий (метки «да»/«нет» развилки слипались в кашу),
       * а средний вертикальный/горизонтальный — у каждого ребра свой. */
      var pts = link.points;
      var p0 = pts[1] || pts[0], p1 = pts[2] || pts[1] || p0;
      var lx = (p0[0] + p1[0]) / 2, ly = (p0[1] + p1[1]) / 2 - 12;
      var anchor = "middle", budget = EDGE_W, boxH = EDGE_H, lifted = false;
      if (link.labelGap) {
        /* Ветвь ментальной карты: колено целиком лежит в просвете между
         * колонками, его концы стоят ровно на границах плашек. Бюджет = ширина
         * просвета, метка центрирована по нему — значит по горизонтали она
         * физически не может выйти на плашку, какой бы ни был наклон ветви.
         * Поднять метку над линией, как у соседей по ряду, нельзя: сверху
         * стоит соседняя ветвь. Скольжение по сегменту (ветка вертикального
         * коридора ниже) тоже не годится: hClearance ищет просвет только
         * ВПРАВО, а половина ветвей карты идёт влево — метка уезжала бы на
         * плашку родителя. */
        budget = Math.min(EDGE_W, link.labelGap);
      } else if (pts.length === 2) {       // прямой сегмент (hub_spoke): середина
        lx = (pts[0][0] + pts[1][0]) / 2; ly = (pts[0][1] + pts[1][1]) / 2 - 12;
        /* Соседи по одной дорожке соединяются прямой горизонталью, и промежуток
         * между ними бывает уже метки: у трёх узлов в дорожке он 30px против
         * бюджета 200 — подпись пряталась под обе плашки разом, наружу торчали
         * огрызки «…ожи…/…шен…». Не влезает в просвет — поднимаем над плашкой,
         * как у соседей по ряду блок-схемы. У лучей хаба просвет всегда шире
         * метки, они остаются на линии. */
        if (Math.abs(pts[0][1] - pts[1][1]) < 1 &&
            Math.abs(pts[1][0] - pts[0][0]) < EDGE_W) {
          ly = pts[0][1] - (boxes && boxes[link.from] ? boxes[link.from].h : 0) / 2 - 12;
        } else {
          /* Косая связь графа: поднимать метку некуда — над ней такая же плашка,
           * как под ней. Значит бюджет метки должен равняться просвету, а не
           * фиксированным 200: у семи узлов графа связи между плашками бывают по
           * 40px, и блок 200×56 ложился разом на ОБЕ плашки — «при по» съедала
           * одна, «ложительном / решении» вылезало наружу обрубком. Сужаем блок
           * до длины сегмента, дальше подпись сама уменьшит кегль и, если не
           * влезет, честно оборвётся многоточием — тем же clampLabelBox, что и
           * подписи узлов. Пола у бюджета нет намеренно: «подпись не шире
           * просвета» — инвариант, и пол его ломает, оставляя огрызок поверх
           * плашки. Плотно набитый граф с подписью на каждой связи честно
           * скажет «…», а не притворится, будто всё поместилось. */
          var segLen = Math.sqrt(Math.pow(pts[1][0] - pts[0][0], 2) +
                                 Math.pow(pts[1][1] - pts[0][1], 2));
          budget = Math.min(EDGE_W, segLen);
        }
      } else if (Math.abs(p0[0] - p1[0]) < 1 && Math.abs(p0[1] - p1[1]) < 1 &&
                 Math.abs(pts[0][1] - pts[pts.length - 1][1]) < 1) {
        /* Средний сегмент выродился в точку, и всё ребро идёт по горизонтали:
         * соседи стоят в одном ряду, ломаная «колено-колено» на деле прямая.
         * Это попадало в ветку вертикального сегмента и метку уносило ВБОК на её
         * полную ширину — прямо на плашку следующего узла. Промежуток между
         * колонками зависит от числа рангов: на трёх он 450 (бюджет метки 200
         * влезает), на пяти — 75, и «при положительном решении» ложилось поперёк
         * соседа. Ставим метку НАД линией, выше плашки: блок растёт вверх, и
         * между рядами под него как раз и заложены две строки.
         * Проверка на горизонтальность ребра обязательна: у связи с единственным
         * потомком оргсхемы средний сегмент тоже вырожден, но ребро вертикально —
         * там метка и так стоит в просвете между рядами, а сдвиг «выше плашки»
         * загнал бы её ПОД плашку родителя. */
        ly = p0[1] - (boxes && boxes[link.from] ? boxes[link.from].h : 0) / 2 - 12;
        lifted = true;
      } else if (Math.abs(p0[0] - p1[0]) < 1) { // вертикальный сегмент: метку вбок
        lx += 14; ly += 12; anchor = "start";
        /* Просвет справа от коридора НЕ постоянен по высоте: на уровне плашек
         * следующей колонки это ~86px (коридор стоит посередине между ЦЕНТРАМИ
         * колонок, а плашка шириной 300 съедает большую часть зазора), зато в
         * щели МЕЖДУ плашками — вся ширина колонки. Метка на середине сегмента
         * с бюджетом 200 ложилась ПОД плашку соседа — та рисуется ПОСЛЕ рёбер,
         * и от «около нуля» на слайде оставалось «около н…» без всякого следа
         * обрезки: текст физически цел, но накрыт. Обход НАД плашкой (ветка
         * anchor="middle") здесь не работает — метка стоит сбоку от линии, а
         * не на ней. Поэтому скользим по сегменту и ищем окно с максимальным
         * просветом: сперва под блок в две строки, потом в одну (одна строка
         * у самой щели между рядами часто получает весь просвет — ради этого
         * и второй заход). Совсем без окна — точка с наибольшим просветом и
         * бюджет по нему: «подпись не шире просвета» — тот же инвариант, что
         * у косых связей графа и ветвей карты. */
        if (boxes) {
          var yA = Math.min(p0[1], p1[1]), yB = Math.max(p0[1], p1[1]);
          var pick = null;
          [EDGE_H, Math.round(EDGE_FS * 1.16) + 4].forEach(function (bh) {
            var cand = null;
            for (var yy = yA + bh; yy <= yB - 2; yy += 4) {
              var room = Math.min(EDGE_W, hClearance(boxes, lx, yy - bh, yy) - 10);
              /* При равном просвете держимся середины сегмента (прежнее
               * место): без коллизий поведение не меняется вовсе. */
              if (!cand || room > cand.room + 1 ||
                  (room > cand.room - 1 &&
                   Math.abs(yy - ly) < Math.abs(cand.y - ly))) {
                cand = { room: room, y: yy, h: bh };
              }
            }
            /* Однострочное окно берём только если оно СТРОГО шире: при равном
             * просторе две строки дают подписи больше места. */
            if (cand && (!pick || cand.room > pick.room + 1)) pick = cand;
          });
          if (pick) { ly = pick.y; budget = pick.room; boxH = pick.h; }
        }
      }
      /* SVG <text> НЕ переносится и ничем не ограничен по ширине. Схема
       * разрешает подпись ребра до 60 символов, и «Согласование условий
       * договора» растягивалось одной строкой на пол-холста — поперёк соседних
       * узлов и поверх второй такой же подписи. Даём подписи бюджет (две строки
       * по ~13 символов — половина шага сетки узлов), сокращаем лишнее тем же
       * clampLabelBox, что и подписи узлов, и раскладываем на tspan'ы. Блок
       * растёт ВВЕРХ: нижняя строка остаётся на прежней базовой линии, то есть
       * короткие «да»/«нет» стоят там же, где стояли. */
      var efs, lead, elines;
      function fit(h) {
        efs = fitFontBox(link.label, budget, h, EDGE_FS);
        lead = Math.round(efs * 1.16);
        elines = wrapLines(clampLabelBox(link.label, budget, h, efs),
                           Math.max(1, Math.floor(budget / (efs * 0.6))));
      }
      fit(boxH);
      /* Блок растёт ВВЕРХ — и упирается в то, что стоит НАД ним. У блок-схемы
       * обратное ребро идёт по коридору в 56px под рядом плашек, а блоку из двух
       * строк нужно 68: подпись возвратной связи налезала на ромб развилки, и
       * «положительном» читалось как «поло⧫ительном» — будто в файле битый
       * символ. Ромб особенно коварен: он РИСУЕТСЯ на 44px выше своей коробки
       * раскладки (hDraw), поэтому меряем нарисованную фигуру.
       * Сначала пробуем перенести блок ПОД линию — там у возвратного ребра
       * обычно пусто, и подпись сохраняется целиком; и только если и снизу
       * тесно, ужимаем блок до фактического просвета (кегль вниз, дальше честное
       * «…»). Метку, уже поднятую НАД плашкой соседа по ряду, не опускаем: под
       * ней та самая плашка, от которой мы её и убирали. */
      if (anchor === "middle" && boxes) {
        var half = Math.max.apply(null, elines.map(function (s) { return s.length; }))
          * efs * 0.6 / 2;
        var blockH = efs + (elines.length - 1) * lead;
        var room = clearance(boxes, lx - half, lx + half, ly, -1);
        if (blockH > room) {
          var under = clearance(boxes, lx - half, lx + half, p0[1] + 12, 1);
          if (!lifted && under >= blockH + 12) {
            ly = p0[1] + 12 + blockH;
          } else if (room > 0) {
            fit(room);
          }
        }
      }
      /* Две подписи могут сойтись в одну кашу и без всяких плашек: в графе две
       * связи, входящие в один узел с одной стороны, дают почти совпадающие
       * середины, и «при положительном решении» печаталось поверх такого же —
       * получалась нечитаемая гребёнка из наложенных букв. Разводим блоки по
       * вертикали в порядке отрисовки (он фиксирован, повторный рендер даст те
       * же координаты): следующий уходит ПОД занятый, если под ним есть место,
       * иначе НАД. Если тесно с обеих сторон — оставляем как есть: гребёнка
       * плоха, но подпись поверх плашки хуже. */
      var lw = Math.max.apply(null, elines.map(function (s) { return s.length; }))
        * efs * 0.6;
      var lx0 = anchor === "start" ? lx : lx - lw / 2;
      var lbox = { x0: lx0, x1: lx0 + lw, y0: ly - (elines.length - 1) * lead - efs,
                   y1: ly + 4 };
      if (taken) {
        for (var pass = 0; pass < 4; pass++) {
          var hit = null;
          for (var t = 0; t < taken.length; t++) {
            var o = taken[t];
            if (lbox.x0 < o.x1 && lbox.x1 > o.x0 && lbox.y0 < o.y1 && lbox.y1 > o.y0) {
              hit = o; break;
            }
          }
          if (!hit) break;
          var dn = hit.y1 - lbox.y0 + 6, up = lbox.y1 - hit.y0 + 6, move = 0;
          if (boxes && clearance(boxes, lbox.x0, lbox.x1, lbox.y1, 1) >= dn) move = dn;
          else if (boxes && clearance(boxes, lbox.x0, lbox.x1, lbox.y0, -1) >= up) move = -up;
          if (!move) break;
          ly += move; lbox.y0 += move; lbox.y1 += move;
        }
        taken.push(lbox);
      }
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
  function onChart(ci) {
    return "var(--on-chart-" + (ci || 1) + ")";   // 0 = --accent, он же зелёный chart-1
  }

  /* Индекс шкалы --chart-N для типов, где узлы образуют РЯД: воронка, пирамида,
     план-график, Венн. --accent и --chart-1 — ОДИН И ТОТ ЖЕ зелёный: галочка
     «акцент» на первом узле не меняла бы ровно ничего, а на любом другом давала
     бы вторую зелёную плашку — слайд заявлял бы родство первой ступени и
     выделенной, которого нет. Поэтому там, где акцент проставлен, остальные
     узлы начинают шкалу с серого: зелёный на схеме остаётся ровно один.
     Возвращает функцию, вызываемую по одному разу на узел ПО ПОРЯДКУ. */
  function chartIndex(spec) {
    var shift = (spec.nodes || []).some(function (n) { return n.accent; }) ? 1 : 0;
    var seen = 0;
    return function (n) {
      if (n.accent) return 0;
      return Math.min(seen++ + shift, 5) + 1;
    };
  }
  function chartFill(ci) {
    return ci ? "var(--chart-" + ci + ")" : "var(--accent)";
  }

  /* Ступени воронки — прямоугольники по замеру макета, но заливка берётся из
   * шкалы --chart-N, а не из нейтрального --bg-card: ряд ступеней и есть ряд
   * данных, шкала его и кодирует. Геометрия (прямоугольники вместо трапеций,
   * правая колонка чисел) при этом макетная — менялся только цвет. */
  function renderFunnel(spec, pos) {
    var parts = [], idx = chartIndex(spec);
    spec.nodes.forEach(function (n) {
      var p = pos[n.id];
      if (!p) return;
      var x = p.x - p.w / 2, y = p.y - p.h / 2;
      var ci = idx(n);
      parts.push('<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
        '<rect x="' + x + '" y="' + y + '" width="' + p.w + '" height="' + p.h +
          '" fill="' + chartFill(ci) + '"/>' +
        labelFO(p, n, onChart(ci)) +
        /* Число — отдельная правая колонка: выключка вправо по общей линии
           x = 1765 и базовая линия у ВЕРХНЕЙ кромки ступени (замер макета:
           +43 от неё), а не по центру плашки. Так колонка чисел читается
           сверху вниз как список и не пляшет вместе с высотой ступеней. */
        (n.value ? '<text x="' + FUNNEL_NUM_X + '" y="' + (y + FUNNEL_NUM_DY) +
          '" text-anchor="end" font-size="' + FUNNEL_NUM_FS + '" ' +
          'fill="var(--fg-body)">' + esc(n.value) + "</text>" : "") +
        "</g>");
    });
    return parts.join("");
  }

  /* Пирамида — ОДНА залитая фигура, а не стопка плашек-ярусов. Ярус задаётся
   * только белым разделителем поверх треугольника, подпись лежит прямо на
   * заливке, без своей подложки. Поэтому renderPyramid рисует одни подписи, а
   * треугольник с разделителями — декор (decorPyramid): фигура общая на все
   * узлы, нарисовать её внутри какого-то одного было бы неправдой, и при
   * перетаскивании яруса мышью она уехала бы вместе с ним.
   *
   * Кегль 32 — замер макета, и он ВЫШЕ общей двадцатьчетвёрки схем осознанно:
   * подписей всего 3–5, они стоят по оси фигуры без плашки, и мелкий кегль в
   * широкой полосе читался бы как сноска. */
  function renderPyramid(spec, pos) {
    return spec.nodes.map(function (n) {
      var p = pos[n.id];
      if (!p) return "";
      /* Фон яруса один на всю фигуру (--bg-card), поэтому и цвет подписи один
         — nodeText. Исключение то же, что у заливки: на акцентном ярусе под
         подписью зелёное поле, и там нужен тёмный текст. */
      return '<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
        labelFO(p, n, nodeText(n), PYR_FS) + "</g>";
    }).join("");
  }

  /* Венн — ЕДИНСТВЕННЫЙ тип ряда, который палитру --chart-N не берёт, и это
     не недосмотр. Шкала кодирует ПОРЯДОК, а множества Венна не упорядочены:
     они пересекаются, и «второе» ничем не младше «первого». Практика это
     подтвердила: залитое первое множество занимает половину зоны, так что
     --chart-1 превращал пол-схемы в сплошное зелёное поле (при правиле «один
     акцент на слайд»), а серый хвост шкалы в контурах остальных множеств на
     тёмном фоне просто не виден. Поэтому здесь остаётся решение макета:
     нейтральная заливка первого множества и зелёный контур у пересекающих. */
  function renderVenn(spec, pos) {
    var parts = [], nodes = spec.nodes;
    var LH = 170;                     // высота поля подписи множества
    /* Кегль общий на все подписи множеств: разный размер читался бы как разная
       значимость, которой в данных нет. */
    var fs = VENN_FS;
    nodes.forEach(function (nd) {
      var p = pos[nd.id];
      if (p) fs = Math.min(fs, fitFont(nd.label, p.labelW || p.w, LH, VENN_FS));
    });
    nodes.forEach(function (nd, i) {
      var p = pos[nd.id];
      var x = p.x - p.w / 2, y = p.y - p.h / 2;
      /* Залиты ВСЕ множества, и все одним тоном --bg-card. Заливка здесь
         значит «это поле принадлежит множеству», а не «это множество главнее»,
         поэтому контурный прямоугольник был бы дырой в композиции: сквозь него
         видно фон зоны, и множество читается как пустое место. Один и тот же
         тон, а не два разных, нужен ради общей части: две РАЗНЫЕ заливки
         внахлёст дали бы там третий цвет, то есть сущность, которой в данных
         нет (ровно это и делали прежние полупрозрачные круги). Так же сделано
         и в макете — отличаются множества только обводкой. */
      var box = '<rect x="' + x + '" y="' + y + '" width="' + p.w +
        '" height="' + p.h + '" fill="var(--bg-card)" ' + (i === 0
          ? 'stroke="var(--fg-muted)" stroke-width="1.5" stroke-opacity="0.5"/>'
          : 'stroke="var(--accent)" stroke-width="2"/>');
      parts.push('<g class="dgm-node" data-node-id="' + esc(nd.id) + '">' + box +
        labelFO({ x: p.x + (p.labelDx || 0), y: p.y + (p.labelDy || 0),
                  w: p.labelW || p.w, h: LH }, nd, "var(--fg-body)", fs) + "</g>");
    });
    /* Общая часть соседней пары обведена зелёным ПУНКТИРОМ — это и есть
       «пересечение». Рисуем поверх плашек: контур тонкий, по самой границе, и
       подписи он не задевает — те стоят в собственных полосах множеств. */
    for (var k = 1; k < nodes.length; k++) {
      var a = pos[nodes[k - 1].id], b = pos[nodes[k].id];
      if (!a || !b) continue;
      var ix0 = Math.max(a.x - a.w / 2, b.x - b.w / 2);
      var ix1 = Math.min(a.x + a.w / 2, b.x + b.w / 2);
      var iy0 = Math.max(a.y - a.h / 2, b.y - b.h / 2);
      var iy1 = Math.min(a.y + a.h / 2, b.y + b.h / 2);
      if (ix1 - ix0 < 8 || iy1 - iy0 < 8) continue;    // узлы растащили — пересечения нет
      parts.push('<rect x="' + ix0 + '" y="' + iy0 + '" width="' + (ix1 - ix0) +
        '" height="' + (iy1 - iy0) + '" fill="none" stroke="var(--accent)" ' +
        'stroke-width="2" stroke-dasharray="2 6" stroke-linecap="round"/>');
    }
    var cl = spec.meta && spec.meta.center_label;
    if (cl) {
      /* Габарит подписи — сама общая часть ВСЕХ множеств, а не «сколько текста
         пришло». Голый <text> тянулся во всю длину: «Планирование маршрута
         доставки» ложилось поверх подписей соседей, и слайд читался как месиво
         наложенных букв. */
      var cx0 = -Infinity, cx1 = Infinity, cy0 = -Infinity, cy1 = Infinity;
      nodes.forEach(function (nd) {
        var p = pos[nd.id];
        if (!p) return;
        cx0 = Math.max(cx0, p.x - p.w / 2); cx1 = Math.min(cx1, p.x + p.w / 2);
        cy0 = Math.max(cy0, p.y - p.h / 2); cy1 = Math.min(cy1, p.y + p.h / 2);
      });
      var cw = Math.max(60, cx1 - cx0 - 40);
      var ch = Math.min(LH, Math.max(60, cy1 - cy0 - 40));
      var cx = (cx0 + cx1) / 2, cy = (cy0 + cy1) / 2;
      var cfs = fitFontBox(cl, cw, ch, VENN_FS);
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

  /* Ментальная карта считает кегль ОТДЕЛЬНО для центра и для ветвей. Общий
     кегль build'а берётся по самой тесной плашке, то есть по ветви 70 высотой,
     — и крупный центр получил бы ту же двадцатьчетвёрку, что подпись ветви,
     потерявшись в собственном блоке. В макете центр набран заголовочным
     кеглем и полужирным: это название карты, а не ещё один узел. */
  function renderMindmap(spec, pos) {
    var parts = [], nodes = spec.nodes, rootId = nodes[0] && nodes[0].id;
    var fs = NODE_FS;
    nodes.forEach(function (n) {
      var p = pos[n.id];
      if (p && n.id !== rootId) fs = Math.min(fs, fitFont(n.label, p.w, p.h));
    });
    nodes.forEach(function (n) {
      var p = pos[n.id];
      if (!p) return;
      var rt = n.id === rootId;
      parts.push('<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
        shapePath(n, p) +
        labelFO(p, n, nodeText(n), rt ? MIND_ROOT_FS : fs, null, rt ? 600 : 0) +
        "</g>");
    });
    return parts.join("");
  }

  /* Хаб и лучи рисуются ТЕКСТОМ, без единой плашки: в макете подложку имеет
     только панель, а имена — просто надписи на ней. Поэтому свой рендерер, а
     не общий цикл build (тот всегда зовёт shapePath). Имя хаба — зелёное и
     крупное, лучи — обычный текст; кегль лучей общий на весь ряд, как и у
     плашек, иначе «Базы данных» и «Объектное хранилище» встали бы разными
     размерами в одной строке. */
  function renderHubSpoke(spec, pos) {
    var parts = [], nodes = spec.nodes, hubId = nodes[0] && nodes[0].id;
    var fs = HUB_SPOKE_FS;
    nodes.forEach(function (n) {
      if (n.id === hubId) return;
      var p = pos[n.id];
      if (p) fs = Math.min(fs, fitFont(n.label, p.w, p.h, HUB_SPOKE_FS));
    });
    nodes.forEach(function (n) {
      var p = pos[n.id];
      if (!p) return;
      var hub = n.id === hubId;
      /* Акцент у луча — тоже зелёным ТЕКСТОМ, а не зелёной плашкой: плашек
         здесь нет вовсе, и nodeText вернул бы графит, то есть тёмное на
         тёмной панели — подпись исчезла бы. */
      var color = (hub || n.accent) ? "var(--accent)" : "var(--fg-body)";
      parts.push('<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
        (hub ? hubLabel(n, p, color) : labelFO(p, n, color, fs)) + "</g>");
    });
    return parts.join("");
  }

  /* Кегль имени хаба — из ширины блока, а блок равен пролёту между крайними
     стрелками, поэтому строка кончается ровно там, где стрелка начинается. */
  function hubFont(label, p) {
    var byHeight = Math.floor((p.h - 2 * LABEL_PAD) / 1.16);
    var em = textEm(label);
    return em ? Math.min(Math.floor((p.w - 2 * LABEL_PAD) / em), byHeight) : byHeight;
  }

  /* Ниже HUB_FS_MIN имя в одну строку уже нечитаемо — значит, оно и не должно
     быть одной строкой. Отдаём такое имя обычному labelFO: тот перенесёт его по
     словам и, если не влезет даже так, обрежет с многоточием. Точный путь
     страховки не имеет намеренно — он для имён, которые по метрике ТОЧНО
     помещаются, и любая «подстраховка» там только портила бы текст. */
  function hubLabel(n, p, color) {
    var fs = hubFont(n.label, p);
    return fs >= HUB_FS_MIN
      ? labelFOAt(p, String(n.label == null ? "" : n.label).trim(), color, fs)
      : labelFO(p, n, color, HUB_FS_MIN);
  }

  /* Полосы плана-графика красит шкала --chart-N по порядку строк; акцентная
     работа — зелёная. Геометрия полос и шкала месяцев сверху остались по
     замеру макета, из него не взят только нейтральный тон заливки. */
  function renderGantt(spec, pos) {
    var parts = [], idx = chartIndex(spec);
    spec.nodes.forEach(function (n) {
      var p = pos[n.id];
      var ci = idx(n), fill = chartFill(ci);
      /* Подпись живёт внутри полосы, пока та достаточно широка; короткая
         работа (один период на длинной шкале) уводит подпись наружу — иначе
         текст молча резался бы по границе полосы. */
      var box, color, align;
      if (p.w >= 300) {
        box = { x: p.x, y: p.y, w: p.w, h: p.h };
        /* Подпись внутри полосы лежит поверх заливки шкалы — цвет берём из
           --on-chart-N; вынесенная наружу лежит на фоне зоны и остаётся
           обычной. */
        color = onChart(ci); align = "center";
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
           читалось как чужая вставка. Капсулы start/end у блок-схемы тоже
           убраны (shapePath) — в макетах Figma скруглений нет нигде. */
        '" height="' + p.h + '" fill="' + fill + '"/>' +
        labelFO(box, n, color, NODE_FS, align) + "</g>");
    });
    return parts.join("");
  }

  /* Подпись столбика прижата к его ВЕРХНЕМУ ЛЕВОМУ углу (+32 по x, +28 по y —
     замер), а не центрирована в нём. У столбиков разной высоты центр гуляет по
     вертикали от ступени к ступени, и центрированные подписи шли лесенкой с
     шагом вдвое меньше самой лестницы — ряд выглядел кривым. Прижатые к верху
     они стоят ровно там, где кончается столбик, то есть сами показывают его
     высоту. Кегль 36 — замер; он выше общих для схем 24, потому что подпись
     здесь единственное содержимое широкой колонки.
     Пометка «Уровень N» (value) стоит НАД полем, в общей строке на самом верху
     холста, с той же выключкой влево по колонке: это шапка колонки, а не
     подпись столбика, и висеть она обязана на одной высоте у всех ступеней,
     иначе снова получится лесенка подписей. */
  function renderSteps(spec, pos) {
    var parts = [];
    spec.nodes.forEach(function (n) {
      var p = pos[n.id];
      if (!p) return;
      var x = p.x - p.w / 2, top = p.y - p.h / 2;
      /* +20 к ширине — поле, которое labelFO отбивает само (по 10 с каждой
         стороны): без поправки текст встал бы с отступом 42 вместо замеренных
         32, и колонка подписей разошлась бы с колонкой пометок «Уровень N». */
      var inner = p.w - 2 * STEP_PAD;
      var box = { x: x + STEP_PAD + inner / 2,
                  y: top + STEP_LABEL_DY + STEP_LABEL_H / 2,
                  w: inner + 20, h: STEP_LABEL_BOX };
      var g = '<g class="dgm-node" data-node-id="' + esc(n.id) + '">' +
        '<rect x="' + x + '" y="' + top + '" width="' + p.w +
        '" height="' + p.h + '" fill="' + nodeFill(n) + '"/>' +
        /* Акцентная ступень набрана Semibold — в макете это единственное место
           во всём наборе схем с небазовым начертанием, и оно работает вместе с
           зелёным, а не вместо него. */
        labelFO(box, n, nodeText(n), STEP_FS, "left", n.accent ? 600 : 0);
      if (n.value) {
        g += '<text x="' + (x + STEP_PAD) + '" y="' + STEP_CAP_Y +
          '" font-size="' + STEP_CAP_FS + '" font-weight="700" ' +
          'fill="var(--fg-muted)">' + esc(n.value) + "</text>";
      }
      parts.push(g + "</g>");
    });
    return parts.join("");
  }

  /* ---- декор фона (оси, разделители, дорожки) — рисуется ПОД узлами ---- */

  /* Панель хаба — прямоугольник во всю зону схемы. Рисуется в декоре, то есть
     ПОСЛЕ точечной сетки и ДО узлов: сетка остаётся фоном зоны у всех схем, а
     здесь её просто перекрывает панель — ровно как в макете, где под панелью
     точек не видно. Заливка — var(--bg-card) (в тёмной теме это и есть
     замеренный #383838), чтобы светлая тема получила зеркальный светлый блок,
     а не чёрную плашку. */
  function decorHubSpoke() {
    return '<rect x="0" y="0" width="' + W + '" height="' + H +
      '" fill="var(--bg-card)"/>';
  }

  /* Оси матрицы. Рисуются в декоре, то есть ПОД квадрантами — но попадают
     ровно в зазоры между ними (вертикальная на x = 900 в зазоре 880…920,
     горизонтальная на y = 390.5 в зазоре 370…411), так что ни одна плашка их
     не перекрывает. Раньше оси стояли с отбивкой 16…40 от краёв холста и по
     его центрам, а квадранты занимали 26%/74% — ось шла ПО плашкам и обрывалась
     раньше, чем кончалось поле.
     Длина ровно по полю: вертикальная от нижней кромки холста до верхней кромки
     квадрантов (720 → 61), горизонтальная от левого края до правого края
     правого квадранта минус сотня под подпись (0 → 1700). Остриё стрелки у
     вертикальной стоит на верхней кромке квадрантов, а не у края холста:
     выше — только подпись оси. */
  function decorMatrix(spec, layout, markerId) {
    var m = spec.meta || {};
    var ax = MTX_W + MTX_GAP_X / 2;                   // 900 — середина зазора по x
    var ay = MTX_TOP + MTX_H + MTX_GAP_Y / 2;         // 390.5 — середина зазора по y
    var out = '<line x1="0" y1="' + ay + '" x2="' + (W - 100) + '" y2="' + ay +
      '" stroke="var(--fg-muted)" stroke-width="2" marker-end="url(#' + markerId + ')"/>' +
      '<line x1="' + ax + '" y1="' + H + '" x2="' + ax + '" y2="' + MTX_TOP +
      '" stroke="var(--fg-muted)" stroke-width="2" marker-end="url(#' + markerId + ')"/>';
    if (m.x_axis) {
      /* Выключка вправо по самому краю холста: в макете подпись стоит СПРАВА от
         острия, на одной высоте с осью, и доведена до правой границы зоны. */
      out += '<text x="' + W + '" y="' + (ay + 8) + '" text-anchor="end" ' +
        'font-size="' + MTX_AXIS_FS + '" fill="var(--fg-muted)">' + esc(m.x_axis) + "</text>";
    }
    if (m.y_axis) {
      /* По центру оси, а не с выключкой влево от неё: подпись вертикальной оси
         — это надпись НА оси, и при любой её длине она обязана остаться над
         острием, а не уехать в угол. */
      out += '<text x="' + ax + '" y="' + (MTX_TOP - 20) + '" text-anchor="middle" ' +
        'font-size="' + MTX_AXIS_FS + '" fill="var(--fg-muted)">' + esc(m.y_axis) + "</text>";
    }
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
      /* Границы рисуем у ВСЕХ дорожек, включая верхнюю и нижнюю кромки зоны
         (в макете при трёх дорожках четыре линии). Раньше внутренние линии
         были, а крайних не было, и верхняя дорожка снизу отбивалась, а сверху
         нет — полоса читалась как открытая, без потолка.
         Толщина 1 и прозрачность 0.12: в тёмной теме --fg-muted (#D3D3D3) с
         такой прозрачностью по фону #222222 даёт ровно замеренный #383838, то
         есть линию-намёк. Светлая тема получает такой же тихий серый по белому,
         чего фиксированный hex не дал бы. */
      out += '<line x1="0" y1="' + (i * laneH) + '" x2="' + W + '" y2="' + (i * laneH) +
        '" stroke="var(--fg-muted)" stroke-width="1" opacity="0.12"/>';
      if (i === lanes.length - 1) {
        out += '<line x1="0" y1="' + H + '" x2="' + W + '" y2="' + H +
          '" stroke="var(--fg-muted)" stroke-width="1" opacity="0.12"/>';
      }
      /* Подпись лежит ВНУТРИ полосы, у её левого края, прижатая к верхней
         линии со сдвигом +10 (замер макета) — а не по вертикальному центру
         отдельной колонки, как было. Разница смысловая: прижатая к линии
         подпись читается как заголовок полосы, центрированная — как ещё один
         элемент в ряду плашек, вровень с ними.
         Поле подписи узкое и фиксированное, поэтому здесь тот же контракт, что
         и у подписей узлов: подбор кегля, перенос на обёртке с min-width:0 и
         явная обрезка с многоточием. Без него «Отдел клиентского сопровождения»
         молча срезалось overflow:hidden до «…сопровождени» с половинкой буквы. */
      var lw = Math.min(SWIM_LABEL_W, Math.max(24, labelW - 40));
      var lh = Math.max(24, Math.min(laneH - SWIM_LABEL_DY, 2 * NODE_FS * 1.2));
      var lfs = fitFontBox(lane, lw, lh, NODE_FS);
      out += '<foreignObject x="0" y="' + (i * laneH + SWIM_LABEL_DY) + '" width="' + lw +
        '" height="' + lh + '">' +
        '<div xmlns="http://www.w3.org/1999/xhtml" style="width:100%;height:100%;' +
        'display:flex;align-items:flex-start;overflow:hidden;font-size:' + lfs + 'px;' +
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
      /* Подпись шкалы стоит в одной строке с номерами периодов, у левого края
         (в макете «Месяцы» на той же базовой линии, что и цифры). Раньше она
         висела выше них отдельной строкой и читалась как второй заголовок
         слайда. */
      out += '<text x="0" y="' + GANTT_NUM_Y + '" font-size="' + GANTT_FS +
        '" fill="var(--fg-muted)">' + esc(ax) + "</text>";
    }
    for (var i = 0; i <= total; i++) {
      var x = Math.min(W - 1, i * unit);
      /* Линии шкалы одинаковые — и крайние тоже. Раньше нулевая была вдвое
         контрастнее остальных и работала осью Y, которой у плана-графика нет:
         слева от неё ничего не отложено, это просто начало первого периода.
         Толщина 1, прозрачность 0.12 — те же, что у границ дорожек: в тёмной
         теме это замеренный #383838 по фону #222222. */
      out += '<line x1="' + x + '" y1="' + GANTT_TOP + '" x2="' + x + '" y2="' + H +
        '" stroke="var(--fg-muted)" stroke-width="1" opacity="0.12"/>';
      /* Номер прижат ВПРАВО к линии, закрывающей его период, а не центрирован
         над ним. Так номер читается как отметка на шкале («к концу первого»),
         а не как заголовок колонки, и на узких периодах соседние номера не
         съезжаются серединами друг к другу.
         Номера показываем, только пока они читаются: на длинной шкале подписи
         слипаются в серую полосу — сетка без номеров честнее. */
      if (total <= 16 && i < total) {
        out += '<text x="' + ((i + 1) * unit - 4) + '" y="' + GANTT_NUM_Y +
          '" text-anchor="end" font-size="' + GANTT_FS + '" fill="var(--fg-muted)">' +
          (i + 1) + "</text>";
      }
    }
    return out;
  }

  /* Вертикальные рейки по левому краю каждой колонки, на всю высоту зоны.
     Подступенки (полупрозрачные прямоугольники под плашкой) убраны вместе со
     старой лесенкой: у столбиков общее основание, достраивать их до низа
     больше нечем — они уже там.
     Рейка делает то, чего не делал подступенок: показывает колонку целиком,
     включая пустое место НАД столбиком, — то есть насколько ступень не дотянула
     до верхней. Поэтому она идёт от кромки до кромки зоны, а не по высоте
     столбика.
     Позицию берём по фактическому левому краю столбика (декор рисуется после
     applyOffsets): утащенная мышью ступень уводит рейку за собой, иначе рейка
     осталась бы стоять у пустого места. */
  function decorSteps(spec, layout) {
    var pos = layout.nodes, out = "";
    spec.nodes.forEach(function (n) {
      var p = pos[n.id];
      if (!p) return;
      var x = p.x - p.w / 2;
      out += '<line x1="' + x + '" y1="0" x2="' + x + '" y2="' + H +
        '" stroke="var(--fg-muted)" stroke-width="1" opacity="0.3"/>';
    });
    return out;
  }

  /* Внимание: ключ в DECOR заодно ОТКЛЮЧАЕТ авто-центрирование (см. placed) —
     и для воронки с пирамидой это не побочный эффект, а условие. Колонка
     ступеней стоит на x = 629, а не по центру холста: справа отрезана полоса
     под числа. recenter сдвинул бы её на 900, числа уехали бы за край. */
  var DECOR = {
    hub_spoke: decorHubSpoke,
    funnel: decorFunnel,
    pyramid: decorPyramid,
    matrix: decorMatrix,
    swimlanes: decorSwimlanes,
    gantt_lite: decorGantt,
    steps: decorSteps,
  };

  var NODE_RENDERERS = {
    hub_spoke: renderHubSpoke,
    mindmap: renderMindmap,
    funnel: renderFunnel,
    pyramid: renderPyramid,
    matrix: renderMatrix,
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

  /* Сдвинуть схему так, чтобы её габарит стоял по центру холста.

     Раскладки считают позиции от «идеальной» геометрии: корень карты — в
     середине, ранги дерева — от середины. На НЕсимметричных данных схема
     прижималась к краю: карта с одной ветвью оставляла 745px пустоты слева и
     45px справа — слайд выглядел сломанным, хотя каждый узел стоял «правильно».
     Только перенос: масштаб трогать нельзя, он соврал бы про расстояния. */
  function recenter(result) {
    var pos = result.nodes, ids = Object.keys(pos || {});
    if (!ids.length) return result;
    var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    ids.forEach(function (id) {
      var p = pos[id];
      x0 = Math.min(x0, p.x - p.w / 2); x1 = Math.max(x1, p.x + p.w / 2);
      y0 = Math.min(y0, p.y - p.h / 2); y1 = Math.max(y1, p.y + p.h / 2);
    });
    // Схема шире/выше холста: любой сдвиг по этой оси только срежет край.
    var dx = x1 - x0 > W ? 0 : (W - x1 - x0) / 2;
    var dy = y1 - y0 > H ? 0 : (H - y1 - y0) / 2;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return result;
    ids.forEach(function (id) { pos[id].x += dx; pos[id].y += dy; });
    (result.links || []).forEach(function (l) {
      (l.points || []).forEach(function (pt) { pt[0] += dx; pt[1] += dy; });
    });
    return result;
  }

  /* Раскладка + центрирование — ОДНА точка входа для отрисовки и для редактора:
     разойдись они, магнит при перетаскивании считал бы по другим координатам,
     чем видит автор. Кинды с фоновой разметкой (оси матрицы, дорожки, сетка
     Ганта, ступени) не центрируем: их декор нарисован в абсолютных координатах
     холста и уехал бы от узлов. */
  function placed(spec) {
    var result = LAYOUTS[spec.kind](spec);
    return DECOR[spec.kind] ? result : recenter(result);
  }

  function build(spec) {
    var result = placed(spec);
    var auto = snapshot(result.nodes);
    applyOffsets(result.nodes, spec.offsets);
    relink(result, auto);

    var markerId = "dgm-arrow-" + (++uid);
    var gridId = "dgm-grid-" + uid;
    var parts = ['<svg class="diagram-svg m-enter" viewBox="0 0 ' + W + " " + H +
      '" fill="none" xmlns="http://www.w3.org/2000/svg" role="img">'];
    /* Подложка схемы — точечная сетка (замер с макетов: шаг 30, ядро точки 3px,
     * фаза x=2 y=12 от левого верхнего угла зоны). Рисуем ВНУТРИ svg паттерном,
     * а не ::before у .diagram-host, по двум причинам: (1) запечённый редактором
     * SVG уносит подложку с собой — иначе точки есть в предпросмотре и исчезают
     * в выгруженной деке; (2) экспорт в PPTX снимает картинкой именно svg, а
     * синтетический ::before в неё не попадёт. Шаг общий на все виды схем:
     * сетка — это фон зоны, а не часть конкретной раскладки. */
    /* Точка стоит ВНУТРИ плитки (cx/cy = фаза), а не в её углу: в углу паттерн
     * обрезал бы три четверти круга, и сетка вышла бы из четвертинок. */
    if (!NO_GRID[spec.kind]) {
      parts.push('<defs><pattern id="' + gridId + '" width="' + GRID_STEP +
        '" height="' + GRID_STEP + '" patternUnits="userSpaceOnUse">' +
        '<circle cx="' + GRID_PHASE[0] + '" cy="' + GRID_PHASE[1] + '" r="' + GRID_DOT +
        '" fill="var(--dgm-dot)"/></pattern></defs>');
      parts.push('<rect class="dgm-grid" x="0" y="0" width="' + W + '" height="' + H +
        '" fill="url(#' + gridId + ')" aria-hidden="true"/>');
    }
    /* Голова стрелки — ОТКРЫТАЯ галочка, не закрашенный треугольник (правило
     * 2026-10-27, бренд-референс). markerUnits по умолчанию strokeWidth →
     * маркер масштабируется толщиной линии: при stroke-width 3 внутренний
     * штрих 1 даёт те же 3px на голове, плечо ~17px, раскрыв ~76°. */
    parts.push('<defs><marker id="' + markerId + '" viewBox="0 0 10 10" refX="8.5" refY="5" ' +
      'markerWidth="10" markerHeight="10" orient="auto-start-reverse">' +
      '<path d="M4 1.5L8.5 5L4 8.5" fill="none" stroke="var(--fg-muted)" stroke-width="1"/></marker></defs>');
    var decor = DECOR[spec.kind];
    if (decor) parts.push(decor(spec, result, markerId));
    var taken = [];   // занятые подписями места — чтобы не печатать их друг на друге
    result.links.forEach(function (link) {
      parts.push(linkPath(link, markerId, result.nodes, taken));
    });
    var custom = NODE_RENDERERS[spec.kind];
    if (custom) {
      parts.push(custom(spec, result.nodes));
    } else {
      /* Кегль общий на всю схему — по самой тесной плашке. Подбор в каждой
       * плашке отдельно давал «Сбор данных» 28-м рядом с «Коммерческие условия»
       * 20-м: подписи перестали резаться, но разнобой читался как небрежность. */
      var fs = NODE_FS;
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
      return applyOffsets(placed(clean).nodes, clean.offsets);
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
    LAYOUTS: LAYOUTS, placed: placed, applyOffsets: applyOffsets, num: num,
    relink: relink, reroute: reroute, sideOf: sideOf,
    layoutFlowchart: layoutFlowchart, layoutProcess: layoutProcess,
    layoutCycle: layoutCycle, layoutFunnel: layoutFunnel,
    layoutHierarchy: layoutHierarchy, flowRanks: flowRanks,
    layoutMatrix: layoutMatrix, layoutPyramid: layoutPyramid,
    layoutHubSpoke: layoutHubSpoke,
    layoutVenn: layoutVenn, layoutSwimlanes: layoutSwimlanes,
    layoutGantt: layoutGantt, layoutSteps: layoutSteps,
    layoutMindmap: layoutMindmap, layoutNetwork: layoutNetwork,
    laneList: laneList, trimSegment: trimSegment, fitFont: fitFont,
    clampLabel: clampLabel, labelLines: labelLines, wrapLines: wrapLines,
    textEm: textEm, hubFont: hubFont,
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
