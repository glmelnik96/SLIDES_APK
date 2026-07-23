"""Классификатор сбоев сборки → понятный пользователю текст (webapp.build_errors)."""
import pytest

from webapp.build_errors import GENERIC, user_message


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
