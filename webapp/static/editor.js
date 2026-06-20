const params = new URLSearchParams(location.search);
const sessionId = params.get("session");
const frame = document.getElementById("deck");
document.getElementById("html").href = `/api/jobs/${sessionId}/deck?download=1`;

let slides = [];
let current = 0;
let pendingGoTo = 0; // slide to show after the next iframe load

function loadDeck() {
  // cache-bust so edits/chat rewrites are reflected on reload
  frame.src = `/api/jobs/${sessionId}/deck?t=${Date.now()}`;
}

frame.onload = () => {
  const doc = frame.contentDocument;
  if (!doc) return;
  slides = [...doc.querySelectorAll(".slide")];
  // Make leaf text nodes editable in place.
  slides.forEach((s) => s.querySelectorAll("*").forEach((el) => {
    if (el.children.length === 0 && el.textContent.trim()) {
      el.setAttribute("contenteditable", "true");
    }
  }));
  suppressDeckNavOnEdit(doc);
  buildThumbs();
  goTo(Math.min(pendingGoTo, slides.length - 1));
};

// The deck engine (deck.js) attaches document-level click/keydown handlers that
// page through slides (click in left/right third → prev/next; arrows/space → nav).
// In the editor that hijacks clicking-to-edit and typing. We install capture-phase
// listeners that stop propagation when the user is interacting with editable text,
// so the deck's navigation handlers never fire during editing.
function suppressDeckNavOnEdit(doc) {
  doc.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest('[contenteditable="true"]')) {
      e.stopPropagation();
    }
  }, true);
  doc.addEventListener("keydown", (e) => {
    const el = e.target;
    if (el && (el.isContentEditable || el.closest?.('[contenteditable="true"]'))) {
      e.stopPropagation();
    }
  }, true);
}

function buildThumbs() {
  const box = document.getElementById("thumbs");
  box.innerHTML = "";
  slides.forEach((_, i) => {
    const t = document.createElement("div");
    t.className = "thumb";
    t.textContent = "Слайд " + (i + 1);
    t.onclick = () => goTo(i);
    box.appendChild(t);
  });
}

function goTo(i) {
  if (!slides.length) return;
  current = Math.max(0, Math.min(slides.length - 1, i));
  const win = frame.contentWindow;
  if (win && win.deck && win.deck.goTo) win.deck.goTo(current);
  document.getElementById("counter").textContent = `${current + 1} / ${slides.length}`;
  document.getElementById("chatTarget").textContent = `Слайд ${current + 1}`;
  [...document.querySelectorAll(".thumb")].forEach((t, idx) =>
    t.classList.toggle("active", idx === current));
}

document.getElementById("prev").onclick = () => goTo(current - 1);
document.getElementById("next").onclick = () => goTo(current + 1);

function currentDeckHtml() {
  const doc = frame.contentDocument;
  if (!doc || !doc.documentElement) {
    // iframe is mid-reload or not ready — caller must handle this.
    throw new Error("дека ещё не загрузилась, подождите секунду");
  }
  return "<!DOCTYPE html>" + doc.documentElement.outerHTML;
}

async function saveDeck() {
  const r = await fetch(`/api/jobs/${sessionId}/deck`, {
    method: "POST", body: currentDeckHtml(),
  });
  return r.ok;
}

document.getElementById("save").onclick = async () => {
  const ok = await saveDeck();
  flash(document.getElementById("save"), ok ? "Сохранено" : "Ошибка");
};

document.getElementById("png").onclick = async () => {
  await saveDeck(); // persist in-place edits before render
  location.href = `/api/jobs/${sessionId}/png.zip`;
};

function flash(btn, text) {
  const orig = btn.textContent;
  btn.textContent = text;
  setTimeout(() => { btn.textContent = orig; }, 1500);
}

/* ---- chat ---- */
const chatLog = document.getElementById("chatLog");
const chatText = document.getElementById("chatText");
const chatSend = document.getElementById("chatSend");

function addMsg(cls, text) {
  document.getElementById("chatEmpty")?.remove();
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}

// A chat edit calls Kimi (a reasoning model) and can legitimately take a couple
// of minutes. Bound it so a stalled request can never hang the page forever, and
// keep the user informed + able to cancel.
const CHAT_TIMEOUT_MS = 240000; // 4 min hard ceiling
let chatInFlight = null;        // AbortController while a request is running
let chatTimerId = null;

function setChatBusy(busy) {
  chatSend.textContent = busy ? "Отмена" : "Применить к слайду";
  chatSend.classList.toggle("btn-stop", busy);
}

function tickElapsed(thinking, t0) {
  const secs = Math.round((Date.now() - t0) / 1000);
  thinking.textContent = `Применяю правку… ${secs} сек (можно отменить)`;
}

async function sendChat() {
  // If a request is already running, the button acts as Cancel.
  if (chatInFlight) {
    chatInFlight.abort();
    return;
  }
  const instruction = chatText.value.trim();
  if (!instruction) return;
  const slideIndex = current + 1;
  addMsg("user", `Слайд ${slideIndex}: ${instruction}`);
  chatText.value = "";

  const thinking = addMsg("bot", "Применяю правку…");
  const t0 = Date.now();
  const controller = new AbortController();
  chatInFlight = controller;
  let timedOut = false;
  setChatBusy(true);
  chatTimerId = setInterval(() => tickElapsed(thinking, t0), 1000);
  const timeoutId = setTimeout(() => { timedOut = true; controller.abort(); },
                               CHAT_TIMEOUT_MS);

  try {
    // Persist current in-place edits first so the model edits the latest version.
    // Inside try so a failure here can't leave the UI permanently stuck.
    await saveDeck();
    const r = await fetch(`/api/jobs/${sessionId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slide_index: slideIndex, instruction }),
      signal: controller.signal,
    });
    if (!r.ok) {
      thinking.className = "msg err";
      thinking.textContent = "Ошибка: " + (await r.text());
    } else {
      thinking.textContent = `Слайд ${slideIndex} обновлён.`;
      pendingGoTo = current;
      loadDeck(); // reload iframe with the rewritten slide
    }
  } catch (e) {
    thinking.className = "msg err";
    if (timedOut) {
      thinking.textContent =
        "Правка отменена: превышено время ожидания (4 мин). Попробуйте ещё раз.";
    } else if (e && e.name === "AbortError") {
      thinking.textContent = "Правка отменена.";
    } else {
      thinking.textContent = "Ошибка: " + (e && e.message ? e.message : e);
    }
  } finally {
    clearTimeout(timeoutId);
    clearInterval(chatTimerId);
    chatTimerId = null;
    chatInFlight = null;
    setChatBusy(false);
  }
}

chatSend.onclick = sendChat;
chatText.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendChat(); }
});

/* init */
loadDeck();
