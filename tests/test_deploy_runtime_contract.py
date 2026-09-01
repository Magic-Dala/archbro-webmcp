from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "deploy-stack.sh"
IDENTITY_SCRIPT = Path(__file__).resolve().parents[1] / "qa" / "setup_archbro_identity_platform.ps1"


def test_identity_platform_public_and_staging_hosts_remain_parameter_driven() -> None:
    script = IDENTITY_SCRIPT.read_text(encoding="utf-8")
    lowered = script.lower()
    assert '\n$publichost =' not in lowered
    assert 'foreach ($domain in @($PublicHost, $StagingHost))' in script
    assert '$allowedReferrers = @("https://$PublicHost/*")' in script


def _validate(tmp_path: Path, stack: str, env_text: str) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        pytest.skip("deploy contract execution is exercised on the Linux CI runner")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for deploy contract validation")

    target = tmp_path / stack
    target.mkdir()
    (target / ".env").write_text(env_text, encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sudo = fake_bin / "sudo"
    sudo.write_text('#!/usr/bin/env bash\nexec "$@"\n', encoding="utf-8")
    sudo.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ARCHBRO_VALIDATE_ONLY"] = "1"

    return subprocess.run(
        [
            bash,
            str(SCRIPT),
            stack,
            str(target),
            "example.invalid/archbro:test",
            "example.invalid",
            "user",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_dev_accepts_explicit_local_staging_contract(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        "archbro-dev",
        "ARCHBRO_ENV=local\nARCHBRO_AUTH_MODE=local\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dev_accepts_complete_firebase_cutover_contract(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        "archbro-dev",
        "\n".join(
            [
                "ARCHBRO_ENV=production",
                "ARCHBRO_AUTH_MODE=firebase",
                "FIREBASE_PROJECT_ID=archbro-dev-example",
                "ARCHBRO_FIREBASE_API_KEY=example-key",
                "ARCHBRO_FIREBASE_AUTH_DOMAIN=archbro-main-example.firebaseapp.com",
                "",
            ]
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dev_rejects_mixed_runtime_auth_modes(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        "archbro-dev",
        "ARCHBRO_ENV=local\nARCHBRO_AUTH_MODE=firebase\n",
    )
    assert result.returncode != 0
    assert "must use local/local or a complete production/firebase configuration" in (
        result.stdout + result.stderr
    )


def test_main_rejects_local_staging_contract(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        "archbro-main",
        "ARCHBRO_ENV=local\nARCHBRO_AUTH_MODE=local\n",
    )
    assert result.returncode != 0
    assert "ARCHBRO_ENV=production" in result.stdout + result.stderr


def test_main_rejects_incomplete_firebase_contract_before_recreate(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        "archbro-main",
        "ARCHBRO_ENV=production\nARCHBRO_AUTH_MODE=firebase\n",
    )
    assert result.returncode != 0
    assert "FIREBASE_PROJECT_ID or GOOGLE_CLOUD_PROJECT" in result.stdout + result.stderr


def test_main_rejects_a_firebase_contract_without_the_browser_auth_domain(
    tmp_path: Path,
) -> None:
    # Google sign-in opens https://<authDomain>/__/auth/handler, so a contract
    # missing it deploys and boots but breaks the button. Catch it here, where
    # the message names the key, rather than at container start.
    result = _validate(
        tmp_path,
        "archbro-main",
        "\n".join(
            [
                "ARCHBRO_ENV=production",
                "ARCHBRO_AUTH_MODE=firebase",
                "FIREBASE_PROJECT_ID=archbro-main-example",
                "ARCHBRO_FIREBASE_API_KEY=example-key",
                "",
            ]
        ),
    )
    assert result.returncode != 0
    assert "ARCHBRO_FIREBASE_AUTH_DOMAIN" in result.stdout + result.stderr


def test_main_accepts_complete_firebase_contract_with_google_project_alias(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        "archbro-main",
        "\n".join(
            [
                "ARCHBRO_ENV=production",
                "ARCHBRO_AUTH_MODE=firebase",
                "GOOGLE_CLOUD_PROJECT=archbro-main-example",
                "ARCHBRO_FIREBASE_API_KEY=example-key",
                "ARCHBRO_FIREBASE_AUTH_DOMAIN=archbro-main-example.firebaseapp.com",
                "",
            ]
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("duplicate_line", "key"),
    [
        ("ARCHBRO_ENV=local", "ARCHBRO_ENV"),
        ("ARCHBRO_AUTH_MODE=local", "ARCHBRO_AUTH_MODE"),
        ("FIREBASE_PROJECT_ID=second-project", "FIREBASE_PROJECT_ID"),
        ("ARCHBRO_FIREBASE_API_KEY=second-key", "ARCHBRO_FIREBASE_API_KEY"),
    ],
)
def test_main_rejects_duplicate_contract_assignments(
    tmp_path: Path,
    duplicate_line: str,
    key: str,
) -> None:
    result = _validate(
        tmp_path,
        "archbro-main",
        "\n".join(
            [
                "ARCHBRO_ENV=production",
                "ARCHBRO_AUTH_MODE=firebase",
                "FIREBASE_PROJECT_ID=archbro-main-example",
                "ARCHBRO_FIREBASE_API_KEY=example-key",
                duplicate_line,
                "",
            ]
        ),
    )
    assert result.returncode != 0
    assert f"must set {key} exactly once" in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("empty_value", "expected_error"),
    [
        ('""', "FIREBASE_PROJECT_ID or GOOGLE_CLOUD_PROJECT"),
        ("''", "FIREBASE_PROJECT_ID or GOOGLE_CLOUD_PROJECT"),
        ('"   "', "must set non-empty literal FIREBASE_PROJECT_ID"),
    ],
)
def test_main_rejects_empty_or_whitespace_firebase_project(
    tmp_path: Path,
    empty_value: str,
    expected_error: str,
) -> None:
    result = _validate(
        tmp_path,
        "archbro-main",
        "\n".join(
            [
                "ARCHBRO_ENV=production",
                "ARCHBRO_AUTH_MODE=firebase",
                f"FIREBASE_PROJECT_ID={empty_value}",
                "ARCHBRO_FIREBASE_API_KEY=example-key",
                "",
            ]
        ),
    )
    assert result.returncode != 0
    assert expected_error in result.stdout + result.stderr


@pytest.mark.parametrize("empty_value", ['""', "''", '"   "'])
def test_main_rejects_empty_or_whitespace_browser_api_key(
    tmp_path: Path,
    empty_value: str,
) -> None:
    result = _validate(
        tmp_path,
        "archbro-main",
        "\n".join(
            [
                "ARCHBRO_ENV=production",
                "ARCHBRO_AUTH_MODE=firebase",
                "FIREBASE_PROJECT_ID=archbro-main-example",
                f"ARCHBRO_FIREBASE_API_KEY={empty_value}",
                "",
            ]
        ),
    )
    assert result.returncode != 0
    assert "must set non-empty literal ARCHBRO_FIREBASE_API_KEY" in (
        result.stdout + result.stderr
    )


def test_main_rejects_interpolated_firebase_contract_value(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        "archbro-main",
        "\n".join(
            [
                "ARCHBRO_ENV=production",
                "ARCHBRO_AUTH_MODE=firebase",
                "FIREBASE_PROJECT_ID=${FIREBASE_PROJECT_ID_FROM_HOST}",
                "ARCHBRO_FIREBASE_API_KEY=example-key",
                "",
            ]
        ),
    )
    assert result.returncode != 0
    assert "must set non-empty literal FIREBASE_PROJECT_ID" in result.stdout + result.stderr


def test_main_accepts_complete_contract_with_crlf(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        "archbro-main",
        "\r\n".join(
            [
                "ARCHBRO_ENV=production",
                "ARCHBRO_AUTH_MODE=firebase",
                "FIREBASE_PROJECT_ID=archbro-main-example",
                "ARCHBRO_FIREBASE_API_KEY=example-key",
                "ARCHBRO_FIREBASE_AUTH_DOMAIN=archbro-main-example.firebaseapp.com",
                "",
            ]
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_main_accepts_quoted_contract_with_inline_comments(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        "archbro-main",
        "\n".join(
            [
                ' ARCHBRO_ENV = "production" # runtime mode',
                " ARCHBRO_AUTH_MODE = 'firebase' # auth mode",
                ' FIREBASE_PROJECT_ID = "archbro-main-example" # project',
                " ARCHBRO_FIREBASE_API_KEY = 'example-key' # browser key",
                " ARCHBRO_FIREBASE_AUTH_DOMAIN = 'archbro.firebaseapp.com' # auth host",
                "",
            ]
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
