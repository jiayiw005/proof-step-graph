from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import networkx as nx

NODE_GOAL   = "goal"
NODE_TACTIC = "tactic"
EDGE_INPUT  = "input"   # goal → tactic
EDGE_OUTPUT = "output"  # tactic → goal


# ─────────────────────── node ID helpers ─────────────────────────────────────

def goal_node_id(step: int, pos: int) -> str:
    """Stable ID for a goal that appeared at position `pos` in step `step`."""
    return f"g{step}_{pos}"

def tactic_node_id(step: int) -> str:
    return f"t{step}"


# ─────────────────────── ProofGraph ──────────────────────────────────────────

@dataclass
class ProofGraph:
    """
    Bipartite DAG for a single Lean theorem proof.

    Nodes carry attributes:
      - type        : NODE_GOAL or NODE_TACTIC
      - (goal)  target     : str   — goal type expression
      - (goal)  variables  : list[str]
      - (goal)  case_name  : str | None
      - (goal)  is_initial : bool
      - (tactic) tactic          : str
      - (tactic) used_constants  : list[str]
      - (tactic) step_idx        : int

    Edges carry:
      - type : EDGE_INPUT or EDGE_OUTPUT
    """
    theorem_name: str
    G: nx.DiGraph = field(default_factory=nx.DiGraph)

    # ── construction helpers ─────────────────────────────────────────────────

    def add_goal_node(
        self,
        node_id: str,
        target: str,
        variables: list[str],
        case_name: Optional[str] = None,
        is_initial: bool = False,
    ) -> None:
        self.G.add_node(
            node_id,
            type=NODE_GOAL,
            target=target,
            variables=variables,
            case_name=case_name,
            is_initial=is_initial,
        )

    def add_tactic_node(
        self,
        node_id: str,
        tactic: str,
        used_constants: list[str],
        step_idx: int,
    ) -> None:
        self.G.add_node(
            node_id,
            type=NODE_TACTIC,
            tactic=tactic,
            used_constants=used_constants,
            step_idx=step_idx,
        )

    def add_input_edge(self, goal_id: str, tactic_id: str) -> None:
        self.G.add_edge(goal_id, tactic_id, type=EDGE_INPUT)

    def add_output_edge(self, tactic_id: str, goal_id: str) -> None:
        self.G.add_edge(tactic_id, goal_id, type=EDGE_OUTPUT)

    # ── views ────────────────────────────────────────────────────────────────

    def goal_nodes(self) -> Iterator[tuple[str, dict]]:
        return ((n, d) for n, d in self.G.nodes(data=True) if d.get("type") == NODE_GOAL)

    def tactic_nodes(self) -> Iterator[tuple[str, dict]]:
        return ((n, d) for n, d in self.G.nodes(data=True) if d.get("type") == NODE_TACTIC)

    def initial_goals(self) -> list[tuple[str, dict]]:
        return [(n, d) for n, d in self.goal_nodes() if d.get("is_initial")]

    def terminal_goals(self) -> list[tuple[str, dict]]:
        """Goals with no outgoing INPUT edges (closed by a tactic)."""
        return [
            (n, d) for n, d in self.goal_nodes()
            if not any(self.G[n][s].get("type") == EDGE_INPUT for s in self.G.successors(n))
        ]

    # ── statistics ───────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        goal_count   = sum(1 for _ in self.goal_nodes())
        tactic_count = sum(1 for _ in self.tactic_nodes())
        branching = []
        for n, _ in self.tactic_nodes():
            out_goals = [s for s in self.G.successors(n) if self.G[n][s].get("type") == EDGE_OUTPUT]
            branching.append(len(out_goals))
        return {
            "theorem": self.theorem_name,
            "n_goals": goal_count,
            "n_tactics": tactic_count,
            "n_initial_goals": len(self.initial_goals()),
            "n_terminal_goals": len(self.terminal_goals()),
            "n_edges": self.G.number_of_edges(),
            "max_branching": max(branching, default=0),
            "avg_branching": (sum(branching) / len(branching)) if branching else 0.0,
            "is_dag": nx.is_directed_acyclic_graph(self.G),
        }

    # ── serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "theorem_name": self.theorem_name,
            "nodes": [
                {"id": n, **{k: v for k, v in d.items()}}
                for n, d in self.G.nodes(data=True)
            ],
            "edges": [
                {"src": u, "dst": v, **{k: w for k, w in d.items()}}
                for u, v, d in self.G.edges(data=True)
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProofGraph":
        pg = cls(theorem_name=d["theorem_name"])
        for node in d["nodes"]:
            node = dict(node)
            nid = node.pop("id")
            pg.G.add_node(nid, **node)
        for edge in d["edges"]:
            edge = dict(edge)
            src, dst = edge.pop("src"), edge.pop("dst")
            pg.G.add_edge(src, dst, **edge)
        return pg

    def save_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    @classmethod
    def load_json(cls, path: str | Path) -> "ProofGraph":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"ProofGraph({self.theorem_name!r}: "
            f"{s['n_goals']} goals, {s['n_tactics']} tactics, "
            f"branch={s['max_branching']})"
        )
