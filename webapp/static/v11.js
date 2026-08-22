/* Моторика канона v11, общая для всех экранов /slides.
   Здесь ровно две вещи, и обе — из эталона Brand_Image_site:

   1. Шторка убирается вверх, когда читатель идёт вниз, и возвращается на
      первом же движении вверх. Пороги низкие и несимметричные намеренно:
      порог возврата ниже порога ухода, а у самого верха шторка возвращается
      безусловно — застрять в убранном виде она не может ни при каком
      сценарии прокрутки.

   2. Живой чертёж заводится внутри рамы ленты. alignTo обязателен: без него
      фаза чертежа случайная, и содержимое, даже кратное 24, не ложится на
      линии. Сцена — сама рама: за её пределами курсор слушать незачем.

   Файл подключается с defer, поэтому DOM уже разобран и ждать событий не надо.
   Правки канона приходят сюда только вслед за docs/DESIGN_CANON_v11.md. */
(function () {
  var body = document.body;

  var last = window.pageYOffset, ticking = false;
  window.addEventListener('scroll', function () {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      ticking = false;
      var y = window.pageYOffset;
      if (y < 64 || y < last - 4) body.classList.remove('lp-tuck');
      else if (y > 96 && y > last + 4) body.classList.add('lp-tuck');
      last = y;
    });
  }, { passive: true });

  var grid = document.querySelector('.lp-grid--page');
  if (grid && window.lpGrid) {
    // Рама на /slides — лента, в редакторе — сцена с кадром. Якорь фазы тоже
    // разный: сетка карточек или сам кадр деки.
    var stage = grid.closest('.feed, .stage') || grid.parentElement;
    window.lpGrid({
      grid: grid,
      stage: stage,
      alignTo: stage ? stage.querySelector('.feed__grid, #frameWrap') : null,
    });
  }
})();
