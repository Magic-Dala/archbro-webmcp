from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_github_browser_contract_reuses_firebase_identity_without_repository_scope():
    app = (ROOT / "frontend/web/app.js").read_text(encoding="utf-8")
    firebase_module = (ROOT / "frontend/web/firebase-auth.js").read_text(
        encoding="utf-8"
    )

    assert "GithubAuthProvider" in firebase_module
    assert "signInWithGitHubAccount" in firebase_module
    assert "auth/missing-auth-domain" in firebase_module
    assert "['github', signInWithGitHubAccount]" in app
    assert "GitHub login is not enabled yet" not in app
    assert ".addScope(" not in firebase_module
    assert "GithubAuthProvider" not in app
