# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Evaluation prompt no longer crashes with `UnicodeEncodeError` when stdout
  uses a legacy Windows code page (cp950 could not encode `²` in O(n²));
  stdio is now pinned to UTF-8.
- Replaced deprecated `datetime.utcnow()` with timezone-aware
  `datetime.now(timezone.utc)`.

### Added

- Full rubric schema validation: required criterion fields, unique ids,
  positive weights, thresholds within [0, 1], allowed scoring methods, and
  readable YAML parse errors — all problems reported in one message.
- Criterion `id`s now appear in prompt headings so the assessor can echo
  exact ids in the JSON output.
- `scoring.method` drives the instruction block: `weighted_average`
  (partial credit), `pass_fail` (strict 1/0), `points` (scale-normalized).
- `report.include` drives which JSON sections the output must contain;
  `report.format` requesting markdown also asks for a rendered report.
- Target context embedded in the prompt: single file → capped content,
  directory → capped recursive listing with build/VCS dirs pruned;
  `--no-context` opts out.
- Pytest suite (21 tests, including a cp950 subprocess regression test)
  and a GitHub Actions workflow (Ubuntu + Windows × Python 3.10/3.13).
- This changelog, plus README sections for known limitations, design
  decisions, and roadmap.

### Changed

- The CLI now prints the prompt to stdout in every mode (summary and pipe
  hints go to stderr); previously the default mode discarded the prompt.
- `--target` defaults to the current working directory instead of a
  literal "current directory" label.
- PyYAML requirement loosened from `==6.0.1` to `>=6.0.1,<7` — 6.0.1 has
  no wheels for Python 3.13+.

## [1.0.0] - 2026-08-17

### Added

- Initial rubric library: `code-quality`, `project-acceptance`,
  `api-review`, `documentation`, `security-checklist`.
- Custom rubric template and the `eval_runner.py` CLI
  (`--rubric`, `--target`, `--list`, `--prompt-only`).

[Unreleased]: https://github.com/Galen-Chu/AI-Eval-Rubric/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Galen-Chu/AI-Eval-Rubric/releases/tag/v1.0.0
