"""Агент-разработчик Android (MOBILE).

Получает Task {"task", "context"} (в context уже лежит общий контракт) и возвращает
{"files": {путь: содержимое}, "tokens": int} — Gradle-проект на Kotlin + Jetpack Compose.

Как работает:
  1. design: Qwen проектирует структуру — пакет, разрешения, версии сборки, зависимости,
     Kotlin-файлы с публичными API. Версии — из ТЗ, а чего в ТЗ нет, выбирает Qwen;
     в коде МАС версий нет. Версия Kotlin из ТЗ проверяется.
  2. files: каждый файл генерируется отдельным запросом (до CONCURRENCY одновременно);
     в каждый запрос уходит вся структура, чтобы файлы сходились по именам.
  3. Сборочные файлы (Gradle, манифест, ресурсы, README) — из шаблонов, а не от LLM:
     так сборка предсказуема, Qwen задаёт только значения версий и список зависимостей.

Плохая генерация (невалидный JSON, чужой package, несбалансированные скобки, обрыв по лимиту)
повторяется до MAX_ATTEMPTS раз с причиной отказа, потом GenerationError — дальше решает человек.
"""

import asyncio
import json
import os
import re
from html import escape
from pathlib import Path

import android_build
import snippets
from llm import ask_llm
from generation_utils import MAX_ATTEMPTS, GenerationError, _extract_json, _strip_think

MAX_FILES = int(os.getenv("MOBILE_MAX_FILES", "14"))
CONCURRENCY = int(os.getenv("MOBILE_CONCURRENCY", "2"))  # общий лимит ключа делят все агенты (см. llm.py)
BASE_URL = os.getenv("MOBILE_BASE_URL", "http://10.0.2.2:8080/")  # хост-машина из эмулятора
COMPILE = os.getenv("MOBILE_COMPILE", "1") == "1"  # собирать проект и править ошибки компиляции и тестов
FIX_ROUNDS = int(os.getenv("MOBILE_FIX_ROUNDS", "5"))  # ошибок за раунд становится в 2–3 раза меньше
SNIPPETS = snippets.load(Path(__file__).parent / "snippets" / "android")
CONFIGURATIONS = {"implementation", "debugImplementation", "testImplementation", "androidTestImplementation"}
# Без них шаблон сборки не соберётся: Compose-активность и JUnit для unit-тестов
REQUIRED_DEPENDENCIES = {"androidx.activity:activity-compose", "junit:junit"}

LIBRARIES = (
    "Jetpack Compose Material 3 (+ material-icons-extended), Navigation Compose, Lifecycle ViewModel Compose, "
    "androidx.core (NotificationCompat), Kotlin Coroutines (+ kotlinx-coroutines-play-services), "
    "Retrofit + OkHttp + kotlinx.serialization (конвертер retrofit2.converter.kotlinx.serialization.asConverterFactory), "
    "Google Play Services Location (FusedLocationProviderClient); "
    "для unit-тестов — JUnit 4 и kotlinx-coroutines-test. Других библиотек нет: Room, Hilt, Koin, Gson недоступны."
)

DESIGN_SYSTEM = f"""Ты — ведущий Android-разработчик. По задаче и контексту спроектируй нативное Android-приложение на Kotlin + Jetpack Compose.
Доступные библиотеки: {LIBRARIES}

Правила:
1. Архитектура MVVM: data (API-клиент строго по контракту из контекста, модели, репозитории), бизнес-логика, ui (экраны Compose, ViewModel), MainActivity.
2. Файл MainActivity.kt обязателен и лежит в корне пакета: app/src/main/java/<пакет>/MainActivity.kt, класс MainActivity : ComponentActivity.
3. Не больше {MAX_FILES} Kotlin-файлов, из них хотя бы один unit-тест в app/src/test/java/<пакет>/... на бизнес-логику без Android-зависимостей.
4. Адрес бэкенда — BuildConfig.BASE_URL. Внешние сервисы — только через интерфейс-адаптер с mock-реализацией.
5. Gradle-файлы, AndroidManifest и ресурсы генерируются по шаблону: от тебя нужны только разрешения ("permissions"), версии ("versions") и зависимости ("dependencies").
6. Версии: если версия указана в контексте (ТЗ) — используй именно её. Остальные выбери стабильными и совместимыми между собой и с версией Kotlin: Android Gradle Plugin, Gradle, compileSdk/minSdk, библиотеки. Плагины Compose Compiler и kotlinx.serialization берут версию Kotlin.
7. dependencies — все библиотеки, которые используются в коде, включая {", ".join(sorted(REQUIRED_DEPENDENCIES))}. Координата "группа:артефакт:версия"; для BOM — "platform": true, тогда артефакты из него указывай без версии.
8. "purpose" каждого файла — точные публичные объявления, которые он содержит (классы, интерфейсы, функции с сигнатурами).
   "depends_on" — пути файлов проекта, чьи объявления этот файл использует, без циклов. Файлы пишутся в этом порядке, и каждый видит готовые объявления своих зависимостей: модели ← API ← репозиторий ← бизнес-логика ← ViewModel ← экраны ← MainActivity; unit-тест зависит от проверяемых файлов.
9. Для типовых тем есть эталонный код, он будет подставлен при написании файлов. Если файл относится к теме — в dependencies должны быть её артефакты:
{chr(10).join(f"   - {s.name} ({', '.join(s.keywords[:5])}): {', '.join(s.dependencies) or 'без зависимостей'}" for s in SNIPPETS)}

Ответь только JSON без пояснений:
{{"app_name": "...", "package": "ru.example.app", "permissions": ["android.permission.INTERNET"],
 "versions": {{"kotlin": "<версия>", "android_gradle_plugin": "<версия>", "gradle": "<версия>", "compile_sdk": <число>, "min_sdk": <число>}},
 "dependencies": [{{"configuration": "implementation", "coordinate": "группа:артефакт:<версия>", "platform": false}}],
 "files": [{{"path": "app/src/main/java/ru/example/app/MainActivity.kt", "purpose": "...", "depends_on": ["app/src/main/java/ru/example/app/ui/MainScreen.kt"]}}]}}"""

FILE_SYSTEM = f"""Ты — Android-разработчик. Напиши ровно один файл Kotlin-проекта (Kotlin + Jetpack Compose, MVVM) по его описанию в структуре проекта.
Доступные библиотеки: {LIBRARIES}
Правила:
- Первая строка кода — package, соответствующий пути файла.
- Объявления других файлов проекта импортируй по их пакетам. Если даны объявления уже написанных файлов — используй ровно их имена, параметры и типы; иначе — имена из структуры проекта.
- Соблюдай контракт API из контекста без изменений. Адрес бэкенда — BuildConfig.BASE_URL.
- Код полный и компилируемый, без заглушек TODO() и без пояснений вне кода.
- Если даны эталоны — следуй их API, импортам и подходу; package и имена бери из структуры проекта.
Ответь только содержимым файла в одном блоке ```kotlin."""

FIX_SYSTEM = f"""Ты — Android-разработчик. Сборка Kotlin-проекта (Kotlin + Jetpack Compose) упала — исправь ошибки в присланных файлах.
Доступные библиотеки: {LIBRARIES} Используй только зависимости из структуры проекта.
Правила:
- Меняй только то, что нужно для исправления. Присланы файл с ошибками и файлы, на которые он ссылается: согласуй их между собой (например, добавь недостающее поле в ViewModel или используй существующее). Публичные объявления, на которые ссылаются другие файлы проекта, не переименовывай.
- Если упал unit-тест — по критериям приёмки из контекста реши, где ошибка: в коде или в ожиданиях теста, и исправь неверное.
- Если даны эталоны — следуй их API и импортам.
- Каждый исправленный файл — целиком, в отдельном блоке ```kotlin, первая строка блока — // FILE: <путь>. Файлы без изменений не присылай."""

CONFIG_SYSTEM = """Ты — Android-разработчик. Сборка Gradle упала на конфигурации проекта: версиях плагинов, Gradle, SDK или зависимостях.
Исправь versions и dependencies: версии — выпущенные, стабильные и совместимые между собой; версию Kotlin, указанную в ТЗ, менять нельзя.
Если приведён список выпущенных версий из репозиториев — бери версию только из него, не угадывай.
Ответь только JSON в том же формате, что сейчас: {"versions": {...}, "dependencies": [...]}"""

PACKAGE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
PERMISSION_RE = re.compile(r"^android\.permission\.[A-Z_]+$")
VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}(-[A-Za-z0-9.]+)?$")
COORDINATE_RE = re.compile(r"^([\w.-]+:[\w.-]+)(:[\w.+-]+)?$")


def _kotlin_version(context: str) -> str | None:
    """Версия Kotlin, указанная в ТЗ, или None."""
    m = re.search(r"Kotlin\D{0,3}(\d+\.\d+(?:\.\d+)?)", context, flags=re.IGNORECASE)
    return m.group(1) if m else None


def _validate_versions(v, context: str) -> dict:
    if not isinstance(v, dict):
        raise ValueError("versions — объект {kotlin, android_gradle_plugin, gradle, compile_sdk, min_sdk}")
    for key in ("kotlin", "android_gradle_plugin", "gradle"):
        if not isinstance(v.get(key), str) or not VERSION_RE.match(v[key]):
            raise ValueError(f"versions.{key} = {v.get(key)!r} — нужна конкретная версия вида X.Y.Z")
    required = _kotlin_version(context)
    if required and v["kotlin"] != required and not v["kotlin"].startswith(required + "."):
        raise ValueError(f"versions.kotlin = {v['kotlin']}, а в ТЗ указан Kotlin {required}")
    for key in ("compile_sdk", "min_sdk"):
        if not isinstance(v.get(key), int) or isinstance(v[key], bool) or v[key] < 21:
            raise ValueError(f"versions.{key} — целый уровень API не ниже 21")
    if v["min_sdk"] > v["compile_sdk"]:
        raise ValueError("versions.min_sdk больше compile_sdk")
    return {k: v[k] for k in ("kotlin", "android_gradle_plugin", "gradle", "compile_sdk", "min_sdk")}


def _validate_dependencies(deps) -> list[dict]:
    if not isinstance(deps, list) or not deps:
        raise ValueError("dependencies — непустой список {configuration, coordinate, platform}")
    result, has_platform, artifacts = [], False, set()
    for d in deps:
        if not isinstance(d, dict) or d.get("configuration") not in CONFIGURATIONS:
            raise ValueError(f"dependency {d!r}: configuration — одна из {sorted(CONFIGURATIONS)}")
        m = COORDINATE_RE.match(d.get("coordinate") or "") if isinstance(d.get("coordinate"), str) else None
        if not m:
            raise ValueError(f"dependency {d.get('coordinate')!r}: координата группа:артефакт[:версия]")
        platform = d.get("platform") is True
        if platform and not m.group(2):
            raise ValueError(f"BOM {d['coordinate']} без версии")
        has_platform |= platform
        artifacts.add(m.group(1))
        result.append({"configuration": d["configuration"], "coordinate": d["coordinate"], "platform": platform})
    if not has_platform:
        missing = [d["coordinate"] for d in result if not COORDINATE_RE.match(d["coordinate"]).group(2)]
        if missing:
            raise ValueError(f"без версии и без BOM: {missing}")
    missing = REQUIRED_DEPENDENCIES - artifacts
    if missing:
        raise ValueError(f"в dependencies нет {sorted(missing)}")
    return result


def _validate_design(text: str, context: str) -> dict:
    d = _extract_json(text, dict)
    pkg = d.get("package")
    if not isinstance(pkg, str) or not PACKAGE_RE.match(pkg):
        raise ValueError(f"package {pkg!r} — нужен вида ru.example.app (строчные латинские, через точку)")
    if not isinstance(d.get("app_name"), str) or not d["app_name"].strip():
        raise ValueError("нет app_name")

    perms = d.get("permissions", [])
    if not isinstance(perms, list) or not all(isinstance(p, str) and PERMISSION_RE.match(p) for p in perms):
        raise ValueError("permissions — список вида android.permission.ИМЯ")
    perms = sorted(set(perms) | {"android.permission.INTERNET"})

    files = d.get("files")
    if not isinstance(files, list) or not 2 <= len(files) <= MAX_FILES:
        raise ValueError(f"files — список от 2 до {MAX_FILES} файлов")
    pkg_dir = pkg.replace(".", "/")
    path_re = re.compile(rf"^app/src/(main|test)/java/{re.escape(pkg_dir)}/([a-z0-9_]+/)*[A-Z]\w*\.kt$")
    seen = set()
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("path"), str) or not isinstance(f.get("purpose"), str):
            raise ValueError("элемент files — объект {path, purpose}")
        if not path_re.match(f["path"]):
            raise ValueError(f"путь {f['path']!r} должен быть app/src/main|test/java/{pkg_dir}/.../Имя.kt")
        if f["path"] in seen:
            raise ValueError(f"файл {f['path']} повторяется")
        seen.add(f["path"])
    if f"app/src/main/java/{pkg_dir}/MainActivity.kt" not in seen:
        raise ValueError(f"нет app/src/main/java/{pkg_dir}/MainActivity.kt")
    if not any(p.startswith("app/src/test/") for p in seen):
        raise ValueError("нет ни одного unit-теста в app/src/test/java/...")
    for f in files:
        deps = f.get("depends_on")
        if not isinstance(deps, list) or not all(isinstance(p, str) for p in deps):
            raise ValueError(f"у файла {f['path']} нет depends_on — список путей файлов проекта (может быть пустым)")
        unknown = [p for p in deps if p not in seen or p == f["path"]]
        if unknown:
            raise ValueError(f"depends_on файла {f['path']}: {unknown} — не файлы проекта из files")

    files = [{"path": f["path"], "purpose": f["purpose"], "depends_on": list(dict.fromkeys(f["depends_on"]))}
             for f in files]
    _layers(files)  # циклы — ошибка проектирования
    dependencies = _validate_dependencies(d.get("dependencies"))
    _check_snippet_dependencies(files, dependencies)
    return {"app_name": d["app_name"].strip(), "package": pkg, "permissions": perms,
            "versions": _validate_versions(d.get("versions"), context),
            "dependencies": dependencies, "files": files}


def _layers(files: list[dict]) -> list[list[dict]]:
    """Слои генерации: сначала файлы без зависимостей, потом те, чьи зависимости уже написаны."""
    left, done, layers = list(files), set(), []
    while left:
        layer = [f for f in left if set(f["depends_on"]) <= done]
        if not layer:
            raise ValueError("циклические depends_on: " + "; ".join(
                f"{Path(f['path']).name} -> {[Path(p).name for p in f['depends_on'] if p not in done]}" for f in left))
        layers.append(layer)
        done |= {f["path"] for f in layer}
        left = [f for f in left if f["path"] not in done]
    return layers


def _transitive_deps(path: str, files: list[dict]) -> list[str]:
    """Все зависимости файла (и зависимости зависимостей) в порядке генерации."""
    by_path = {f["path"]: f for f in files}
    seen: list[str] = []

    def visit(p: str):
        for d in by_path[p]["depends_on"]:
            if d not in seen:
                visit(d)
                seen.append(d)
    visit(path)
    return seen


def _outline(code: str) -> str:
    """Публичные объявления файла без тел функций: package, классы с полями и сигнатурами, функции.

    Тела классов (class/interface/object) сохраняются, тела функций, лямбд, init — сворачиваются в { … }.
    Скобки внутри строк и комментариев не считаются.
    """
    out: list[str] = []
    skip_depth = 0           # > 0 — внутри свёрнутого тела
    header = ""              # текст текущего объявления — по нему решаем, класс это или функция
    tokens = r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])\'|//[^\n]*|/\*[\s\S]*?\*/|[{};]|[^{};"\'/]+|[\s\S]'
    for token in re.findall(tokens, code):
        if skip_depth:
            skip_depth += {"{": 1, "}": -1}.get(token, 0)
            continue
        if token.startswith("//") or token.startswith("/*"):
            continue
        if token == "{":
            kinds = re.findall(r"\b(class|interface|object|fun)\b", header)
            if kinds and kinds[-1] != "fun":
                out.append("{")
            else:
                out.append("{ … }")
                skip_depth = 1
            header = ""
        elif token in ("}", ";"):
            out.append(token)
            header = ""
        else:
            out.append(token)
            header += token
    lines = [l.rstrip() for l in "".join(out).splitlines()]
    keep = [l for l in lines if l.strip() and not l.strip().startswith(("import ", "private ", "@file:"))]
    return "\n".join(keep)


def _snippets_for(text: str) -> list[snippets.Snippet]:
    return snippets.select(SNIPPETS, text)


def _check_snippet_dependencies(files: list[dict], dependencies: list[dict]):
    """Файлу подставят эталон — значит, артефакты эталона должны быть в зависимостях."""
    artifacts = {COORDINATE_RE.match(d["coordinate"]).group(1) for d in dependencies}
    for f in files:
        for s in _snippets_for(f"{f['path']} {f['purpose']}"):
            missing = sorted(set(s.dependencies) - artifacts)
            if missing:
                raise ValueError(f"файл {f['path']} относится к теме '{s.name}' — добавь в dependencies {missing}")


def _strip_literals(code: str) -> str:
    """Убирает строки, символы и комментарии — чтобы считать скобки только в коде."""
    return re.sub(r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])+\'|//[^\n]*|/\*[\s\S]*?\*/', "", code)


def _validate_kotlin(text: str, path: str) -> str:
    text = _strip_think(text)
    blocks = re.findall(r"```(?:kotlin|kt)?[^\n]*\n(.*?)```", text, flags=re.DOTALL)
    code = (max(blocks, key=len) if blocks else text).strip()
    if not code:
        raise ValueError("пустой файл")

    expected = path.split("/java/", 1)[1].rsplit("/", 1)[0].replace("/", ".")
    first = next((l.strip() for l in _strip_literals(code).splitlines() if l.strip() and not l.strip().startswith("@file:")), "")
    if first != f"package {expected}":
        raise ValueError(f"первая строка должна быть 'package {expected}', а не {first[:80]!r}")

    bare = _strip_literals(code)
    for open_, close in ("{}", "()", "[]"):
        if bare.count(open_) != bare.count(close):
            raise ValueError(f"несбалансированные скобки {open_}{close} — файл оборван или с ошибкой")
    if path.endswith("/MainActivity.kt") and "class MainActivity" not in code:
        raise ValueError("в MainActivity.kt нет class MainActivity")
    return code + "\n"


def _block_path(block: str, before: str, allowed: set[str]) -> tuple[str | None, str]:
    """Чей это блок: // FILE: первой строкой, путь в строке перед блоком или по package и классу."""
    m = re.match(r"\s*//\s*FILE:\s*(\S+)[^\n]*\n", block)
    if m:
        return m.group(1), block[m.end():]
    last_line = before.rstrip().rsplit("\n", 1)[-1]
    for path in allowed:
        if path in last_line:
            return path, block
    pkg = re.search(r"^\s*package\s+([\w.]+)", block, flags=re.MULTILINE)
    if pkg:
        candidates = [p for p in allowed
                      if p.split("/java/", 1)[1].rsplit("/", 1)[0].replace("/", ".") == pkg.group(1)
                      and Path(p).stem in DECLARATION_RE.findall(block)]
        if len(candidates) == 1:
            return candidates[0], block
    return None, block


def _validate_fix(text: str, allowed: set[str]) -> dict[str, str]:
    """Ответ на исправление: блоки ```kotlin, у каждого — // FILE: <путь> первой строкой (или путь перед блоком)."""
    text = _strip_think(text)
    fixed = {}
    for m in re.finditer(r"```(?:kotlin|kt)?[^\n]*\n(.*?)```", text, flags=re.DOTALL):
        path, code = _block_path(m.group(1), text[:m.start()], allowed)
        if path is None:
            continue
        if path not in allowed:
            raise ValueError(f"файл {path} не из исправляемых: {sorted(allowed)}")
        fixed[path] = _validate_kotlin(code, path)
    if not fixed:
        raise ValueError("нет ни одного блока ```kotlin с первой строкой // FILE: <путь>")
    return fixed


async def _ask_until_valid(step: str, prompt: str, system: str, think: bool, validate) -> tuple:
    """Запрос к Qwen до MAX_ATTEMPTS раз, пока validate не примет ответ. Возвращает (значение, токены)."""
    tokens, feedback = 0, ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        p = prompt + (f"\n\nПредыдущий ответ отклонён: {feedback}. Исправь." if feedback else "")
        r = await ask_llm(p, system=system, think=think)
        tokens += r["tokens"]
        try:
            if r.get("truncated"):
                raise ValueError("ответ обрезан по лимиту токенов — сделай короче")
            return validate(r["text"]), tokens
        except ValueError as e:
            feedback = str(e)
            tail = r["text"][-300:].replace("\n", "\\n")
            print(f"[MOBILE {step}] попытка {attempt} отклонена: {feedback}\n    хвост ответа: {tail}", flush=True)
    raise GenerationError(f"MOBILE {step}", feedback, MAX_ATTEMPTS, "MOBILE")


def _render(template: str, **values) -> str:
    for key, value in values.items():
        template = template.replace(f"@{key}@", str(value))
    return template


SETTINGS = """\
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "@NAME@"
include(":app")
"""

ROOT_BUILD = """\
plugins {
    id("com.android.application") version "@AGP@" apply false
    id("org.jetbrains.kotlin.android") version "@KOTLIN@" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "@KOTLIN@" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "@KOTLIN@" apply false
}
"""

APP_BUILD = """\
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "@PKG@"
    compileSdk = @SDK@

    defaultConfig {
        applicationId = "@PKG@"
        minSdk = @MIN_SDK@
        targetSdk = @SDK@
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "BASE_URL", "\\"@BASE_URL@\\"")
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
@DEPENDENCIES@
}
"""

MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

@PERMISSIONS@

    <application
        android:allowBackup="true"
        android:label="@string/app_name"
        android:usesCleartextTraffic="true"
        android:theme="@android:style/Theme.Material.Light.NoActionBar">
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""

README = """\
# @APP_NAME@ — Android

Нативное Android-приложение: Kotlin @KOTLIN@, Jetpack Compose, MVVM. Пакет `@PKG@`.

## Запуск

Нужны JDK 17 и Android SDK (API @SDK@) — проще всего открыть папку в Android Studio.

1. Сгенерировать Gradle Wrapper (бинарного `gradle-wrapper.jar` в архиве нет):
   ```
   gradle wrapper --gradle-version @GRADLE@
   ```
2. Собрать APK: `./gradlew assembleDebug` → `app/build/outputs/apk/debug/app-debug.apk`
3. Unit-тесты: `./gradlew testDebugUnitTest`
4. Установить на эмулятор или устройство: `adb install -r app/build/outputs/apk/debug/app-debug.apk`

Адрес бэкенда — `BASE_URL` в `app/build.gradle.kts` (по умолчанию `@BASE_URL@`: так эмулятор видит localhost хост-машины).

## Структура

@STRUCTURE@
"""


def _render_dependencies(deps: list[dict]) -> str:
    return "\n".join(
        f'    {d["configuration"]}(platform("{d["coordinate"]}"))' if d["platform"]
        else f'    {d["configuration"]}("{d["coordinate"]}")'
        for d in deps
    )


def _scaffold(design: dict) -> dict[str, str]:
    """Сборочные файлы проекта из шаблонов; версии и зависимости — из design."""
    pkg = design["package"]
    app_name = design["app_name"]
    v = design["versions"]
    common = dict(PKG=pkg, KOTLIN=v["kotlin"], AGP=v["android_gradle_plugin"], GRADLE=v["gradle"],
                  SDK=v["compile_sdk"], MIN_SDK=v["min_sdk"], BASE_URL=BASE_URL)
    android_string = escape(app_name, quote=False).replace("'", "\\'").replace('"', '\\"')
    return {
        "settings.gradle.kts": _render(SETTINGS, NAME=pkg.rsplit(".", 1)[-1]),
        "build.gradle.kts": _render(ROOT_BUILD, **common),
        "gradle.properties": "org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8\n"
                             "android.useAndroidX=true\nkotlin.code.style=official\n",
        "gradle/wrapper/gradle-wrapper.properties":
            f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{v['gradle']}-bin.zip\n",
        "app/build.gradle.kts": _render(APP_BUILD, DEPENDENCIES=_render_dependencies(design["dependencies"]), **common),
        "app/src/main/AndroidManifest.xml": _render(MANIFEST, PERMISSIONS="\n".join(
            f'    <uses-permission android:name="{p}" />' for p in design["permissions"])),
        "app/src/main/res/values/strings.xml":
            f'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
            f'    <string name="app_name">{android_string}</string>\n</resources>\n',
        ".gitignore": ".gradle/\nbuild/\nlocal.properties\n.idea/\n*.iml\n",
        "README.md": _render(README, APP_NAME=app_name, STRUCTURE="\n".join(
            f"- `{f['path']}` — {f['purpose']}" for f in design["files"]), **common),
    }


async def mobile_agent(task: dict) -> dict:
    """Task {"task", "context"} -> {"files": {путь: содержимое}, "tokens": int}."""
    brief = f"Задача:\n{task['task']}\n\nКонтекст:\n{task['context']}"
    # Без рассуждений: иначе Qwen продумывает в них весь проект и упирается в лимит токенов
    design, tokens = await _ask_until_valid("design", brief, DESIGN_SYSTEM, False,
                                            lambda text: _validate_design(text, task["context"]))

    if COMPILE:  # несуществующие версии ловим по репозиториям сразу, а не раундами сборки
        design, t = await _ensure_versions_exist(task, design)
        tokens += t

    structure = json.dumps(design, ensure_ascii=False, indent=2)
    sem = asyncio.Semaphore(CONCURRENCY)

    files = _scaffold(design)

    async def write_file(f: dict) -> tuple:
        refs = _snippets_for(f"{f['path']} {f['purpose']}")
        ready = _transitive_deps(f["path"], design["files"])
        prompt = (f"{brief}\n\nСтруктура проекта:\n{structure}"
                  + ("\n\nУже написанные файлы, от которых зависит этот, — их объявления (используй ровно эти имена, "
                     "параметры и типы; тела функций опущены):\n"
                     + "\n\n".join(f"```kotlin\n// FILE: {p}\n{_outline(files[p])}\n```" for p in ready) if ready else "")
                  + f"\n\nНапиши файл {f['path']}\nНазначение: {f['purpose']}"
                  + (f"\n\nЭталоны:\n{snippets.render(refs)}" if refs else ""))
        async with sem:
            return await _ask_until_valid(f["path"], prompt, FILE_SYSTEM, False,
                                          lambda text: _validate_kotlin(text, f["path"]))

    # По слоям зависимостей: каждый файл видит готовые объявления тех, от кого зависит
    for layer in _layers(design["files"]):
        written = await asyncio.gather(*(write_file(f) for f in layer))
        for f, (code, t) in zip(layer, written):
            files[f["path"]] = code
            tokens += t
    if COMPILE:
        files, t = await _build_and_fix(task, design, files, sem)
        tokens += t
    return {"files": files, "tokens": tokens}


DECLARATION_RE = re.compile(
    r"^(?:(?:public|internal|data|sealed|abstract|open|enum|inline|value|annotation|const|suspend)\s+)*"
    r"(?:class|interface|object|fun|val|var|typealias)\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?(\w+)", flags=re.MULTILINE)


def _related_files(path: str, files: dict[str, str], limit: int = 5) -> list[str]:
    """Файлы проекта, на которые ссылается файл: по импортам объявлений и по именам классов-файлов."""
    code = files[path]
    declared: dict[str, str] = {}  # "пакет.Имя" -> файл
    for p, c in files.items():
        if p.endswith(".kt") and p != path:
            pkg = p.split("/java/", 1)[1].rsplit("/", 1)[0].replace("/", ".")
            for name in DECLARATION_RE.findall(c):
                declared.setdefault(f"{pkg}.{name}", p)
    related = [declared[i] for i in re.findall(r"^import ([\w.]+)", code, flags=re.MULTILINE) if i in declared]
    related += [p for p in files if p.startswith("app/src/main/") and p.endswith(".kt") and p != path
                and re.search(rf"\b{re.escape(Path(p).stem)}\b", code)]
    return list(dict.fromkeys(related))[:limit]


def _fix_groups(result: android_build.BuildResult, files: dict[str, str]) -> list[tuple[list[str], str]]:
    """Что исправлять вместе: файл с ошибками (или упавший тест) + файлы проекта, на которые он ссылается.

    Ошибки часто на стыке файлов (обращение к полю, которого нет в ViewModel), поэтому исправитель видит
    и может править обе стороны. Группы правятся по очереди — каждая видит правки предыдущих.
    """
    problems = result.file_errors or result.test_failures
    groups, covered = [], set()
    for path in problems:
        if path not in files or path in covered:
            continue  # файл уже правится в группе, где он связанный
        paths = [path] + _related_files(path, files)
        covered |= set(paths)
        errors = "\n".join(f"{p}:\n  " + "\n  ".join(_quote_lines(problems[p], files[p]))
                           for p in paths if p in problems)
        groups.append((paths, errors))
    return groups


def _quote_lines(errors: list[str], code: str) -> list[str]:
    """К ошибке «строка:столбец текст» — сама строка кода: номера строк модель не считает."""
    lines = code.splitlines()
    quoted = []
    for e in errors:
        m = re.match(r"(\d+):\d+ ", e)
        n = int(m.group(1)) if m else 0
        quoted.append(f"{e}\n      > {lines[n - 1].strip()}" if 0 < n <= len(lines) else e)
    return quoted


async def _fix_group(task: dict, design: dict, files: dict[str, str], paths: list[str], errors: str,
                     with_brief: bool, sem: asyncio.Semaphore, think: bool = False) -> tuple[dict, int]:
    purposes = " ".join(f"{f['path']} {f['purpose']}" for f in design["files"] if f["path"] in paths)
    refs = _snippets_for(f"{purposes} {errors}")
    code = "\n\n".join(f"```kotlin\n// FILE: {p}\n{files[p]}```" for p in paths)
    prompt = (
        (f"Задача:\n{task['task']}\n\nКонтекст:\n{task['context']}\n\n" if with_brief else "")
        + f"Структура проекта:\n{json.dumps(design, ensure_ascii=False, indent=2)}\n\n"
        + f"Ошибки сборки:\n{errors}\n\nФайлы:\n{code}"
        + (f"\n\nЭталоны:\n{snippets.render(refs)}" if refs else "")
    )
    async with sem:
        return await _ask_until_valid(f"fix {', '.join(Path(p).name for p in paths)}", prompt, FIX_SYSTEM, think,
                                      lambda text: _validate_fix(text, set(paths)))


async def _ensure_versions_exist(task: dict, design: dict) -> tuple[dict, int]:
    """Сверяет версии с репозиториями Maven и Gradle; не выпущенные — исправляет Qwen по списку реальных."""
    tokens = 0
    for attempt in range(FIX_ROUNDS + 1):
        problems = await asyncio.to_thread(android_build.check_versions, design["versions"], design["dependencies"])
        if not problems:
            return design, tokens
        print(f"[MOBILE versions] проверка {attempt + 1}: " + "; ".join(problems), flush=True)
        if attempt == FIX_ROUNDS:
            break
        design, t = await _fix_config(task, design, "\n".join(problems), hints=False)
        tokens += t
    raise GenerationError("MOBILE versions", "\n".join(problems), FIX_ROUNDS, "MOBILE")


async def _fix_config(task: dict, design: dict, error: str, hints: bool = True) -> tuple[dict, int]:
    kotlin = _kotlin_version(task["context"])
    known = await asyncio.to_thread(android_build.hints_for_error, error) if hints else []
    prompt = (
        f"Сейчас:\n{json.dumps({'versions': design['versions'], 'dependencies': design['dependencies']}, ensure_ascii=False, indent=2)}"
        f"\n\nОшибка сборки:\n{error}"
        + ("\n\nПо репозиториям:\n" + "\n".join(known) if known else "")
        + (f"\n\nВ ТЗ указан Kotlin {kotlin}." if kotlin else "")
    )

    def validate(text: str) -> dict:
        d = _extract_json(text, dict)
        dependencies = _validate_dependencies(d.get("dependencies"))
        _check_snippet_dependencies(design["files"], dependencies)
        return {**design, "versions": _validate_versions(d.get("versions"), task["context"]),
                "dependencies": dependencies}

    return await _ask_until_valid("fix build config", prompt, CONFIG_SYSTEM, False, validate)


async def _build_and_fix(task: dict, design: dict, files: dict[str, str],
                         sem: asyncio.Semaphore) -> tuple[dict[str, str], int]:
    """Сборка + unit-тесты; ошибки отдаются Qwen до FIX_ROUNDS раундов, потом — GenerationError."""
    tokens = 0
    previous: dict[str, list[str]] = {}  # ошибки прошлого раунда — чтобы заметить застой
    for round_ in range(FIX_ROUNDS + 1):
        result = await android_build.build_project(files, design["versions"]["gradle"])
        if result.ok:
            print(f"[MOBILE build] собрано, unit-тесты прошли; раундов исправления: {round_}", flush=True)
            files["README.md"] += ("\n## Проверка\n\nПри генерации проект собран (`assembleDebug`) и прошёл "
                                   f"unit-тесты (`testDebugUnitTest`); раундов исправления: {round_}.\n")
            return files, tokens
        print(f"[MOBILE build] раунд {round_ + 1}: сборка не прошла\n{result.summary()[:1500]}", flush=True)
        if round_ == FIX_ROUNDS:
            break
        if result.config_error:
            design, t = await _fix_config(task, design, result.config_error)
            files.update(_scaffold(design))
            tokens += t
            continue
        groups = _fix_groups(result, files)
        if not groups:
            break
        current = result.file_errors or result.test_failures
        for paths, errors in groups:  # по очереди: следующая группа видит правки предыдущей
            # те же ошибки, что в прошлом раунде, — без рассуждений не исправилось, включаем их
            stuck = any(p in previous and _error_texts(previous[p]) == _error_texts(current[p])
                        for p in paths if p in current)
            if stuck:
                print(f"[MOBILE build] застой в {[Path(p).name for p in paths]} — исправление с рассуждениями", flush=True)
            try:
                changed, t = await _fix_group(task, design, files, paths, errors, bool(result.test_failures), sem,
                                              think=stuck)
            except GenerationError as e:  # не вышло — группа ждёт следующего раунда, итог решит сборка
                print(f"[MOBILE build] группу {[Path(p).name for p in paths]} не исправить: {e.message}", flush=True)
                continue
            files.update(changed)
            tokens += t
        previous = current
    raise GenerationError("MOBILE build", result.summary()[:3000], FIX_ROUNDS, "MOBILE")


def _error_texts(errors: list[str]) -> list[str]:
    """Ошибки без номеров строк: после правки строки сдвигаются, а ошибка та же."""
    return sorted(re.sub(r"^\d+:\d+ ", "", e) for e in errors)
