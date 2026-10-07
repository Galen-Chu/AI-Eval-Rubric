"""
Evaluation Runner · 評估執行器

Loads a rubric YAML and prepares the evaluation context for an LLM
to assess a target (code, docs, API, project) against the rubric criteria.

The runner handles:
- Rubric loading and validation
- Generating the evaluation prompt (rubric + target context)
- Defining the JSON report format the assessor must return

Scoring itself is performed by the LLM the prompt is piped to; the runner
collects no results.

Usage:
    python runner/eval_runner.py --rubric rubrics/code-quality.yaml --target ./src/
    python runner/eval_runner.py --list
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML required. Install with: pip install pyyaml")
    sys.exit(1)

ROOT = Path(__file__).parent.parent
RUBRICS_DIR = ROOT / "rubrics"

VALID_SCORING_METHODS = {"weighted_average", "pass_fail", "points"}

# Target-context snapshot bounds and directories never worth listing.
IGNORED_DIR_NAMES = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "venv", ".venv",
    "dist", "build", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
    ".tox", ".eggs",
}
MAX_LISTED_FILES = 200
MAX_FILE_CONTEXT_CHARS = 10_000


def _force_utf8_stdio():
    """Pin stdio to UTF-8.

    Windows pipes and redirected files default to the legacy ANSI code page
    (e.g. cp950), which cannot encode characters that appear in rubric text
    such as '²' (O(n²)) — printing the prompt crashed with UnicodeEncodeError.
    Reconfigure is skipped when the stream does not support it.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _in_unit_range(value) -> bool:
    """True when value is a number (not bool) within [0.0, 1.0]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return 0.0 <= value <= 1.0


def _validate_rubric(rubric):
    """Collect every schema problem as a single readable ValueError."""
    errors = []

    for key in ("name", "criteria", "scoring"):
        if key not in rubric:
            errors.append(f"missing required key: '{key}'")
    if errors:
        return errors  # nothing else can be checked reliably

    criteria = rubric["criteria"]
    if not isinstance(criteria, list) or not criteria:
        errors.append("'criteria' must be a non-empty list")
        criteria = []

    seen_ids = set()
    for idx, criterion in enumerate(criteria, 1):
        where = f"criterion {idx}"
        if not isinstance(criterion, dict):
            errors.append(f"{where}: must be a mapping, got {type(criterion).__name__}")
            continue
        cid = criterion.get("id")
        if cid:
            where = f"criterion {idx} ({cid!r})"
            if cid in seen_ids:
                errors.append(f"{where}: duplicate id (already used above)")
            seen_ids.add(cid)
        else:
            errors.append(f"{where}: missing required field 'id'")

        for field in ("title", "weight"):
            if field not in criterion:
                errors.append(f"{where}: missing required field {field!r}")
        if "weight" in criterion:
            weight = criterion["weight"]
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
                errors.append(f"{where}: 'weight' must be a positive number, got {weight!r}")
        if "threshold" in criterion and not _in_unit_range(criterion["threshold"]):
            errors.append(f"{where}: 'threshold' must be a number in [0.0, 1.0], "
                          f"got {criterion['threshold']!r}")
        if "checklist" in criterion:
            checklist = criterion["checklist"]
            if not isinstance(checklist, list) or not all(isinstance(c, str) for c in checklist):
                errors.append(f"{where}: 'checklist' must be a list of strings")

    scoring = rubric["scoring"]
    if not isinstance(scoring, dict):
        errors.append(f"'scoring' must be a mapping, got {type(scoring).__name__}")
    else:
        method = scoring.get("method", "weighted_average")
        if method not in VALID_SCORING_METHODS:
            errors.append(f"'scoring.method' must be one of "
                          f"{sorted(VALID_SCORING_METHODS)}, got {method!r}")
        if "pass_threshold" in scoring and not _in_unit_range(scoring["pass_threshold"]):
            errors.append(f"'scoring.pass_threshold' must be a number in [0.0, 1.0], "
                          f"got {scoring['pass_threshold']!r}")

    return errors


def load_rubric(rubric_path: str) -> dict:
    """Load and validate a rubric YAML file."""
    path = Path(rubric_path)
    if not path.exists():
        raise FileNotFoundError(f"Rubric not found: {path}")

    try:
        with open(path, encoding="utf-8") as f:
            rubric = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {path}:\n{e}") from e

    if not isinstance(rubric, dict):
        raise ValueError(f"Invalid rubric {path}: expected a YAML mapping, "
                         f"got {type(rubric).__name__}")

    errors = _validate_rubric(rubric)
    if errors:
        raise ValueError(f"Invalid rubric {path}:\n  - " + "\n  - ".join(errors))

    return rubric


def list_rubrics() -> list:
    """List all available rubrics."""
    rubrics = []
    if not RUBRICS_DIR.exists():
        return rubrics

    for filepath in sorted(RUBRICS_DIR.glob("*.y*ml")):
        try:
            rubric = yaml.safe_load(filepath.read_text(encoding="utf-8"))
            rubrics.append({
                "file": filepath.name,
                "name": rubric.get("name", filepath.stem),
                "category": rubric.get("category", "unknown"),
                "description": rubric.get("description", "")[:80],
                "criteria_count": len(rubric.get("criteria", [])),
            })
        except Exception as e:
            rubrics.append({
                "file": filepath.name,
                "name": filepath.stem,
                "category": "error",
                "description": str(e)[:80],
                "criteria_count": 0,
            })

    return rubrics


def _scoring_instructions(scoring: dict) -> str:
    """Method-specific scoring math shared by every instruction block."""
    method = scoring.get("method", "weighted_average")
    pass_threshold = scoring.get("pass_threshold", 0.75)
    common = (
        "4. A criterion passes when its score >= its threshold; "
        f"the rubric passes when the overall score >= {pass_threshold}\n"
        "5. List blocking issues (failed items that must be fixed) and "
        "actionable recommendations"
    )
    if method == "pass_fail":
        return (
            "1. Score each checklist item strictly 1.0 (pass) or 0.0 (fail) — "
            "no partial credit\n"
            "2. Per-criterion score = fraction of its checklist items that passed\n"
            "3. Overall score = weighted average of criterion scores "
            "(using each weight)\n" + common
        )
    if method == "points":
        top = max(scoring.get("scale", [0, 1]))
        return (
            f"1. Score each checklist item on a 0-{top} point scale\n"
            f"2. Normalize each item score to 0-1 by dividing by {top}; "
            "per-criterion score = normalized average\n"
            "3. Overall score = weighted average of criterion scores "
            "(using each weight)\n" + common
        )
    return (
        "1. For each criterion, score each checklist item "
        "(0 = fail, 0.5 = partial, 1 = pass)\n"
        "2. Per-criterion score = average of its checklist item scores\n"
        "3. Overall score = weighted average of criterion scores "
        "(using each weight)\n" + common
    )


# JSON snippets emitted for each report.include entry; sections not listed
# here fall back to a generic findings array.
_OUTPUT_SECTION_TEMPLATES = {
    "per_criterion_scores": (
        '  "criteria_scores": [\n'
        '    {\n'
        '      "id": "<criterion id>",\n'
        '      "title": "<title>",\n'
        '      "score": 0.0,\n'
        '      "weight": 1.0,\n'
        '      "threshold": 0.7,\n'
        '      "passed": true,\n'
        '      "checklist_results": [\n'
        '        {"item": "<checklist item>", "score": 1.0, "passed": true}\n'
        '      ]\n'
        '    }\n'
        '  ]'
    ),
    "overall_score": '  "overall_score": 0.0',
    "pass_fail_summary": '  "overall_passed": true/false',
    "blocking_issues": '  "blocking_issues": ["<issue that must be fixed before acceptance>"]',
    "recommendations": '  "recommendations": ["<actionable suggestion>"]',
    "security_flags": '  "security_flags": [{"item": "<checklist item>", "severity": "high|medium|low", "detail": "<finding>"}]',
    "remediation_steps": '  "remediation_steps": ["<ordered step to fix a finding>"]',
    "missing_sections": '  "missing_sections": ["<expected doc section that is absent>"]',
    "outdated_references": '  "outdated_references": [{"reference": "<stale mention>", "reason": "<why outdated>"}]',
}


def _format_output_sections(rubric: dict) -> str:
    """Render the JSON output template required by the rubric's report.include."""
    report = rubric.get("report") or {}
    include = report.get("include") or list(_OUTPUT_SECTION_TEMPLATES)[:4]

    lines = [
        '  "rubric": "<rubric name>"',
        '  "timestamp": "<ISO-8601 UTC>"',
    ]
    for section in include:
        lines.append(_OUTPUT_SECTION_TEMPLATES.get(
            section, f'  "{section}": ["<{section} findings>"]'
        ))
    return ",\n".join(lines)


def generate_eval_prompt(rubric: dict, target_path: str, target_context: str = "") -> str:
    """Generate the evaluation prompt combining rubric and target context."""
    name = rubric["name"]
    criteria_text = ""

    for i, criterion in enumerate(rubric["criteria"], 1):
        criteria_text += (
            f"\n### {i}. {criterion['title']} "
            f"(id: {criterion['id']}, weight: {criterion['weight']})\n"
        )
        criteria_text += f"Threshold: {criterion.get('threshold', 0.7)}\n"
        for check in criterion.get("checklist", []):
            criteria_text += f"- [ ] {check}\n"

    scoring = rubric["scoring"]
    context_block = f"\n## Target context:\n{target_context}\n" if target_context else ""
    report = rubric.get("report") or {}
    markdown_note = (
        "\nAfter the JSON block, also render the same results as a markdown "
        "report (summary table plus one section per criterion).\n"
        if "markdown" in str(report.get("format", "")) else ""
    )

    prompt = f"""You are an evaluation assessor. Apply the following rubric to
the target and produce a scored report.

## Rubric: {name}
## Target: {target_path}
{context_block}
## Criteria:
{criteria_text}
## Scoring:
- Method: {scoring.get('method', 'weighted_average')}
- Scale: {scoring.get('scale', [0, 1])}
- Pass threshold: {scoring.get('pass_threshold', 0.75)}

## Instructions:
{_scoring_instructions(scoring)}
- Use the exact criterion id from each heading above in the JSON output
- Judge only the material present in the target context unless more is supplied

## Output format (JSON):
{{
{_format_output_sections(rubric)}
}}
{markdown_note}"""
    return prompt


def build_target_context(target_path: str) -> str:
    """Collect a bounded snapshot of the target to embed in the prompt.

    A single file contributes its (truncated) content; a directory
    contributes a file listing with common build/VCS directories pruned.
    A path that does not exist is treated as an opaque label the caller
    supplies material for by other means.
    """
    path = Path(target_path)
    if not path.exists():
        return (f"(no filesystem context for '{target_path}' — evaluate "
                "based on material supplied separately)")
    if path.is_file():
        return _file_context(path)
    return _directory_context(path)


def _file_context(path: Path) -> str:
    size = path.stat().st_size
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        return (f"Target is a single file: {path.name} ({size} bytes, "
                f"not readable as UTF-8 text: {e})")
    if "\x00" in text:
        return f"Target is a single file: {path.name} ({size} bytes, binary — content omitted)"
    if len(text) > MAX_FILE_CONTEXT_CHARS:
        text = text[:MAX_FILE_CONTEXT_CHARS] + f"\n... [truncated {len(text) - MAX_FILE_CONTEXT_CHARS} chars]"
    return f"Target is a single file: {path.name} ({size} bytes)\n\n```\n{text}\n```"


def _directory_context(root: Path) -> str:
    entries = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIR_NAMES)
        for fname in sorted(filenames):
            file_path = Path(dirpath) / fname
            try:
                size = file_path.stat().st_size
            except OSError:
                size = 0
            entries.append(f"- {file_path.relative_to(root).as_posix()} ({size} bytes)")
            if len(entries) >= MAX_LISTED_FILES:
                truncated = True
                break
        if truncated:
            break
    header = (f"Target is a directory: {root} — {len(entries)} file(s) listed "
              "(common build/VCS directories excluded)")
    if truncated:
        entries.append(f"... [listing truncated at {MAX_LISTED_FILES} files]")
    return header + ":\n" + "\n".join(entries)


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="AI-Eval-Rubric Runner")
    parser.add_argument("--rubric", type=str, help="Path to rubric YAML")
    parser.add_argument("--target", type=str, help="Path to target being evaluated")
    parser.add_argument("--list", action="store_true", help="List available rubrics")
    parser.add_argument("--prompt-only", action="store_true",
                        help="Print only the evaluation prompt (summary goes to stderr)")
    parser.add_argument("--no-context", action="store_true",
                        help="Omit target filesystem context from the prompt")
    args = parser.parse_args()

    if args.list:
        rubrics = list_rubrics()
        print(f"\n  Available Rubrics ({len(rubrics)}):")
        print(f"  {'='*60}\n")
        for r in rubrics:
            print(f"  {r['file']:<30} {r['category']:<12} {r['criteria_count']} criteria")
            print(f"  {'':>30} {r['description']}")
            print()
        return

    if not args.rubric:
        parser.print_help()
        return

    try:
        rubric = load_rubric(args.rubric)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    target = args.target or os.getcwd()
    target_context = "" if args.no_context else build_target_context(target)
    prompt = generate_eval_prompt(rubric, target, target_context=target_context)

    print(prompt)

    if not args.prompt_only:
        print(
            f"\nRubric: {rubric['name']} · Target: {target} · "
            f"Criteria: {len(rubric['criteria'])} · "
            f"Pass threshold: {rubric['scoring'].get('pass_threshold', 0.75)}\n"
            "Pipe the prompt to an LLM CLI, e.g.:\n"
            f'  bash:        claude -p "$(python runner/eval_runner.py --rubric {args.rubric} --prompt-only --target {target})"\n'
            f"  PowerShell:  claude -p (python runner/eval_runner.py --rubric {args.rubric} --prompt-only --target {target})",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
