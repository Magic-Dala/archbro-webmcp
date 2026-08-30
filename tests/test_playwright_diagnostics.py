import os
from pathlib import Path
import subprocess
import sys

from qa.playwright_diagnostics import diagnostic_scope, failure_details


def _raise_comparison_failure() -> None:
    actual = "stale"
    expected = "fresh"
    assert actual == expected


def test_failure_details_preserve_assertion_line_operands_and_traceback() -> None:
    try:
        _raise_comparison_failure()
    except AssertionError as exc:
        details = failure_details(exc)
    else:
        raise AssertionError("fixture was expected to fail")

    assert details["type"] == "AssertionError"
    assert details["line"] > 0
    assert details["assertion"] == "assert actual == expected"
    assert details["values"] == {"actual": "'stale'", "expected": "'fresh'"}
    assert "_raise_comparison_failure" in details["traceback"]


def test_diagnostic_scope_captures_values_before_the_fixture_closes() -> None:
    lifecycle = {"closed": False}

    def close_fixture() -> None:
        lifecycle["closed"] = True

    try:
        with diagnostic_scope(close_fixture):
            actual = "closed" if lifecycle["closed"] else "open"
            expected = "ready"
            assert actual == expected
    except AssertionError as exc:
        details = exc.archbro_failure_details
    else:
        raise AssertionError("fixture was expected to fail")

    assert lifecycle["closed"] is True
    assert details["values"] == {"actual": "'open'", "expected": "'ready'"}


def test_final_fix_harness_runs_via_its_documented_direct_script_command(tmp_path: Path) -> None:
    playwright_package = tmp_path / "playwright"
    playwright_package.mkdir()
    (playwright_package / "__init__.py").write_text("", encoding="utf-8")
    (playwright_package / "sync_api.py").write_text(
        """
class Browser:
    def close(self):
        pass

class BrowserContext:
    pass

class Page:
    pass

class Route:
    pass

class _Chromium:
    def launch(self, headless=True):
        return Browser()

class _Playwright:
    chromium = _Chromium()

class _PlaywrightContext:
    def __enter__(self):
        return _Playwright()

    def __exit__(self, *args):
        pass

def sync_playwright():
    return _PlaywrightContext()
""".lstrip(),
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tmp_path)
    environment["ARCHBRO_FINAL_FIX_CASES"] = "__import_probe__"
    completed = subprocess.run(
        [sys.executable, str(repo_root / "qa" / "playwright_final_fix.py")],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "FINAL_FIX_PLAYWRIGHT_PASS" in completed.stdout
