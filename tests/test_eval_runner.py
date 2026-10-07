"""Tests for runner/eval_runner.py — loaded by path so the runner stays a script."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    for rubric_file in sorted((ROOT / "rubrics").glob("*.yaml")):
        rubric = eval_runner.load_rubric(str(rubric_file))
        assert rubric["name"], rubric_file.name
        assert rubric["criteria"], rubric_file.name


def write_rubric(tmp_path, content: str) -> str:
    path = tmp_path / "rubric.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


VALID_RUBRIC = """\
name: test-rubric
criteria:
  - id: alpha
    title: Alpha
    weight: 1.0
    threshold: 0.7
    checklist: ["check one", "check two"]
scoring:
  method: weighted_average
  scale: [0, 1]
  pass_threshold: 0.75
"""


def test_prompt_contains_criterion_ids():
    prompt = eval_runner.generate_eval_prompt(
        eval_runner.load_rubric(str(ROOT / "rubrics" / "code-quality.yaml")), "."
    )
    for criterion_id in ("readability", "maintainability", "testing"):
        assert f"id: {criterion_id}" in prompt


def test_invalid_rubrics_raise_value_error(tmp_path):
    cases = {
        # label: (yaml content, expected message fragment)
        "missing scoring key": (
            "name: x\ncriteria:\n  - id: a\n    title: A\n    weight: 1\n",
            "missing required key: 'scoring'",
        ),
        "empty file": ("", "expected a YAML mapping"),
        "not a mapping": ("- just\n- a\n- list\n", "expected a YAML mapping"),
        "broken yaml": ("name: [unclosed\n", "Invalid YAML"),
        "no criteria": (
            "name: x\ncriteria: []\nscoring: {method: weighted_average}\n",
            "non-empty list",
        ),
        "criterion missing weight": (
            "name: x\ncriteria:\n  - id: a\n    title: A\n    checklist: [c]\n"
            "scoring: {method: weighted_average}\n",
            "missing required field 'weight'",
        ),
        "duplicate ids": (
            "name: x\ncriteria:\n"
            "  - {id: a, title: A, weight: 1}\n"
            "  - {id: a, title: B, weight: 1}\n"
            "scoring: {method: weighted_average}\n",
            "duplicate id",
        ),
        "negative weight": (
            "name: x\ncriteria:\n  - {id: a, title: A, weight: -1}\n"
            "scoring: {method: weighted_average}\n",
            "positive number",
        ),
        "threshold out of range": (
            "name: x\ncriteria:\n  - {id: a, title: A, weight: 1, threshold: 1.5}\n"
            "scoring: {method: weighted_average}\n",
            r"\[0\.0, 1\.0\]",
        ),
        "unknown scoring method": (
            "name: x\ncriteria:\n  - {id: a, title: A, weight: 1}\n"
            "scoring: {method: vibes}\n",
            "scoring.method",
        ),
        "checklist not strings": (
            "name: x\ncriteria:\n  - {id: a, title: A, weight: 1, checklist: [1, 2]}\n"
            "scoring: {method: weighted_average}\n",
            "list of strings",
        ),
    }
    for label, (content, message) in cases.items():
        path = write_rubric(tmp_path, content)
        with pytest.raises(ValueError, match=message):
            eval_runner.load_rubric(path)


def test_missing_rubric_file_raises_filenotfound():
    with pytest.raises(FileNotFoundError):
        eval_runner.load_rubric("does/not/exist.yaml")


def test_cli_reports_invalid_rubric_cleanly(tmp_path):
    path = write_rubric(tmp_path, "name: [broken\n")
    result = run_cli("--rubric", path, "--prompt-only")
    assert result.returncode == 1
    err = result.stderr.decode("utf-8", errors="replace")
    assert "Error" in err and "Traceback" not in err


def test_valid_custom_rubric_loads(tmp_path):
    rubric = eval_runner.load_rubric(write_rubric(tmp_path, VALID_RUBRIC))
    assert rubric["name"] == "test-rubric"
