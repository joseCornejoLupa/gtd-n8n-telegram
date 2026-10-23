"""El repositorio es público: ningún secreto debe poder llegar a git."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture(scope="module", autouse=True)
def inside_git_repo():
    try:
        git("rev-parse", "--git-dir")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("No es un repositorio git")


def test_secrets_directory_is_ignored():
    for name in ("api_key.txt", "db_password.txt", "allowed_telegram_ids.txt"):
        out = subprocess.run(
            ["git", "check-ignore", f"secrets/{name}"], cwd=ROOT, capture_output=True, text=True
        )
        assert out.returncode == 0, f"secrets/{name} no está ignorado por git"


def test_no_tracked_file_lives_under_secrets():
    tracked = git("ls-files").splitlines()
    assert not [f for f in tracked if f.startswith("secrets/")]


def test_compose_secrets_point_at_ignored_files():
    compose = (ROOT / "docker-compose.yml").read_text()
    for name in ("db_password", "api_key", "allowed_telegram_ids"):
        assert f"./secrets/{name}.txt" in compose


def test_local_compose_override_is_ignored():
    # Ahí van los ajustes del servidor de cada quien (red de n8n, puertos): no se versiona.
    out = subprocess.run(
        ["git", "check-ignore", "docker-compose.override.yml"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert out.returncode == 0
