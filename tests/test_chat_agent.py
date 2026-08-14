import json
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from webapp import chat_agent, draft


class FakeClient:
    """Scripts model replies. chat_json is used by the intent classifier (returns
    Intent JSON); chat is used for template pick / planner-talk / slide fill."""
    def __init__(self, intent: dict, *, template="cards-6", text="ответ",
                 proposed=None):
        self._intent = intent
        self._template = template
        self._text = text
        self._proposed = proposed  # dict for ProposedContent, or None

    def chat_json(self, messages, model_cls, **kw):
        # the classifier asks for Intent; the filler asks for SlideContent
        name = model_cls.__name__
        if name == "Intent":
            return model_cls.model_validate(self._intent)
        if name == "ProposedContent":
            return model_cls.model_validate(self._proposed or {"slides": []})
        # SlideContent for fill_slide → minimal valid-ish content
        return model_cls.model_validate({"content": {"title": "T",
                                                      "cards": [{"text": "x"}]}})

    def chat(self, messages, **kw):
        # template pick returns an id; planner/chat returns prose
        sys = messages[0]["content"] if messages else ""
        if "id макета" in sys:
            return self._template
        return self._text


def _seed(tmp_path, monkeypatch, slides=()):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    plan = draft.DraftPlan(title="Демо", slides=list(slides))
    draft.save_plan("s", plan)
    return plan


def test_intent_retitle(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    c = FakeClient({"action": "retitle", "topic": "Новый тайтл"})
    res = chat_agent.run_turn("s", "назови презентацию Новый тайтл", 1, client=c)
    assert res.changed and "Новый тайтл" in res.reply
    assert draft.load_plan("s").title == "Новый тайтл"


def test_intent_add_appends_light_outline_slide(monkeypatch, tmp_path):
    # add — лёгкий аутлайн: тема в brief, без синхронной сборки/шаблона.
    _seed(tmp_path, monkeypatch)
    c = FakeClient({"action": "add", "topic": "наши сервисы"}, template="cards-6")
    res = chat_agent.run_turn("s", "добавь слайд про наши сервисы", 1, client=c)
    plan = draft.load_plan("s")
    assert res.changed and len(plan.slides) == 1
    assert plan.slides[0].brief == "наши сервисы"
    assert plan.slides[0].filled is False
    assert plan.slides[0].template_id is None
    assert res.go_to == 1


def test_intent_delete(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(template_id="cover"),
                  draft.DraftSlide(template_id="cards-6")])
    c = FakeClient({"action": "delete"})
    res = chat_agent.run_turn("s", "удали этот слайд", 2, client=c)
    assert res.changed and len(draft.load_plan("s").slides) == 1


# ── B-5 (аудит 2026-08-14, critical): «удали слайд 99» удалял ТЕКУЩИЙ слайд ──
def test_delete_with_explicit_number_deletes_that_slide(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(template_id="cover"),
                  draft.DraftSlide(template_id="cards-6")])
    c = FakeClient({"action": "delete", "target": 1})
    res = chat_agent.run_turn("s", "удали слайд 1", 2, client=c)
    assert res.changed and "1" in res.reply
    assert [s.template_id for s in draft.load_plan("s").slides] == ["cards-6"]


def test_delete_of_nonexistent_number_refuses_and_keeps_plan(monkeypatch, tmp_path):
    """«Удали слайд 99» при 2 слайдах: раньше молча удалялся текущий слайд, а
    ответ рапортовал «Удалил слайд 2» — потеря контента + ложь о сделанном."""
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(template_id="cover"),
                  draft.DraftSlide(template_id="cards-6")])
    c = FakeClient({"action": "delete", "target": 99})
    res = chat_agent.run_turn("s", "Удали слайд 99", 2, client=c)
    assert not res.changed
    assert len(draft.load_plan("s").slides) == 2      # ничего не удалено
    assert "удалил" not in res.reply.lower()          # и не врём об обратном
    assert "99" in res.reply                          # называем, чего нет


def test_rule_fallback_delete_extracts_number(monkeypatch, tmp_path):
    """LLM-классификатор упал → rule-фолбэк. Номер из «удали слайд 3» обязан
    доехать до target, иначе фолбэк повторит B-5."""
    intent = chat_agent._rule_intent("удали слайд 3")
    assert intent is not None and intent.action == "delete"
    assert intent.target == 3
    # без номера — как раньше: текущий слайд
    intent2 = chat_agent._rule_intent("убери этот слайд")
    assert intent2 is not None and intent2.action == "delete"
    assert intent2.target is None


def test_intent_move(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(template_id="cover"),
                  draft.DraftSlide(template_id="cards-6")])
    c = FakeClient({"action": "move", "to": 1})
    res = chat_agent.run_turn("s", "перемести на первое место", 2, client=c)
    assert res.changed
    assert [s.template_id for s in draft.load_plan("s").slides] == ["cards-6", "cover"]


def test_rewrite_of_a_diagram_sends_the_current_schema(monkeypatch, tmp_path):
    """Схема правится в панели узлов (PUT /fields), и content слайда при этом
    остаётся с момента заполнения. В чат уезжал именно он — просьба «упрости»
    молча откатывала всё, что автор наменял руками."""
    from webapp import slide_types
    import htmlslides.pipeline.filler as filler
    fields = slide_types.validate_fields("diagram", {
        "heading": "Как проходит заявка", "subtitle": "",
        "diagram": {"kind": "process", "nodes": [
            {"id": "a", "label": "Заявка"}, {"id": "b", "label": "Запуск"}],
            "offsets": {"a": {"dx": 40, "dy": -20}}}})
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(
        template_id="diagram", slide_type="diagram", fields=fields,
        content={"title": "Старое", "subtitle": "",
                 "diagram": '{"kind":"process","nodes":['
                            '{"id":"z","label":"Позавчерашний узел"}]}'})])
    seen = {}

    def _spy(client, library, sp, *, deck_title="", extra=""):
        seen["content"] = dict(sp.content)
        seen["template_id"] = sp.template_id
        return sp.model_copy(update={"content": {
            "title": "Новое", "subtitle": "",
            "diagram": '{"kind":"process","nodes":['
                       '{"id":"a","label":"Заявка"}]}'}})
    monkeypatch.setattr(filler, "fill_slide", _spy)

    res = chat_agent.run_turn("s", "упрости схему", 1,
                              client=FakeClient({"action": "rewrite"}))
    assert res.changed
    assert seen["template_id"] == "diagram"
    assert "Позавчерашний узел" not in seen["content"]["diagram"]
    assert '"a"' in seen["content"]["diagram"]      # схема из панели, не из content
    assert seen["content"]["title"] == "Как проходит заявка"
    s = draft.load_plan("s").slides[0]
    assert s.fields["heading"] == "Новое"           # правка доехала до typed-полей
    assert s.fields["diagram"]["nodes"][0]["id"] == "a"


# ── B-7 (аудит 2026-08-14): сборка затирала чат-правку слайда ────────────────
def test_rewrite_marks_slide_filled_so_build_keeps_the_edit(monkeypatch, tmp_path):
    """Rewrite не ставил filled=True → «Заполнить слайды» видел слайд в целях
    (brief and not filled) и перезаполнял его по СТАРОМУ brief — правка из чата
    молча исчезала."""
    import htmlslides.pipeline.filler as filler
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(
        template_id="cards-6", brief="метрики квартала", filled=False,
        content={"title": "Старое"})])
    monkeypatch.setattr(filler, "fill_slide", lambda client, library, sp, **kw:
                        sp.model_copy(update={"content": {"title": "Новое"}}))
    res = chat_agent.run_turn("s", "сократи слайд", 1,
                              client=FakeClient({"action": "rewrite"}))
    assert res.changed
    s = draft.load_plan("s").slides[0]
    assert s.content["title"] == "Новое"
    assert s.filled is True   # больше не цель сборки — правка переживёт build


# ── B-6 (аудит 2026-08-14, major): rewrite принял заглушки «—» как успех ─────
def test_rewrite_rejects_stub_content(monkeypatch, tmp_path):
    """Инцидент: «сократи слайд» на stats с реальными цифрами вернул от модели
    [{"value":"—","label":"Метрика 1","caption":"Описание не предоставлено"}] —
    дека показала прочерки, чат отчитался «Обновил слайд». Заглушечный контент
    обязан быть отвергнут, слайд — остаться прежним."""
    import htmlslides.pipeline.filler as filler
    real = {"heading": "Метрики", "stats": [
        {"value": "87%", "label": "удержание"},
        {"value": "3200", "label": "клиентов"},
        {"value": "99,95%", "label": "аптайм"}]}
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(
        template_id="stats-3", brief="метрики квартала", filled=True,
        slide_type="stats", fields=real)])
    stub = {"title": "Метрики", "stats": [
        {"value": "—", "label": "Метрика 1", "caption": "Описание не предоставлено"},
        {"value": "—", "label": "Метрика 2", "caption": "Описание не предоставлено"},
        {"value": "—", "label": "Метрика 3", "caption": "Описание не предоставлено"}]}
    monkeypatch.setattr(filler, "fill_slide", lambda client, library, sp, **kw:
                        sp.model_copy(update={"content": stub}))
    res = chat_agent.run_turn("s", "сократи слайд", 1,
                              client=FakeClient({"action": "rewrite"}))
    assert res.changed is False
    assert "Обновил" not in res.reply
    s = draft.load_plan("s").slides[0]
    assert s.fields == real            # реальные цифры не тронуты
    assert s.slide_type == "stats"


def test_looks_stubbed_heuristic():
    stub = {"stats": [{"value": "—", "label": "Метрика 1",
                       "caption": "Описание не предоставлено"}]}
    assert chat_agent._looks_stubbed(stub) is True
    assert chat_agent._looks_stubbed({}) is True  # пустой контент = тоже порча
    # одиночный легитимный прочерк среди живого текста — не повод отказывать
    real = {"title": "Итоги квартала", "rows": [
        {"name": "Выручка", "value": "3,2 млн"},
        {"name": "Отток", "value": "—"},
        {"name": "NPS", "value": "62"}]}
    assert chat_agent._looks_stubbed(real) is False


def test_intent_plan_is_conversational(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    c = FakeClient({"action": "plan"}, text="Предлагаю 5 слайдов: …")
    res = chat_agent.run_turn("s", "давай спланируем структуру", 1, client=c)
    assert not res.changed and "слайд" in res.reply.lower()
    # conversation history persisted
    convo = chat_agent.load_chat("s")
    assert convo.turns[-1].role == "assistant" and convo.turns[0].role == "user"


def test_classify_failure_degrades_to_chat(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)

    class Boom:
        def chat_json(self, *a, **k):
            raise RuntimeError("bad json")
        def chat(self, *a, **k):
            return "ладно"
    res = chat_agent.run_turn("s", "что-то непонятное", 1, client=Boom())
    assert not res.changed  # safe fallback, no crash


def test_context_brief_includes_slides_and_history(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(template_id="cover", content={"title": "Привет"})])
    plan = draft.load_plan("s")
    convo = chat_agent.Conversation(turns=[chat_agent.Turn(role="user", text="hi")])
    brief = chat_agent.context_brief(plan, convo, 1)
    assert "cover" in brief and "Привет" in brief and "hi" in brief


def test_intent_propose_content_fills_typed_fields(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="строение гриба", filled=False),
        draft.DraftSlide(brief="в цифрах", filled=False)])
    proposed = {"slides": [
        {"index": 1, "slide_type": "bullets",
         "fields": {"heading": "Строение", "bullets": ["шляпка", "ножка"]}},
        {"index": 2, "slide_type": "stats",
         "fields": {"heading": "Цифры", "stats": [{"value": "90%", "label": "лес"}]}},
    ]}
    c = FakeClient({"action": "propose_content"}, proposed=proposed)
    res = chat_agent.run_turn("s", "разложи слайды по полям", 1, client=c)
    plan = draft.load_plan("s")
    assert res.changed
    assert plan.slides[0].slide_type == "bullets"
    assert plan.slides[0].fields["bullets"] == ["шляпка", "ножка"]
    assert plan.slides[1].slide_type == "stats"


def test_propose_content_skips_invalid_item(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(brief="x", filled=False)])
    # missing heading → invalid → slide stays raw
    proposed = {"slides": [{"index": 1, "slide_type": "bullets",
                            "fields": {"bullets": ["a"]}}]}
    c = FakeClient({"action": "propose_content"}, proposed=proposed)
    chat_agent.run_turn("s", "разложи", 1, client=c)
    assert draft.load_plan("s").slides[0].slide_type is None


def test_propose_content_no_targets(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)  # empty plan
    c = FakeClient({"action": "propose_content"}, proposed={"slides": []})
    res = chat_agent.run_turn("s", "разложи", 1, client=c)
    assert res.changed is False


# ── B-4 (аудит 2026-08-14): советы чата ссылались на скрытые кнопки ──────────
# Кнопка «Заполнить слайды»/«Собрать деку» видна только пока есть build-target
# (brief без filled/freeform/slide_type — hasBuildTargets() в editor.js). Совет
# «нажми кнопку», когда её нет на экране, — тупик для пользователя.

def test_build_now_reply_names_button_when_targets_exist(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(brief="тема", filled=False)])
    c = FakeClient({"action": "build_now"})
    res = chat_agent.run_turn("s", "собери деку", 1, client=c)
    assert "Заполнить слайды" in res.reply


def test_build_now_reply_hides_missing_button(monkeypatch, tmp_path):
    # полностью типизированная дека → кнопки нет → чат не должен её советовать
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(
        brief="строение", slide_type="bullets",
        fields={"heading": "Строение", "bullets": ["a", "b"]})])
    c = FakeClient({"action": "build_now"})
    res = chat_agent.run_turn("s", "собери деку", 1, client=c)
    assert "Заполнить слайды" not in res.reply
    assert res.reply  # не молчим — объясняем, что дека уже собрана


def test_build_now_reply_on_empty_plan(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)  # пустой план — собирать нечего и некуда жать
    c = FakeClient({"action": "build_now"})
    res = chat_agent.run_turn("s", "собери деку", 1, client=c)
    assert "Заполнить слайды" not in res.reply


def test_propose_content_full_typing_drops_build_button_advice(monkeypatch,
                                                               tmp_path):
    # все слайды разложены по типам → build-target'ов нет → «жми „Собрать“» лжёт
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="строение гриба", filled=False)])
    proposed = {"slides": [
        {"index": 1, "slide_type": "bullets",
         "fields": {"heading": "Строение", "bullets": ["шляпка", "ножка"]}}]}
    c = FakeClient({"action": "propose_content"}, proposed=proposed)
    res = chat_agent.run_turn("s", "разложи слайды по полям", 1, client=c)
    assert res.changed
    assert "Собрать" not in res.reply


def test_propose_content_partial_typing_keeps_build_button_advice(monkeypatch,
                                                                  tmp_path):
    # часть слайдов осталась сырой → кнопка видна, совет «Собрать» уместен
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="строение гриба", filled=False),
        draft.DraftSlide(brief="в цифрах", filled=False)])
    proposed = {"slides": [
        {"index": 1, "slide_type": "bullets",
         "fields": {"heading": "Строение", "bullets": ["шляпка", "ножка"]}}]}
    c = FakeClient({"action": "propose_content"}, proposed=proposed)
    res = chat_agent.run_turn("s", "разложи слайды по полям", 1, client=c)
    assert res.changed
    assert "Собрать" in res.reply


def test_propose_content_thin_brief_suggests_enrich(monkeypatch, tmp_path):
    # A too-general one-line brief → model returns nothing → guide to enrich first.
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(brief="наши сервисы", filled=False)])
    c = FakeClient({"action": "propose_content"}, proposed={"slides": []})
    res = chat_agent.run_turn("s", "разложи", 1, client=c)
    assert res.changed is False
    assert "дополни" in res.reply.lower()


def test_build_outline_skips_typed_slides(monkeypatch, tmp_path):
    # a typed slide must NOT be sent through the LLM fill during build.
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(
        brief="строение", slide_type="bullets",
        fields={"heading": "Строение", "bullets": ["a", "b"]})])

    class Boom:
        def chat_json(self, *a, **k):
            raise AssertionError("classifier/fill must not run for typed slides")

        def chat(self, *a, **k):
            raise AssertionError("template pick must not run for typed slides")

    chat_agent.build_outline("s", client=Boom())
    plan = draft.load_plan("s")
    assert plan.slides[0].slide_type == "bullets"   # unchanged
    assert plan.slides[0].filled is False           # never LLM-filled


# ── Серверный аудит 2026-08-14 (C-2/C-6): chat_agent сохранял минутный снимок
# плана ЦЕЛИКОМ. Пока модель думала (fill_slide / chat_json — секунды-минуты),
# редактор успевал записать plan.json (формы через _persist_draft, glass-шаг,
# post_chat) — и save_plan(снимок) молча откатывал эти правки. glass._fill_one
# и post_chat уже вклеивают только своё в свежий план; здесь — тот же паттерн.

def _edit_slide2_by_hand():
    """Имитация параллельной правки формы: редактор записал plan.json,
    пока «модель думала»."""
    p = draft.load_plan("s")
    p.slides[1].content = {"title": "Правка руками"}
    draft.save_plan("s", p)


class _EditsDuringLLM(FakeClient):
    """FakeClient, который во время НЕ-классификаторного chat_json выполняет
    сценарий параллельной правки и умеет отдавать заданные payload'ы."""
    def __init__(self, intent, *, payloads=None, edit=_edit_slide2_by_hand, **kw):
        super().__init__(intent, **kw)
        self._payloads = payloads or {}
        self._edit = edit

    def chat_json(self, messages, model_cls, **kw):
        name = model_cls.__name__
        if name != "Intent" and self._edit is not None:
            self._edit()
        if name in self._payloads:
            return model_cls.model_validate(self._payloads[name])
        return super().chat_json(messages, model_cls, **kw)


def test_rewrite_splices_into_fresh_plan(monkeypatch, tmp_path):
    """C-2: правка соседнего слайда, сделанная пока модель переписывала наш,
    обязана пережить сейв rewrite."""
    import htmlslides.pipeline.filler as filler
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(template_id="cards-6", brief="метрики",
                         content={"title": "Старое"}),
        draft.DraftSlide(template_id="cards-6", filled=True,
                         content={"title": "Команда"})])

    def _fill(client, library, sp, **kw):
        _edit_slide2_by_hand()          # автор правит слайд 2, пока модель пишет
        return sp.model_copy(update={"content": {"title": "Новое"}})
    monkeypatch.setattr(filler, "fill_slide", _fill)

    res = chat_agent.run_turn("s", "перепиши слайд", 1,
                              client=FakeClient({"action": "rewrite"}))
    assert res.changed
    plan = draft.load_plan("s")
    assert plan.slides[0].content["title"] == "Новое"            # правка чата
    assert plan.slides[1].content["title"] == "Правка руками"    # уцелела


def test_rewrite_of_slide_deleted_mid_fill_refuses(monkeypatch, tmp_path):
    """C-2: слайд удалили, пока модель его переписывала, — сейв снимка
    воскрешал его. Честный отказ вместо воскрешения."""
    import htmlslides.pipeline.filler as filler
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(template_id="cover", content={"title": "Обложка"}),
        draft.DraftSlide(template_id="cards-6", brief="метрики",
                         content={"title": "Старое"})])

    def _fill(client, library, sp, **kw):
        draft.save_plan("s", draft.delete_slide(draft.load_plan("s"), 2))
        return sp.model_copy(update={"content": {"title": "Новое"}})
    monkeypatch.setattr(filler, "fill_slide", _fill)

    res = chat_agent.run_turn("s", "перепиши слайд", 2,
                              client=FakeClient({"action": "rewrite"}))
    assert res.changed is False
    assert len(draft.load_plan("s").slides) == 1   # не воскрешён
    assert "Обновил" not in res.reply              # и не врём об успехе


def test_build_outline_preserves_concurrent_form_edit(monkeypatch, tmp_path):
    """C-6: сборка идёт минутами и сохраняет план после каждого слайда — правка
    формы, сделанная во время сборки, откатывалась следующим же сейвом."""
    import htmlslides.pipeline.filler as filler
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="метрики квартала", filled=False),
        draft.DraftSlide(template_id="cards-6", filled=True,
                         content={"title": "Готовый"})])

    def _fill(client, library, sp, **kw):
        _edit_slide2_by_hand()
        return sp.model_copy(update={"content": {"title": "Метрики"}})
    monkeypatch.setattr(filler, "fill_slide", _fill)

    chat_agent.build_outline("s", client=FakeClient({"action": "chat"}))
    plan = draft.load_plan("s")
    assert plan.slides[0].filled is True
    assert plan.slides[1].content["title"] == "Правка руками"


def test_build_outline_does_not_resurrect_deleted_slide(monkeypatch, tmp_path):
    """C-6: слайд удалили во время сборки — сейв снимка возвращал его в план."""
    import htmlslides.pipeline.filler as filler
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="метрики", filled=False),
        draft.DraftSlide(brief="команда", filled=False)])
    calls = {"n": 0}

    def _fill(client, library, sp, **kw):
        calls["n"] += 1
        if calls["n"] == 1:   # во время заполнения слайда 1 автор удалил слайд 2
            draft.save_plan("s", draft.delete_slide(draft.load_plan("s"), 2))
        return sp.model_copy(update={"content": {"title": "T"}})
    monkeypatch.setattr(filler, "fill_slide", _fill)

    chat_agent.build_outline("s", client=FakeClient({"action": "chat"}))
    plan = draft.load_plan("s")
    assert len(plan.slides) == 1                    # удалённый не воскрес
    assert plan.slides[0].filled is True


def test_propose_content_preserves_concurrent_edit(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="строение гриба", filled=False),
        draft.DraftSlide(template_id="cards-6", filled=True,
                         content={"title": "Готовый"})])
    proposed = {"slides": [
        {"index": 1, "slide_type": "bullets",
         "fields": {"heading": "Строение", "bullets": ["шляпка", "ножка"]}}]}
    c = _EditsDuringLLM({"action": "propose_content"}, proposed=proposed)
    res = chat_agent.run_turn("s", "разложи слайды по полям", 1, client=c)
    plan = draft.load_plan("s")
    assert res.changed
    assert plan.slides[0].slide_type == "bullets"
    assert plan.slides[1].content["title"] == "Правка руками"


def test_enrich_briefs_preserves_concurrent_edit(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="наши сервисы", filled=False),
        draft.DraftSlide(template_id="cards-6", filled=True,
                         content={"title": "Готовый"})])
    payloads = {"EnrichedOutline": {"slides": [
        {"index": 1, "brief": "наши сервисы: аудит, внедрение, поддержка 24/7"}]}}
    c = _EditsDuringLLM({"action": "enrich"}, payloads=payloads)
    res = chat_agent.run_turn("s", "дополни план деталями", 1, client=c)
    plan = draft.load_plan("s")
    assert res.changed
    assert "24/7" in plan.slides[0].brief
    assert plan.slides[1].content["title"] == "Правка руками"


def test_generate_outline_preserves_concurrent_edit(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(template_id="cover", content={"title": "Обложка"}),
        draft.DraftSlide(template_id="cards-6", filled=True,
                         content={"title": "Готовый"})])
    payloads = {"OutlineDraft": {"slides": [
        {"title": "Итоги", "brief": "итоги года"}]}}
    c = _EditsDuringLLM({"action": "plan"}, payloads=payloads)
    res = chat_agent.run_turn("s", "набросай план про итоги", 1, client=c)
    plan = draft.load_plan("s")
    assert res.changed
    assert len(plan.slides) == 3
    assert plan.slides[1].content["title"] == "Правка руками"


# ── Статический аудит диффа a537324, 2026-08-15 (S-1/S-2/S-4/S-6) ──────────────

def test_rewrite_empty_brief_does_not_splice_into_wrong_slide(monkeypatch, tmp_path):
    """S-1a: у слайдов из пикера brief="" — гард «brief не сменился» вакуумный.
    Удаление раннего слайда во время fill сдвигает индексы, и rewrite вклеивался
    в ЧУЖОЙ слайд, затирая его содержимое и рапортуя об успехе."""
    import htmlslides.pipeline.filler as filler
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(template_id="cover", content={"title": "Обложка"}),
        draft.DraftSlide(template_id="cards-6", content={"title": "Цены"}),
        draft.DraftSlide(template_id="cards-6", content={"title": "Команда"})])

    def _fill(client, library, sp, **kw):
        draft.save_plan("s", draft.delete_slide(draft.load_plan("s"), 1))
        return sp.model_copy(update={"content": {"title": "Новое"}})
    monkeypatch.setattr(filler, "fill_slide", _fill)

    res = chat_agent.run_turn("s", "перепиши слайд", 2,
                              client=FakeClient({"action": "rewrite"}))
    assert res.changed is False
    plan = draft.load_plan("s")
    assert [s.content["title"] for s in plan.slides] == ["Цены", "Команда"]


def test_rewrite_during_inline_edit_of_same_slide_refuses(monkeypatch, tmp_path):
    """S-1b: пока модель переписывала слайд, автор отредактировал его же
    двойным кликом (freeform html). brief не менялся — старый гард пропускал,
    update_slide затирал content (ключ "html" исчезал), freeform оставался True
    → слайд рендерился ПУСТЫМ. Правка руками важнее — честный отказ."""
    import htmlslides.pipeline.filler as filler
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(template_id="cards-6", brief="метрики",
                         content={"title": "Старое"})])

    def _fill(client, library, sp, **kw):
        p = draft.load_plan("s")
        p.slides[0].freeform = True
        p.slides[0].content = {"html": "<div>руками</div>"}
        draft.save_plan("s", p)
        return sp.model_copy(update={"content": {"title": "Новое"}})
    monkeypatch.setattr(filler, "fill_slide", _fill)

    res = chat_agent.run_turn("s", "перепиши слайд", 1,
                              client=FakeClient({"action": "rewrite"}))
    assert res.changed is False
    s = draft.load_plan("s").slides[0]
    assert s.freeform is True
    assert s.content == {"html": "<div>руками</div>"}


def test_rewrite_of_freeform_slide_becomes_template_slide(monkeypatch, tmp_path):
    """S-1 (смежное, без гонки): rewrite freeform-слайда оставлял freeform=True
    при content без "html" → draft_render рисовал пустоту вместо результата
    модели. После правки моделью слайд снова шаблонный."""
    import htmlslides.pipeline.filler as filler
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(freeform=True, content={"html": "<div>руками</div>"})])

    def _fill(client, library, sp, **kw):
        return sp.model_copy(update={"content": {"title": "Новое"}})
    monkeypatch.setattr(filler, "fill_slide", _fill)

    res = chat_agent.run_turn("s", "перепиши слайд", 1,
                              client=FakeClient({"action": "rewrite"}))
    assert res.changed
    s = draft.load_plan("s").slides[0]
    assert s.freeform is False
    assert s.content.get("title") == "Новое"


def test_propose_content_no_crash_when_plan_shrinks_and_nothing_applies(
        monkeypatch, tmp_path):
    """S-2: applied==0 + план укоротился за время LLM-вызова → thin считался
    по устаревшим targets на СВЕЖЕМ плане → IndexError → нейтральный 500,
    ход чата терялся."""
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="коротко", filled=False),
        draft.DraftSlide(brief="тоже коротко", filled=False)])

    def _shrink():
        draft.save_plan("s", draft.delete_slide(draft.load_plan("s"), 2))
    proposed = {"slides": [{"index": 2, "slide_type": "bullets",
                            "fields": {"heading": "H", "bullets": ["a"]}}]}
    c = _EditsDuringLLM({"action": "propose_content"}, proposed=proposed,
                        edit=_shrink)
    res = chat_agent.run_turn("s", "разложи слайды по полям", 1, client=c)
    assert res.changed is False
    assert res.reply


def test_enrich_briefs_zero_applied_is_honest_refusal(monkeypatch, tmp_path):
    """S-4: все предложения отсеяны гардами (brief успел смениться) → раньше
    отвечали «Дополнил план деталями (0 сл.)» с changed=True — ложь об
    обогащении, которого не было."""
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="наши сервисы", filled=False)])

    def _change_brief():
        p = draft.load_plan("s")
        p.slides[0].brief = "уже другой brief"
        draft.save_plan("s", p)
    payloads = {"EnrichedOutline": {"slides": [
        {"index": 1, "brief": "наши сервисы: аудит и поддержка"}]}}
    c = _EditsDuringLLM({"action": "enrich"}, payloads=payloads,
                        edit=_change_brief)
    res = chat_agent.run_turn("s", "дополни план деталями", 1, client=c)
    assert res.changed is False
    assert "0 сл." not in res.reply
    assert draft.load_plan("s").slides[0].brief == "уже другой brief"


def test_rule_fallback_delete_ordinal_number_before_word():
    """S-6: «удали 3-й слайд» в rule-фолбэке давал target=None → удалялся
    ТЕКУЩИЙ слайд (B-5 жив в фолбэке для этой формулировки)."""
    intent = chat_agent._rule_intent("удали 3-й слайд")
    assert intent is not None and intent.action == "delete"
    assert intent.target == 3
    # «удали 3 слайда» — это количество, не номер: ложного target быть не должно
    intent2 = chat_agent._rule_intent("удали 3 слайда")
    assert intent2 is not None and intent2.target is None
