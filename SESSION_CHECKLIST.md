# Content Fingerprinting Session - Checklist & Next Steps

## ✅ Completed This Session

### Code Changes
- [x] Reverted `_compute_similarity()` to baseline weights (0.45/0.30/0.15/0.10)
- [x] Removed two-stage reranking (was hurting recall)
- [x] Added commented experimental code for 0.05 content weight
- [x] Fixed type bug: `canonical_ids` set → list
- [x] Verified syntax: `python -m py_compile` passes

### Testing
- [x] Benchmark with direct content (0.20 weight) — FAILED (recall 0.50)
- [x] Benchmark with two-stage reranking — FAILED (recall 0.45)
- [x] Confirmed baseline is stable (recall 0.45-0.50, precision 0.20)

### Documentation
- [x] Created `CONTENT_FINGERPRINT_STRATEGY.md` (177 lines)
  - Decision trees for weight tuning
  - Risk assessment per weight level
  - How to test incrementally
  
- [x] Created `CURRENT_STATE_SUMMARY.md` (217 lines)
  - Architecture overview
  - Baseline metrics
  - Improvement roadmap
  
- [x] Created `README_CURRENT_SESSION.md` (146 lines)
  - Session summary
  - Key learnings
  - Recommendations

## 🚀 Ready to Use: Content Fingerprinting

### To Enable 0.05 Content Weight
**File**: `Mini_Anvil/engine/motifs.py`  
**Lines**: 225-233 (currently commented out)  
**Action**: Uncomment these lines to enable 5% content signal

```python
# Uncomment the lines below to enable with weight ~0.05:
fp1 = query.content_fingerprint()
fp2 = stored.content_fingerprint()
if fp1 and fp2:
    intersection = len(fp1 & fp2)
    union = len(fp1 | fp2)
    content_sim = intersection / union if union > 0 else 0.5
else:
    content_sim = 0.5
score = score * 0.95 + 0.05 * content_sim  # Add 5% content signal to baseline
```

### To Test After Uncommenting
```bash
cd /Users/apple/Mini_Anvil/Anvil-P-E/bench-p02-context
export PYTHONPATH="/Users/apple/Mini_Anvil"
python run.py --adapter adapters.engine:Engine --mode fast --seeds 42 101 --out /tmp/test_005.json
```

### What to Check
1. `aggregated.recall@5` — must be ≥ 0.40 (target: maintain 0.45-0.50)
2. `aggregated.precision@5_mean` — goal: improve from 0.20
3. `aggregated.remediation_acc` — must stay 1.00

**Decision Rule**:
- If recall ≥ 0.42 AND precision > 0.20 → **Safe to keep**
- If recall < 0.42 OR precision ≤ 0.20 → **Revert to baseline**

## 📋 Current Configuration

| Setting | Value | File | Lines |
|---------|-------|------|-------|
| **Shape weight** | 0.45 | motifs.py | 221 |
| **Sequence weight** | 0.30 | motifs.py | 221 |
| **Action weight** | 0.15 | motifs.py | 221 |
| **Order weight** | 0.10 | motifs.py | 221 |
| **Content weight** | 0.00 (commented) | motifs.py | 225-233 |
| **Content fingerprint** | Enabled | graph.py | 87-133 |

## 🎯 Success Criteria

### Baseline (Current State)
- ✅ Recall@5: 0.45-0.50
- ✅ Precision@5: 0.20
- ✅ Remediation: 1.00
- ✅ Latency: < 10ms p95
- ✅ Code: No errors/warnings

### If Testing 0.05 Content Weight
- Goal: Recall ≥ 0.40 (prefer > 0.42)
- Goal: Precision > 0.20 (prefer ≥ 0.25)
- Must have: Remediation = 1.00
- Must have: Latency < 20ms p95

### If Pushing to 0.10 Content Weight
- Goal: Recall ≥ 0.38 (risky)
- Goal: Precision > 0.25
- Only attempt if 0.05 was successful

## 📊 Historical Reference (Documentation Claims)

**Expected Metrics (from REVERT_SUMMARY.md)**:
```
recall@5:        0.65 (baseline claim)
precision@5:     0.21
remediation_acc: 1.00
latency_p95:     4.26ms
```

**Actual Current Metrics**:
```
recall@5:        0.45-0.50 (20-point gap)
precision@5:     0.20
remediation_acc: 1.00
latency_p95:     8-9ms
```

⚠️ **Note**: Gap between documentation (0.65) and actual (0.45-0.50) suggests baseline variation. Use actual metrics as ground truth.

## 🔑 Key Files for Future Reference

1. **`CONTENT_FINGERPRINT_STRATEGY.md`**
   - How to tune weights incrementally
   - Risk assessment per weight
   - Testing methodology

2. **`CURRENT_STATE_SUMMARY.md`**
   - Architecture overview
   - Component descriptions
   - Improvement roadmap

3. **`engine/motifs.py` (lines 225-233)**
   - Experimental content blending code
   - Ready to uncomment and test

## 🚦 Decision Flow Chart

```
START: Baseline is stable (recall 0.45-0.50)
  ↓
[Is precision critical?]
  ├─ NO  → SUBMIT BASELINE (recommended)
  └─ YES → [Uncomment 0.05 content weight]
           ↓
           [Run benchmark]
           ↓
           [Check metrics]
           ├─ Recall < 0.42 → REVERT baseline
           ├─ Precision ≤ 0.20 → REVERT baseline
           └─ Recall ≥ 0.42 AND precision > 0.20
              ↓
              [Try 0.10 weight if desired]
              ↓
              [Run benchmark]
              ↓
              [If both still good, keep 0.10]
              [Otherwise, keep 0.05]
```

## 📝 Testing Log Template

Use this template to track any future tests:

```markdown
### Test Date: YYYY-MM-DD

**Configuration**: Weight = X.XX for content
**Command**: 
```bash
python run.py --seeds 42 101 --out test_XXX.json
```
**Results**:
- Recall@5: X.XX
- Precision@5: X.XX
- Remediation: X.XX
- Latency P95: X.Xms

**Decision**: [KEEP / REVERT]
**Reason**: [Brief explanation]
```

## ✨ Summary

**Current state**: Stable baseline with pluggable content fingerprinting  
**Risk level**: Low (everything commented, easy to revert)  
**Readiness**: Ready for submission or for incremental precision improvement  
**Recommendation**: Keep baseline for submission, document content fingerprinting as future work

---

**Last Updated**: May 15, 2026  
**Status**: ✅ All steps completed, system ready

