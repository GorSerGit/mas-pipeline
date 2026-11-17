"""Тесты агента Android с подменённым Qwen.

Запуск из папки контейнера:  python -m unittest discover tests
"""

import asyncio
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import android_build  # noqa: E402
import mobile_agent as m  # noqa: E402
import generation_utils as orchestrator  # noqa: E402
from android_build import BuildResult  # noqa: E402

PKG = "ru.techservice.parking"
PKG_DIR = PKG.replace(".", "/")
FILES = [
    {"path": f"app/src/main/java/{PKG_DIR}/MainActivity.kt", "purpose": "class MainActivity : ComponentActivity"},
    {"path": f"app/src/main/java/{PKG_DIR}/data/ParkingApi.kt", "purpose": "interface ParkingApi"},
    {"path": f"app/src/main/java/{PKG_DIR}/ui/MapViewModel.kt", "purpose": "class MapViewModel"},
    {"path": f"app/src/test/java/{PKG_DIR}/ZoneLogicTest.kt", "purpose": "class ZoneLogicTest"},
]
FILES[0]['depends_on'] = [FILES[2]['path']]
FILES[1]['depends_on'] = []
FILES[2]['depends_on'] = [FILES[1]['path']]
FILES[3]['depends_on'] = [FILES[2]['path']]
# Версии нарочно вымышленные: в коде МАС версий нет, всё приходит от Qwen
VERSIONS = {"kotlin": "9.1.0", "android_gradle_plugin": "9.2.0", "gradle": "9.3", "compile_sdk": 99, "min_sdk": 21}
DEPENDENCIES = [
    {"configuration": "implementation", "coordinate": "com.example:bom:7.7", "platform": True},
    {"configuration": "implementation", "coordinate": "com.example:ui"},
    {"configuration": "implementation", "coordinate": "androidx.activity:activity-compose:7.1"},
    {"configuration": "testImplementation", "coordinate": "junit:junit:7.2"},
] + [  # зависимости эталонов, которые подставляются файлам из FILES
    {"configuration": conf, "coordinate": c} for conf, c in [
        ("implementation", "androidx.compose.material3:material3"),
        ("implementation", "androidx.navigation:navigation-compose"),
        ("implementation", "androidx.core:core-ktx"),
        ("implementation", "androidx.lifecycle:lifecycle-viewmodel-compose"),
        ("implementation", "androidx.lifecycle:lifecycle-runtime-compose"),
        ("implementation", "com.squareup.retrofit2:retrofit"),
        ("implementation", "com.squareup.retrofit2:converter-kotlinx-serialization"),
        ("implementation", "com.squareup.okhttp3:okhttp"),
        ("implementation", "org.jetbrains.kotlinx:kotlinx-serialization-json"),
        ("testImplementation", "org.jetbrains.kotlinx:kotlinx-coroutines-test"),
    ]
]
DESIGN = {"app_name": "Парковочный помощник", "package": PKG,
          "permissions": ["android.permission.ACCESS_FINE_LOCATION", "android.permission.POST_NOTIFICATIONS"],
          "versions": VERSIONS, "dependencies": DEPENDENCIES, "files": FILES}
TASK = {"task": "Сделай Android-приложение водителя",
        "context": "Стек: Kotlin 9.1.0, Android.\n\n## Общий контракт (менять нельзя)\nGET /api/v1/zones"}


def good_code(path: str) -> str:
    pkg = path.split("/java/", 1)[1].rsplit("/", 1)[0].replace("/", ".")
    name = path.rsplit("/", 1)[1][:-3]
    return (f"```kotlin\npackage {pkg}\n\n// скобка в комментарии {{\n"
            f"class {name} {{\n    val s = \"строка с {{ и ( внутри\"\n    fun f(x: Int) = listOf(x)[0]\n}}\n```")


def fixed_code(path: str) -> str:
    """Ответ на исправление: файл в блоке с первой строкой // FILE:."""
    return good_code(path).replace("```kotlin\n", f"```kotlin\n// FILE: {path}\n", 1).replace(
        "// скобка в комментарии", "// исправлено", 1)


class FakeLLM:
    """Отвечает по system-промпту; design, файлы, исправления — по сценарию, иначе — правильно."""

    def __init__(self, design_answers=None, file_answers=None, truncated_first=(), fix_answers=None,
                 config_answers=None):
        self.design_answers = list(design_answers or [])
        self.file_answers = {k: list(v) for k, v in (file_answers or {}).items()}
        self.truncated_first = set(truncated_first)
        self.fix_answers = list(fix_answers or [])
        self.config_answers = list(config_answers or [])
        self.calls = []

    async def __call__(self, prompt, system="", think=True):
        self.calls.append({"prompt": prompt, "system": system, "think": think})
        if system == m.DESIGN_SYSTEM:
            text = self.design_answers.pop(0) if self.design_answers else json.dumps(DESIGN, ensure_ascii=False)
            return {"text": text, "tokens": 100, "truncated": False}
        if system == m.FIX_SYSTEM:
            paths = re.findall(r"// FILE: (\S+)", prompt.split("Файлы:", 1)[1].split("Эталоны:", 1)[0])
            text = self.fix_answers.pop(0) if self.fix_answers else "\n".join(fixed_code(p) for p in paths)
            return {"text": text, "tokens": 20, "truncated": False}
        if system == m.CONFIG_SYSTEM:
            text = self.config_answers.pop(0)
            return {"text": text, "tokens": 30, "truncated": False}
        path = re.search(r"Напиши файл (\S+)", prompt).group(1)
        if path in self.truncated_first:
            self.truncated_first.discard(path)
            return {"text": "```kotlin\npackage", "tokens": 7, "truncated": True}
        queue = self.file_answers.get(path)
        text = queue.pop(0) if queue else good_code(path)
        return {"text": text, "tokens": 10, "truncated": False}

    def file_calls(self, path):
        return [c for c in self.calls if f"Напиши файл {path}" in c["prompt"]]

    def by_system(self, system):
        return [c for c in self.calls if c["system"] == system]


class FakeBuild:
    """Подмена Gradle: отдаёт результаты по очереди, потом — успех; запоминает собранные файлы."""

    def __init__(self, results=()):
        self.results = list(results)
        self.builds = []

    async def __call__(self, files, gradle_version):
        self.builds.append({"files": dict(files), "gradle": gradle_version})
        result = self.results.pop(0) if self.results else BuildResult(ok=True)
        if isinstance(result, Exception):
            raise result
        return result


def run(task=TASK):
    return asyncio.run(m.mobile_agent(task))


class MobileAgentTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLLM()
        self.build = FakeBuild()
        self.version_problems = []  # очередь ответов предпроверки версий; пусто — всё выпущено
        self.version_checks = []
        self._orig = (m.ask_llm, android_build.build_project, m.COMPILE,
                      android_build.check_versions, android_build.hints_for_error)
        m.ask_llm = self.fake
        android_build.build_project = self.build
        m.COMPILE = True

        def check_versions(versions, dependencies):
            self.version_checks.append(dict(versions))
            return self.version_problems.pop(0) if self.version_problems else []
        android_build.check_versions = check_versions
        android_build.hints_for_error = lambda error: []

    def tearDown(self):
        (m.ask_llm, android_build.build_project, m.COMPILE,
         android_build.check_versions, android_build.hints_for_error) = self._orig

    def use(self, fake):
        self.fake = fake
        m.ask_llm = fake

    def builds(self, *results):
        self.build = FakeBuild(results)
        android_build.build_project = self.build

    def test_project_structure(self):
        files = run()["files"]
        for path in ("settings.gradle.kts", "build.gradle.kts", "gradle.properties", "app/build.gradle.kts",
                     "gradle/wrapper/gradle-wrapper.properties", "app/src/main/AndroidManifest.xml",
                     "app/src/main/res/values/strings.xml", "README.md", ".gitignore"):
            self.assertIn(path, files)
        for f in FILES:
            self.assertTrue(files[f["path"]].startswith("package "), f["path"])
        # результат принимает оркестратор
        self.assertTrue(all(isinstance(v, str) for v in files.values()))

    def test_code_extracted_from_fence(self):
        code = run()["files"][FILES[1]["path"]]
        self.assertNotIn("```", code)
        self.assertTrue(code.startswith(f"package {PKG}.data\n"))

    def test_tokens_summed(self):
        self.assertEqual(run()["tokens"], 100 + 10 * len(FILES))

    def test_versions_from_design(self):
        files = run()["files"]
        root = files["build.gradle.kts"]
        self.assertIn('id("com.android.application") version "9.2.0"', root)
        for plugin in ("android", "plugin.compose", "plugin.serialization"):
            self.assertIn(f'id("org.jetbrains.kotlin.{plugin}") version "9.1.0"', root)
        self.assertIn("gradle-9.3-bin.zip", files["gradle/wrapper/gradle-wrapper.properties"])
        app = files["app/build.gradle.kts"]
        self.assertIn("compileSdk = 99", app)
        self.assertIn("targetSdk = 99", app)
        self.assertIn("minSdk = 21", app)

    def test_dependencies_rendered(self):
        app = run()["files"]["app/build.gradle.kts"]
        self.assertIn('    implementation(platform("com.example:bom:7.7"))', app)
        self.assertIn('    implementation("com.example:ui")', app)
        self.assertIn('    testImplementation("junit:junit:7.2")', app)

    def test_kotlin_from_tz_enforced(self):
        bad = json.dumps(dict(DESIGN, versions=dict(VERSIONS, kotlin="9.0.0")))
        with self.assertRaisesRegex(ValueError, "в ТЗ указан Kotlin 9.1.0"):
            m._validate_design(bad, TASK["context"])
        # уточнение версии из ТЗ допустимо: в ТЗ "Kotlin 9.1" -> 9.1.0
        m._validate_design(json.dumps(DESIGN), "Kotlin 9.1")

    def test_no_kotlin_in_tz_takes_design(self):
        root = run({"task": "x", "context": "без версий"})["files"]["build.gradle.kts"]
        self.assertIn('version "9.1.0"', root)

    def test_manifest_permissions_and_internet(self):
        manifest = run()["files"]["app/src/main/AndroidManifest.xml"]
        for p in ("INTERNET", "ACCESS_FINE_LOCATION", "POST_NOTIFICATIONS"):
            self.assertIn(f'android:name="android.permission.{p}"', manifest)
        self.assertIn('android:name=".MainActivity"', manifest)

    def test_app_build_uses_package_and_base_url(self):
        app = run()["files"]["app/build.gradle.kts"]
        self.assertIn(f'namespace = "{PKG}"', app)
        self.assertIn(f'applicationId = "{PKG}"', app)
        self.assertIn(f'buildConfigField("String", "BASE_URL", "\\"{m.BASE_URL}\\"")', app)

    def test_strings_escaped(self):
        design = dict(DESIGN, app_name="Driver's <Helper>")
        self.use(FakeLLM(design_answers=[json.dumps(design)]))
        strings = run()["files"]["app/src/main/res/values/strings.xml"]
        self.assertIn("Driver\\'s &lt;Helper&gt;", strings)

    def test_readme_has_run_instructions_and_structure(self):
        readme = run()["files"]["README.md"]
        self.assertIn("## Запуск", readme)
        self.assertIn("./gradlew assembleDebug", readme)
        self.assertIn("gradle wrapper --gradle-version 9.3", readme)
        self.assertIn("Kotlin 9.1.0", readme)
        self.assertIn(FILES[2]["path"], readme)

    def test_reasoning_off(self):
        # с рассуждениями Qwen упирается в лимит токенов ещё до ответа
        run()
        self.assertFalse(any(c["think"] for c in self.fake.calls))

    def test_design_in_reasoning_noise(self):
        noise = 'думаю: {"package": "Bad.Pkg"} ... </think>```json\n' + json.dumps(DESIGN) + "\n```"
        self.use(FakeLLM(design_answers=[noise]))
        self.assertIn(FILES[0]["path"], run()["files"])

    def test_design_retry_with_feedback(self):
        bad = json.dumps(dict(DESIGN, package="Ru.Bad"))
        self.use(FakeLLM(design_answers=[bad]))
        run()
        design_calls = [c for c in self.fake.calls if c["system"] == m.DESIGN_SYSTEM]
        self.assertEqual(len(design_calls), 2)
        self.assertIn("Предыдущий ответ отклонён: package 'Ru.Bad'", design_calls[1]["prompt"])

    def test_design_rejections(self):
        cases = {
            "нет MainActivity": dict(DESIGN, files=FILES[1:]),
            "нет теста": dict(DESIGN, files=FILES[:3]),
            "путь вне пакета": dict(DESIGN, files=FILES + [{"path": "app/src/main/java/other/X.kt", "purpose": "x"}]),
            "дубль": dict(DESIGN, files=FILES + [FILES[1]]),
            "слишком много файлов": dict(DESIGN, files=FILES + [
                {"path": f"app/src/main/java/{PKG_DIR}/F{i}.kt", "purpose": "x"} for i in range(m.MAX_FILES)]),
            "плохое разрешение": dict(DESIGN, permissions=["LOCATION"]),
            "нет versions": {k: v for k, v in DESIGN.items() if k != "versions"},
            "версия не версия": dict(DESIGN, versions=dict(VERSIONS, gradle="latest")),
            "sdk не число": dict(DESIGN, versions=dict(VERSIONS, compile_sdk="99")),
            "sdk bool": dict(DESIGN, versions=dict(VERSIONS, min_sdk=True)),
            "min_sdk > compile_sdk": dict(DESIGN, versions=dict(VERSIONS, min_sdk=100)),
            "нет зависимостей": dict(DESIGN, dependencies=[]),
            "нет обязательной зависимости": dict(DESIGN, dependencies=[
                d for d in DEPENDENCIES if not d["coordinate"].startswith("junit:")]),
            "нет зависимости эталона": dict(DESIGN, dependencies=[
                d for d in DEPENDENCIES if "retrofit" not in d["coordinate"]]),
            "без версии и без BOM": dict(DESIGN, dependencies=DEPENDENCIES[1:]),
            "BOM без версии": dict(DESIGN, dependencies=[dict(DEPENDENCIES[0], coordinate="com.example:bom")]
                                   + DEPENDENCIES[1:]),
            "плохая configuration": dict(DESIGN, dependencies=DEPENDENCIES + [
                {"configuration": "compile", "coordinate": "a:b:1"}]),
        }
        for name, design in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    m._validate_design(json.dumps(design), TASK["context"])

    def test_design_fails_after_max_attempts(self):
        self.use(FakeLLM(design_answers=["не json"] * m.MAX_ATTEMPTS))
        with self.assertRaises(orchestrator.GenerationError) as e:
            run()
        self.assertEqual(e.exception.step, "MOBILE design")
        self.assertEqual(e.exception.agent, "MOBILE")
        self.assertEqual(e.exception.attempts, m.MAX_ATTEMPTS)

    def test_wrong_package_retried(self):
        path = FILES[2]["path"]
        self.use(FakeLLM(file_answers={path: ["```kotlin\npackage ru.wrong\nclass MapViewModel\n```"]}))
        files = run()["files"]
        self.assertTrue(files[path].startswith(f"package {PKG}.ui"))
        calls = self.fake.file_calls(path)
        self.assertEqual(len(calls), 2)
        self.assertIn(f"package {PKG}.ui", calls[1]["prompt"])

    def test_unbalanced_braces_fail(self):
        path = FILES[1]["path"]
        broken = f"```kotlin\npackage {PKG}.data\ninterface ParkingApi {{\n    fun zones(\n```"
        self.use(FakeLLM(file_answers={path: [broken] * m.MAX_ATTEMPTS}))
        with self.assertRaises(orchestrator.GenerationError) as e:
            run()
        self.assertEqual(e.exception.step, f"MOBILE {path}")
        self.assertIn("несбалансированные скобки", e.exception.message)

    def test_truncated_retried(self):
        path = FILES[3]["path"]
        self.use(FakeLLM(truncated_first=[path]))
        files = run()["files"]
        self.assertIn(path, files)
        self.assertIn("обрезан", self.fake.file_calls(path)[1]["prompt"])

    def test_main_activity_must_declare_class(self):
        with self.assertRaises(ValueError):
            m._validate_kotlin(f"```kotlin\npackage {PKG}\nclass Other\n```", FILES[0]["path"])

    def test_file_annotation_before_package_allowed(self):
        code = m._validate_kotlin(f'@file:OptIn(Foo::class)\npackage {PKG}.data\nclass A', FILES[1]["path"])
        self.assertIn("package", code)

    # --- мини-RAG ---

    def test_snippets_in_file_prompt(self):
        run()
        api = self.fake.file_calls(FILES[1]["path"])[0]["prompt"]
        self.assertIn("### Эталон: network", api)
        self.assertIn("toMediaType()", api)
        test = self.fake.file_calls(FILES[3]["path"])[0]["prompt"]
        self.assertIn("### Эталон: unit_test", test)

    def test_design_prompt_lists_snippet_topics(self):
        for s in m.SNIPPETS:
            self.assertIn(f"- {s.name} (", m.DESIGN_SYSTEM)

    def test_design_must_include_snippet_dependencies(self):
        no_retrofit = [d for d in DEPENDENCIES if "retrofit" not in d["coordinate"]]
        self.use(FakeLLM(design_answers=[json.dumps(dict(DESIGN, dependencies=no_retrofit))]))
        run()
        retry = self.fake.by_system(m.DESIGN_SYSTEM)[1]["prompt"]
        self.assertIn("относится к теме 'network'", retry)

    # --- сборка и исправление ---

    def test_build_ok_no_fixes(self):
        files = run()["files"]
        self.assertEqual(len(self.build.builds), 1)
        self.assertEqual(self.build.builds[0]["gradle"], "9.3")
        self.assertIn("раундов исправления: 0", files["README.md"])
        self.assertEqual(self.fake.by_system(m.FIX_SYSTEM), [])

    def test_compile_errors_fixed(self):
        api = FILES[1]["path"]
        self.builds(BuildResult(ok=False, file_errors={api: ["12:5 Unresolved reference 'toMediaTypeOrNull'."]}))
        result = run()
        fixes = self.fake.by_system(m.FIX_SYSTEM)
        self.assertEqual(len(fixes), 1)
        prompt = fixes[0]["prompt"]
        self.assertIn("Unresolved reference 'toMediaTypeOrNull'", prompt)
        self.assertIn(f"// FILE: {api}", prompt)
        self.assertIn("### Эталон: network", prompt)
        self.assertNotIn("Задача:", prompt)  # для ошибок компиляции ТЗ не нужно — экономим токены
        self.assertIn("// исправлено", result["files"][api])
        self.assertIn("// исправлено", self.build.builds[1]["files"][api])  # пересобрано с исправлением
        self.assertIn("раундов исправления: 1", result["files"]["README.md"])
        self.assertEqual(result["tokens"], 100 + 10 * len(FILES) + 20)

    def test_compile_error_fixed_with_referenced_files(self):
        main, vm, api = FILES[0]["path"], FILES[2]["path"], FILES[1]["path"]
        self.use(FakeLLM(file_answers={main: [good_code(main).replace(
            "fun f(", "val vm: MapViewModel? = null\n    fun f(")]}))
        self.builds(BuildResult(ok=False, file_errors={
            main: ["33:23 No value passed for parameter 'viewModel'."],
            vm: ["5:1 Unresolved reference 'state'."],
            api: ["1:1 boom"],
        }))
        files = run()["files"]
        fixes = self.fake.by_system(m.FIX_SYSTEM)
        self.assertEqual(len(fixes), 2)  # MainActivity+MapViewModel — одна группа, у MapViewModel своей нет; ParkingApi — своя
        joint = next(c["prompt"] for c in fixes if f"// FILE: {main}" in c["prompt"])
        self.assertIn(f"// FILE: {vm}", joint)
        self.assertIn("No value passed for parameter 'viewModel'", joint)
        self.assertIn("Unresolved reference 'state'", joint)
        self.assertNotIn(f"// FILE: {api}", joint)
        self.assertIn("// исправлено", files[vm])

    def test_related_by_import_of_top_level_function(self):
        api, vm = FILES[1]["path"], FILES[2]["path"]
        files = {
            api: f"package {PKG}.data\n\nimport {PKG}.ui.isInsideZone\n\nfun check() = isInsideZone(1.0)\n",
            vm: f"package {PKG}.ui\n\nfun isInsideZone(x: Double) = x > 0\n\nclass MapViewModel\n",
            FILES[0]["path"]: f"package {PKG}\n\nclass MainActivity\n",
        }
        self.assertEqual(m._related_files(api, files), [vm])
        self.assertEqual(m._related_files(FILES[0]["path"], files), [])

    def test_fix_groups_run_sequentially_on_fresh_files(self):
        api, vm = FILES[1]["path"], FILES[2]["path"]
        # MapViewModel ссылается на ParkingApi: вторая группа (vm + api) должна получить уже исправленный api
        self.use(FakeLLM(file_answers={vm: [good_code(vm).replace("fun f(", "val api: ParkingApi? = null\n    fun f(")]}))
        self.builds(BuildResult(ok=False, file_errors={api: ["1:1 a"], vm: ["2:2 b"]}))
        run()
        fixes = self.fake.by_system(m.FIX_SYSTEM)
        self.assertEqual(len(fixes), 2)
        self.assertNotIn(f"// FILE: {vm}", fixes[0]["prompt"])  # первая группа — только api
        second_files = fixes[1]["prompt"].split("Файлы:", 1)[1]
        self.assertIn(f"// FILE: {api}", second_files)
        self.assertIn("// исправлено", second_files.split(f"// FILE: {api}", 1)[1].split("```", 1)[0])

    def test_error_lines_quoted(self):
        code = "package a\n\nclass X {\n    val y = remember { 1 }\n}\n"
        quoted = m._quote_lines(["4:13 @Composable invocations can only happen...", "99:1 вне файла", "без номера"], code)
        self.assertEqual(quoted[0], "4:13 @Composable invocations can only happen...\n      > val y = remember { 1 }")
        self.assertEqual(quoted[1:], ["99:1 вне файла", "без номера"])

    def test_stuck_errors_escalate_to_reasoning(self):
        main = FILES[0]["path"]
        same = [BuildResult(ok=False, file_errors={main: ["4:1 Functions which invoke @Composable..."]}),
                BuildResult(ok=False, file_errors={main: ["5:1 Functions which invoke @Composable..."]})]
        self.builds(*same)
        run()
        fixes = self.fake.by_system(m.FIX_SYSTEM)
        self.assertEqual([c["think"] for c in fixes], [False, True])  # номер строки сдвинулся, ошибка та же
        self.assertIn("> class MainActivity {", fixes[0]["prompt"].replace("\n      ", "\n"))

    def test_fix_formats_accepted(self):
        api, vm = FILES[1]["path"], FILES[2]["path"]
        body = f"package {PKG}.data\n\ninterface ParkingApi\n"
        allowed = {api, vm}
        cases = {
            "// FILE: первой строкой": f"```kotlin\n// FILE: {api}\n{body}```",
            "путь строкой перед блоком": f"**{api}**\n```kotlin\n{body}```",
            "заголовок с путём": f"### Файл {api}\n\n```kotlin\n{body}```",
            "без пути — по package и классу": f"Исправленный файл:\n```kotlin\n{body}```",
        }
        for name, text in cases.items():
            with self.subTest(name):
                fixed = m._validate_fix(text, allowed)
                self.assertEqual(list(fixed), [api])
                self.assertTrue(fixed[api].startswith(f"package {PKG}.data"))

    def test_fix_without_identifiable_file_rejected(self):
        with self.assertRaisesRegex(ValueError, "нет ни одного блока"):
            m._validate_fix("```kotlin\nval x = 1\n```", {FILES[1]["path"]})

    def test_unfixable_group_skipped_not_fatal(self):
        api, vm = FILES[1]["path"], FILES[2]["path"]
        self.builds(BuildResult(ok=False, file_errors={api: ["1:1 a"], vm: ["2:2 b"]}))
        garbage = ["не код"] * m.MAX_ATTEMPTS  # группа api так и не исправилась
        self.use(FakeLLM(fix_answers=garbage))
        files = run()["files"]  # вторая сборка (по умолчанию успешная) решает итог
        self.assertIn("// исправлено", files[vm])
        self.assertNotIn("// исправлено", files[api])
        self.assertEqual(len(self.build.builds), 2)

    def test_failed_test_fixed_with_related_files(self):
        test, logic = FILES[3]["path"], FILES[2]["path"]
        self.use(FakeLLM(file_answers={test: [good_code(test).replace(
            "fun f(", "val vm = MapViewModel()\n    fun f(")]}))
        self.builds(BuildResult(ok=False, test_failures={test: ["warnsBeforeEnd: expected:<true> but was:<false>"]}))
        files = run()["files"]
        prompt = self.fake.by_system(m.FIX_SYSTEM)[0]["prompt"]
        self.assertIn(f"// FILE: {test}", prompt)
        self.assertIn(f"// FILE: {logic}", prompt)  # тест ссылается на MapViewModel — его тоже можно править
        self.assertIn("Задача:", prompt)  # для логики нужны критерии приёмки из ТЗ
        self.assertIn("// исправлено", files[logic])

    def test_config_error_fixed_by_versions(self):
        self.builds(BuildResult(ok=False, config_error="Plugin [id: 'com.android.application', version: '9.2.0'] was not found"))
        new = {"versions": dict(VERSIONS, android_gradle_plugin="9.2.1"), "dependencies": DEPENDENCIES}
        self.use(FakeLLM(config_answers=[json.dumps(new)]))
        files = run()["files"]
        prompt = self.fake.by_system(m.CONFIG_SYSTEM)[0]["prompt"]
        self.assertIn("was not found", prompt)
        self.assertIn("В ТЗ указан Kotlin 9.1.0", prompt)
        self.assertIn('version "9.2.1"', files["build.gradle.kts"])
        self.assertIn('version "9.2.1"', self.build.builds[1]["files"]["build.gradle.kts"])

    def test_config_fix_cannot_change_kotlin_from_tz(self):
        self.builds(BuildResult(ok=False, config_error="incompatible"))
        bad = {"versions": dict(VERSIONS, kotlin="9.0.0"), "dependencies": DEPENDENCIES}
        good = {"versions": VERSIONS, "dependencies": DEPENDENCIES}
        self.use(FakeLLM(config_answers=[json.dumps(bad), json.dumps(good)]))
        run()
        self.assertIn("в ТЗ указан Kotlin 9.1.0", self.fake.by_system(m.CONFIG_SYSTEM)[1]["prompt"])

    def test_fix_rejects_foreign_file(self):
        api, other = FILES[1]["path"], FILES[2]["path"]
        self.builds(BuildResult(ok=False, file_errors={api: ["1:1 error"]}))
        self.use(FakeLLM(fix_answers=[fixed_code(other)]))
        files = run()["files"]
        fixes = self.fake.by_system(m.FIX_SYSTEM)
        self.assertEqual(len(fixes), 2)
        self.assertIn("не из исправляемых", fixes[1]["prompt"])
        self.assertNotIn("// исправлено", files[other])

    def test_build_still_failing_gives_up(self):
        api = FILES[1]["path"]
        failing = BuildResult(ok=False, file_errors={api: ["3:1 Unresolved reference 'x'."]})
        self.builds(*[failing] * (m.FIX_ROUNDS + 1))
        with self.assertRaises(orchestrator.GenerationError) as e:
            run()
        self.assertEqual(e.exception.step, "MOBILE build")
        self.assertIn("Unresolved reference 'x'", e.exception.message)
        self.assertEqual(len(self.build.builds), m.FIX_ROUNDS + 1)

    # --- предпроверка версий по репозиториям ---

    def test_unreleased_version_fixed_before_files(self):
        hint = "зависимость com.squareup.retrofit2:converter-kotlinx-serialization:0.10.0 не выпущена; выпущенные стабильные версии: 7.0, 7.1"
        self.version_problems = [[hint]]
        new = {"versions": VERSIONS, "dependencies": DEPENDENCIES}
        self.use(FakeLLM(config_answers=[json.dumps(new)]))
        run()
        config = self.fake.by_system(m.CONFIG_SYSTEM)
        self.assertEqual(len(config), 1)
        self.assertIn("выпущенные стабильные версии: 7.0, 7.1", config[0]["prompt"])
        self.assertEqual(len(self.version_checks), 2)  # проверили, исправили, проверили снова
        first_file = next(i for i, c in enumerate(self.fake.calls) if c["system"] == m.FILE_SYSTEM)
        self.assertLess(self.fake.calls.index(config[0]), first_file)  # исправление — до генерации файлов

    def test_unreleased_versions_give_up(self):
        self.version_problems = [["Gradle 9.3 не выпущен"]] * (m.FIX_ROUNDS + 1)
        good = json.dumps({"versions": VERSIONS, "dependencies": DEPENDENCIES})
        self.use(FakeLLM(config_answers=[good] * m.FIX_ROUNDS))
        with self.assertRaises(orchestrator.GenerationError) as e:
            run()
        self.assertEqual(e.exception.step, "MOBILE versions")
        self.assertEqual(self.fake.by_system(m.FILE_SYSTEM), [])  # файлы не генерировали зря

    def test_config_fix_gets_repository_hints(self):
        android_build.hints_for_error = lambda error: ["плагин com.android.application:9.2.0 не выпущена; выпущенные: 9.1"]
        self.builds(BuildResult(ok=False, config_error="Plugin [id: 'com.android.application', version: '9.2.0'] was not found"))
        self.use(FakeLLM(config_answers=[json.dumps({"versions": VERSIONS, "dependencies": DEPENDENCIES})]))
        run()
        self.assertIn("По репозиториям:\nплагин com.android.application:9.2.0",
                      self.fake.by_system(m.CONFIG_SYSTEM)[0]["prompt"])

    def test_build_infra_error_not_retried(self):
        self.builds(android_build.BuildInfraError("нет доступа к репозиториям"))
        with self.assertRaises(android_build.BuildInfraError):
            run()

    def test_compile_disabled(self):
        m.COMPILE = False
        files = run()["files"]
        self.assertEqual(self.build.builds, [])
        self.assertEqual(self.version_checks, [])  # без сборки и в репозитории не ходим
        self.assertNotIn("## Проверка", files["README.md"])



if __name__ == "__main__":
    unittest.main()
