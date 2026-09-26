"""Learn how to weigh the pipeline's signals from the player's own decks.

Reads a ``jev_eval.py`` trace (its ``features`` rows: every legal candidate
with Jev's verdict, the brain map layers, the EDHREC numbers, and whether the
player actually ran the card) and fits a logistic regression that predicts
"the player ran this". Evaluation is leave-one-deck-out: the weights used on a
deck never saw that deck's cards, so a gain here is a gain on a deck the
weights were not fitted to.

    python tools/fit_blend.py tools/traces/jev_eval_<stamp>.json
    python tools/fit_blend.py <trace> [<trace> ...] --save   # write app/pipeline/jev_blend.json

``--save`` fits on every deck and writes the weights ranking uses. Say why in
the commit, with the leave-one-deck-out numbers it printed.

Pure Python on purpose: a dozen features and a few thousand rows do not need
numpy, and the app does not otherwise depend on it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.pipeline.jev import BLEND_OPTIONAL, BLEND_PATH, blend_vector  # noqa: E402

TOP = 10
OPTIONAL = BLEND_OPTIONAL
ALWAYS = ("jev", "asked", "pool_position", "combo")
# Fewer, less-overlapping signals. The full set fits the brain map's layers
# and its total side by side, and with ~100 positives correlated inputs trade
# weight against each other and flip sign (mechanical came out negative).
COMPACT = ("jev", "asked", "bm_total", "edhrec_rate", "edhrec_synergy")


def features(row: dict, names: tuple[str, ...]) -> list[float]:
    return blend_vector(row, names)


def fit(xs: list[list[float]], ys: list[int], *, l2: float = 0.01,
        steps: int = 400, rate: float = 0.5) -> tuple[list[float], list[float], list[float]]:
    """Logistic regression by batch gradient descent on standardized inputs.
    Positives are weighted up to the negatives' total so a 1-in-20 label does
    not train a model that predicts "no" for everything."""
    n, d = len(xs), len(xs[0])
    mean = [sum(x[j] for x in xs) / n for j in range(d)]
    std = [math.sqrt(sum((x[j] - mean[j]) ** 2 for x in xs) / n) or 1.0 for j in range(d)]
    zs = [[(x[j] - mean[j]) / std[j] for j in range(d)] for x in xs]
    positives = sum(ys) or 1
    pos_weight = (n - positives) / positives
    w = [0.0] * (d + 1)
    for _ in range(steps):
        grad = [0.0] * (d + 1)
        for z, y in zip(zs, ys):
            s = w[0] + sum(wj * zj for wj, zj in zip(w[1:], z))
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, s))))
            err = (p - y) * (pos_weight if y else 1.0)
            grad[0] += err
            for j in range(d):
                grad[j + 1] += err * z[j]
        total = n + (pos_weight - 1) * positives
        for j in range(d + 1):
            reg = l2 * w[j] if j else 0.0
            w[j] -= rate * (grad[j] / total + reg)
    return w, mean, std


def score(w, mean, std, x) -> float:
    return w[0] + sum(wj * (xj - m) / s for wj, xj, m, s in zip(w[1:], x, mean, std))


def hits(ranked_labels: list[int]) -> int:
    return sum(ranked_labels[:TOP])


def evaluate(records: list[dict], names: tuple[str, ...]) -> dict:
    decks = sorted({r["deck_id"] for r in records})
    per_kind = {"role": [0, 0], "synergy": [0, 0]}
    single = {}
    for deck in decks:
        train = [r for r in records if r["deck_id"] != deck]
        xs = [features(row, names) for r in train for row in r["features"]]
        ys = [row["label"] for r in train for row in r["features"]]
        w, mean, std = fit(xs, ys)
        for r in (r for r in records if r["deck_id"] == deck):
            kind = "synergy" if r["role"] == "synergy" else "role"
            rows = r["features"]
            ranked = sorted(rows, key=lambda row: score(w, mean, std, features(row, names)), reverse=True)
            per_kind[kind][0] += hits([row["label"] for row in ranked])
            per_kind[kind][1] += len(r["held_out"])
    return {k: v[0] / v[1] if v[1] else None for k, v in per_kind.items()}


def single_signal(records: list[dict], key: str, reverse: bool = True) -> dict:
    per_kind = {"role": [0, 0], "synergy": [0, 0]}
    for r in records:
        kind = "synergy" if r["role"] == "synergy" else "role"
        rows = r["features"]
        ranked = sorted(rows, key=lambda row: (row.get(key) is None, -(row.get(key) or 0.0) if reverse else (row.get(key) or 0.0)))
        per_kind[kind][0] += hits([row["label"] for row in ranked])
        per_kind[kind][1] += len(r["held_out"])
    return {k: v[0] / v[1] if v[1] else None for k, v in per_kind.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trace", type=Path, nargs="+")
    parser.add_argument("--save", action="store_true", help=f"write {BLEND_PATH.name}")
    args = parser.parse_args(argv)

    records = []
    for path in args.trace:
        data = json.loads(path.read_text(encoding="utf-8"))
        records += [r for r in data["records"] if r.get("features")]
    print(f"{len(records)} cases, {sum(len(r['features']) for r in records)} candidates, "
          f"{sum(row['label'] for r in records for row in r['features'])} positives")

    def show(label: str, result: dict) -> None:
        print(f"   {label:<34} role {result['role']:.0%}   theme {result['synergy']:.0%}")

    print("single signals (end to end: held-out cards in the top 10 / all held out)")
    for key in ("jev", "bm_total", "bm_personal", "bm_mechanical", "edhrec_rate", "edhrec_synergy"):
        show(key, single_signal(records, key))
    show("pool order", single_signal(records, "pool_position", reverse=False))

    print("learned blends, leave one deck out")
    variants = {
        "jev + asked": ("jev", "asked"),
        "brain map + edhrec (no jev)": ("bm_total", "bm_consensus", "bm_mechanical", "bm_personal",
                                         "edhrec_rate", "edhrec_synergy", "pool_position", "combo"),
        "everything": ALWAYS + OPTIONAL,
        "compact": COMPACT,
    }
    for label, names in variants.items():
        show(label, evaluate(records, names))

    if args.save:
        names = ALWAYS + OPTIONAL
        xs = [features(row, names) for r in records for row in r["features"]]
        ys = [row["label"] for r in records for row in r["features"]]
        w, mean, std = fit(xs, ys)
        model = {
            "features": list(names), "bias": w[0], "weights": w[1:], "mean": mean, "std": std,
            "fitted_on": {"traces": [p.name for p in args.trace], "cases": len(records),
                          "candidates": len(xs), "positives": sum(ys)},
            "leave_one_deck_out": evaluate(records, names),
        }
        BLEND_PATH.write_text(json.dumps(model, indent=2), encoding="utf-8")
        print(f"saved {BLEND_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
