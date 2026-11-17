"""Сборка Android-проекта Gradle'ом — проверка кода, который сгенерировал агент MOBILE.

Нужны JDK и Android SDK (ANDROID_HOME, лицензии приняты) — они ставятся в Docker-образ.
Gradle нужной версии (её выбирает Qwen) скачивается при первой сборке и кэшируется,
недостающие платформы SDK докачивает Android Gradle Plugin.

Итог сборки делится на три вида проблем:
  - ошибки компиляции Kotlin по файлам и упавшие unit-тесты — их правит Qwen;
  - ошибка конфигурации (версии, зависимости) — тоже правит Qwen;
  - нет сети, нет JDK/SDK, таймаут — BuildInfraError: это ошибка среды МАС, прогон падает.
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

CACHE = Path(os.getenv("ANDROID_BUILD_CACHE", "/cache"))
BUILD_TIMEOUT = float(os.getenv("MOBILE_BUILD_TIMEOUT", "1800"))
GRADLE_DISTRIBUTIONS = "https://services.gradle.org/distributions"
TASKS = ["assembleDebug", "testDebugUnitTest"]

NETWORK_ERROR_RE = re.compile(
    r"UnknownHostException|Connection refused|Connect timed out|Read timed out|"
    r"Network is unreachable|No route to host|SSLHandshakeException|Temporary failure in name resolution",
    flags=re.IGNORECASE,
)
MAVEN_REPOSITORIES = [  # те же, что в шаблоне settings.gradle.kts
    "https://dl.google.com/dl/android/maven2",
    "https://repo.maven.apache.org/maven2",
    "https://plugins.gradle.org/m2",
]
STABLE_VERSION_RE = re.compile(r"^\d+(\.\d+)*$")
KOTLIN_ERROR_RE = re.compile(r"^e: (?:file://)?(\S+?\.kts?):(\d+):(\d+):? (.+)$", flags=re.MULTILINE)

_lock = asyncio.Lock()  # сборки по одной: Gradle и SDK-кэш общие


class BuildInfraError(RuntimeError):
    """Сборку нельзя выполнить из-за среды (сеть, JDK, SDK, таймаут) — не вина сгенерированного кода."""


@dataclass
class BuildResult:
    ok: bool
    file_errors: dict[str, list[str]] = field(default_factory=dict)    # путь -> ошибки компиляции
    test_failures: dict[str, list[str]] = field(default_factory=dict)  # путь теста -> упавшие проверки
    config_error: str | None = None                                    # версии, зависимости, Gradle
    log_tail: str = ""

    def summary(self) -> str:
        parts = [f"{p}:\n  " + "\n  ".join(e) for p, e in {**self.file_errors, **self.test_failures}.items()]
        if self.config_error:
            parts.append(self.config_error)
        return "\n".join(parts) or self.log_tail


def check_environment():
    """JDK есть, SDK-папка с принятыми лицензиями готова (сам SDK докачает Android Gradle Plugin)."""
    if not shutil.which("java"):
        raise BuildInfraError("нет JDK (java) — сборка Android невозможна")
    sdk = os.getenv("ANDROID_HOME")
    if not sdk:
        raise BuildInfraError("не задан ANDROID_HOME")
    licenses = os.getenv("ANDROID_LICENSES")  # в образе лицензии лежат отдельно, SDK — в томе кэша
    if licenses and Path(licenses).is_dir():
        shutil.copytree(licenses, Path(sdk) / "licenses", dirs_exist_ok=True)
    if not (Path(sdk) / "licenses").is_dir():
        raise BuildInfraError(f"в {sdk} нет принятых лицензий Android SDK (licenses/)")


def _download_gradle(version: str) -> Path | None:
    """Путь к bin/gradle нужной версии; None — такой версии Gradle не существует."""
    home = CACHE / "gradle-dists" / f"gradle-{version}"
    gradle = home / "bin" / "gradle"
    if gradle.exists():
        return gradle
    archive = CACHE / "gradle-dists" / f"gradle-{version}-bin.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(f"{GRADLE_DISTRIBUTIONS}/gradle-{version}-bin.zip", archive)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise BuildInfraError(f"не скачать Gradle {version}: HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise BuildInfraError(f"не скачать Gradle {version}: {e}")
    with zipfile.ZipFile(archive) as z:
        z.extractall(archive.parent)
    archive.unlink()
    gradle.chmod(0o755)
    return gradle


def _fetch(url: str) -> bytes | None:
    """Содержимое по URL; None — 404. Сетевые ошибки — BuildInfraError."""
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None
        raise BuildInfraError(f"репозиторий ответил HTTP {e.code}: {url}")
    except (urllib.error.URLError, OSError) as e:
        raise BuildInfraError(f"нет доступа к репозиториям: {url}: {e}")


def _version_key(version: str) -> tuple:
    return tuple(int(p) for p in version.split("."))


@lru_cache(maxsize=512)
def released_versions(group: str, artifact: str) -> tuple[str, ...] | None:
    """Стабильные выпущенные версии артефакта по всем репозиториям; None — артефакта нигде нет."""
    found, exists = set(), False
    for repo in MAVEN_REPOSITORIES:
        data = _fetch(f"{repo}/{group.replace('.', '/')}/{artifact}/maven-metadata.xml")
        if data is None:
            continue
        exists = True
        found |= {v.text.strip() for v in ET.fromstring(data).iter("version") if v.text}
    if not exists:
        return None
    return tuple(sorted((v for v in found if STABLE_VERSION_RE.match(v)), key=_version_key))


@lru_cache(maxsize=1)
def gradle_versions() -> tuple[str, ...]:
    """Выпущенные стабильные версии Gradle, от новых к старым."""
    data = _fetch("https://services.gradle.org/versions/all") or b"[]"
    released = [v["version"] for v in json.loads(data)
                if not (v.get("snapshot") or v.get("nightly") or v.get("broken") or v.get("rcFor")
                        or v.get("milestoneFor")) and STABLE_VERSION_RE.match(v["version"])]
    return tuple(sorted(released, key=_version_key, reverse=True))


def _missing(group: str, artifact: str, version: str, what: str) -> str | None:
    versions = released_versions(group, artifact)
    if versions is None:
        return f"{what} {group}:{artifact} не существует ни в Google Maven, ни в Maven Central, ни в Gradle Plugin Portal"
    if version not in versions:
        return f"{what} {group}:{artifact}:{version} не выпущена; выпущенные стабильные версии: {', '.join(versions[-12:])}"
    return None


def check_versions(versions: dict, dependencies: list[dict]) -> list[str]:
    """Проблемы с версиями до сборки: несуществующие артефакты, версии плагинов, Gradle."""
    checks = [("com.android.application", "com.android.application.gradle.plugin",
               versions["android_gradle_plugin"], "плагин"),
              ("org.jetbrains.kotlin.android", "org.jetbrains.kotlin.android.gradle.plugin",
               versions["kotlin"], "плагин")]
    for d in dependencies:
        parts = d["coordinate"].split(":")
        if len(parts) == 3:
            checks.append((*parts, "зависимость"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        problems = [p for p in pool.map(lambda c: _missing(*c), checks) if p]
    released = gradle_versions()
    if versions["gradle"] not in released:
        problems.append(f"Gradle {versions['gradle']} не выпущен; выпущенные версии: {', '.join(released[:12])}")
    return problems


def hints_for_error(error: str) -> list[str]:
    """Для «Could not find g:a:v» и ненайденных плагинов — какие версии существуют на самом деле."""
    found = [(g, a, v, "зависимость") for g, a, v in re.findall(r"Could not find ([\w.-]+):([\w.-]+):([\w.+-]+?)\.?\s", error)]
    found += [(pid, f"{pid}.gradle.plugin", v, "плагин")
              for pid, v in re.findall(r"Plugin \[id: '([\w.-]+)', version: '([^']+)'", error)]
    return [h for h in dict.fromkeys(_missing(*f) for f in dict.fromkeys(found)) if h]


def _what_went_wrong(log: str) -> str:
    """Блоки '* What went wrong:' из вывода Gradle — суть ошибки конфигурации."""
    blocks = re.findall(r"\* What went wrong:\n(.*?)(?=\n\* Try:|\n\* Exception is:|\Z)", log, flags=re.DOTALL)
    return "\n".join(b.strip() for b in blocks)[:3000]


def _test_failures(workdir: Path, files: dict[str, str]) -> dict[str, list[str]]:
    """Упавшие тесты из XML-отчётов Gradle, по файлам тестов."""
    failures: dict[str, list[str]] = {}
    for report in (workdir / "app" / "build" / "test-results").rglob("*.xml"):
        try:
            root = ET.parse(report).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is None:
                continue
            cls = case.get("classname", "")
            short = cls.rsplit(".", 1)[-1].split("$")[0]
            path = f"app/src/test/java/{cls.split('$')[0].replace('.', '/')}.kt"
            if path not in files:  # класс может лежать в файле с другим именем
                path = next((p for p, c in files.items() if "/src/test/" in p
                             and re.search(rf"\bclass\s+{re.escape(short)}\b", c)), path)
            details = (problem.get("message") or "").strip()
            stack = (problem.text or "").strip().splitlines()
            where = next((l.strip() for l in stack if short in l and ".kt:" in l), "")
            failures.setdefault(path, []).append(f"{case.get('name')}: {details[:500]} {where}".strip())
    return failures


def parse_build(log: str, workdir: Path, files: dict[str, str], ok: bool) -> BuildResult:
    tail = "\n".join(log.splitlines()[-60:])
    if ok:
        return BuildResult(ok=True, log_tail=tail)
    if NETWORK_ERROR_RE.search(log):
        line = next(l for l in log.splitlines() if NETWORK_ERROR_RE.search(l))
        raise BuildInfraError(f"нет доступа к репозиториям зависимостей: {line.strip()[:300]}")

    prefix = str(workdir).replace("\\", "/").rstrip("/") + "/"
    file_errors: dict[str, list[str]] = {}
    script_errors: list[str] = []  # build.gradle.kts — из шаблона, правится через версии и зависимости
    for path, line, col, message in KOTLIN_ERROR_RE.findall(log):
        rel = path.replace("\\", "/")
        rel = rel[len(prefix):] if rel.startswith(prefix) else rel.split(f"/{workdir.name}/", 1)[-1]
        if rel.endswith(".kts"):
            script_errors.append(f"{rel}:{line}:{col} {message.strip()}")
        else:
            file_errors.setdefault(rel, []).append(f"{line}:{col} {message.strip()}")

    tests = {} if file_errors or script_errors else _test_failures(workdir, files)
    config = None
    if not file_errors and not tests:
        config = "\n".join(script_errors + [_what_went_wrong(log)]).strip() or tail
    return BuildResult(ok=False, file_errors=file_errors, test_failures=tests, config_error=config, log_tail=tail)


async def build_project(files: dict[str, str], gradle_version: str) -> BuildResult:
    """Собирает проект во временной папке: assembleDebug + testDebugUnitTest."""
    check_environment()
    async with _lock:
        gradle = await asyncio.to_thread(_download_gradle, gradle_version)
        if gradle is None:
            return BuildResult(ok=False, config_error=f"Gradle {gradle_version} не существует "
                                                      f"({GRADLE_DISTRIBUTIONS}) — выбери выпущенную версию")
        workdir = Path(tempfile.mkdtemp(prefix="mobile-build-"))
        try:
            for path, content in files.items():
                target = workdir / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            env = {**os.environ, "GRADLE_USER_HOME": str(CACHE / "gradle-home")}
            proc = await asyncio.create_subprocess_exec(
                str(gradle), *TASKS, "--continue", "--console=plain", "-p", str(workdir),
                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), BUILD_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise BuildInfraError(f"сборка не уложилась в {BUILD_TIMEOUT:.0f} с")
            return parse_build(out.decode("utf-8", errors="replace"), workdir, files, proc.returncode == 0)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
