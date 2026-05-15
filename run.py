"""
Anvil P-02 — Main benchmark CLI entry point.

Usage (from repo root):
    python run.py --adapter adapters.engine:Engine --mode fast \\
        --seeds 42 101 202 303 404 --out report.json

    python run.py --adapter adapters.engine:Engine --mode fast \\
        --seeds 42 101 --n-services 30 --days 14 --out report_stress.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import os

# Make bench harness modules importable (generator, harness, metrics, schema)
sys.path.append(os.path.join(os.path.dirname(__file__), "bench-p02-context"))

from generator import GenConfig
from harness import run


def adapter_factory_from_spec(spec: str):
    module_name, class_name = spec.split(":")
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return lambda: cls()


def main() -> None:
    parser = argparse.ArgumentParser(description="Anvil P-02 benchmark runner")
    parser.add_argument("--adapter", required=True,
                        help="Dotted spec: module:ClassName  e.g. adapters.engine:Engine")
    parser.add_argument("--mode", choices=["fast", "deep"], default="fast")
    parser.add_argument("--seeds", type=int, nargs="+", default=[42])
    parser.add_argument("--n-services", type=int, default=12)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--out", default="report.json")
    args = parser.parse_args()

    factory = adapter_factory_from_spec(args.adapter)

    cfg = GenConfig(
        n_services=args.n_services,
        days=args.days,
    )

    report = run(factory, cfg, mode=args.mode, seeds=args.seeds, warmup=args.warmup)

    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport written to {args.out}")
    agg = report.get("aggregated", {})
    print(f"  recall@5:         {agg.get('recall@5', 'n/a')}")
    print(f"  precision@5_mean: {agg.get('precision@5_mean', 'n/a')}")
    print(f"  remediation_acc:  {agg.get('remediation_acc', 'n/a')}")
    print(f"  latency_p95_ms:   {agg.get('latency_p95_ms', 'n/a')}")
    score = report.get("score", {})
    print(f"  weighted_score:   {score.get('weighted_score', 'n/a')} / {score.get('max_automated', 'n/a')}")


if __name__ == "__main__":
    main()
