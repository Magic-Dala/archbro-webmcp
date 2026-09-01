from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_email_password_browser_contract_has_no_anonymous_fallback():
    index = (ROOT / "frontend/web/index.html").read_text(encoding="utf-8")
    firebase_module = (ROOT / "frontend/web/firebase-auth.js").read_text(
        encoding="utf-8"
    )

    assert "signInAnonymously" not in firebase_module
    assert "createUserWithEmailAndPassword" in firebase_module
    assert "signInWithEmailAndPassword" in firebase_module
    assert "signOutFromFirebase" in firebase_module
    assert index.index("/static/firebase-auth-client.js") < index.index(
        "/static/app.js"
    )


def test_email_password_browser_behavior_with_fake_firebase_sdk():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the deterministic browser-auth test")

    completed = subprocess.run(
        [node, "--test", str(ROOT / "qa/test_firebase_email_auth.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
