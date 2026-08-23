/* Жизненный цикл кадров ASCII-прогресса (webapp/static/ascii.js).

   Модуль уезжает в два соседних приложения (App1 и App3), поэтому его цена
   кадра — часть контракта, а не наша частность: он считает на ГЛАВНОМ потоке.
   Проверяем ровно одно обещание, записанное в самом файле: кадры идут только
   тогда, когда их есть кому увидеть.

   Почему стенд, а не jsdom: ascii.js трогает document/window на загрузке и
   рисует в canvas. Полноценный DOM тут не нужен — нужен управляемый
   requestAnimationFrame, чтобы считать кадры, поэтому окружение подставляем
   руками и крутим очередь кадров сами. Заодно это доказывает главное свойство
   модуля для соседей: он не знает ни одного нашего селектора и заводится в
   любом окружении, где есть canvas.
*/
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const SRC = path.join(__dirname, "..", "..", "webapp", "static", "ascii.js");

function stand(opts) {
  const reduced = !!(opts && opts.reducedMotion);
  let queue = [], seq = 0;
  const cancelled = new Set();

  const canvas = {
    clientWidth: 400, clientHeight: 225, width: 0, height: 0,
    style: {}, dataset: {},
    getContext: () => ({
      setTransform() {}, clearRect() {}, fillRect() {}, fillText() {},
      measureText: () => ({ width: 6 }),
      font: "", textAlign: "", textBaseline: "", fillStyle: "", globalAlpha: 1,
    }),
  };

  const win = {
    devicePixelRatio: 1,
    matchMedia: () => ({ matches: reduced }),   // prefers-reduced-motion
    addEventListener() {},
    getComputedStyle: () => ({ getPropertyValue: () => "", backgroundColor: "" }),
    requestAnimationFrame(fn) { const id = ++seq; queue.push([id, fn]); return id; },
    cancelAnimationFrame(id) { cancelled.add(id); },
  };
  const doc = { readyState: "complete", addEventListener() {}, querySelectorAll: () => [] };

  new Function("window", "document", "getComputedStyle", "requestAnimationFrame",
               "cancelAnimationFrame", "devicePixelRatio",
               fs.readFileSync(SRC, "utf8"))(
    win, doc, win.getComputedStyle, win.requestAnimationFrame,
    win.cancelAnimationFrame, 1);

  // Прокрутить до n запланированных кадров, вернуть, сколько реально отработало.
  function pump(n) {
    let ran = 0;
    for (let i = 0; i < n && queue.length; i++) {
      const [id, fn] = queue.shift();
      if (cancelled.has(id)) { i--; continue; }
      ran++; fn(i * 16);
    }
    return ran;
  }

  return { canvas, pump, pending: () => queue.length, api: win.CloudAscii };
}

test("прогресс: видимый холст крутит кадры", () => {
  const s = stand();
  const p = s.api.progress(s.canvas);
  p.set(0.3).start();
  assert.strictEqual(s.pump(5), 5);
});

test("прогресс: спрятанный холст паркует цикл, а не греет вентилятор", () => {
  // Каркас деки уходит под .hidden на этапе вопросов и снимается медиазапросом
  // на окнах ниже 720 — в обоих случаях clientWidth/Height = 0. До фикса draw()
  // выходил по нулевому размеру, но _tick продолжал планировать следующий кадр,
  // и цикл жил до перезагрузки страницы.
  const s = stand();
  const p = s.api.progress(s.canvas);
  p.set(0.3).start();
  s.pump(2);
  s.canvas.clientWidth = 0; s.canvas.clientHeight = 0;
  s.pump(3);
  assert.strictEqual(s.pending(), 0, "под спрятанным холстом кадров быть не должно");
});

test("прогресс: под спрятанным холстом set() цикл не будит", () => {
  const s = stand();
  const p = s.api.progress(s.canvas);
  p.set(0.3).start();
  s.pump(2);
  s.canvas.clientWidth = 0; s.canvas.clientHeight = 0;
  s.pump(3);
  p.set(0.5);
  assert.strictEqual(s.pending(), 0);
});

test("прогресс: вернувшийся в раскладку холст оживает от set()", () => {
  // Будит именно set(), а не таймер: доля готовности обновляется всю сборку,
  // поэтому парковка не может стать вечной, пока работа идёт.
  const s = stand();
  const p = s.api.progress(s.canvas);
  p.set(0.3).start();
  s.pump(2);
  s.canvas.clientWidth = 0; s.canvas.clientHeight = 0;
  s.pump(3);
  s.canvas.clientWidth = 400; s.canvas.clientHeight = 225;
  p.set(0.6);
  assert.strictEqual(s.pending(), 1, "холст вернулся — цикл обязан ожить");
  assert.strictEqual(s.pump(4), 4);
});

test("прогресс: stop() гасит цикл окончательно", () => {
  const s = stand();
  const p = s.api.progress(s.canvas);
  p.set(0.3).start();
  s.pump(2);
  p.stop();
  s.pump(3);
  assert.strictEqual(s.pending(), 0);
});

test("прогресс: при prefers-reduced-motion цикл не заводится вовсе", () => {
  // Дышать нечему — рисуется один статичный кадр, дальше картину обновляет
  // только set(). Это обещание канона, а не оптимизация.
  const s = stand({ reducedMotion: true });
  const p = s.api.progress(s.canvas);
  p.set(0.3).start();
  assert.strictEqual(s.pending(), 0);
  assert.strictEqual(p.live, false);
});
