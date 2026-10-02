"""Report — turn a batch of CaseRecords into something a person reads.

Two outputs from the same records:
- `print_report`: a summary table (case × grader) followed by one block per
  judge score quoting the judge's reasoning and failures, so a number is
  never shown without the why behind it.
- `write_report`: one YAML file per case under a fresh
  `runs/run-<YYYY-MM-DD_HH-MM-SS>/` folder, with every multi-line string
  (judge reasoning, final answers) as a `|` block so each file reads like
  the printed report. Transcripts are not included; they live in Langfuse
  when tracing is on.

Stdlib plus PyYAML. Pass/fail graders print as ✓ / ✗; fractional scores print as
a decimal so a judge's 0.6 is not rounded into a tick.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from harness_evals.grader import Score
from harness_evals.runner import CaseRecord

RUN_FOLDER_FORMAT = 'run-%Y-%m-%d_%H-%M-%S'


#     ================================
# --> Helper funcs
#     ================================


def _mark(score: Score) -> str:
    """✓ / ✗ for pass-fail scores, two decimals for anything in between."""
    if score.value == 1.0:
        return '✓'

    if score.value == 0.0:
        return '✗'

    return f'{score.value:.2f}'


def _table(records: Sequence[CaseRecord]) -> list[str]:
    """Case × grader grid with aligned columns."""
    names = [s.name for s in records[0].scores]
    width = max(len(r.case.id) for r in records)
    cols = [max(len(n), 4) for n in names]

    header = 'case'.ljust(width) + '  ' + '  '.join(n.ljust(w) for n, w in zip(names, cols))
    lines = [header, '-' * len(header)]

    for r in records:
        marks = '  '.join(_mark(s).ljust(w) for s, w in zip(r.scores, cols))

        lines.append(r.case.id.ljust(width) + '  ' + marks)

    return lines


def _reasoning_blocks(records: Sequence[CaseRecord]) -> list[str]:
    """One block per judge score: the case, the score, and the judge's own words."""
    lines: list[str] = []

    for r in records:
        for s in r.scores:
            if not s.reasoning:
                continue

            lines.append(f'── {r.case.id} · {s.name} {s.value:.2f} ' + '─' * 40)
            lines.append(s.reasoning.strip())
            lines.append('')

    return lines


def _record_dict(r: CaseRecord) -> dict:
    """Plain-data view of one record, scores first so a reader sees the verdict before the detail."""
    meta = r.run.meta

    return {
        'id': r.case.id,
        'scores': [_score_dict(s) for s in r.scores],
        'task': r.case.task,
        'criteria': list(r.case.criteria),
        'final': r.run.final,
        'stop_reason': meta.stop_reason,
        'iterations': meta.iterations,
        'usage': asdict(meta.usage),
        'tool_calls': [name for name, _ in meta.tool_outcomes],
        'errors': list(meta.errors),
    }


def _score_dict(s: Score) -> dict:
    """Drop empty fields so pass/fail rows stay to one line."""
    return {k: v for k, v in asdict(s).items() if v != ''}


class _BlockDumper(yaml.SafeDumper):
    """SafeDumper that writes multi-line strings as `|` blocks."""


def _str_presenter(dumper: yaml.SafeDumper, text: str) -> yaml.ScalarNode:
    style = '|' if '\n' in text else None

    return dumper.represent_scalar('tag:yaml.org,2002:str', text, style=style)


_BlockDumper.add_representer(str, _str_presenter)


#     ================================
# --> Public
#     ================================


def print_report(records: Sequence[CaseRecord]) -> None:
    """Print the summary table, then the reasoning behind every judge score."""
    if not records:
        print('no records')

        return

    print('\n'.join(_table(records)))
    print()
    print('\n'.join(_reasoning_blocks(records)))


def write_report(records: Sequence[CaseRecord], runs_dir: Path = Path('runs')) -> Path:
    """Write one `<case id>.yaml` per record into a new `runs_dir/run-<datetime>/` folder; return that folder."""
    run_dir = runs_dir / datetime.now().strftime(RUN_FOLDER_FORMAT)
    run_dir.mkdir(parents=True, exist_ok=True)

    for r in records:
        with (run_dir / f'{r.case.id}.yaml').open('w', encoding='utf-8') as f:
            yaml.dump(_record_dict(r), f, Dumper=_BlockDumper, sort_keys=False, allow_unicode=True, width=100)

    return run_dir
