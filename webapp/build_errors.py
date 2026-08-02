"""Исключение сборки → человекочитаемое сообщение для пользователя.

Единая точка, где технический сбой пайплайна превращается в понятный багрепорт.
Раньше runner.py гасил ЛЮБОЕ исключение одной строкой «Внутренняя ошибка
сборки», из-за чего ни пользователь, ни мы по логам не понимали, что именно
сломалось: неподдерживаемый формат, обрыв связи с ИИ, лимит Cloud.ru или
настоящий баг. Теперь известные причины получают точный текст с подсказкой, что
делать; всё неизвестное — общий фолбэк (полный трейс остаётся в journalctl).

Импорты классов движка/openai ленивые и защищены try/except: юнит-тесты веб-слоя
крутятся без установленного пакета htmlslides и без openai, и модуль обязан
импортироваться в таком окружении.
"""
from __future__ import annotations

# Держим текст 1-в-1 с прежним поведением, чтобы существующие тесты и привычный
# UX не менялись для по-настоящему неизвестных сбоев.
GENERIC = "Внутренняя ошибка сборки — попробуйте ещё раз"


def user_message(exc: BaseException) -> str:
    """Понятная пользователю причина сбоя. Неизвестное → GENERIC."""
    return (_openai_message(exc)
            or _pipeline_message(exc)
            or _value_error_message(exc)
            or GENERIC)


def error_code(exc: BaseException) -> str:
    """Код класса сбоя для статистики (`by_error` в блоке админки платформы).

    Наружу уходит ТОЛЬКО этот код-перечисление: текст ошибки может нести фрагменты
    клиентского документа, а имена файлов и содержимое мы не отдаём никому. Коды
    держим рядом с `user_message`, чтобы классификация была одна на оба выхода
    (пользователю — текст, в метрику — код). Неизвестное → "generic".

    Watchdog-таймаут кодирует раннер ("build_timeout"): исключения-причины у него
    нет — сборку останавливает кооперативная отмена по дедлайну.
    """
    return (_openai_code(exc) or _pipeline_code(exc)
            or ("input_invalid" if isinstance(exc, ValueError) else None)
            or "generic")


def _openai_code(exc: BaseException) -> str | None:
    try:
        import openai
    except ImportError:
        return None
    # Порядок как в _openai_message: узкие подклассы раньше базовых.
    if isinstance(exc, openai.APITimeoutError):
        return "provider_timeout"
    if isinstance(exc, openai.APIConnectionError):
        return "provider_connection"
    if isinstance(exc, openai.RateLimitError):
        return "provider_ratelimit"
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "provider_auth"
    if isinstance(exc, openai.APIError):
        return "provider_error"
    return None


def _pipeline_code(exc: BaseException) -> str | None:
    try:
        from htmlslides.assembler import AssembleError
        from htmlslides.parsers.render import RenderUnavailable
        from htmlslides.pipeline.client import LLMFormatError
        from htmlslides.pipeline.filler import FillError
    except ImportError:
        return None
    if isinstance(exc, LLMFormatError):
        return "plan_format"
    if isinstance(exc, FillError):
        return "fill"
    if isinstance(exc, AssembleError):
        return "assemble"
    if isinstance(exc, RenderUnavailable):
        return "render_unavailable"
    return None


def _openai_message(exc: BaseException) -> str | None:
    """Сеть/лимиты/авторизация при обращении к Cloud.ru FM (openai-совместимый)."""
    try:
        import openai
    except ImportError:
        return None
    # Порядок важен: узкие подклассы раньше базовых (APITimeoutError <
    # APIConnectionError; RateLimitError/Auth < APIStatusError < APIError).
    if isinstance(exc, openai.APITimeoutError):
        return ("Сервис ИИ не ответил вовремя (таймаут). Обычно помогает "
                "повторный запуск через минуту.")
    if isinstance(exc, openai.APIConnectionError):
        return ("Не удалось связаться с сервисом ИИ (обрыв соединения). "
                "Проверьте подключение и повторите запуск.")
    if isinstance(exc, openai.RateLimitError):
        return ("Сервис ИИ перегружен запросами (лимит Cloud.ru). Подождите "
                "немного и повторите запуск.")
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ("Сервис ИИ отклонил авторизацию — вероятно, проблема с ключом "
                "Cloud.ru. Сообщите администратору.")
    if isinstance(exc, openai.APIStatusError):
        code = getattr(exc, "status_code", None)
        tail = f" (код {code})" if code else ""
        return (f"Сервис ИИ вернул ошибку{tail}. Повторите запуск; если "
                "повторяется — сообщите администратору.")
    if isinstance(exc, openai.APIError):
        return "Сбой при обращении к сервису ИИ. Повторите запуск."
    return None


def _pipeline_message(exc: BaseException) -> str | None:
    """Осмысленные сбои движка htmlslides (план/заполнение/сборка/рендер)."""
    try:
        from htmlslides.assembler import AssembleError
        from htmlslides.parsers.render import RenderUnavailable
        from htmlslides.pipeline.client import LLMFormatError
        from htmlslides.pipeline.filler import FillError
    except ImportError:
        return None
    if isinstance(exc, LLMFormatError):
        return ("Не удалось спланировать презентацию: модель вернула неполный "
                "ответ (возможно, превышен лимит Cloud.ru). Повторите запуск.")
    if isinstance(exc, FillError):
        return ("Не удалось оформить один из слайдов даже после повторной "
                "попытки. Повторите запуск — обычно со второго раза проходит.")
    if isinstance(exc, AssembleError):
        return ("Не удалось собрать HTML презентации (нарушен контракт "
                "шаблона). Повторите запуск; если повторяется — сообщите "
                "администратору.")
    if isinstance(exc, RenderUnavailable):
        return ("Не удалось обработать исходные слайды: на сервере недоступен "
                "рендер. Сообщите администратору.")
    return None


def _value_error_message(exc: BaseException) -> str | None:
    """ValueError из build_deck несёт кураторский русский текст (неподдерживаемый
    формат, «точный перенос не .docx», пустой источник) — показываем как есть."""
    if isinstance(exc, ValueError):
        text = str(exc).strip()
        if text:
            return text
    return None
