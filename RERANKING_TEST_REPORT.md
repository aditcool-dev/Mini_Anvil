# Two-Stage Reranking Benchmark Report

## Test Configuration
- **Approach**: Two-stage reranking with safer edge overlap boosting
- **Seeds**: 42, 101
- **Test Parameters**: 30 services, 14 days, fast mode
- **Total Test Incidents**: 20 (10 per seed)

## Key Metrics

| Metric | Value | Target | Baseline | Status |
|--------|-------|--------|----------|--------|
| **recall@5** | 0.45 | ≥ 0.6 | ~0.65 | ❌ Below Target |
| **precision@5_mean** | 0.20 | > 0.2 | 0.20 | ⚠️ Unchanged |
| **remediation_acc** | 1.00 | - | - | ✅ Perfect |
| **weighted_score** | 0.515 | - | - | ⚠️ Low |

## Per-Seed Results

### Seed 42
- recall@5: 0.50
- precision@5_mean: 0.20
- remediation_acc: 1.00
- latency_p95_ms: 8.96

### Seed 101
- recall@5: 0.40
- precision@5_mean: 0.20
- remediation_acc: 1.00
- latency_p95_ms: 9.96

## Analysis

### What Worked
✅ **Remediation Accuracy (100%)**
- The system correctly suggests remediation actions for all detected incidents
- This component is robust across all test cases

### What Didn't Work
❌ **Recall Below Baseline (45% vs 65%)**
- The two-stage reranking approach is underperforming
- Only 50% of incidents in seed 42 are correctly matched
- Only 40% of incidents in seed 101 are correctly matched

❌ **Precision Unchanged (20%)**
- No improvement in precision compared to baseline
- Suggests the reranking is not providing better discrimination between families

### Root Cause Analysis

1. **Limited Discriminative Features**: 
   - Motif analysis shows 39 out of 48 incidents have identical causal shapes
   - Event sequences are too similar (mostly just `['DEPLOY']`)
   - Edge overlap metrics can't distinguish between nearly-identical incidents

2. **Insufficient Training Signal**:
   - Generated test data doesn't have enough structural diversity
   - Most incidents triggered by same service (svc-17, svc-05, etc.)
   - Causal chains are too simple to provide family differentiation

3. **Edge Overlap Boost Too Weak**:
   - 0.15 boost factor per shared edge insufficient to rerank candidates
   - When all candidates have similar edges, boost magnitude doesn't matter
   - Conservative approach trades accuracy for safety

### Why Recall Degraded

The two-stage reranking introduced instability because:
1. Top 20 candidates from first stage often have identical baseline scores
2. Small boost from edge overlap creates random tiebreaking
3. Reranking doesn't preserve baseline orderings effectively

## Conclusion

The two-stage reranking approach, while theoretically "safer", does not improve performance on this dataset because:

1. **The fundamental problem is data limitation**: Test incidents don't have distinguishing structural features
2. **Edge overlap is not a good discriminator**: When causal shapes are identical, boosting by overlap count doesn't help
3. **Baseline ranking is unstable**: When many candidates have near-identical similarity scores, reranking amplifies noise

## Recommendations

To improve recall beyond 0.65:

1. **Enhance data diversity**: Generate incidents with more diverse causal patterns
2. **Add richer features**: Include temporal patterns, cross-service correlations, remediation success rates
3. **Use machine learning ranking**: Replace hand-crafted similarity with learned ranking model
4. **Improve motif encoding**: Add domain-specific patterns (e.g., cascading failures, latency propagation)
5. **Weight by incident confidence**: Prioritize matches from high-confidence past incidents
