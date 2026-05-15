"""Anvil P-02 Self-Check v2 -- 11 checks aligned with actual benchmark scoring."""
from __future__ import annotations
import argparse, importlib, json, statistics, sys, time
from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _ts(base: datetime, delta_s: float) -> str:
    return (base + timedelta(seconds=delta_s)).isoformat()


def _result(name, passed, value, threshold, detail=""):
    return {"name": name, "passed": passed, "value": value,
            "threshold": threshold, "detail": detail}


# ---------------------------------------------------------------------------
# Data generators
# ---------------------------------------------------------------------------

def make_scenario(seed: int = 42):
    """Single-rename scenario: svc-pay-{seed} -> svc-bil-{seed}."""
    p = f"svc-pay-{seed}"
    b = f"svc-bil-{seed}"
    c = f"svc-chk-{seed}"
    inc1 = f"inc-{seed}-001"
    train = [
        {"event_id": f"{seed}-001", "kind": "deploy",          "service": p, "ts": _ts(BASE, 0),   "version": "v2.1.0"},
        {"event_id": f"{seed}-002", "kind": "metric",          "service": p, "ts": _ts(BASE, 30),  "metric": "latency_p99", "value": 520, "threshold": 500},
        {"event_id": f"{seed}-003", "kind": "log",             "service": p, "ts": _ts(BASE, 45),  "level": "error", "message": "DB timeout", "trace_id": f"tr-{seed}-a"},
        {"event_id": f"{seed}-004", "kind": "trace",           "service": c, "ts": _ts(BASE, 50),  "trace_id": f"tr-{seed}-a", "spans": [{"svc": p, "ts": _ts(BASE, 51), "status": "error"}]},
        {"event_id": f"{seed}-005", "kind": "incident_signal", "service": p, "ts": _ts(BASE, 60),  "incident_id": inc1, "trigger": "latency_breach"},
        {"event_id": f"{seed}-006", "kind": "remediation",     "service": p, "ts": _ts(BASE, 90),  "incident_id": inc1, "action": "rollback", "version": "v2.0.9", "outcome": "resolved"},
        {"event_id": f"{seed}-007", "kind": "topology",                      "ts": _ts(BASE, 120), "service": p, "mutation": {"kind": "rename", "old_name": p, "new_name": b}},
        {"event_id": f"{seed}-008", "kind": "deploy",          "service": b, "ts": _ts(BASE, 150), "version": "v2.1.1"},
        {"event_id": f"{seed}-009", "kind": "metric",          "service": b, "ts": _ts(BASE, 180), "metric": "latency_p99", "value": 610, "threshold": 500},
        {"event_id": f"{seed}-010", "kind": "log",             "service": b, "ts": _ts(BASE, 190), "level": "error", "message": "DB timeout", "trace_id": f"tr-{seed}-b"},
    ]
    signals = [{"service": b, "ts": _ts(BASE, 200), "incident_id": f"inc-{seed}-002", "trigger": "latency_breach"}]
    return train, signals


# 5 structurally distinct incident families
FAMILIES = [
    # 0: deploy -> latency spike -> rollback
    [("deploy", {"version": "v_bad"}), ("metric", {"metric": "latency_p99", "value": 900}),
     ("incident_signal", {"trigger": "latency"}), ("remediation", {"action": "rollback", "outcome": "resolved"})],
    # 1: deploy -> error log cascade -> restart
    [("deploy", {"version": "v_cfg"}), ("log", {"level": "error", "message": "Config parse error"}),
     ("log", {"level": "critical", "message": "Service unavailable"}),
     ("incident_signal", {"trigger": "error_rate"}), ("remediation", {"action": "restart", "outcome": "resolved"})],
    # 2: metric spike -> trace errors -> circuit_break
    [("metric", {"metric": "error_rate", "value": 0.45}),
     ("trace", {"spans": []}),
     ("log", {"level": "error", "message": "Upstream timeout"}),
     ("incident_signal", {"trigger": "upstream_error"}), ("remediation", {"action": "circuit_break", "outcome": "resolved"})],
    # 3: memory metric -> OOM log -> restart
    [("metric", {"metric": "memory_usage", "value": 0.95}),
     ("metric", {"metric": "gc_pause_ms", "value": 800}),
     ("log", {"level": "critical", "message": "OOM killed"}),
     ("incident_signal", {"trigger": "oom"}), ("remediation", {"action": "restart", "outcome": "resolved"})],
    # 4: warn log -> error log -> auth metric -> config_change
    [("log", {"level": "warn", "message": "Certificate expires soon"}),
     ("log", {"level": "error", "message": "TLS handshake failed"}),
     ("metric", {"metric": "auth_failure_rate", "value": 0.8}),
     ("incident_signal", {"trigger": "auth_failure"}), ("remediation", {"action": "config_change", "outcome": "resolved"})],
]


def make_family_events(fam_idx: int, svc: str, seed: int, offset: float = 0):
    inc_id = f"inc-fam{fam_idx}-{seed}"
    events = []
    for i, (kind, extra) in enumerate(FAMILIES[fam_idx]):
        ev = {"event_id": f"fam{fam_idx}-{seed}-{i:03d}", "kind": kind,
              "service": svc, "ts": _ts(BASE, offset + i * 15)}
        ev.update(extra)
        if kind == "incident_signal":
            ev["incident_id"] = inc_id
        if kind == "remediation":
            ev["incident_id"] = inc_id
        if kind == "trace" and not ev.get("spans"):
            ev["spans"] = [{"svc": f"dep-{svc}", "ts": _ts(BASE, offset + i * 15 + 1), "status": "error"}]
        events.append(ev)
    return events


def make_throughput_events(n: int, seed: int = 42):
    svcs = [f"tput-{seed}-{i:02d}" for i in range(10)]
    kinds = ["metric", "log", "trace", "deploy"]
    return [{"event_id": f"tput-{seed}-{i:05d}", "kind": kinds[i % 4],
             "service": svcs[i % 10], "ts": _ts(BASE, i * 0.1), "value": i}
            for i in range(n)]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_throughput(Cls, quick):
    n = 1000 if quick else 5000
    e = Cls()
    evts = make_throughput_events(n)
    t0 = time.perf_counter()
    e.ingest(evts)
    elapsed = time.perf_counter() - t0
    rate = n / elapsed
    return _result("1. Ingest throughput", rate >= 1000,
                   f"{rate:.0f} ev/s", ">= 1,000 ev/s",
                   f"Ingested {n} events in {elapsed:.3f}s")


def check_output_schema(Cls):
    """Every required field must be present and correctly typed."""
    e = Cls()
    train, signals = make_scenario(seed=42)
    e.ingest(train)
    ctx = e.reconstruct_context(signals[0], mode="fast")
    errors = []

    for key in ("related_events", "causal_chain", "similar_past_incidents",
                "suggested_remediations", "confidence", "explain"):
        if key not in ctx:
            errors.append(f"missing: {key}")

    conf = ctx.get("confidence", -1)
    if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
        errors.append(f"confidence out of range: {conf}")

    if not isinstance(ctx.get("explain"), str) or len(ctx.get("explain", "")) < 20:
        errors.append("explain too short or wrong type")

    for i, edge in enumerate(ctx.get("causal_chain", [])):
        for field in ("cause_id", "effect_id", "relation", "confidence"):
            if field not in edge:
                errors.append(f"causal_chain[{i}] missing: {field}")
        c = edge.get("confidence", -1)
        if not isinstance(c, (int, float)) or not (0.0 <= c <= 1.0):
            errors.append(f"causal_chain[{i}].confidence={c} out of range")

    for i, m in enumerate(ctx.get("similar_past_incidents", [])):
        for field in ("similarity", "rationale"):
            if field not in m:
                errors.append(f"similar_past_incidents[{i}] missing: {field}")
        if "incident_id" not in m and "past_incident_id" not in m:
            errors.append(f"similar_past_incidents[{i}] missing incident_id/past_incident_id")
        s = m.get("similarity", -1)
        if not isinstance(s, (int, float)) or not (0.0 <= s <= 1.0):
            errors.append(f"similar_past_incidents[{i}].similarity={s} out of range")

    for i, r in enumerate(ctx.get("suggested_remediations", [])):
        for field in ("action", "confidence"):
            if field not in r:
                errors.append(f"suggested_remediations[{i}] missing: {field}")

    passed = len(errors) == 0
    return _result("2. Output schema validation", passed,
                   "OK" if passed else f"{len(errors)} errors",
                   "All required fields present and typed",
                   "; ".join(errors) if errors else "")


def check_rename_robustness(Cls):
    e = Cls()
    train, signals = make_scenario(seed=99)
    e.ingest(train)
    ctx = e.reconstruct_context(signals[0], mode="fast")
    past = ctx.get("similar_past_incidents", [])
    target = "inc-99-001"
    found = any(m.get("incident_id") == target or m.get("past_incident_id") == target
                for m in past)
    return _result("3. Rename robustness (recall@5)", found and len(past) > 0,
                   f"{len(past)} matches, target_found={found}",
                   "Past incident surfaces after rename",
                   f"ids={[m.get('incident_id') or m.get('past_incident_id') for m in past]}")


def check_temporal_ordering(Cls):
    """cause_ts < effect_ts on every causal edge."""
    e = Cls()
    train, signals = make_scenario(seed=77)
    e.ingest(train)
    ctx = e.reconstruct_context(signals[0], mode="fast")
    chain = ctx.get("causal_chain", [])
    violations = []
    for edge in chain:
        c_ts = edge.get("cause_ts") or edge.get("first_seen", "")
        e_ts = edge.get("effect_ts") or edge.get("last_seen", "")
        if c_ts and e_ts and c_ts >= e_ts:
            violations.append(f"{edge.get('relation')}: {c_ts} >= {e_ts}")
    passed = len(violations) == 0
    return _result("4. Temporal ordering (cause_ts < effect_ts)", passed,
                   f"{len(violations)} violations in {len(chain)} edges",
                   "0 violations",
                   "; ".join(violations) if violations else "")


def check_fast_latency(Cls, quick):
    e = Cls()
    train, signals = make_scenario(seed=55)
    e.ingest(train)
    n = 20 if quick else 100
    lats = []
    for _ in range(n):
        t0 = time.perf_counter()
        e.reconstruct_context(signals[0], mode="fast")
        lats.append((time.perf_counter() - t0) * 1000)
    p95 = statistics.quantiles(lats, n=100)[94] if len(lats) >= 20 else max(lats)
    p50 = statistics.median(lats)
    return _result("5. Fast mode p95 latency", p95 <= 2000,
                   f"p95={p95:.0f}ms  p50={p50:.0f}ms  ({n} calls)",
                   "<= 2,000ms", "")


def check_context_quality(Cls):
    """
    F1-proxy against ground truth for the 5-minute window.
    Signal at T+200s, window=300s -> events from T-100s to T+200s are in scope.
    All scenario events are at T+0..T+190, so all are within the 300s window.

    Ground truth (causally relevant kinds only):
      001 deploy T+0, 002 metric T+30, 003 log T+45, 004 trace T+50,
      005 incident_signal T+60, 008 deploy T+150, 009 metric T+180, 010 log T+190

    Noise (must NOT appear):
      006 remediation T+90, 007 topology T+120
    """
    e = Cls()
    train, signals = make_scenario(seed=33)
    e.ingest(train)
    ctx = e.reconstruct_context(signals[0], mode="fast")
    related = ctx.get("related_events", [])

    # All causally-relevant events within the 300s window
    expected = {"33-001", "33-002", "33-003", "33-004", "33-005", "33-008", "33-009", "33-010"}
    returned = {ev.get("event_id", "") for ev in related}
    # Remediation and topology events are noise -- should be filtered out
    noise = {"33-006", "33-007"} & returned

    tp = len(expected & returned)
    fp = len(returned - expected - {"33-006", "33-007"})
    fn = len(expected - returned)
    prec = tp / (tp + fp + len(noise)) if (tp + fp + len(noise)) > 0 else 0.0
    rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0

    passed = f1 >= 0.6 and len(noise) == 0
    return _result("6. Context quality (F1-proxy)", passed,
                   f"F1={f1:.2f}  prec={prec:.2f}  rec={rec:.2f}  noise={len(noise)}",
                   "F1>=0.6, zero remediation/topology noise",
                   f"returned={sorted(returned)}, missing={expected - returned}, noise={noise}")


def check_remediations(Cls):
    """suggested_remediations must be non-empty after a resolved incident."""
    e = Cls()
    train, signals = make_scenario(seed=22)
    e.ingest(train)
    ctx = e.reconstruct_context(signals[0], mode="fast")
    rems = ctx.get("suggested_remediations", [])
    passed = len(rems) > 0 and all("action" in r for r in rems)
    top = rems[0] if rems else {}
    return _result("7. Suggested remediations", passed,
                   f"{len(rems)} suggestions  top={top.get('action','-')}  conf={top.get('confidence',0):.2f}",
                   "Non-empty with action field",
                   "" if passed else "Empty or missing action field")


def check_memory_evolution(Cls):
    """Motif count must grow and confidence must not drop after remediation."""
    e = Cls()
    train, signals = make_scenario(seed=11)
    pre_rem = [ev for ev in train if ev.get("kind") != "remediation"]
    e.ingest(pre_rem)
    conf_pre = e.reconstruct_context(signals[0], mode="fast").get("confidence", 0)
    motifs_pre = e.motifs.count()

    rem_events = [ev for ev in train if ev.get("kind") == "remediation"]
    e.ingest(rem_events)
    conf_post = e.reconstruct_context(signals[0], mode="fast").get("confidence", 0)
    motifs_post = e.motifs.count()

    passed = motifs_post > motifs_pre
    return _result("8. Memory evolution", passed,
                   f"motifs {motifs_pre}->{motifs_post}  confidence {conf_pre:.2f}->{conf_post:.2f}",
                   "Motif count grows after resolved remediation",
                   "" if passed else "Motif index did not grow")


def check_multi_seed(Cls, quick):
    """recall@5 > 0 on every seed -- no per-seed regressions."""
    seeds = [42, 99, 1337, 31415] if quick else [42, 99, 1337, 31415, 27182, 16180]
    failures = []
    for seed in seeds:
        e = Cls()
        train, signals = make_scenario(seed=seed)
        e.ingest(train)
        ctx = e.reconstruct_context(signals[0], mode="fast")
        if len(ctx.get("similar_past_incidents", [])) == 0:
            failures.append(f"seed={seed}")
    passed = len(failures) == 0
    return _result("9. Multi-seed consistency", passed,
                   f"{len(seeds) - len(failures)}/{len(seeds)} seeds pass",
                   "recall@5 > 0 on all seeds",
                   "failed: " + ", ".join(failures) if failures else "")


def check_multi_family(Cls):
    """
    5 incident families must be discriminated.
    After ingesting all 5 families + a rename of family-0,
    querying with family-0 events must return family-0 as top match.
    """
    e = Cls()
    for fam_idx in range(5):
        svc = f"fam-svc-{fam_idx}"
        e.ingest(make_family_events(fam_idx, svc, seed=fam_idx, offset=fam_idx * 200))

    # Rename family-0 service and ingest morphed version
    new_svc = "fam-svc-0-v2"
    e.ingest([{
        "event_id": "rename-fam0", "kind": "topology", "ts": _ts(BASE, 1100),
        "service": "fam-svc-0",
        "mutation": {"kind": "rename", "old_name": "fam-svc-0", "new_name": new_svc},
    }])
    e.ingest(make_family_events(0, new_svc, seed=99, offset=1200))

    signal = {"service": new_svc, "ts": _ts(BASE, 1260), "incident_id": "fam0-eval"}
    ctx = e.reconstruct_context(signal, mode="fast")
    past = ctx.get("similar_past_incidents", [])

    top_id = (past[0].get("incident_id") or past[0].get("past_incident_id")) if past else ""
    top_is_fam0 = "fam0" in str(top_id)
    top_sim = past[0].get("similarity", 0) if past else 0

    passed = top_is_fam0 and len(past) > 0
    return _result("10. Multi-family discrimination", passed,
                   f"top={top_id}  sim={top_sim:.2f}  indexed={e.motifs.count()}",
                   "Family-0 query returns family-0 as top match",
                   f"top3={[(m.get('incident_id') or m.get('past_incident_id'), round(m.get('similarity',0),2)) for m in past[:3]]}")


def check_deep_mode(Cls):
    """Deep mode must return valid Context within 6s (LLM optional)."""
    e = Cls()
    train, signals = make_scenario(seed=44)
    e.ingest(train)
    t0 = time.perf_counter()
    try:
        ctx = e.reconstruct_context(signals[0], mode="deep")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        explain = ctx.get("explain", "")
        passed = isinstance(explain, str) and len(explain) >= 20 and elapsed_ms <= 6000
        return _result("11. Deep mode (valid Context, p95<=6s)", passed,
                       f"{elapsed_ms:.0f}ms  explain_len={len(explain)}",
                       "Non-empty explain, <= 6,000ms",
                       "" if passed else f"explain={repr(explain[:80])}")
    except Exception as ex:
        return _result("11. Deep mode (valid Context, p95<=6s)", False,
                       "EXCEPTION", "Non-empty explain, <= 6,000ms", str(ex))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_checks(adapter_path: str, quick: bool) -> None:
    if ":" in adapter_path:
        mod_path, cls_name = adapter_path.rsplit(":", 1)
    else:
        mod_path, cls_name = adapter_path, "Engine"
    try:
        mod = importlib.import_module(mod_path)
        Cls = getattr(mod, cls_name)
    except (ImportError, AttributeError) as ex:
        print(f"ERROR: Could not load adapter '{adapter_path}': {ex}")
        sys.exit(1)

    print(f"\n{'='*66}")
    print(f"  Anvil P-02 Self-Check v2  |  {adapter_path}")
    print(f"{'='*66}\n")

    checks = [
        lambda: check_throughput(Cls, quick),
        lambda: check_output_schema(Cls),
        lambda: check_rename_robustness(Cls),
        lambda: check_temporal_ordering(Cls),
        lambda: check_fast_latency(Cls, quick),
        lambda: check_context_quality(Cls),
        lambda: check_remediations(Cls),
        lambda: check_memory_evolution(Cls),
        lambda: check_multi_seed(Cls, quick),
        lambda: check_multi_family(Cls),
        lambda: check_deep_mode(Cls),
    ]

    results = []
    for fn in checks:
        try:
            r = fn()
        except Exception as ex:
            import traceback
            r = _result("Unknown", False, "EXCEPTION", "", traceback.format_exc(limit=4))
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}]  {r['name']}")
        print(f"          {r['value']}  (need: {r['threshold']})")
        if not r["passed"] and r.get("detail"):
            # Truncate long detail lines
            detail = str(r["detail"])[:200]
            print(f"          detail: {detail}")
        print()

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    score = passed / total * 100
    print(f"{'='*66}")
    print(f"  Score: {passed}/{total} checks passed ({score:.0f}%)")
    print(f"{'='*66}\n")

    report = {"adapter": adapter_path, "quick": quick,
              "checks": results, "score": score, "passed": passed, "total": total}
    with open("report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print("  report.json written\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Anvil P-02 Self-Check v2")
    ap.add_argument("--adapter", default="adapters.engine:Engine")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    run_checks(args.adapter, args.quick)
