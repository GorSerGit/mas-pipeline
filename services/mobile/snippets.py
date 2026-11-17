"""Мини-RAG: эталонные фрагменты кода, которые подставляются в промпт по ключевым словам.

Фрагмент — markdown-файл с заголовком:
    ---
    keywords: retrofit, api, network
    dependencies: com.squareup.retrofit2:retrofit, org.jetbrains.kotlinx:kotlinx-serialization-json
    source: main            (main | test — куда фрагмент кладётся в эталонной сборке)
    ---
    пояснения и один блок ```kotlin
Версий во фрагментах нет — только код и координаты артефактов.
Подбор — по совпадению ключевых слов с текстом (путь и назначение файла, ошибки сборки):
фрагментов десяток, векторная база не нужна.
"""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Snippet:
    name: str
    keywords: tuple[str, ...]
    dependencies: tuple[str, ...]
    source: str
    body: str

    @property
    def code(self) -> str:
        m = re.search(r"```kotlin\n(.*?)```", self.body, flags=re.DOTALL)
        return m.group(1) if m else ""


def _split(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(",") if v.strip())


def load(directory: Path) -> list[Snippet]:
    snippets = []
    for path in sorted(Path(directory).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n(.*)", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"{path}: нет заголовка --- ... ---")
        meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
        snippets.append(Snippet(
            name=path.stem,
            keywords=_split(meta.get("keywords", "").lower()),
            dependencies=_split(meta.get("dependencies", "")),
            source=meta.get("source", "main").strip(),
            body=m.group(2).strip(),
        ))
    return snippets


def _words(text: str) -> set[str]:
    """Слова текста; CamelCase и пути разбиваются: ParkingApi.kt -> parking, api, kt."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return set(re.findall(r"[a-zа-яё0-9]+", text.lower()))


def select(snippets: list[Snippet], text: str, limit: int = 2) -> list[Snippet]:
    """До limit фрагментов с наибольшим числом совпавших ключевых слов."""
    words = _words(text)
    scored = [(sum(k in words for k in s.keywords), s.name, s) for s in snippets]
    return [s for score, _, s in sorted(scored, key=lambda x: (-x[0], x[1])) if score > 0][:limit]


def render(snippets: list[Snippet]) -> str:
    return "\n\n".join(f"### Эталон: {s.name}\n{s.body}" for s in snippets)
