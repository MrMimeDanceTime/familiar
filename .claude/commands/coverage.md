---
description: Measure how much of a candidate pool the brain map can score, against the saved baseline
---

Run the brain-map coverage report and interpret the result.

```
cd backend && .venv\Scripts\python.exe tools/coverage_report.py
```

Then report:

- The mechanical-layer percentage, and how it moved against the baseline.
- **Any deck whose mechanical coverage dropped**, by name. This is the finding
  that matters: a change helping one deck and hurting another nets out to
  roughly zero in the total, so the per-deck deltas are the signal and the
  total is not.
- Decks scoring unusually low, with a read on whether that is a real gap or an
  honest floor. Some commanders have genuinely thin mechanical identity —
  Torbran carries exactly one usable tag — and the consensus layer carries
  those. A low number is not automatically a bug.

If coverage dropped and the change was deliberate, explain why before running
with `--save`. Never `--save` to clear a drop that has not been accounted for:
the baseline is the evidence.

Arguments: $ARGUMENTS (e.g. `--deck 19` to measure a single deck)
