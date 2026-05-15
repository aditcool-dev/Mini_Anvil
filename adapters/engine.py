"""
Anvil P-02 — Persistent Context Engine Adapter

This is the adapter.py the harness calls. Every method signature is binding.

Architecture:
  Layer 1: IdentityResolver  — canonical IDs across renames
  Layer 2: EventStore        — DuckDB temporal event log
  Layer 3: OperationalGraph  — NetworkX causal graph
  Layer 4: ContextAssembler  — fast/deep context reconstruction
"""

from __future__ import annotations

import threading
import uuid
from typing import Iterable, Literal

from engine.assembler import ContextAssembler
from engine.graph import OperationalGraph
from engine.identity import IdentityResolver
from engine.motifs import BehavioralMotifIndex
from engine.store import EventStore


class Engine:
    """
    Main adapter class. Implements the Anvil P-02 Adapter interface.

    Thread safety:
    - ingest() acquires a write lock (handles rename + event atomically)
    - reconstruct_context() is read-only and concurrent-safe
    """

    def __init__(self) -> None:
        self.resolver = IdentityResolver()
        self.store = EventStore()           # DuckDB in-process
        self.graph = OperationalGraph()     # NetworkX DiGraph
        self.motifs = BehavioralMotifIndex()
        self.assembler = ContextAssembler()
        self._lock = threading.Lock()
        self._open_incidents: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(self, events: Iterable[dict]) -> None:
        """
        Process a stream of events. Thread-safe.
        Topology events (rename/dep shift) are processed first within the lock
        to ensure canonical_id consistency for all subsequent events.
        Uses batch inserts for throughput ≥ 1,000 ev/s.
        """
        event_list = list(events)

        # Separate topology events — process them first
        topology_events = [e for e in event_list if e.get("kind") == "topology"]
        other_events = [e for e in event_list if e.get("kind") != "topology"]

        with self._lock:
            # Process topology mutations first (rename/dep changes)
            for event in topology_events:
                self._on_topology(event)

            # Resolve all canonical_ids and prepare batch insert rows
            batch_rows: list[tuple] = []
            for event in other_events:
                service = event.get("service", event.get("svc", ""))
                if not service:
                    continue
                cid = self.resolver.resolve(service)
                event_id = event.get("event_id") or event.get("id") or str(uuid.uuid4())
                ts = event.get("ts", "")
                kind = event.get("kind", "unknown")
                trace_id = event.get("trace_id")
                batch_rows.append((event_id, cid, ts, kind, trace_id, event))

            # Batch insert all events at once
            if batch_rows:
                self.store.append_batch(batch_rows)

            # Process graph/motif updates (non-storage logic)
            for event in other_events:
                service = event.get("service", event.get("svc", ""))
                if not service:
                    continue
                cid = self.resolver.resolve(service)
                kind = event.get("kind", "")
                if kind == "deploy":
                    self._on_deploy(event, cid)
                elif kind in ("log", "metric", "trace"):
                    self._on_signal(event, cid)
                elif kind == "incident_signal":
                    self._on_incident(event, cid)
                elif kind == "remediation":
                    self._on_remediation(event, cid)

    def _on_topology(self, event: dict) -> None:
        """Handle topology mutation events (rename, dependency shift)."""
        mutation = event.get("mutation", event.get("change", {}))
        if not mutation:
            mutation = event

        kind = mutation.get("kind", mutation.get("type", ""))
        ts = event.get("ts", "")

        if kind == "rename" or "rename" in str(mutation):
            old_name = mutation.get("old_name", mutation.get("from", ""))
            new_name = mutation.get("new_name", mutation.get("to", ""))
            if old_name and new_name:
                self.resolver.rename(old_name, new_name, ts)
        elif kind in ("dep_add", "dep_remove", "dependency"):
            src = mutation.get("src", mutation.get("source", ""))
            dst = mutation.get("dst", mutation.get("target", ""))
            if src:
                self.resolver.resolve(src)
            if dst:
                self.resolver.resolve(dst)

        # Store topology event in event store
        cid = self.resolver.resolve(event.get("service", event.get("src", "topology")))
        event_id = event.get("event_id") or event.get("id") or str(uuid.uuid4())
        ts = event.get("ts", "")
        self.store.append(
            event_id=event_id,
            canonical_id=cid,
            ts=ts,
            kind="topology",
            raw=event,
            trace_id=None,
        )

    def _process_event(self, event: dict) -> None:
        """Process a non-topology event."""
        kind = event.get("kind", "")
        service = event.get("service", event.get("svc", ""))

        if not service:
            return  # Skip events without a service identifier

        # Resolve service name → canonical_id (CRITICAL STEP)
        cid = self.resolver.resolve(service)

        # Store in temporal event store
        self._store_event(event, cid)

        # Route to appropriate handler
        if kind == "deploy":
            self._on_deploy(event, cid)
        elif kind in ("log", "metric", "trace"):
            self._on_signal(event, cid)
        elif kind == "incident_signal":
            self._on_incident(event, cid)
        elif kind == "remediation":
            self._on_remediation(event, cid)
        # Unknown kinds: log and continue (never crash)

    def _store_event(self, event: dict, cid: str) -> None:
        """Write event to temporal store with canonical_id tag."""
        event_id = event.get("event_id") or event.get("id") or str(uuid.uuid4())
        ts = event.get("ts", "")
        kind = event.get("kind", "unknown")
        trace_id = event.get("trace_id")

        self.store.append(
            event_id=event_id,
            canonical_id=cid,
            ts=ts,
            kind=kind,
            raw=event,
            trace_id=trace_id,
        )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_deploy(self, event: dict, cid: str) -> None:
        """Record deploy timestamp for this entity. Used as causal chain start."""
        version = event.get("version", "unknown")
        ts = event.get("ts", "")
        self.graph.record_deploy(cid, version, ts)

    def _on_signal(self, event: dict, cid: str) -> None:
        """
        Process metric/log/trace signals. Builds causal edges:

        1. deploy → signal  (if a recent deploy exists for this entity)
        2. metric → log     (if an error log follows a metric anomaly on same entity)
        3. log → trace      (if a log and trace share a trace_id on same entity)
        4. caller → callee  (upstream call edges from trace spans)
        """
        ts = event.get("ts", "")
        kind = event.get("kind", "signal")
        eid = event.get("event_id") or event.get("id") or ts
        trace_id = event.get("trace_id")

        # Track signal timestamps FIRST so cross-signal lookups work correctly
        self.graph.record_signal(cid, kind, ts, trace_id, eid)

        # 1. deploy → signal edge
        recent_deploy = self.graph.get_recent_deploy(cid, ts, window_s=600)
        if recent_deploy:
            self.graph.add_edge(
                src_cid=cid,
                dst_cid=cid,
                relation=f"deploy_to_{kind}",
                evidence_id=trace_id or eid,
                ts_src=recent_deploy["ts"],
                ts_dst=ts,
            )

        # 2. metric → log: if this is an error log, link from the most recent
        #    metric anomaly on the same entity (within 5 minutes)
        if kind == "log" and event.get("level") in ("error", "critical", "fatal"):
            recent_metric = self.graph.get_recent_signal(cid, ts, kind="metric", window_s=300)
            if recent_metric:
                self.graph.add_edge(
                    src_cid=cid,
                    dst_cid=cid,
                    relation="metric_to_error_log",
                    evidence_id=eid,
                    ts_src=recent_metric["ts"],
                    ts_dst=ts,
                )

        # 3. log → trace: if this trace shares a trace_id with a prior log on same entity
        if kind == "trace" and trace_id:
            recent_log = self.graph.get_recent_signal(cid, ts, kind="log", window_s=300)
            if recent_log and recent_log.get("trace_id") == trace_id:
                self.graph.add_edge(
                    src_cid=cid,
                    dst_cid=cid,
                    relation="log_to_trace",
                    evidence_id=trace_id,
                    ts_src=recent_log["ts"],
                    ts_dst=ts,
                )

        # 4. Trace spans → upstream call edges (caller → callee)
        if trace_id and kind == "trace":
            for span in event.get("spans", []):
                span_svc = span.get("svc", span.get("service", ""))
                if not span_svc:
                    continue
                span_cid = self.resolver.resolve(span_svc)
                if span_cid != cid:
                    span_ts = span.get("ts", ts)
                    # caller (cid) → callee (span_cid)
                    self.graph.add_edge(
                        src_cid=cid,
                        dst_cid=span_cid,
                        relation="upstream_call",
                        evidence_id=trace_id,
                        ts_src=ts,
                        ts_dst=span_ts if span_ts > ts else ts,
                    )

        # Track signal timestamps for cross-signal edge formation
        self.graph.record_signal(cid, kind, ts, trace_id, eid)

    def _on_incident(self, event: dict, cid: str) -> None:
        """Open an incident window for this entity."""
        incident_id = event.get("incident_id", str(uuid.uuid4()))
        self._open_incidents[incident_id] = {
            "cid": cid,
            "ts": event.get("ts", ""),
            "trigger": event.get("trigger", ""),
        }

    def _on_remediation(self, event: dict, cid: str) -> None:
        """
        Close an incident window. If outcome=resolved, reinforce causal edges
        and index the completed incident as a behavioral motif.
        """
        inc_id = event.get("incident_id", "")
        outcome = event.get("outcome", "unknown")

        if outcome == "resolved":
            self.graph.reinforce_remediation(cid, event)

            # Index this as a completed incident motif
            edges = self.graph.get_causal_chain(cid, max_hops=2)
            motif = self.graph.extract_motif(edges)
            motif.remediation_action = event.get("action", "")
            motif.remediation_outcome = outcome
            motif.incident_id = inc_id
            motif.timestamp = event.get("ts", "")
            self.motifs.index_incident(motif)

        # Close the incident window
        if inc_id in self._open_incidents:
            del self._open_incidents[inc_id]

    # ------------------------------------------------------------------
    # Context reconstruction
    # ------------------------------------------------------------------

    def reconstruct_context(
        self,
        signal: dict,
        mode: Literal["fast", "deep"] = "fast",
    ) -> dict:
        """
        Reconstruct context for an incident signal.
        Reads are concurrent-safe with the append-only store.
        No lock needed here.
        """
        return self.assembler.assemble(
            signal=signal,
            mode=mode,
            resolver=self.resolver,
            event_store=self.store,
            graph=self.graph,
            motif_index=self.motifs,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.store.close()
