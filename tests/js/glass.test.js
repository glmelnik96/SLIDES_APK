// Переработка «пошаговой сборки» 2026-08-20: клиентский цикл без пауз.
// Сервер отдаёт action ("fill" | "score" | null) вместо blocked; вопросы не
// блокируют конвейер. Чистая логика решения по ответу шага и текста статуса
// вынесена в errtext.js, чтобы гоняться через `node --test`.
const test = require("node:test");
const assert = require("node:assert");
const { glassStepDecision, glassStatusText, glassSlideLabel, glassCurrentTarget,
  glassFillLine, glassStages, glassAutoExitReady, glassQuestNote } =
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

// ── экран сборки (переработка UX 2026-08-20) ────────────────────────────────
const P = (slides) => ({ slides });
const S = (over) => Object.assign(
  { brief: "Тема\nтекст", filled: false, status: null }, over);

test("glassCurrentTarget: первый brief&&!filled&&без статуса, зеркало _next_index", () => {
  const plan = P([S({ filled: true }), S({ status: "needs_input" }),
                  S({ brief: "Как устроен процесс\nдетали" }), S({})]);
  const t = glassCurrentTarget(plan);
  assert.strictEqual(t.index, 3);
  assert.strictEqual(t.label, "Как устроен процесс");
});

test("glassCurrentTarget: работы нет → null", () => {
  assert.strictEqual(glassCurrentTarget(P([S({ filled: true })])), null);
  assert.strictEqual(glassCurrentTarget(null), null);
});

test("glassSlideLabel: заголовок > бриф > номер; длинное режется", () => {
  assert.strictEqual(glassSlideLabel({ fields: { title: "Итоги" } }, 5), "Итоги");
  assert.strictEqual(glassSlideLabel({ content: { title: "Планы" } }, 5), "Планы");
  assert.strictEqual(glassSlideLabel({ brief: "Тема раздела\nтело" }, 5), "Тема раздела");
  assert.strictEqual(glassSlideLabel({}, 5), "Слайд 5");
  assert.ok(glassSlideLabel({ brief: "х".repeat(80) }, 1).length <= 48);
});

test("glassFillLine — телеметрия с тикающим таймером", () => {
  assert.strictEqual(glassFillLine({ index: 4, label: "Процесс" }, 23),
    "Заполняю слайд 4 — «Процесс»… 23 с");
  assert.strictEqual(glassFillLine({ index: 4, label: "Процесс" }, null),
    "Заполняю слайд 4 — «Процесс»…");
  assert.strictEqual(glassFillLine(null, 5), "");
});

test("glassStages: степпер этапов Документ→Раскладка→Заполнение→Редактор", () => {
  // раскладка идёт (есть unscored)
  let st = glassStages({ total: 8, unscored: 3, filled: 2, open: 0, quest: false });
  assert.deepStrictEqual(st.map((s) => s.state), ["done", "active", "active", "todo"]);
  assert.strictEqual(st[2].label, "Заполнение 2/8");
  // раскладка кончилась, заполнение идёт
  st = glassStages({ total: 8, unscored: 0, filled: 5, open: 0, quest: false });
  assert.deepStrictEqual(st.map((s) => s.state), ["done", "done", "active", "todo"]);
  // всё заполнено, вопросов нет — активен «Редактор»
  st = glassStages({ total: 8, unscored: 0, filled: 8, open: 0, quest: false });
  assert.deepStrictEqual(st.map((s) => s.state), ["done", "done", "done", "active"]);
  // самое начало: план ещё пуст
  st = glassStages({ total: 0, unscored: 0, filled: 0, open: 0, quest: false });
  assert.deepStrictEqual(st.map((s) => s.state), ["done", "active", "todo", "todo"]);
});

test("glassStages: этап «Вопросы» появляется, только когда они есть", () => {
  // вопросы копятся молча во время заполнения: этап впереди (todo)
  let st = glassStages({ total: 8, unscored: 0, filled: 5, open: 2, quest: false });
  assert.deepStrictEqual(st.map((s) => s.label),
    ["Документ", "Раскладка", "Заполнение 5/8", "Вопросы (2)", "Редактор"]);
  assert.deepStrictEqual(st.map((s) => s.state),
    ["done", "done", "active", "todo", "todo"]);
  // экран перешёл к карточкам (quest=true): акцент на «Вопросы»,
  // заполнение упирается в ответы и активным не выглядит
  st = glassStages({ total: 8, unscored: 0, filled: 6, open: 2, quest: true });
  assert.deepStrictEqual(st.map((s) => s.state),
    ["done", "done", "todo", "active", "todo"]);
  // все filled, но вопросы остались: «Редактор» ещё не активен
  st = glassStages({ total: 8, unscored: 0, filled: 8, open: 1, quest: true });
  assert.deepStrictEqual(st.map((s) => s.state),
    ["done", "done", "done", "active", "todo"]);
});

test("glassQuestNote: тихий счётчик вопросов у степпера", () => {
  assert.strictEqual(glassQuestNote(1),
    "? 1 вопрос — разберём после заполнения");
  assert.strictEqual(glassQuestNote(2),
    "? 2 вопроса — разберём после заполнения");
  assert.strictEqual(glassQuestNote(5),
    "? 5 вопросов — разберём после заполнения");
  assert.strictEqual(glassQuestNote(0), "");
});

test("glassAutoExitReady: авто-переход когда автозаполнение исчерпано", () => {
  // остались только слайды-вопросы → выходим
  assert.ok(glassAutoExitReady(P([S({ filled: true }), S({ status: "needs_input" })])));
  // всё заполнено (осечка = filled заглушкой) → выходим
  assert.ok(glassAutoExitReady(P([S({ filled: true }), S({ filled: true, status: "failed" })])));
  // есть незаполненный без вопроса (в очереди/unscored) → рано
  assert.ok(!glassAutoExitReady(P([S({ filled: true }), S({})])));
  assert.ok(!glassAutoExitReady(P([S({ status: "unscored" })])));
  // пустой план → рано
  assert.ok(!glassAutoExitReady(P([])));
  assert.ok(!glassAutoExitReady(null));
});
