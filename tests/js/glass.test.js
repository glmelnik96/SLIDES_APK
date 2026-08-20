// Переработка «пошаговой сборки» 2026-08-20: клиентский цикл без пауз.
// Сервер отдаёт action ("fill" | "score" | null) вместо blocked; вопросы не
// блокируют конвейер. Чистая логика решения по ответу шага и текста статуса
// вынесена в errtext.js, чтобы гоняться через `node --test`.
const test = require("node:test");
const assert = require("node:assert");
const { glassStepDecision, glassStatusText } =
  require("../../webapp/static/errtext.js");

// ── glassStepDecision: что делать с ответом /glass/step | /glass/score ──────
test("decision: fill показывает свежий слайд и продолжает цикл", () => {
  const d = glassStepDecision({ done: false, action: "fill", index: 4 });
  assert.strictEqual(d.jump, 3);         // 1-based → 0-based
  assert.strictEqual(d.stop, false);
  assert.strictEqual(d.done, false);
});

test("decision: score не дёргает сцену и продолжает цикл", () => {
  const d = glassStepDecision({ done: false, action: "score", index: 2 });
  assert.strictEqual(d.jump, null);      // разметка — не повод прыгать по сцене
  assert.strictEqual(d.stop, false);
});

test("decision: action null — работы нет, цикл останавливается без done", () => {
  const d = glassStepDecision({ done: false, action: null, index: null });
  assert.strictEqual(d.stop, true);
  assert.strictEqual(d.done, false);
});

test("decision: done останавливает цикл насовсем", () => {
  const d = glassStepDecision({ done: true, action: null, index: null });
  assert.strictEqual(d.stop, true);
  assert.strictEqual(d.done, true);
});

test("decision: пустой ответ не роняет цикл", () => {
  const d = glassStepDecision(null);
  assert.strictEqual(d.stop, true);
  assert.strictEqual(d.jump, null);
  assert.strictEqual(d.retryLater, false);
});

// retryLater: «работы нет», но в плане остались незаполненные слайды БЕЗ
// вопроса — их держит чужой шаг (другая вкладка или наш же оборванный по
// таймауту запрос, живой прогон 2026-08-20). Цикл перезапускается таймером,
// чтобы подобрать поздний результат; слайды с вопросом ждут автора, не таймер.
test("decision: незаполненный слайд без вопроса просит перезапуск таймером", () => {
  const d = glassStepDecision({ done: false, action: null, index: null,
    plan: { slides: [
      { brief: "т", filled: true, status: null },
      { brief: "х", filled: false, status: null },   // держит чужой шаг
    ] } });
  assert.strictEqual(d.stop, true);
  assert.strictEqual(d.retryLater, true);
});

test("decision: остались только вопросы — таймер не нужен, ждём автора", () => {
  const d = glassStepDecision({ done: false, action: null, index: null,
    plan: { slides: [
      { brief: "т", filled: true, status: null },
      { brief: "х", filled: false, status: "needs_input" },
    ] } });
  assert.strictEqual(d.retryLater, false);
});

test("decision: done не перезапускается таймером", () => {
  const d = glassStepDecision({ done: true, action: null, index: null,
    plan: { slides: [{ brief: "х", filled: false, status: null }] } });
  assert.strictEqual(d.retryLater, false);
});

test("decision: пока есть работа (fill), таймер не назначается", () => {
  const d = glassStepDecision({ done: false, action: "fill", index: 2,
    plan: { slides: [{ brief: "х", filled: false, status: null }] } });
  assert.strictEqual(d.retryLater, false);
});

// ── glassStatusText: текст панели сборки ────────────────────────────────────
test("status: идёт заполнение без вопросов", () => {
  const t = glassStatusText({ filled: 3, total: 8, open: 0, working: true,
                              loopDone: false });
  assert.ok(t.includes("готово 3 из 8"), t);
  assert.ok(!t.includes("вопрос"), t);
});

test("status: вопросы не останавливают сборку — счётчик в статусе", () => {
  const t = glassStatusText({ filled: 3, total: 8, open: 2, working: true,
                              loopDone: false });
  assert.ok(t.includes("готово 3 из 8"), t);
  assert.ok(t.includes("2 вопроса"), t);
  assert.ok(t.includes("не останавливается"), t);
});

test("status: работа кончилась, остались только вопросы — зовём отвечать", () => {
  const t = glassStatusText({ filled: 6, total: 8, open: 2, working: false,
                              loopDone: false });
  assert.ok(t.includes("Готово 6 из 8"), t);
  assert.ok(t.includes("ждёт"), t);
  assert.ok(t.includes("2 вопроса"), t);
});

test("status: всё заполнено", () => {
  const t = glassStatusText({ filled: 8, total: 8, open: 0, working: false,
                              loopDone: true });
  assert.strictEqual(t, "Все слайды заполнены.");
});

test("status: пустой план — раскладка документа", () => {
  const t = glassStatusText({ filled: 0, total: 0, open: 0, working: true,
                              loopDone: false });
  assert.strictEqual(t, "Раскладываю документ…");
});

test("status: осечки заполнения дописываются в хвост", () => {
  const t = glassStatusText({ filled: 8, total: 8, open: 0, working: false,
                              loopDone: true, failed: 2 });
  assert.ok(t.startsWith("Все слайды заполнены."), t);
  assert.ok(t.includes("2 слайда не заполнились"), t);
  assert.ok(t.includes("заглушку"), t);
});

test("status: notice плана дописывается последним", () => {
  const t = glassStatusText({ filled: 1, total: 4, open: 0, working: true,
                              loopDone: false, notice: "Документ обрезан." });
  assert.ok(t.endsWith("Документ обрезан."), t);
});
