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


def test_output_template_has_timestamp_placeholder():
    prompt = eval_runner.generate_eval_prompt(
        eval_runner.load_rubric(str(ROOT / "rubrics" / "code-quality.yaml")), "."
    )
    assert '"timestamp": "<ISO-8601 UTC>"' in prompt  # filled at evaluation time


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


def load_prompt(rubric_file: str, target=".") -> str:
    rubric = eval_runner.load_rubric(str(ROOT / "rubrics" / rubric_file))
    return eval_runner.generate_eval_prompt(rubric, target)


def test_scoring_method_pass_fail_gets_strict_instructions():
    prompt = load_prompt("security-checklist.yaml")
    assert "1.0 (pass) or 0.0 (fail)" in prompt
    assert "no partial credit" in prompt


def test_scoring_method_weighted_average_allows_partial():
    prompt = load_prompt("code-quality.yaml")
    assert "0.5 = partial" in prompt


def test_scoring_method_points_uses_scale_max(tmp_path):
    points_rubric = """\
name: points-rubric
criteria:
  - id: a
    title: A
    weight: 1.0
    checklist: ["x"]
scoring:
  method: points
  scale: [0, 10]
  pass_threshold: 0.7
"""
    rubric = eval_runner.load_rubric(write_rubric(tmp_path, points_rubric))
    prompt = eval_runner.generate_eval_prompt(rubric, ".")
    assert "0-10 point scale" in prompt
    assert "dividing by 10" in prompt


def test_report_include_drives_output_sections():
    security = load_prompt("security-checklist.yaml")
    assert '"security_flags"' in security
    assert '"remediation_steps"' in security
    assert '"blocking_issues"' in security
    assert '"recommendations"' not in security  # not in this rubric's include

    docs = load_prompt("documentation.yaml")
    assert '"missing_sections"' in docs
    assert '"outdated_references"' in docs
    assert "After the JSON block, also render the same results as a markdown report" in docs


def test_target_context_single_file_embedded_content(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    context = eval_runner.build_target_context(str(target))
    assert "single file" in context
    assert "return a + b" in context


def test_target_context_file_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_runner, "MAX_FILE_CONTEXT_CHARS", 50)
    target = tmp_path / "big.txt"
    target.write_text("x" * 500, encoding="utf-8")
    context = eval_runner.build_target_context(str(target))
    assert "[truncated 450 chars]" in context
    assert "x" * 500 not in context


def test_target_context_binary_file_omitted(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\x01\x02binary")
    context = eval_runner.build_target_context(str(target))
    assert "binary — content omitted" in context


def test_target_context_directory_listing_and_ignores(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hi", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "index").write_text("ignored", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("x", encoding="utf-8")

    context = eval_runner.build_target_context(str(tmp_path))
    assert "directory" in context
    assert "src/main.py" in context
    assert "README.md" in context
    assert ".git/" not in context and "node_modules/" not in context


def test_target_context_directory_listing_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_runner, "MAX_LISTED_FILES", 3)
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    context = eval_runner.build_target_context(str(tmp_path))
    assert "[listing truncated at 3 files]" in context


def test_target_context_missing_path_is_labelled():
    context = eval_runner.build_target_context("no/such/path")
    assert "no filesystem context" in context


def test_cli_no_context_flag_omits_listing(tmp_path):
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    result = run_cli("--rubric", "rubrics/code-quality.yaml",
                     "--prompt-only", "--target", str(tmp_path))
    assert "file.txt" in result.stdout.decode("utf-8")

    result = run_cli("--rubric", "rubrics/code-quality.yaml",
                     "--prompt-only", "--no-context", "--target", str(tmp_path))
    assert "file.txt" not in result.stdout.decode("utf-8")


def test_cli_default_mode_prints_prompt_on_stdout_hints_on_stderr(tmp_path):
    result = run_cli("--rubric", "rubrics/code-quality.yaml",
                     "--target", str(tmp_path), "--no-context")
    assert result.returncode == 0
    stdout = result.stdout.decode("utf-8")
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert stdout.startswith("You are an evaluation assessor")
    assert "Pipe the prompt" in stderr
    assert "Rubric: code-quality" in stderr
