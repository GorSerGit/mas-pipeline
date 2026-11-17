"""Эталонные фрагменты действительно компилируются: проект из всех фрагментов собирается Gradle'ом.

Нужны JDK, Android SDK и доступ к репозиториям Maven — без них тест пропускается.
В контейнере:  docker exec generator python -m unittest tests.test_snippets_build
"""

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import android_build  # noqa: E402
import mobile_agent  # noqa: E402

PKG = "ref.snippets"
FIXTURE = Path(__file__).parent / "fixtures" / "android_build_versions.json"


def reference_project() -> dict[str, str]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sources = {}
    for s in mobile_agent.SNIPPETS:
        header = f"package {PKG}.{s.name}\n\n" + (f"import {PKG}.BuildConfig\n" if "BuildConfig" in s.code else "")
        sources[f"app/src/{s.source}/java/{PKG.replace('.', '/')}/{s.name}/Snippet.kt"] = header + s.code
    sources[f"app/src/main/java/{PKG.replace('.', '/')}/MainActivity.kt"] = (
        f"package {PKG}\n\nimport androidx.activity.ComponentActivity\n\nclass MainActivity : ComponentActivity()\n")
    design = {
        "app_name": "Snippets", "package": PKG,
        "permissions": ["android.permission.ACCESS_FINE_LOCATION", "android.permission.INTERNET",
                        "android.permission.POST_NOTIFICATIONS"],
        "versions": fixture["versions"], "dependencies": fixture["dependencies"],
        "files": [{"path": p, "purpose": "эталон"} for p in sources],
    }
    return {**mobile_agent._scaffold(design), **sources}


class SnippetsBuildTest(unittest.TestCase):
    def test_reference_project_builds_and_tests_pass(self):
        try:
            android_build.check_environment()
        except android_build.BuildInfraError as e:
            self.skipTest(str(e))
        files = reference_project()
        gradle = json.loads(FIXTURE.read_text(encoding="utf-8"))["versions"]["gradle"]
        result = asyncio.run(android_build.build_project(files, gradle))
        self.assertTrue(result.ok, result.summary())


if __name__ == "__main__":
    unittest.main()
