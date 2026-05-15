"""
Test script to evaluate precision@5 improvements with different threshold settings.
"""

import sys

from adapters.engine import Engine
from self_check import make_family_events, make_scenario


def test_precision_with_thresholds():
    """Test precision@5 with different threshold values."""

    thresholds = [0.5, 0.6, 0.7, 0.8]
    results = {}

    for threshold in thresholds:
        print(f"\n{'=' * 60}")
        print(f"Testing with min_threshold={threshold}")
        print(f"{'=' * 60}")

        # Create engine
        engine = Engine()

        # Ingest training data with all 5 families
        train_events = []
        for fam_idx in range(5):
            svc = f"svc-fam{fam_idx}"
            events = make_family_events(fam_idx, svc, seed=0, offset=fam_idx * 100)
            train_events.extend(events)

        engine.ingest(train_events)

        # Test query from family 0
        query_events = make_family_events(0, "svc-fam0-renamed", seed=1, offset=1000)
        engine.ingest(
            query_events[:-2]
        )  # Don't include incident_signal and remediation

        # Reconstruct context
        signal = {
            "service": "svc-fam0-renamed",
            "ts": query_events[-2]["ts"],
            "incident_id": query_events[-2]["incident_id"],
            "trigger": query_events[-2]["trigger"],
        }

        # Override threshold in the motifs module temporarily
        import engine.motifs as motifs_module

        original_find = engine.motifs.find_similar

        def wrapped_find_similar(query_motif, top_k=5, **kwargs):
            kwargs["min_threshold"] = threshold
            return original_find(query_motif, top_k=top_k, **kwargs)

        engine.motifs.find_similar = wrapped_find_similar

        ctx = engine.reconstruct_context(signal, mode="fast")

        # Analyze results
        similar = ctx.get("similar_past_incidents", [])
        print(f"\nFound {len(similar)} similar incidents:")

        family_0_found = False
        correct_count = 0
        for i, inc in enumerate(similar):
            inc_id = inc.get("incident_id", inc.get("past_incident_id", ""))
            similarity = inc.get("similarity", 0.0)
            rationale = inc.get("rationale", "")

            # Check if it's from family 0
            is_family_0 = "fam0" in inc_id.lower() or "fam0" in rationale.lower()
            if is_family_0:
                family_0_found = True
                correct_count += 1
                marker = "✓ CORRECT"
            else:
                marker = "✗ WRONG FAMILY"

            print(f"  {i + 1}. {inc_id} - sim={similarity:.3f} {marker}")
            print(f"     {rationale[:100]}...")

        # Calculate metrics
        precision = correct_count / len(similar) if similar else 0.0
        recall = 1.0 if family_0_found else 0.0

        print(f"\nMetrics for threshold={threshold}:")
        print(
            f"  Precision@5: {precision:.1%} ({correct_count}/{len(similar)} correct)"
        )
        print(f"  Recall@5: {recall:.1%} (family-0 in top-5: {family_0_found})")

        results[threshold] = {
            "precision": precision,
            "recall": recall,
            "count": len(similar),
        }

        engine.close()

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"{'Threshold':<12} {'Precision@5':<15} {'Recall@5':<12} {'Count'}")
    print(f"{'-' * 60}")
    for threshold in thresholds:
        r = results[threshold]
        print(
            f"{threshold:<12} {r['precision']:<15.1%} {r['recall']:<12.1%} {r['count']}"
        )


if __name__ == "__main__":
    test_precision_with_thresholds()
