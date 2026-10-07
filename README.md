# 📏 AI-Eval-Rubric

[![CI](https://github.com/Galen-Chu/AI-Eval-Rubric/actions/workflows/ci.yml/badge.svg)](https://github.com/Galen-Chu/AI-Eval-Rubric/actions/workflows/ci.yml)

> Structured evaluation rubrics and acceptance workflows for software
> development processes and project deliverables.

---

## 🧭 Purpose · 目的

Provides a library of **evaluation rubrics** (structured YAML definitions of
criteria, weights, and scoring rules) that can be applied to:

- Code review & quality assessment
- Project acceptance & completion verification
- API design review
- Documentation quality
- Security checklist compliance

Used by `mod-eval-report` (from [AI-Agent-Skill](https://github.com/Galen-Chu/AI-Agent-Skill))
as the Schema source for automated acceptance checks.

---

## 🏗️ Architecture · 架構

```
Rubric Definition (YAML)
       │
       ▼
Evaluation Runner ──▶ Applied to target (code / docs / API / project)
       │
       ▼
Scored Report (JSON/MD)
```

---

## 📁 Structure · 結構

```
AI-Eval-Rubric/
├── rubrics/                      # Pre-built evaluation rubrics
│   ├── code-quality.yaml        # Code review criteria
│   ├── project-acceptance.yaml  # Project completion verification
│   ├── api-review.yaml          # API design & consistency
│   ├── documentation.yaml       # Docs completeness & clarity
│   └── security-checklist.yaml # Security compliance
├── templates/
│   └── rubric-template.yaml     # Template for custom rubrics
├── runner/
│   └── eval_runner.py           # CLI evaluation runner
├── tests/
│   └── test_eval_runner.py      # Pytest suite for the runner
├── .github/workflows/ci.yml    # CI (pytest on Ubuntu + Windows)
├── requirements.txt             # Runtime deps (PyYAML)
├── requirements-dev.txt         # Dev deps (pytest)
├── README.md
└── LICENSE
```

---

## 🚀 Quick Start · 快速開始

### Apply a rubric

```bash
python runner/eval_runner.py --rubric rubrics/code-quality.yaml --target ./src/
```

The runner prints the evaluation **prompt to stdout** (summary and hints go
to stderr), so it is pipe-friendly on any platform — output is always UTF-8:

```bash
# bash
claude -p "$(python runner/eval_runner.py --rubric rubrics/code-quality.yaml --prompt-only --target ./src/)"
```

```powershell
# PowerShell
claude -p (python runner/eval_runner.py --rubric rubrics/code-quality.yaml --prompt-only --target ./src/)
```

The prompt embeds a snapshot of the target: a single file contributes its
content (capped at 10,000 chars; binaries are omitted), a directory
contributes a recursive file listing (capped at 200 entries, with
`.git`/`node_modules`/`__pycache__`-style directories pruned). Pass
`--no-context` to omit it and supply the material yourself.

### List available rubrics

```bash
python runner/eval_runner.py --list
```

### Create a custom rubric

```bash
cp templates/rubric-template.yaml rubrics/my-rubric.yaml
# Edit criteria, weights, thresholds
python runner/eval_runner.py --rubric rubrics/my-rubric.yaml --target ./my-project/
```

### Run the tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -v
```

---

## 📋 Rubric Schema · 評估規格

Every rubric follows this YAML structure:

```yaml
name: my-rubric
version: 1.0.0
description: What this rubric evaluates
category: code | project | api | docs | security

criteria:
  - id: criterion-id
    title: Human-readable criterion name
    weight: 1.0          # Relative importance (higher = more important)
    threshold: 0.8       # Pass/fail threshold (0.0-1.0)
    checklist:
      - "Check point 1"
      - "Check point 2"

scoring:
  method: weighted_average   # weighted_average | pass_fail | points
  scale: [0, 1]             # Score range
  pass_threshold: 0.75      # Overall pass threshold

report:
  format: markdown + json
  include:
    - per_criterion_scores
    - overall_score
    - pass_fail_summary
    - recommendations
```

### Validation · 驗證規則

`load_rubric` rejects a rubric with one aggregated, readable error listing
every problem:

- Top level must be a mapping containing `name`, `criteria`, `scoring`
- Each criterion requires `id`, `title`, `weight`; ids must be unique
- `weight` must be a positive number; `threshold` and
  `scoring.pass_threshold` must lie in `[0.0, 1.0]`
- `scoring.method` must be one of the values below; `checklist` items
  must be strings

### Scoring methods · 計分方式

| Method | Checklist scoring | Per-criterion score |
|--------|-------------------|---------------------|
| `weighted_average` | 0 / 0.5 / 1 per item (partial credit) | Average of item scores |
| `pass_fail` | 1.0 (pass) or 0.0 (fail), no partial credit | Fraction of items passed |
| `points` | 0..max(scale) points per item, normalized to 0–1 | Normalized average |

The overall score is always the weighted average of criterion scores.

---

## 🔄 Available Rubrics · 現有評估規格

| Rubric | Category | Criteria | Use Case |
|--------|----------|----------|----------|
| `code-quality` | code | 6 | Code review, PR acceptance |
| `project-acceptance` | project | 5 | Project completion verification |
| `api-review` | api | 5 | API design & consistency check |
| `documentation` | docs | 4 | Documentation quality |
| `security-checklist` | security | 5 | Security compliance scan |

---

## 🔗 Integration · 整合

### With AI-Agent-Skill

`mod-eval-report` reads rubrics from this repo:

```python
# In mod-eval-report agent definition
# (use the raw URL — the blob URL returns HTML, not YAML)
rubric_source: https://raw.githubusercontent.com/Galen-Chu/AI-Eval-Rubric/main/rubrics/{name}.yaml
```

### With AI-Pipeline-Hook

Pipeline steps can include evaluation gates:

```yaml
steps:
  - id: evaluate_output
    type: claude-skill
    skill: mod-eval-report
    rubric: code-quality
    description: Apply code-quality rubric to generated output
```

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 👤 Author

**Galen Chu**

- GitHub: [@Galen-Chu](https://github.com/Galen-Chu)
- LinkedIn: [Galen Chu](https://www.linkedin.com/in/galen-chu-203590b5/)
