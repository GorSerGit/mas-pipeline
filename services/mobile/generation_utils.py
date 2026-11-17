"""Local generation helpers; no orchestration dependencies."""
import json

MAX_ATTEMPTS = 3

class GenerationError(Exception):
    """Генерация не удалась за MAX_ATTEMPTS попыток — нужен человек."""

    def __init__(self, step: str, message: str, attempts: int, agent: str | None = None):
        super().__init__(message)
        self.step = step
        self.message = message
        self.attempts = attempts
        self.agent = agent

    def to_dict(self) -> dict:
        return {
            "error": "generation_failed",
            "step": self.step,
            "agent": self.agent,
            "attempts": self.attempts,
            "last_error": self.message,
        }

def _strip_think(text: str) -> str:
    """Qwen3 возвращает рассуждения перед </think> (открывающего тега может не быть) — отбрасываем их."""
    return text.rsplit("</think>", 1)[-1].strip()

def _extract_json(text: str, kind: type = list):
    """Последний JSON-массив (или объект при kind=dict) в ответе: Qwen может писать похожие на JSON куски в рассуждениях."""
    text = _strip_think(text)
    opener = "[" if kind is list else "{"
    decoder, found = json.JSONDecoder(), None
    i = text.find(opener)
    while i != -1:
        try:
            value, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            end = i + 1
        else:
            if isinstance(value, kind):
                found = value  # вложенные скобки внутри найденного значения пропускаем
        i = text.find(opener, end)
    if found is None:
        raise ValueError(f"в ответе нет валидного JSON-{'массива' if kind is list else 'объекта'}")
    return found
