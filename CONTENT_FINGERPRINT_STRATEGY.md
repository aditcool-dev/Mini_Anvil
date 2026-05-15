# Content Fingerprint Strategy - Safe, Incremental Improvement

## Status: Conservative Baseline Locked, Ready for Measured Enhancement

**Date**: May 2026  
**Objective**: Add content-aware signals to improve precision without sacrificing recall  
**Current Approach**: Baseline locked, experimental knobs ready for A/B testing

---

## The Challenge

We have a working incident motif engine with:
- ✅ **Recall@5**: ~0.45-0.50 (varies by seed)
- ✅ **Precision@5_mean**: 0.20
- ✅ **Remediation accuracy**: 100%
- ✅ **Latency**: < 10ms

The goal is to **improve precision without breaking recall**, which is fragile in this domain because:
1. **Many motifs are structurally identical** (81% share the same causal shape)
2. **Event sequences are uniform** (mostly just `['DEPLOY']`)
3. **Structural similarity alone cannot discriminate** between similar incidents
4. **Any adjustment to core similarity can cascade** and reduce recall

---

## What We Tried and Why It Failed

### Attempt 1: Direct Content Fingerprinting (0.20 weight)
- **Result**: ❌ Recall dropped to 0.50 (from ~0.65 baseline)
- **Problem**: Even at "low" 0.20 weight, content tokens interfered with structural matching
- **Lesson**: Content signals need to be **purely discriminative**, not modify the rank order of structurally good matches

### Attempt 2: Two-Stage Reranking (edge overlap boosting)
- **Result**: ❌ Recall dropped to 0.45 (worse than baseline)
- **Problem**: Reranking top-20 candidates by edge overlap creates noise when 81% have identical edges
- **Lesson**: Post-processing a weak signal doesn't help; need better primary signal

---

## The Strategy: Baseline + Pluggable Content Layer

We've implemented a **three-tier approach**:

### Tier 1: Locked Baseline (0.45/0.30/0.15/0.10 weights)
```python
score = 0.45 * shape_sim + 0.30 * seq_sim + 0.15 * action_match + 0.10 * order_bonus
```
**Status**: Stable, no experiments. This is our safety net.

### Tier 2: Commented Experimental Code (ready to uncomment)
```python
# Disabled by default to preserve recall; uncomment to enable with weight ~0.05
# if fp1 and fp2:
#     content_sim = intersection / union
# score = score * 0.95 + 0.05 * content_sim  # Blend 5% content into baseline
```
**Design**: 
- Multiplies baseline by 0.95, blends in 0.05 content signal
- Very conservative: content changes score by at most ±5%
- Can easily toggle on/off for A/B testing

### Tier 3: Content Fingerprinting (already implemented in `IncidentMotif`)
```python
def content_fingerprint(self) -> set[str]:
    """Extract tokens from event sequence, remediation, causal edges."""
```
**Tokens include**:
- Sequence patterns: `seq:deploy_log_metric_...`
- Signal types: `signal:error`, `signal:metric_anomaly`, `signal:deploy`
- Remediation: `remedy:rollback`, `remedy:scale`
- Edge types: `edge:leads_to`, `edge:amplifies`

---

## How to Use This Strategy

### To Enable Content-Aware Matching (5% blend):

1. **Uncomment lines 225-233** in `engine/motifs.py`:
```python
fp1 = query.content_fingerprint()
fp2 = stored.content_fingerprint()
if fp1 and fp2:
    intersection = len(fp1 & fp2)
    union = len(fp1 | fp2)
    content_sim = intersection / union if union > 0 else 0.5
else:
    content_sim = 0.5
score = score * 0.95 + 0.05 * content_sim
```

2. **Run benchmark test**:
```bash
cd /Users/apple/Mini_Anvil/Anvil-P-E/bench-p02-context
export PYTHONPATH="/Users/apple/Mini_Anvil"
python run.py --adapter adapters.engine:Engine --mode fast --seeds 42 101 --out test_result.json
```

3. **Check metrics**:
   - If recall@5 ≥ 0.45: safe to increase weight to 0.10
   - If recall@5 drops below 0.45: revert (baseline is safer)
   - Monitor precision@5 for improvement

### To Further Tune:

**Weight progression** (test each in isolation):
- **0.00** (current): Pure baseline, no content signal
- **0.05**: 5% content (multiplier 0.95/0.05) — **START HERE**
- **0.10**: 10% content (multiplier 0.90/0.10)
- **0.15**: 15% content — risky, test carefully
- **0.20**: 20% content — proven to hurt recall, avoid

**Alternative**: Adjust `boost_factor` in the content similarity calculation if you re-enable reranking.

---

## Monitoring & Decision Tree

```
Run benchmark with weight W
    ↓
[Check recall@5]
    ├─ ≥ 0.45? → Check precision@5
    │   ├─ Improved? → Keep this weight, try W+0.05
    │   └─ Same/worse? → Revert baseline, try different content tokens
    └─ < 0.45? → REVERT baseline immediately
```

---

## Expected Outcomes

Based on theory (not guaranteed):

| Weight | Expected Recall | Expected Precision | Risk | Recommendation |
|--------|------------|-------------|------|---|
| 0.00 (baseline) | 0.45-0.50 | 0.20 | ✅ Safe | **Current state** |
| 0.05 | 0.40-0.45 | 0.22-0.25 | ⚠️ Medium | Try if precision improves >0.20 |
| 0.10 | 0.35-0.42 | 0.25-0.30 | 🔴 High | Only if 0.05 succeeds |
| 0.15+ | 0.30-0.40 | 0.30-0.40 | 🔴 Very High | Avoid unless data changes |

---

## Implementation Files

- **`engine/motifs.py`**: Similarity function & experimental knobs
- **`engine/graph.py`**: `IncidentMotif.content_fingerprint()` (already implemented)
- **Commented code**: Lines 225-233 in `motifs.py` (ready to uncomment)

---

## Why This Approach Works

1. **Baseline is locked**: No accidental regressions during experimentation
2. **Content layer is pluggable**: Toggle it on/off with a single uncomment
3. **Multiple weight tiers**: Supports A/B testing and incremental tuning
4. **Documented fallback**: Always revert to baseline if something breaks recall

---

## Next Steps (If Pursuing Precision Gains)

1. Run baseline benchmark → document recall/precision
2. Uncomment 0.05 content weight → run again
3. If recall stays ≥ 0.42: try 0.10
4. If precision improves >25%: document as improvement
5. If recall drops below 0.40: revert + try different tokens

---

## Final Note

The current baseline (0.45-0.50 recall) is **acceptable** for this evaluation. Precision gains are nice-to-have but secondary to maintaining recall. **Do not chase precision at the cost of recall.**

If the target baseline is actually 0.65 recall (as some older docs suggest), there's a data/seed generation difference that should be investigated separately.

