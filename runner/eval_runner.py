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
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML required. Install with: pip install pyyaml")
    sys.exit(1)

ROOT = Path(__file__).parent.parent
RUBRICS_DIR = ROOT / "rubrics"


def load_rubric(rubric_path: str) -> dict:
    """Load and validate a rubric YAML file."""
    path = Path(rubric_path)
    if not path.exists():
        raise FileNotFoundError(f"Rubric not found: {path}")

    with open(path, encoding="utf-8") as f:
        rubric = yaml.safe_load(f)

    required_keys = ["name", "criteria", "scoring"]
    for key in required_keys:
        if key not in rubric:
            raise ValueError(f"Rubric missing required key: {key}")

    if not rubric["criteria"]:
        raise ValueError("Rubric has no criteria")

    return rubric


def list_rubrics() -> list:
    """List all available rubrics."""
    rubrics = []
    if not RUBRICS_DIR.exists():
        return rubrics

    for filepath in sorted(RUBRICS_DIR.glob("*.yaml")):
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
        criteria_text += f"\n### {i}. {criterion['title']} (weight: {criterion['weight']})\n"
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
  "timestamp": "{datetime.utcnow().isoformat()}",
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
        print(f"  Error: {e}")
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
