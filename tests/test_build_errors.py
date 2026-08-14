"""Классификатор сбоев сборки → понятный пользователю текст (webapp.build_errors)."""
import pytest

from webapp.build_errors import GENERIC, error_code, user_message


def test_value_error_message_is_surfaced_verbatim():
    """ValueError движка несёт кураторский русский текст — показываем как есть
    (реальный прод-кейс: «точный перенос … не .docx»)."""
    exc = ValueError("точный перенос на Этапе 1 поддерживает .pptx/.md/.txt, не .docx")
    assert user_message(exc) == str(exc)


def test_empty_value_error_falls_back_to_generic():
    assert user_message(ValueError("")) == GENERIC


def test_unknown_exception_is_generic():
    """Настоящий баг (неизвестный тип) не течёт в UI — общий фолбэк."""
    assert user_message(RuntimeError("kaboom internal dump")) == GENERIC


def test_llm_format_error_is_human_readable():
    from htmlslides.pipeline.client import LLMFormatError
    msg = user_message(LLMFormatError("truncated json"))
    assert msg != GENERIC
    assert "спланировать" in msg
    assert "kaboom" not in msg  # сырой текст не течёт


def test_fill_error_is_human_readable():
    from htmlslides.pipeline.filler import FillError
    msg = user_message(FillError("slot contract broke"))
    assert msg != GENERIC
    assert "слайд" in msg.lower()


def test_assemble_error_is_human_readable():
    from htmlslides.assembler import AssembleError
    msg = user_message(AssembleError("unknown theme"))
    assert msg != GENERIC
    assert "HTML" in msg


def test_openai_timeout_message(monkeypatch):
    openai = pytest.importorskip("openai")
    import httpx
    exc = openai.APITimeoutError(request=httpx.Request("POST", "http://x"))
    msg = user_message(exc)
    assert "таймаут" in msg.lower()
    assert msg != GENERIC


def test_openai_connection_message():
    openai = pytest.importorskip("openai")
    import httpx
    exc = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    msg = user_message(exc)
    assert "связаться" in msg.lower() or "соединени" in msg.lower()
    assert msg != GENERIC


# ── error_code: коды классов для статистики (наружу уходит только код) ────────
def test_error_code_unknown_is_generic():
    assert error_code(RuntimeError("kaboom internal dump")) == "generic"


def test_error_code_value_error_is_input_invalid():
    """Кураторский ValueError = проблема исходника, а не наш сбой."""
    assert error_code(ValueError("формат .doc не поддерживается")) == "input_invalid"


def test_error_code_pipeline_classes():
    from htmlslides.assembler import AssembleError
    from htmlslides.pipeline.client import LLMFormatError
    from htmlslides.pipeline.filler import FillError
    assert error_code(LLMFormatError("truncated")) == "plan_format"
    assert error_code(FillError("slot")) == "fill"
    assert error_code(AssembleError("theme")) == "assemble"


def test_error_code_openai_classes():
    openai = pytest.importorskip("openai")
    import httpx
    req = httpx.Request("POST", "http://x")
    assert error_code(openai.APITimeoutError(request=req)) == "provider_timeout"
    assert error_code(
        openai.APIConnectionError(request=req)) == "provider_connection"


def test_error_code_never_leaks_error_text():
    """Код — перечисление; текст ошибки может нести фрагменты документа и наружу
    не уходит (запрет из нашего описания метрик для админки платформы)."""
    secret = "Договор с ООО Ромашка на 12 млн"
    for exc in (RuntimeError(secret), ValueError(secret)):
        code = error_code(exc)
        assert secret not in code
        assert code.replace("_", "").isalpha()   # только snake_case-латиница


# ── нечитаемый исходник (аудит 2026-08-14, A-1): без ложного «попробуйте ещё раз»
def test_unreadable_pptx_package_is_classified():
    """Битый/недокачанный .pptx: python-pptx кидает PackageNotFoundError. Раньше
    падало в GENERIC с советом «попробуйте ещё раз» — retry детерминированно
    воспроизводит тот же провал, совет врал."""
    pptx_exc = pytest.importorskip("pptx.exc")
    exc = pptx_exc.PackageNotFoundError("Package not found at '/tmp/секретный договор.pptx'")
    assert error_code(exc) == "input_unreadable"
    msg = user_message(exc)
    assert msg != GENERIC
    assert "попробуйте ещё раз" not in msg.lower()
    assert "файл" in msg.lower()
    assert "секретный" not in msg  # путь/имя исходника не течёт в UI


def test_unreadable_zip_is_classified():
    """То же для голого zipfile.BadZipFile (обрыв на середине архива)."""
    import zipfile
    exc = zipfile.BadZipFile("File is not a zip file")
    assert error_code(exc) == "input_unreadable"
    msg = user_message(exc)
    assert msg != GENERIC
    assert "попробуйте ещё раз" not in msg.lower()


def test_unreadable_docx_package_is_classified():
    """python-docx кидает СВОЙ PackageNotFoundError (docx.opc.exceptions)."""
    docx_exc = pytest.importorskip("docx.opc.exceptions")
    exc = docx_exc.PackageNotFoundError("Package not found at '/tmp/x.docx'")
    assert error_code(exc) == "input_unreadable"
    assert user_message(exc) != GENERIC


# ── недоступность модели (инцидент 2026-08-04): без ложного «повторите» ───────
def test_provider_unavailable_has_own_code_and_honest_text():
    """Обе модели молчат: советовать «повторите запуск» — врать. Повтор упрётся
    в ту же стену, а пользователь потеряет ещё полчаса."""
    from htmlslides.pipeline.client import ProviderUnavailable
    exc = ProviderUnavailable("нет живых моделей")
    assert error_code(exc) == "provider_unavailable"
    msg = user_message(exc)
    assert msg != GENERIC
    assert "повторите запуск" not in msg.lower()


def test_model_not_found_is_provider_unavailable_not_retry_advice():
    """404 = модель снята или переименована. Прежний текст звал «повторить запуск»
    — заведомо бесполезный совет: нужен администратор."""
    openai = pytest.importorskip("openai")
    import httpx
    resp = httpx.Response(404, request=httpx.Request("POST", "http://x"))
    exc = openai.NotFoundError("model not found", response=resp, body=None)
    assert error_code(exc) == "provider_unavailable"
    msg = user_message(exc)
    assert "повторите запуск" not in msg.lower()
    assert "администратор" in msg.lower()
