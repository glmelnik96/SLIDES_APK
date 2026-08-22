/* Моторика канона v11, общая для всех экранов /slides. Осталась одна вещь:
   шторка убирается вверх, когда читатель идёт вниз, и возвращается на первом
   же движении вверх. Пороги низкие и несимметричные намеренно: порог возврата
   ниже порога ухода, а у самого верха шторка возвращается безусловно —
   застрять в убранном виде она не может ни при каком сценарии прокрутки.

   Живой чертёж канона (grid.js) с обоих экранов снят: подсветка курсора по
   расчерченному фону работает на лендинге, где под ней пусто, и мешает в
   инструменте, где под ней строки работ и кадр деки. Сам grid.js оставлен в
   репозитории — он часть канона, и вернуть его стоит одной строки разметки.

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
})();
