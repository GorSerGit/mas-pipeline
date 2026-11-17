"""Тесты разбора результатов сборки Gradle и эталонных фрагментов (мини-RAG).

Запуск из папки контейнера:  python -m unittest discover tests
"""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import android_build as b  # noqa: E402
import snippets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
API = "app/src/main/java/ru/a/data/ParkingApi.kt"
TEST = "app/src/test/java/ru/a/domain/ZoneLogicTest.kt"

REPORT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="ru.a.domain.ZoneLogicTest" tests="2" failures="1">
  <testcase name="warnsBeforeEnd" classname="ru.a.domain.ZoneLogicTest" time="0.01">
    <failure message="expected:&lt;true&gt; but was:&lt;false&gt;" type="java.lang.AssertionError">java.lang.AssertionError: expected:&lt;true&gt; but was:&lt;false&gt;
	at org.junit.Assert.fail(Assert.java:89)
	at ru.a.domain.ZoneLogicTest.warnsBeforeEnd(ZoneLogicTest.kt:21)</failure>
  </testcase>
  <testcase name="ok" classname="ru.a.domain.ZoneLogicTest" time="0.01"/>
</testsuite>
"""


class ParseBuildTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="mobile-build-"))
        self.files = {API: "package ru.a.data", TEST: "package ru.a.domain\nclass ZoneLogicTest"}

    def parse(self, log, ok=False):
        return b.parse_build(log, self.dir, self.files, ok)

    def test_ok(self):
        self.assertTrue(self.parse("BUILD SUCCESSFUL", ok=True).ok)

    def test_kotlin_errors_by_file(self):
        prefix = str(self.dir).replace("\\", "/")
        log = (f"> Task :app:compileDebugKotlin FAILED\n"
               f"e: file://{prefix}/{API}:12:5 Unresolved reference 'toMediaTypeOrNull'.\n"
               f"e: file://{prefix}/{API}:13:9 Argument type mismatch: actual type is 'MediaType?', but 'MediaType' was expected.\n"
               f"w: file://{prefix}/{API}:3:1 Unused import\n"
               "BUILD FAILED")
        r = self.parse(log)
        self.assertFalse(r.ok)
        self.assertEqual(list(r.file_errors), [API])
        self.assertEqual(r.file_errors[API][0], "12:5 Unresolved reference 'toMediaTypeOrNull'.")
        self.assertEqual(len(r.file_errors[API]), 2)  # предупреждения не считаются
        self.assertIsNone(r.config_error)

    def test_build_script_error_is_config(self):
        prefix = str(self.dir).replace("\\", "/")
        r = self.parse(f"e: file://{prefix}/app/build.gradle.kts:20:5 Unresolved reference: compilerOptions\nBUILD FAILED")
        self.assertEqual(r.file_errors, {})
        self.assertIn("app/build.gradle.kts:20:5", r.config_error)

    def test_dependency_not_found_is_config(self):
        log = ("FAILURE: Build failed with an exception.\n\n* What went wrong:\n"
               "Could not resolve all files for configuration ':app:debugRuntimeClasspath'.\n"
               "> Could not find com.squareup.retrofit2:retrofit:9.9.9.\n\n* Try:\n> Run with --stacktrace")
        r = self.parse(log)
        self.assertIn("Could not find com.squareup.retrofit2:retrofit:9.9.9", r.config_error)
        self.assertNotIn("Try", r.config_error)

    def test_network_failure_is_infra(self):
        log = "> Could not resolve com.android.tools.build:gradle\n> java.net.UnknownHostException: dl.google.com"
        with self.assertRaises(b.BuildInfraError):
            self.parse(log)

    def test_failed_tests_from_reports(self):
        reports = self.dir / "app" / "build" / "test-results" / "testDebugUnitTest"
        reports.mkdir(parents=True)
        (reports / "TEST-ru.a.domain.ZoneLogicTest.xml").write_text(REPORT, encoding="utf-8")
        r = self.parse("> Task :app:testDebugUnitTest FAILED\nBUILD FAILED")
        self.assertEqual(list(r.test_failures), [TEST])
        failure = r.test_failures[TEST][0]
        self.assertIn("warnsBeforeEnd", failure)
        self.assertIn("expected:<true> but was:<false>", failure)
        self.assertIn("ZoneLogicTest.kt:21", failure)
        self.assertIsNone(r.config_error)

    def test_test_class_in_other_file(self):
        self.files = {"app/src/test/java/ru/a/domain/Tests.kt": "package ru.a.domain\nclass ZoneLogicTest {}"}
        reports = self.dir / "app" / "build" / "test-results" / "testDebugUnitTest"
        reports.mkdir(parents=True)
        (reports / "TEST.xml").write_text(REPORT, encoding="utf-8")
        self.assertEqual(list(self.parse("BUILD FAILED").test_failures), ["app/src/test/java/ru/a/domain/Tests.kt"])

    def test_summary(self):
        r = b.BuildResult(ok=False, file_errors={API: ["1:1 boom"]})
        self.assertIn(API, r.summary())
        self.assertIn("1:1 boom", r.summary())

    def test_environment_check(self):
        old = os.environ.pop("ANDROID_HOME", None)
        try:
            with self.assertRaises(b.BuildInfraError):
                b.check_environment()
        finally:
            if old is not None:
                os.environ["ANDROID_HOME"] = old


def metadata(*versions):
    return ("<metadata><versioning><versions>" + "".join(f"<version>{v}</version>" for v in versions)
            + "</versions></versioning></metadata>").encode()


class RepositoryVersionsTest(unittest.TestCase):
    """Проверка версий по репозиториям; сеть подменена."""

    REPO = {
        "dl.google.com/dl/android/maven2/com/android/application/com.android.application.gradle.plugin":
            metadata("8.6.0", "8.7.3", "8.8.0-alpha01"),
        "repo.maven.apache.org/maven2/com/squareup/retrofit2/converter-kotlinx-serialization":
            metadata("2.10.0", "2.11.0", "3.0.0-beta1"),
        "plugins.gradle.org/m2/org/jetbrains/kotlin/android/org.jetbrains.kotlin.android.gradle.plugin":
            metadata("2.2.20", "2.2.21"),
        "repo.maven.apache.org/maven2/junit/junit": metadata("4.13.1", "4.13.2"),
        "dl.google.com/dl/android/maven2/junit/junit": metadata("4.12"),  # объединяется с Maven Central
    }
    GRADLE = json.dumps([{"version": "9.0-rc-1", "rcFor": "9.0"}, {"version": "8.11.1"}, {"version": "8.9"},
                         {"version": "8.10-20240101", "nightly": True}]).encode()

    def setUp(self):
        self._fetch = b._fetch
        self.urls = []

        def fetch(url):
            self.urls.append(url)
            if url.endswith("/versions/all"):
                return self.GRADLE
            return self.REPO.get(url.split("://", 1)[1].rsplit("/", 1)[0])
        b._fetch = fetch
        b.released_versions.cache_clear()
        b.gradle_versions.cache_clear()

    def tearDown(self):
        b._fetch = self._fetch
        b.released_versions.cache_clear()
        b.gradle_versions.cache_clear()

    VERSIONS = {"kotlin": "2.2.21", "android_gradle_plugin": "8.7.3", "gradle": "8.9"}

    def deps(self, *coordinates):
        return [{"configuration": "implementation", "coordinate": c, "platform": False} for c in coordinates]

    def test_all_released(self):
        self.assertEqual(b.check_versions(self.VERSIONS, self.deps(
            "com.squareup.retrofit2:converter-kotlinx-serialization:2.11.0", "junit:junit:4.12")), [])

    def test_unreleased_dependency_lists_stable_versions(self):
        problems = b.check_versions(self.VERSIONS, self.deps("com.squareup.retrofit2:converter-kotlinx-serialization:0.10.0"))
        self.assertEqual(len(problems), 1)
        self.assertIn("converter-kotlinx-serialization:0.10.0 не выпущена", problems[0])
        self.assertIn("2.10.0, 2.11.0", problems[0])
        self.assertNotIn("beta", problems[0])

    def test_unknown_artifact(self):
        problems = b.check_versions(self.VERSIONS, self.deps("com.jakewharton:nothing:1.0"))
        self.assertIn("com.jakewharton:nothing не существует", problems[0])

    def test_plugins_and_gradle(self):
        problems = b.check_versions({"kotlin": "9.9.9", "android_gradle_plugin": "8.8.0-alpha01", "gradle": "9.0"}, [])
        text = "\n".join(problems)
        self.assertIn("com.android.application.gradle.plugin:8.8.0-alpha01 не выпущена", text)
        self.assertIn("8.6.0, 8.7.3", text)
        self.assertIn("org.jetbrains.kotlin.android.gradle.plugin:9.9.9", text)
        self.assertIn("Gradle 9.0 не выпущен; выпущенные версии: 8.11.1, 8.9", text)

    def test_versionless_bom_artifacts_skipped(self):
        self.assertEqual(b.check_versions(self.VERSIONS, self.deps("androidx.compose.ui:ui")), [])
        self.assertFalse(any("compose" in u for u in self.urls))

    def test_hints_from_gradle_error(self):
        error = ("> Could not find com.squareup.retrofit2:converter-kotlinx-serialization:0.8.0.\n"
                 "     Searched in the following locations:\n"
                 "> Could not find com.squareup.retrofit2:converter-kotlinx-serialization:0.8.0.\n"
                 "Plugin [id: 'com.android.application', version: '8.8.0-alpha01'] was not found")
        hints = b.hints_for_error(error)
        self.assertEqual(len(hints), 2)  # повторы схлопнуты
        self.assertIn("converter-kotlinx-serialization:0.8.0 не выпущена", hints[0])
        self.assertIn("com.android.application.gradle.plugin:8.8.0-alpha01", hints[1])

    def test_network_error_is_infra(self):
        def down(url):
            raise b.BuildInfraError("нет доступа")
        b._fetch = down
        with self.assertRaises(b.BuildInfraError):
            b.check_versions(self.VERSIONS, [])


class SnippetsTest(unittest.TestCase):
    def setUp(self):
        self.all = snippets.load(ROOT / "snippets" / "android")

    def test_snippets_well_formed(self):
        self.assertGreaterEqual(len(self.all), 8)
        for s in self.all:
            with self.subTest(s.name):
                self.assertTrue(s.keywords)
                self.assertIn(s.source, ("main", "test"))
                self.assertTrue(s.code.strip(), "нет блока ```kotlin")
                self.assertNotIn("package ", s.code)  # package подставляет проект
                for dep in s.dependencies:
                    self.assertRegex(dep, r"^[\w.-]+:[\w.-]+$")  # без версий

    def test_no_versions_in_snippets(self):
        for s in self.all:
            self.assertIsNone(re.search(r"\b\d+\.\d+\.\d+\b", s.body), s.name)

    def test_select_by_path_and_purpose(self):
        def names(text):
            return [s.name for s in snippets.select(self.all, text)]
        self.assertEqual(names("app/src/main/java/ru/a/data/api/ParkingApi.kt Retrofit-интерфейс"), ["network"])
        self.assertIn("unit_test", names("app/src/test/java/ru/a/domain/ZoneLogicTest.kt"))
        self.assertIn("datetime", names("ParkingSessionManager.kt: предупреждение за 10 минут, timer"))
        self.assertIn("location", names("LocationTracker.kt GPS"))
        self.assertEqual(names("app/src/main/java/ru/a/data/repository/Foo.kt"), [])

    def test_select_limit_and_render(self):
        chosen = snippets.select(self.all, "location notification permission gps", limit=2)
        self.assertEqual(len(chosen), 2)
        text = snippets.render(chosen)
        self.assertIn("### Эталон: ", text)
        self.assertIn("```kotlin", text)


if __name__ == "__main__":
    unittest.main()
