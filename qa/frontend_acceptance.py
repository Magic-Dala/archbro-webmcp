from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "qa" / "playwright_artifacts"
SWEEP_REPORT = ART / "surface_sweep_report.json"
UI_REPORT_DIR = ART / "ui-report"
UI_REPORT_JSON = UI_REPORT_DIR / "report.json"
UI_REPORT_HTML = UI_REPORT_DIR / "index.html"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(process: subprocess.Popen[str], base_url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    health_url = f"{base_url}healthz"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(
                f"Frontend acceptance server exited early with code {process.returncode}.\n"
                f"{stdout[-2000:]}\n{stderr[-2000:]}"
            )
        try:
            with urlopen(health_url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.1)
    raise TimeoutError(f"Frontend acceptance server did not become healthy: {health_url}")


def issue_markup(issue: dict) -> str:
    issue_type = html.escape(str(issue.get("type", "issue")))
    message = html.escape(str(issue.get("message", "")))
    details = {key: value for key, value in issue.items() if key not in {"type", "message"}}
    detail_html = ""
    if details:
        detail_html = f"<pre>{html.escape(json.dumps(details, indent=2, ensure_ascii=False))}</pre>"
    return f"<li><strong>{issue_type}</strong> — {message}{detail_html}</li>"


def build_html(report: dict) -> str:
    surfaces = report.get("surfaces", [])
    failures = [surface for surface in surfaces if surface.get("status") == "FAIL"]
    passes = [surface for surface in surfaces if surface.get("status") == "PASS"]
    ordered = failures + passes
    cards = []
    for surface in ordered:
        status = surface.get("status", "UNKNOWN")
        name = html.escape(str(surface.get("name", "surface")))
        viewport = surface.get("viewport", {})
        viewport_text = f"{viewport.get('width', '?')}×{viewport.get('height', '?')}"
        screenshot = html.escape(f"../{surface.get('screenshot', '')}")
        issues = surface.get("issues", [])
        issues_html = (
            f"<ul class='issues'>{''.join(issue_markup(issue) for issue in issues)}</ul>"
            if issues
            else "<p class='clean'>No objective issues detected.</p>"
        )
        cards.append(
            f"""
            <article class="card {status.lower()}">
              <header>
                <div>
                  <h2>{name}</h2>
                  <p>{html.escape(viewport_text)}</p>
                </div>
                <span class="badge">{html.escape(status)}</span>
              </header>
              <a class="shot-link" href="{screenshot}" target="_blank" rel="noreferrer">
                <img src="{screenshot}" alt="{name} screenshot" loading="lazy" />
              </a>
              {issues_html}
            </article>
            """
        )

    runtime = report.get("runtime", {})
    runtime_issues = []
    for message in runtime.get("console_errors", []):
        runtime_issues.append({"type": "console-error", "message": message})
    for message in runtime.get("page_errors", []):
        runtime_issues.append({"type": "page-error", "message": message})
    for item in runtime.get("http_errors", []):
        runtime_issues.append(
            {
                "type": "http-error",
                "message": f"{item.get('status')} {item.get('url')}",
            }
        )
    fatal = report.get("fatal")
    if fatal:
        runtime_issues.append(
            {
                "type": "fatal",
                "message": f"{fatal.get('type')}: {fatal.get('message')}",
            }
        )
    runtime_markup = (
        f"<ul class='issues'>{''.join(issue_markup(issue) for issue in runtime_issues)}</ul>"
        if runtime_issues
        else "<p class='clean'>Console, page, and HTTP error gates are clean.</p>"
    )

    result = html.escape(str(report.get("result", "UNKNOWN")))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>ArchBro Core Frontend Surface Acceptance</title>
<style>
:root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; background:#f6f7fb; color:#202124; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; }}
main {{ width:min(1480px, calc(100% - 32px)); margin:0 auto; padding:32px 0 64px; }}
.summary {{ display:flex; flex-wrap:wrap; gap:16px; align-items:center; justify-content:space-between; margin-bottom:24px; }}
.summary h1 {{ margin:0 0 6px; font-size:30px; }}
.summary p {{ margin:0; color:#667085; }}
.result {{ padding:10px 14px; border-radius:999px; font-weight:800; background:#fff; border:1px solid #d9deea; }}
.stats {{ display:flex; flex-wrap:wrap; gap:10px; margin:18px 0 26px; }}
.stat {{ background:#fff; border:1px solid #e0e4ec; border-radius:12px; padding:10px 14px; }}
.runtime {{ background:#fff; border:1px solid #e0e4ec; border-radius:16px; padding:18px; margin-bottom:22px; }}
.runtime h2 {{ margin:0 0 10px; font-size:18px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr)); gap:18px; }}
.card {{ background:#fff; border:1px solid #e0e4ec; border-radius:16px; overflow:hidden; box-shadow:0 8px 28px rgba(21,27,38,.05); }}
.card.fail {{ border-color:#e6a8a8; }}
.card header {{ display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding:16px 16px 12px; }}
.card h2 {{ margin:0; font-size:17px; overflow-wrap:anywhere; }}
.card header p {{ margin:5px 0 0; color:#667085; font-size:13px; }}
.badge {{ font-size:12px; font-weight:850; border-radius:999px; padding:5px 9px; background:#eef2f6; }}
.fail .badge {{ background:#fff0f0; color:#9f1d1d; }}
.pass .badge {{ background:#ecf8f2; color:#176b45; }}
.shot-link {{ display:block; background:#eef1f5; border-top:1px solid #edf0f5; border-bottom:1px solid #edf0f5; }}
.shot-link img {{ width:100%; height:320px; object-fit:contain; display:block; background:#fff; }}
.issues {{ margin:0; padding:14px 34px 18px; color:#7a2525; }}
.issues li + li {{ margin-top:10px; }}
.issues pre {{ overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; background:#f7f7f8; color:#353840; padding:9px; border-radius:8px; font-size:11px; }}
.clean {{ margin:0; padding:14px 16px 18px; color:#357257; }}
@media (max-width:640px) {{ main {{ width:min(100% - 20px,1480px); padding-top:20px; }} .shot-link img {{ height:240px; }} }}
</style>
</head>
<body>
<main>
  <section class="summary">
    <div>
      <h1>ArchBro Core Frontend Surface Acceptance</h1>
      <p>Core pages and important product states. This is intentionally not an exhaustive component catalogue.</p>
    </div>
    <div class="result">{result}</div>
  </section>
  <section class="stats">
    <div class="stat"><strong>{len(surfaces)}</strong> surfaces</div>
    <div class="stat"><strong>{len(failures)}</strong> failed</div>
    <div class="stat"><strong>{len(passes)}</strong> passed</div>
  </section>
  <section class="runtime">
    <h2>Runtime gates — {html.escape(str(runtime.get("status", "UNKNOWN")))}</h2>
    {runtime_markup}
  </section>
  <section class="grid">
    {''.join(cards)}
  </section>
</main>
</body>
</html>
"""


def generate_report(test_exit_code: int, stdout: str, stderr: str) -> dict:
    if SWEEP_REPORT.exists():
        sweep = json.loads(SWEEP_REPORT.read_text(encoding="utf-8"))
    else:
        sweep = {
            "schema": "archbro.frontend_surface_sweep.v1",
            "base_url": None,
            "surfaces": [],
            "runtime": {"status": "FAIL", "console_errors": [], "page_errors": [], "http_errors": []},
            "fatal": {"type": "MissingReport", "message": "surface_sweep_report.json was not produced."},
            "result": "FAIL",
        }

    combined = {
        "schema": "archbro.frontend_acceptance_report.v1",
        **sweep,
        "test_exit_code": test_exit_code,
        "runner_output": {
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
        },
    }
    if test_exit_code != 0:
        combined["result"] = "FAIL"

    UI_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    UI_REPORT_JSON.write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
    UI_REPORT_HTML.write_text(build_html(combined), encoding="utf-8")
    return combined


def _redacted(dsn: str) -> str:
    parts = urlsplit(dsn)
    if not parts.password:
        return dsn
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _provision_schema() -> tuple[str, str]:
    """Give this run its own PostgreSQL schema.

    PostgreSQL is the only backend now, so acceptance needs a real database.
    A private schema keeps the previous guarantee that this entry point cannot
    disturb -- or be disturbed by -- whatever is already in the developer's
    database, which is what the isolated-runtime contract was protecting.
    """

    base = (os.getenv("DATABASE_URL") or "postgresql://archbro:archbro@127.0.0.1:5432/archbro").strip()
    schema = f"acceptance_{uuid.uuid4().hex}"
    try:
        with psycopg.connect(base, autocommit=True) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator verbatim
        raise SystemExit(
            f"frontend acceptance needs PostgreSQL and could not reach "
            f"{_redacted(base)}: {exc}\n"
            f"Start one with:  docker compose up -d db\n"
            f"Or point DATABASE_URL at an existing instance."
        ) from exc
    separator = "&" if "?" in base else "?"
    return base, f"{base}{separator}options=-csearch_path%3D{schema}"


def _drop_schema(base_dsn: str, run_dsn: str) -> None:
    schema = run_dsn.rsplit("search_path%3D", 1)[-1]
    try:
        with psycopg.connect(base_dsn, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    except Exception:  # noqa: BLE001 - cleanup must not mask the real result
        pass


def run(open_report: bool = False) -> int:
    ART.mkdir(parents=True, exist_ok=True)
    SWEEP_REPORT.unlink(missing_ok=True)

    port = free_port()
    base_url = f"http://127.0.0.1:{port}/"
    env = os.environ.copy()
    source_root = str(ROOT / "src")
    env["PYTHONPATH"] = source_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["ARCHBRO_PROVIDER"] = "fake"
    env["ARCHBRO_AUTH_MODE"] = "local"
    env["ARCHBRO_PERSISTENCE"] = "postgres"
    base_dsn, run_dsn = _provision_schema()
    env["DATABASE_URL"] = run_dsn

    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "archbro.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    test_exit_code = 1
    test_stdout = ""
    test_stderr = ""
    try:
        wait_for_health(server, base_url)
        test_env = env.copy()
        test_env["ARCHBRO_BASE_URL"] = base_url
        test_env["ARCHBRO_FINAL_FIX_CASES"] = "autonomous_surface_sweep"
        completed = subprocess.run(
            [sys.executable, str(ROOT / "qa" / "playwright_final_fix.py")],
            cwd=ROOT,
            env=test_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        test_exit_code = completed.returncode
        test_stdout = completed.stdout
        test_stderr = completed.stderr
    except Exception as exc:
        test_stderr = f"{type(exc).__name__}: {exc}"
    finally:
        server.terminate()
        try:
            server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate()
        _drop_schema(base_dsn, run_dsn)

    report = generate_report(test_exit_code, test_stdout, test_stderr)
    failures = [surface for surface in report.get("surfaces", []) if surface.get("status") == "FAIL"]
    print(
        "FRONTEND_ACCEPTANCE",
        report.get("result"),
        json.dumps(
            {
                "surfaces": len(report.get("surfaces", [])),
                "failed": len(failures),
                "runtime": report.get("runtime", {}).get("status"),
            }
        ),
    )
    print(f"HUMAN_REPORT {UI_REPORT_HTML}")
    print(f"MACHINE_REPORT {UI_REPORT_JSON}")

    if open_report:
        webbrowser.open(UI_REPORT_HTML.resolve().as_uri())

    return 0 if report.get("result") == "PASS" else 1


class FrontendAcceptanceTest(unittest.TestCase):
    """Threaden-safe deterministic test entry point for frontend acceptance."""

    def test_frontend_acceptance(self) -> None:
        self.assertEqual(run(open_report=False), 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ArchBro autonomous frontend acceptance.")
    parser.add_argument("--open", action="store_true", help="Open the generated HTML report in the default browser.")
    args = parser.parse_args()
    return run(open_report=args.open)


if __name__ == "__main__":
    raise SystemExit(main())
