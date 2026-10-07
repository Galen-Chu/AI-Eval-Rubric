"""Tests for runner/eval_runner.py — loaded by path so the runner stays a script."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "runner" / "eval_runner.py"

_spec = importlib.util.spec_from_file_location("eval_runner", RUNNER_PATH)
eval_runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_runner)


def run_cli(*args, env_extra=None):
    """Run the CLI as a subprocess and return the completed process."""
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(RUNNER_PATH), *args],
        capture_output=True,
        cwd=ROOT,
        env=env,
    )


def test_prompt_only_output_is_utf8_even_under_cp950():
    """Regression: printing the prompt crashed with UnicodeEncodeError when
    stdout used a legacy Windows code page (cp950 cannot encode '²' in O(n²))."""
    result = run_cli(
        "--rubric", "rubrics/code-quality.yaml",
        "--prompt-only", "--target", ".",
        env_extra={"PYTHONIOENCODING": "cp950"},
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    text = result.stdout.decode("utf-8")
    assert "O(n²)" in text


def test_timestamp_in_prompt_is_timezone_aware():
    prompt = eval_runner.generate_eval_prompt(
        eval_runner.load_rubric(str(ROOT / "rubrics" / "code-quality.yaml")), "."
    )
    assert "+00:00" in prompt  # datetime.now(timezone.utc), not naive utcnow()


def test_list_finds_all_shipped_rubrics():
    result = run_cli("--list")
    assert result.returncode == 0
    out = result.stdout.decode("utf-8", errors="replace")
    for rubric in ("code-quality", "project-acceptance", "api-review",
                   "documentation", "security-checklist"):
        assert rubric in out


def test_load_rubric_accepts_shipped_rubrics():
    rubric = eval_runner.load_rubric(str(ROOT / "rubrics" / "code-quality.yaml"))
    assert rubric["name"] == "code-quality"
    assert len(rubric["criteria"]) == 6
