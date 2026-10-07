"""
Evaluation Runner · 評估執行器

Loads a rubric YAML and prepares the evaluation context for an LLM
to assess a target (code, docs, API, project) against the rubric criteria.

The runner handles:
- Rubric loading and validation
- Generating the evaluation prompt (rubric + target context)
- Collecting and formatting results

Usage:
    python runner/eval_runner.py --rubric rubrics/code-quality.yaml --target ./src/
    python runner/eval_runner.py --list
"""
import argparse
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


def generate_eval_prompt(rubric: dict, target_path: str) -> str:
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
    pass_threshold = scoring.get("pass_threshold", 0.75)

    prompt = f"""You are an evaluation assessor. Apply the following rubric to
the target and produce a scored report.

## Rubric: {name}
## Target: {target_path}

## Criteria:
{criteria_text}

## Scoring:
- Method: {scoring.get('method', 'weighted_average')}
- Scale: {scoring.get('scale', [0, 1])}
- Pass threshold: {pass_threshold}

## Instructions:
1. For each criterion, score each checklist item (0 = fail, 0.5 = partial, 1 = pass)
2. Calculate per-criterion score (average of checklist items)
3. Calculate weighted overall score
4. Determine pass/fail per criterion and overall
5. List any blocking issues
6. Provide actionable recommendations

## Output format (JSON):
{{
  "rubric": "{name}",
  "timestamp": "{datetime.now(timezone.utc).isoformat()}",
  "criteria_scores": [
    {{
      "id": "criterion-id",
      "title": "title",
      "score": 0.0,
      "weight": 1.0,
      "threshold": 0.7,
      "passed": true/false,
      "checklist_results": [
        {{"item": "check point", "score": 1.0, "passed": true}}
      ]
    }}
  ],
  "overall_score": 0.0,
  "overall_passed": true/false,
  "blocking_issues": [],
  "recommendations": []
}}
"""
    return prompt


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="AI-Eval-Rubric Runner")
    parser.add_argument("--rubric", type=str, help="Path to rubric YAML")
    parser.add_argument("--target", type=str, help="Path to target being evaluated")
    parser.add_argument("--list", action="store_true", help="List available rubrics")
    parser.add_argument("--prompt-only", action="store_true",
                        help="Output evaluation prompt without running")
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

    target = args.target or "current directory"

    if args.prompt_only:
        prompt = generate_eval_prompt(rubric, target)
        print(prompt)
        return

    # Generate prompt for LLM execution
    prompt = generate_eval_prompt(rubric, target)

    print(f"\n  Rubric: {rubric['name']}")
    print(f"  Target: {target}")
    print(f"  Criteria: {len(rubric['criteria'])}")
    print(f"  Pass threshold: {rubric['scoring'].get('pass_threshold', 0.75)}")
    print(f"\n  Evaluation prompt generated ({len(prompt)} chars)")
    print(f"  Run with --prompt-only to see the full prompt")
    print(f"  Then pipe to Claude: claude -p \"$(python runner/eval_runner.py --rubric {args.rubric} --prompt-only --target {target})\"")


if __name__ == "__main__":
    main()
