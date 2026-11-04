"""Infrastructure composition and shell contracts without requiring Docker."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_multi_stage_image_and_non_root_runtime():
    dockerfile = (ROOT / "infrastructure/Dockerfile").read_text()
    assert "AS builder" in dockerfile
    assert "AS runtime" in dockerfile
    assert "USER runner" in dockerfile
    assert "pip wheel" in dockerfile


def test_compose_extends_existing_postgres_and_is_not_public():
    compose = (ROOT / "infrastructure/compose.tools.yml").read_text()
    assert "postgres:" in compose
    assert "context: ." in compose
    assert "dockerfile: infrastructure/Dockerfile" in compose
    assert "ports:" not in compose
    assert "read_only: true" in compose


def test_shell_scripts_are_syntactically_valid():
    if shutil.which("bash") is None:
        return
    for name in ("bootstrap.sh", "verify.sh"):
        path = ROOT / "infrastructure" / name
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_script_guards_and_cleanup():
    bootstrap = (ROOT / "infrastructure/bootstrap.sh").read_text()
    verify = (ROOT / "infrastructure/verify.sh").read_text()
    assert "set -euo pipefail" in bootstrap
    assert "set -euo pipefail" in verify
    assert "docker compose" in bootstrap
    assert "run --rm tools seed" in bootstrap
    assert "trap 'rm -f" in verify
    assert "00000006" in verify
    assert "00000001" in verify
