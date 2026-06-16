const params = new URLSearchParams(location.search);
const sessionId = params.get('session');
const frame = document.getElementById('deck');
document.getElementById('html').href = `/api/jobs/${sessionId}/deck?download=1`;

let slides = [];
let current = 0;

frame.src = `/api/jobs/${sessionId}/deck`;
frame.onload = () => {
  const doc = frame.contentDocument;
  slides = [...doc.querySelectorAll('.slide')];
  // Make text editable: every leaf element with text gets contenteditable.
  slides.forEach((s) => s.querySelectorAll('*').forEach((el) => {
    if (el.children.length === 0 && el.textContent.trim()) {
      el.setAttribute('contenteditable', 'true');
    }
  }));
  buildThumbs(doc);
  goTo(0);
};

function buildThumbs(doc) {
  const box = document.getElementById('thumbs');
  box.innerHTML = '';
  slides.forEach((_, i) => {
    const t = document.createElement('div');
    t.className = 'thumb';
    t.textContent = 'Слайд ' + (i + 1);
    t.onclick = () => goTo(i);
    box.appendChild(t);
  });
}

function goTo(i) {
  current = Math.max(0, Math.min(slides.length - 1, i));
  const win = frame.contentWindow;
  if (win.deck && win.deck.goTo) win.deck.goTo(current);
  document.getElementById('counter').textContent = `${current + 1} / ${slides.length}`;
  [...document.querySelectorAll('.thumb')].forEach((t, idx) =>
    t.classList.toggle('active', idx === current));
}

document.getElementById('prev').onclick = () => goTo(current - 1);
document.getElementById('next').onclick = () => goTo(current + 1);

document.getElementById('save').onclick = async () => {
  const html = '<!DOCTYPE html>' + frame.contentDocument.documentElement.outerHTML;
  const r = await fetch(`/api/jobs/${sessionId}/deck`, {method:'POST', body: html});
  alert(r.ok ? 'Сохранено' : 'Ошибка сохранения');
};

document.getElementById('png').onclick = async () => {
  // Save current edits first so the render reflects them.
  const html = '<!DOCTYPE html>' + frame.contentDocument.documentElement.outerHTML;
  await fetch(`/api/jobs/${sessionId}/deck`, {method:'POST', body: html});
  location.href = `/api/jobs/${sessionId}/png.zip`;
};
