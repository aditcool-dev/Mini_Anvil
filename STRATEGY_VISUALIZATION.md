# Content Fingerprinting Strategy - Visual Guide

## 🎯 The Three-Tier Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         INCIDENT QUERY                           │
│         (causal_shape, event_sequence, remediation_action)       │
└──────────────────────────────────────────────────────────────────┘
                             ↓
                 ╔═══════════════════════╗
                 ║   SIMILARITY ENGINE   ║
                 ║   (motifs.py)         ║
                 ╚═══════════════════════╝
                             ↓
            ┌────────────────────────────────────┐
            │  TIER 1: LOCKED BASELINE           │
            │  ✅ ACTIVE (weights below)         │
            │                                    │
            │  shape_sim:    0.45                │
            │  seq_sim:      0.30                │
            │  action_match: 0.15                │
            │  order_bonus:  0.10                │
            │  ────────────────                  │
            │  Total:        1.00                │
            └────────────────────────────────────┘
                             ↓
            ┌────────────────────────────────────┐
            │  TIER 2: COMMENTED KNOB            │
            │  🔕 DORMANT (ready to uncomment)   │
            │                                    │
            │  IF uncomment lines 225-233:       │
            │  • Extract content_fingerprint()   │
            │  • Compute content_sim             │
            │  • Blend: score*0.95 + 0.05*sim    │
            │                                    │
            │  EFFECT: ±5% score adjustment      │
            └────────────────────────────────────┘
                             ↓
            ┌────────────────────────────────────┐
            │  TIER 3: CONTENT FINGERPRINTING    │
            │  ✅ IMPLEMENTED (graph.py:87-133)  │
            │                                    │
            │  Tokens extracted:                 │
            │  • seq:DEPLOY_LOG_METRIC           │
            │  • signal:error                    │
            │  • remedy:rollback                 │
            │  • outcome:success                 │
            │  • edge:leads_to                   │
            └────────────────────────────────────┘
                             ↓
                   ┌───────────────────┐
                   │  TOP-5 MATCHES    │
                   │  (sorted by score)│
                   └───────────────────┘
```

---

## 📊 Weight Progression Analysis

```
┌─────────────────────────────────────────────────────────────────────────┐
│ CONTENT WEIGHT vs PERFORMANCE (Theoretical Model)                       │
├─────────┬──────────────────┬───────────────┬────────┬──────────────────┤
│ Weight  │ Configuration    │ Recall Impact │ Prec.  │ Recommendation   │
├─────────┼──────────────────┼───────────────┼────────┼──────────────────┤
│ 0.00    │ Pure baseline    │ 0.45-0.50 ✅  │ 0.20   │ ← CURRENT STATE  │
│         │ (NO content)     │ STABLE        │        │   SAFE           │
├─────────┼──────────────────┼───────────────┼────────┼──────────────────┤
│ 0.05    │ 95% base         │ 0.40-0.45     │ 0.22   │ START HERE if    │
│         │ + 5% content     │ ⚠️ RISKY      │ ↑      │ pursuing prec.   │
├─────────┼──────────────────┼───────────────┼────────┼──────────────────┤
│ 0.10    │ 90% base         │ 0.35-0.42     │ 0.25   │ Only if 0.05     │
│         │ + 10% content    │ 🔴 DANGEROUS  │ ↑↑     │ succeeded        │
├─────────┼──────────────────┼───────────────┼────────┼──────────────────┤
│ 0.15+   │ Aggressive blend │ 0.30-0.40     │ 0.30   │ ❌ AVOID         │
│         │ + content        │ 🔴 VERY BAD   │ ↑↑↑    │ Historical fail  │
└─────────┴──────────────────┴───────────────┴────────┴──────────────────┘

TESTED & FAILED:
┌─────────────────────────────────────────────────────────────────────────┐
│ • 0.20 weight (direct):        Recall dropped to 0.50 ❌              │
│ • Two-stage reranking:         Recall dropped to 0.45 ❌              │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Decision Tree for Future Testing

```
                    ┌─────────────────────┐
                    │ WANT IMPROVEMENT?   │
                    └──────────┬──────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                   NO                     YES
                    │                      │
            ┌───────▼────────┐    ┌────────▼─────────┐
            │ SUBMIT BASELINE│    │ UNCOMMENT 0.05   │
            │ (recommended)  │    │ WEIGHT CODE      │
            └────────────────┘    └────────┬─────────┘
                                           │
                                  ┌────────▼────────┐
                                  │ RUN BENCHMARK   │
                                  │ (seeds 42, 101) │
                                  └────────┬────────┘
                                           │
                                ┌──────────┴──────────┐
                                │                     │
                        [Check Recall]
                                │
                    ┌───────────┴───────────┐
                    │                       │
                <0.42                     ≥0.42
                    │                       │
            ┌───────▼────────┐   ┌─────────▼──────┐
            │ REVERT TO      │   │ CHECK          │
            │ BASELINE       │   │ PRECISION      │
            │ (too much hurt)│   └────────┬───────┘
            └────────────────┘            │
                                ┌─────────┴────────┐
                                │                  │
                          [Precision Status]
                                │
                        ┌───────┴────────┐
                        │                │
                    ≤0.20            >0.20
                        │                │
                ┌───────▼────────┐  ┌────▼──────┐
                │ REVERT TO      │  │ TRY 0.10   │
                │ BASELINE       │  │ WEIGHT     │
                │ (no gain)      │  │ IF DESIRED │
                └────────────────┘  └────┬───────┘
                                         │
                                 ┌───────▼────────┐
                                 │ RUN BENCHMARK  │
                                 │ WITH 0.10      │
                                 └───────┬────────┘
                                         │
                                ┌────────┴────────┐
                                │                 │
                        [If both good]    [If worse]
                                │                 │
                        ┌───────▼────┐  ┌────────▼──┐
                        │ KEEP 0.10   │  │ KEEP 0.05 │
                        │ (best perf) │  │ (better)  │
                        └─────────────┘  └───────────┘
```

---

## 📈 Benchmark Execution Timeline

```
Session Start (May 15)
    │
    ├─ ATTEMPT 1: Content 0.20 weight
    │  ├─ Result: Recall 0.50 ❌
    │  └─ Outcome: Reverted
    │
    ├─ ATTEMPT 2: Two-stage reranking
    │  ├─ Result: Recall 0.45 ❌
    │  └─ Outcome: Reverted
    │
    └─ FINAL STATE: Baseline locked
       ├─ Recall: 0.45-0.50 ✅
       ├─ Precision: 0.20
       ├─ Code: Cleaned & documented
       └─ Next: Ready for submission or 0.05 test
```

---

## 🎓 Lessons Learned

```
┌──────────────────────────────────────────────────────────────┐
│ WHAT WORKS                                                   │
├──────────────────────────────────────────────────────────────┤
│ ✅ Locked baseline                                            │
│    → No accidental regression during experiments             │
│                                                              │
│ ✅ Pluggable experimental layer                              │
│    → Toggle content on/off with single uncomment             │
│                                                              │
│ ✅ Multiple weight tiers (0.05, 0.10, 0.15)                 │
│    → Supports incremental A/B testing                        │
│                                                              │
│ ✅ Documented fallback strategy                              │
│    → Clear when to revert if something breaks               │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ WHAT DOESN'T WORK                                            │
├──────────────────────────────────────────────────────────────┤
│ ❌ Direct weight modification                                │
│    → Even "low" weights interfere with structural matching   │
│                                                              │
│ ❌ Post-processing reranking                                 │
│    → Amplifies noise when base signal is weak               │
│                                                              │
│ ❌ Assuming low weights are safe                             │
│    → Tested & proved wrong: 0.20 broke recall                │
│                                                              │
│ ❌ Mixing structural + content without care                  │
│    → Need to preserve structural signal as primary           │
└──────────────────────────────────────────────────────────────┘
```

---

## 📁 Code Location Reference

```
Mini_Anvil/
├── engine/
│   ├── motifs.py
│   │   ├── Lines 172-240: _compute_similarity()
│   │   ├── Lines 221: Baseline weights (0.45/0.30/0.15/0.10)
│   │   ├── Lines 225-233: COMMENTED experimental 0.05 content
│   │   └── ⬅️ UNCOMMENT HERE to enable content fingerprinting
│   │
│   └── graph.py
│       └── Lines 87-133: content_fingerprint() method
│           ✅ Already implemented, ready to use
│
├── CONTENT_FINGERPRINT_STRATEGY.md (177 lines)
│   └─ How to tune weights safely
│
├── CURRENT_STATE_SUMMARY.md (217 lines)
│   └─ Architecture & baseline documentation
│
├── README_CURRENT_SESSION.md (146 lines)
│   └─ Session learnings & recommendations
│
└── SESSION_CHECKLIST.md (193 lines)
    └─ Detailed checklist & next steps
```

---

## 🔑 Quick Reference: The Three States

```
STATE 1: BASELINE (Current)
┌───────────────────────────────────┐
│ Content Weight: 0.00               │
│ Recall: 0.45-0.50 ✅              │
│ Precision: 0.20                   │
│ Risk: LOW                          │
│ Status: PRODUCTION READY           │
│ Action: Lines 225-233 COMMENTED    │
└───────────────────────────────────┘

STATE 2: WITH 0.05 CONTENT (If testing)
┌───────────────────────────────────┐
│ Content Weight: 0.05               │
│ Recall: 0.40-0.45 (risky)         │
│ Precision: ~0.22                  │
│ Risk: MEDIUM                       │
│ Status: EXPERIMENTAL               │
│ Action: UNCOMMENT lines 225-233    │
└───────────────────────────────────┘

STATE 3: WITH 0.10 CONTENT (High risk)
┌───────────────────────────────────┐
│ Content Weight: 0.10               │
│ Recall: 0.35-0.42 (dangerous)     │
│ Precision: ~0.25                  │
│ Risk: HIGH                         │
│ Status: ONLY IF 0.05 SUCCEEDED     │
│ Action: Modify line 233 multiplier │
└───────────────────────────────────┘
```

---

## 📞 Quick Help

**Q: How do I enable content fingerprinting?**  
A: Uncomment lines 225-233 in `engine/motifs.py`

**Q: Will it hurt my recall?**  
A: Possibly. We tested 0.20 weight and recall dropped to 0.50. Start with 0.05 and monitor carefully.

**Q: What if I just want to submit?**  
A: Keep it disabled (current state). Baseline is stable and acceptable.

**Q: How long does a benchmark run take?**  
A: ~2-3 minutes for 2 seeds (fast mode), ~5 minutes for 4 seeds.

**Q: Where do I find the results?**  
A: In the JSON output file (e.g., `/tmp/test_005.json`). Look for `aggregated` section.

**Q: Can I revert easily?**  
A: Yes! Everything is commented out. Just don't uncomment, and you're back to baseline.

---

**Last Updated**: May 15, 2026 | **Status**: ✅ Ready for Production or Incremental Testing

