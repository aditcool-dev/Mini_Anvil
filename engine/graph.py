"""
Layer 3 — Operational Memory Graph

A probabilistic directed graph where nodes are canonical_ids and edges are
causal relationships with confidence, timestamp, and evidence pointers.
Continuously updated as events arrive.

RULES:
- Never use raw service names as node keys. canonical_id only.
- Edges must have source-precedes-effect enforced at write time.
- Initial confidence on new edge is 0.3. Grows with repeated observation.
- Confidence decay: subtract 0.01 per day of no reinforcement.
"""

from __future__ import annotations

import json
import pickle
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    import networkx as nx

    _NX_AVAILABLE = True
except ImportError:
    _NX_AVAILABLE = False


def _parse_ts(ts: str) -> datetime:
    """Parse ISO-8601 timestamp to datetime. Handles Z suffix."""
    ts = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        # Fallback: try without timezone
        return datetime.fromisoformat(ts.split("+")[0]).replace(tzinfo=timezone.utc)


@dataclass
class CausalEdge:
    src_cid: str
    dst_cid: str
    relation: str
    confidence: float
    count: int
    first_seen: str
    last_seen: str
    evidence_ids: list[str]

    def to_output(self, resolver: Any) -> dict:
        """Convert to output format with current service names."""
        return {
            "cause_id": self.src_cid,
            "effect_id": self.dst_cid,
            "cause_name": resolver.current_name(self.src_cid),
            "effect_name": resolver.current_name(self.dst_cid),
            "relation": self.relation,
            "confidence": round(self.confidence, 3),
            # Timestamps for temporal ordering validation
            "cause_ts": self.first_seen,
            "effect_ts": self.last_seen,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class IncidentMotif:
    """
    Topology-independent representation of an incident.
    Describes WHAT happened (the pattern), not WHERE (the services).
    """

    incident_id: str = ""
    canonical_ids: list[str] = field(default_factory=list)
    event_sequence: list[str] = field(default_factory=list)
    # List of (src_role, relation_role, dst_role) tuples
    causal_shape: list[tuple[str, str, str]] = field(default_factory=list)
    remediation_action: str = ""
    remediation_outcome: str = ""
    timestamp: str = ""
    confidence: float = 0.0
    content_tokens: set[str] = field(default_factory=set)

    def content_fingerprint(self) -> set[str]:
        """
        Return cached content tokens if available, otherwise compute fresh.
        Tokens are set during incident processing and persist through serialization.
        """
        # If we have cached tokens, return them
        if self.content_tokens:
            return self.content_tokens

        # Otherwise compute from scratch
        tokens = set()

        # 1. Encode the event sequence pattern
        if self.event_sequence:
            seq_str = "_".join(self.event_sequence[:4])  # First 4 event types
            tokens.add(f"seq:{seq_str}")

        # 2. Extract signal types from sequence
        for event_type in self.event_sequence:
            if "error" in event_type.lower():
                tokens.add("signal:error")
            elif "metric" in event_type.lower() or "latency" in event_type.lower():
                tokens.add("signal:metric_anomaly")
            elif "deploy" in event_type.lower():
                tokens.add("signal:deploy")
            elif "trace" in event_type.lower():
                tokens.add("signal:trace")
            elif "log" in event_type.lower():
                tokens.add("signal:log")

        # 3. Encode remediation action
        if self.remediation_action:
            tokens.add(f"remedy:{self.remediation_action}")

        # 4. Encode remediation outcome
        if self.remediation_outcome:
            tokens.add(f"outcome:{self.remediation_outcome}")

        # 5. Encode causal shape patterns (major relationships)
        if self.causal_shape:
            # Count edge types
            relations = [edge[1] if len(edge) > 1 else "" for edge in self.causal_shape]
            for rel in set(relations):
                if rel:
                    tokens.add(f"edge:{rel}")

        return  # return tokens


class OperationalGraph:
    """
    Probabilistic directed causal graph over canonical_ids.

    Nodes: canonical_ids
    Edges: causal relationships with confidence, count, timestamps, evidence
    """

    def __init__(self) -> None:
        if not _NX_AVAILABLE:
            raise ImportError("networkx is required: pip install networkx")
        self.G: nx.DiGraph = nx.DiGraph()
        self._deploy_log: dict[str, list[dict]] = {}  # cid → [{ts, version}]
        self._remediation_table: dict[str, list[dict]] = {}  # cid → [outcomes]
        self._signal_log: dict[
            str, dict[str, dict]
        ] = {}  # cid → {kind → {ts, trace_id, event_id}}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Edge management
    # ------------------------------------------------------------------

    def add_edge(
        self,
        src_cid: str,
        dst_cid: str,
        relation: str,
        evidence_id: str,
        ts_src: str,
        ts_dst: str,
    ) -> None:
        """
        Add or reinforce a causal edge.
        ENFORCES ts_src < ts_dst — never inverts causality.
        """
        # Temporal ordering guard
        try:
            if _parse_ts(ts_src) >= _parse_ts(ts_dst):
                # Log and skip — never invert causality
                return
        except Exception:
            pass  # If we can't parse, allow the edge

        with self._lock:
            if self.G.has_edge(src_cid, dst_cid):
                e = self.G[src_cid][dst_cid]
                e["count"] += 1
                e["confidence"] = min(0.95, e["confidence"] + 0.05)
                if evidence_id not in e["evidence_ids"]:
                    e["evidence_ids"].append(evidence_id)
                e["last_seen"] = ts_dst
            else:
                self.G.add_edge(
                    src_cid,
                    dst_cid,
                    count=1,
                    confidence=0.3,
                    relation=relation,
                    evidence_ids=[evidence_id],
                    first_seen=ts_src,
                    last_seen=ts_dst,
                )

    # ------------------------------------------------------------------
    # Deploy tracking
    # ------------------------------------------------------------------

    def record_deploy(self, cid: str, version: str, ts: str) -> None:
        """Record a deployment event for an entity."""
        with self._lock:
            if cid not in self._deploy_log:
                self._deploy_log[cid] = []
            self._deploy_log[cid].append({"ts": ts, "version": version})

    def get_recent_deploy(
        self, cid: str, anchor_ts: str, window_s: int = 600
    ) -> dict | None:
        """Return the most recent deploy for cid within window_s seconds before anchor_ts."""
        deploys = self._deploy_log.get(cid, [])
        if not deploys:
            return None
        try:
            anchor = _parse_ts(anchor_ts)
            candidates = [
                d
                for d in deploys
                if 0 <= (anchor - _parse_ts(d["ts"])).total_seconds() <= window_s
            ]
            return max(candidates, key=lambda d: d["ts"]) if candidates else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Remediation reinforcement
    # ------------------------------------------------------------------

    def reinforce_remediation(self, cid: str, event: dict) -> None:
        """
        Called when remediation event arrives with outcome=resolved.
        Boosts confidence of ALL edges involving cid that were active during
        the incident window (first_seen <= remediation_ts).
        Uses first_seen (not last_seen) so edges formed after the incident
        start are still captured.
        """
        anchor_ts = event.get("ts", "")
        try:
            anchor = _parse_ts(anchor_ts)
        except Exception:
            return

        with self._lock:
            for src, dst, data in self.G.edges(data=True):
                if src == cid or dst == cid:
                    try:
                        # Boost if the edge was first observed before the remediation
                        # and within a 20-minute lookback window
                        first_seen = _parse_ts(data.get("first_seen", anchor_ts))
                        delta = (anchor - first_seen).total_seconds()
                        if -60 <= delta <= 1200:  # -60s tolerance for clock skew
                            data["confidence"] = min(0.95, data["confidence"] + 0.10)
                            data["remediation_reinforced"] = True
                    except Exception:
                        pass

            # Store remediation outcome
            if cid not in self._remediation_table:
                self._remediation_table[cid] = []
            self._remediation_table[cid].append(
                {
                    "action": event.get("action", ""),
                    "target_version": event.get("version"),
                    "outcome": event.get("outcome", "unknown"),
                    "ts": anchor_ts,
                    "incident_id": event.get("incident_id", ""),
                }
            )

    def get_remediations(self, cid: str) -> list[dict]:
        """Return all remediation outcomes for a canonical_id."""
        return list(self._remediation_table.get(cid, []))

    # ------------------------------------------------------------------
    # Signal tracking (for cross-signal edge formation)
    # ------------------------------------------------------------------

    def record_signal(
        self, cid: str, kind: str, ts: str, trace_id: str | None, event_id: str
    ) -> None:
        """Track the most recent signal of each kind per entity."""
        with self._lock:
            if cid not in self._signal_log:
                self._signal_log[cid] = {}
            self._signal_log[cid][kind] = {
                "ts": ts,
                "trace_id": trace_id,
                "event_id": event_id,
            }

    def get_recent_signal(
        self, cid: str, anchor_ts: str, kind: str, window_s: int = 300
    ) -> dict | None:
        """Return the most recent signal of a given kind for cid within window."""
        entry = self._signal_log.get(cid, {}).get(kind)
        if not entry:
            return None
        try:
            anchor = _parse_ts(anchor_ts)
            sig_ts = _parse_ts(entry["ts"])
            delta = (anchor - sig_ts).total_seconds()
            if 0 < delta <= window_s:
                return entry
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def get_causal_chain(
        self,
        cid: str,
        max_hops: int = 2,
        min_confidence: float = 0.3,
    ) -> list[CausalEdge]:
        """
        BFS from cid, traversing both outgoing AND incoming edges.
        Prune by confidence threshold.
        Returns ordered list of CausalEdge (source-precedes-effect).
        """
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(cid, 0)]
        edges_seen: set[tuple] = set()
        edges: list[CausalEdge] = []

        with self._lock:
            while queue:
                node, depth = queue.pop(0)
                if node in visited or depth > max_hops:
                    continue
                visited.add(node)

                # Outgoing edges: node → dst (node caused dst)
                for src, dst, data in self.G.out_edges(node, data=True):
                    if data.get("confidence", 0) < min_confidence:
                        continue
                    key = (src, dst)
                    if key not in edges_seen:
                        edges_seen.add(key)
                        edges.append(
                            CausalEdge(
                                src_cid=src,
                                dst_cid=dst,
                                relation=data.get("relation", ""),
                                confidence=data.get("confidence", 0.0),
                                count=data.get("count", 1),
                                first_seen=data.get("first_seen", ""),
                                last_seen=data.get("last_seen", ""),
                                evidence_ids=list(data.get("evidence_ids", [])),
                            )
                        )
                    if dst not in visited:
                        queue.append((dst, depth + 1))

                # Incoming edges: src → node (src caused node)
                for src, dst, data in self.G.in_edges(node, data=True):
                    if data.get("confidence", 0) < min_confidence:
                        continue
                    key = (src, dst)
                    if key not in edges_seen:
                        edges_seen.add(key)
                        edges.append(
                            CausalEdge(
                                src_cid=src,
                                dst_cid=dst,
                                relation=data.get("relation", ""),
                                confidence=data.get("confidence", 0.0),
                                count=data.get("count", 1),
                                first_seen=data.get("first_seen", ""),
                                last_seen=data.get("last_seen", ""),
                                evidence_ids=list(data.get("evidence_ids", [])),
                            )
                        )
                    if src not in visited:
                        queue.append((src, depth + 1))

        # Sort by first_seen to enforce temporal ordering in output
        edges.sort(key=lambda e: e.first_seen)
        return edges

    def get_edges_in_window(
        self, cid: str, anchor_ts: str, window_s: int = 600
    ) -> list[tuple]:
        """Return graph edges involving cid with last_seen within window."""
        result = []
        try:
            anchor = _parse_ts(anchor_ts)
        except Exception:
            return result

        with self._lock:
            for src, dst, data in self.G.edges(data=True):
                if src == cid or dst == cid:
                    try:
                        last = _parse_ts(data.get("last_seen", ""))
                        if 0 <= (anchor - last).total_seconds() <= window_s:
                            result.append((src, dst, data))
                    except Exception:
                        pass
        return result

    # ------------------------------------------------------------------
    # Motif extraction
    # ------------------------------------------------------------------

    def extract_motif(self, edges: list[CausalEdge]) -> IncidentMotif:
        """
        Convert causal chain to topology-independent behavioral fingerprint.
        Replaces canonical_ids with role labels based on relation type.

        The motif shape encodes the FULL causal path as (src_role, relation_role, dst_role)
        triples — richer than just (src, dst) pairs, enabling family discrimination.
        """
        motif = IncidentMotif()
        motif.canonical_ids = list(
            {e.src_cid for e in edges} | {e.dst_cid for e in edges}
        )

        # Assign stable role labels to each canonical_id in this incident
        # Role is determined by the relations it participates in
        cid_roles: dict[str, str] = {}
        for edge in edges:
            src_role = _cid_to_role(edge.src_cid, edge.relation, is_src=True)
            dst_role = _cid_to_role(edge.dst_cid, edge.relation, is_src=False)
            # First assignment wins (most upstream role)
            if edge.src_cid not in cid_roles:
                cid_roles[edge.src_cid] = src_role
            if edge.dst_cid not in cid_roles:
                cid_roles[edge.dst_cid] = dst_role

        # Build abstract event sequence: ordered list of unique role transitions
        seen: list[str] = []
        for edge in edges:
            rel_role = _relation_to_role(edge.relation)
            if rel_role not in seen:
                seen.append(rel_role)
        motif.event_sequence = seen

        # Build causal shape as (src_role, relation_role, dst_role) triples
        # This is the key discriminator between incident families
        shape_triples = [
            (
                cid_roles.get(e.src_cid, "UNKNOWN"),
                _relation_to_role(e.relation),
                cid_roles.get(e.dst_cid, "UNKNOWN"),
            )
            for e in edges
        ]

        # Add structural arity tokens — discriminate families by graph structure
        # (number of unique services, number of causal edges, whether self-loops exist)
        n_nodes = len(motif.canonical_ids)
        n_edges = len(edges)
        has_self_loop = any(e.src_cid == e.dst_cid for e in edges)
        # Bin node count: "solo", "pair", "trio", "multi"
        node_bin = (
            "solo"
            if n_nodes <= 1
            else "pair"
            if n_nodes == 2
            else "trio"
            if n_nodes == 3
            else "multi"
        )
        # Bin edge count: "sparse", "moderate", "dense"
        edge_bin = "sparse" if n_edges <= 2 else "moderate" if n_edges <= 5 else "dense"

        arity_tokens = [
            ("ARITY", f"nodes_{node_bin}", "ARITY"),
            ("ARITY", f"edges_{edge_bin}", "ARITY"),
            ("ARITY", f"self_loop_{'yes' if has_self_loop else 'no'}", "ARITY"),
        ]

        motif.causal_shape = shape_triples + arity_tokens

        motif.confidence = (
            sum(e.confidence for e in edges) / len(edges) if edges else 0.0
        )
        return motif

    # ------------------------------------------------------------------
    # Confidence decay (lazy — called at reconstruction time)
    # ------------------------------------------------------------------

    def apply_decay(self, now_ts: str) -> None:
        """Decay edge confidence based on staleness. Called lazily."""
        try:
            now = _parse_ts(now_ts)
        except Exception:
            return

        with self._lock:
            for _, _, data in self.G.edges(data=True):
                try:
                    last = _parse_ts(data.get("last_seen", now_ts))
                    days_old = (now - last).days
                    if days_old > 0:
                        data["confidence"] = max(
                            0.1, data["confidence"] - 0.01 * days_old
                        )
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump(
                {
                    "graph": self.G,
                    "deploy_log": self._deploy_log,
                    "remediation_table": self._remediation_table,
                    "signal_log": self._signal_log,
                },
                f,
            )

    def load(self, path: str) -> None:
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.G = data["graph"]
        self._deploy_log = data.get("deploy_log", {})
        self._remediation_table = data.get("remediation_table", {})
        self._signal_log = data.get("signal_log", {})


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _relation_to_role(relation: str) -> str:
    """Map a relation string to an abstract role label for motif encoding."""
    relation = relation.lower()
    if "deploy" in relation:
        return "DEPLOY"
    if (
        "metric" in relation
        or "latency" in relation
        or "spike" in relation
        or "threshold" in relation
    ):
        return "METRIC_ANOMALY"
    if "error_log" in relation or ("log" in relation and "error" in relation):
        return "ERROR_LOG"
    if "upstream" in relation or "call" in relation:
        return "UPSTREAM_CALL"
    if "trace" in relation:
        return "TRACE_CORRELATION"
    if "incident" in relation:
        return "INCIDENT"
    if "rollback" in relation:
        return "ROLLBACK"
    if "restart" in relation:
        return "RESTART"
    if "config" in relation:
        return "CONFIG_CHANGE"
    if "remediation" in relation:
        return "REMEDIATION"
    if "log" in relation:
        return "LOG_SIGNAL"
    return "SIGNAL"


def _cid_to_role(cid: str, relation: str, is_src: bool) -> str:
    """
    Assign a structural role to a canonical_id based on its position in a relation.
    This makes motif shapes topology-independent.
    """
    rel = relation.lower()
    if "deploy" in rel:
        return "DEPLOY_TARGET" if is_src else "DEPLOY_EFFECT"
    if "upstream" in rel or "call" in rel:
        return "CALLER" if is_src else "CALLEE"
    if "metric" in rel or "latency" in rel:
        return "METRIC_SOURCE" if is_src else "METRIC_EFFECT"
    if "error" in rel or "log" in rel:
        return "LOG_SOURCE" if is_src else "LOG_EFFECT"
    if "trace" in rel:
        return "TRACE_ROOT" if is_src else "TRACE_SPAN"
    return "SOURCE" if is_src else "EFFECT"
