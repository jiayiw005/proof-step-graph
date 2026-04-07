from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pantograph.server import Server, TacticFailure, ServerError
from pantograph.expr import GoalState, Goal, Site
from pantograph.data import TacticInvocation, CompilationUnit

from .graph import (
    ProofStepGraph,
    NODE_GOAL, NODE_TACTIC,
    goal_node_id, tactic_node_id,
)
from .parse_lean import parse_goal_block


# ─────────────────────── helpers ─────────────────────────────────────────────

def _goal_content_id(target: str, variables: list[str], case_name: Optional[str]) -> str:
    """
    Content-based ID for static tracing (no metavar names available).
    Identical goal strings share the same node.
    """
    blob = f"{case_name}|{'|'.join(variables)}|{target}"
    return "gc_" + hashlib.md5(blob.encode()).hexdigest()[:12]


def _build_graph_from_invocations(
    theorem_name: str,
    invocations: list[TacticInvocation],
) -> ProofStepGraph:
    """
    Build a ProofStepGraph from a flat list of TacticInvocations (static mode).

    Strategy:
    - Parse before/after strings into lists of per-goal dicts.
    - Goal node IDs are content-based hashes (goals with identical text share a node).
    - For each step, the focused goal is before[0] (Pantograph auto mode always focuses
      the first goal).
    - New goals (in after but not in before, matched by content) are output edges.
    """
    pg = ProofStepGraph(theorem_name=theorem_name)

    # Register goal content → node_id for deduplication
    registered: dict[str, str] = {}

    def ensure_goal_node(gdata: dict, is_initial: bool = False) -> str:
        cid = _goal_content_id(gdata["target"], gdata["variables"], gdata["case_name"])
        if cid not in registered:
            pg.add_goal_node(
                node_id=cid,
                target=gdata["target"],
                variables=gdata["variables"],
                case_name=gdata["case_name"],
                is_initial=is_initial,
            )
            registered[cid] = cid
        return cid

    if not invocations:
        return pg

    # Seed the initial goals from step 0's `before`
    step0_before = parse_goal_block(invocations[0].before)
    for g in step0_before:
        ensure_goal_node(g, is_initial=True)

    for step_idx, inv in enumerate(invocations):
        before_goals = parse_goal_block(inv.before)
        after_goals  = parse_goal_block(inv.after)

        before_cids = {_goal_content_id(g["target"], g["variables"], g["case_name"]) for g in before_goals}
        after_cids  = {_goal_content_id(g["target"], g["variables"], g["case_name"]) for g in after_goals}

        # Tactic node
        tac_id = tactic_node_id(step_idx)
        pg.add_tactic_node(
            node_id=tac_id,
            tactic=inv.tactic,
            used_constants=inv.used_constants,
            step_idx=step_idx,
        )

        # Input edge: focused goal (before[0]) → tactic
        if before_goals:
            focused_cid = ensure_goal_node(before_goals[0])
            pg.add_input_edge(focused_cid, tac_id)

        # Output edges: new goals produced by this tactic
        new_cids = after_cids - before_cids
        for g in after_goals:
            cid = _goal_content_id(g["target"], g["variables"], g["case_name"])
            if cid in new_cids:
                ensure_goal_node(g, is_initial=False)
                pg.add_output_edge(tac_id, cid)

    return pg


# ─────────────────────── StaticProofTracer ───────────────────────────────────

class StaticProofTracer:
    """
    Trace all theorems in a Lean file using PyPantograph's static
    `tactic_invocations` API.  Fast but goal identity is content-approximate.
    """

    def __init__(self, server: Server):
        self.server = server

    def trace_file(self, lean_file: str | Path) -> list[ProofStepGraph]:
        """
        Trace every compilation unit in `lean_file` that has tactic invocations.
        Returns one ProofStepGraph per unit (theorem).  Units without tactic blocks
        (e.g. definitions) are skipped.
        """
        units: list[CompilationUnit] = self.server.tactic_invocations(str(lean_file))
        graphs = []
        for idx, unit in enumerate(units):
            if not unit.invocations:
                continue
            name = f"theorem_{idx}"  # name unknown from static trace alone
            pg = _build_graph_from_invocations(name, unit.invocations)
            if pg.G.number_of_nodes() > 0:
                graphs.append(pg)
        return graphs

    def trace_file_named(
        self,
        lean_file: str | Path,
        theorems: list,
    ) -> list[ProofStepGraph]:
        """
        Like trace_file but assigns theorem names by matching CompilationUnit
        byte offsets to LeanTheorem.byte_offset.

        `theorems` should be a list of LeanTheorem (from parse_lean.extract_theorems).
        Each unit is matched to the theorem whose byte_offset falls inside that unit's
        [i_begin, i_end) range.  Unmatched units get a generic name.
        """
        units = self.server.tactic_invocations(str(lean_file))

        # Build sorted list of (byte_offset, name) for quick lookup
        offset_to_name: dict[int, str] = {}
        if theorems and hasattr(theorems[0], 'byte_offset'):
            for t in theorems:
                offset_to_name[t.byte_offset] = t.name
        else:
            # Fallback: treat theorems as plain strings (old API)
            name_iter = iter(str(t) for t in theorems)
            for unit in units:
                if not unit.invocations:
                    continue
                name = next(name_iter, f"theorem_{len(offset_to_name)}")
                offset_to_name[unit.i_begin] = name

        graphs = []
        for idx, unit in enumerate(units):
            if not unit.invocations:
                continue
            # Find the theorem whose byte_offset falls inside this unit
            name = None
            for byte_off, thm_name in offset_to_name.items():
                if unit.i_begin <= byte_off < unit.i_end:
                    name = thm_name
                    break
            if name is None:
                name = f"theorem_{idx}"
            pg = _build_graph_from_invocations(name, unit.invocations)
            if pg.G.number_of_nodes() > 0:
                graphs.append(pg)
        return graphs


# ─────────────────────── InteractiveProofTracer ──────────────────────────────

@dataclass
class ReplayStep:
    tactic: str
    used_constants: list[str]
    before: GoalState
    after: GoalState
    focused_goal: Goal          # the Goal object that was targeted


class InteractiveProofTracer:
    """
    Replay a known proof interactively to obtain a structured ProofStepGraph.

    Goal nodes are keyed by Goal.id (Lean metavar name), giving exact identity
    across steps and correct sibling/dependency edges.

    Usage:
        server = Server(imports=["Mathlib"], project_path="path/to/lean-project")
        tracer = InteractiveProofTracer(server)
        pg = tracer.trace_theorem(
            name="Nat.add_comm",
            theorem_type="∀ n m : Nat, n + m = m + n",
            tactics=["intro n m", "induction n", ...],
        )
    """

    def __init__(self, server: Server):
        self.server = server

    def replay(
        self,
        theorem_type: str,
        tactics: list[str],
        used_constants: Optional[list[list[str]]] = None,
    ) -> list[ReplayStep]:
        """
        Replay `tactics` on `theorem_type` and return the full step trace.
        Stops early on tactic failure.
        """
        state = self.server.goal_start(theorem_type)
        steps: list[ReplayStep] = []

        for i, tactic in enumerate(tactics):
            if state.is_solved or not state.goals:
                break
            focused = state.goals[0]
            used = used_constants[i] if used_constants else []
            try:
                next_state = self.server.goal_tactic(state, tactic)
            except (TacticFailure, ServerError) as e:
                # Record failed step and stop
                steps.append(ReplayStep(
                    tactic=tactic,
                    used_constants=used,
                    before=state,
                    after=state,
                    focused_goal=focused,
                ))
                break
            steps.append(ReplayStep(
                tactic=tactic,
                used_constants=used,
                before=state,
                after=next_state,
                focused_goal=focused,
            ))
            state = next_state

        return steps

    def build_graph(
        self,
        name: str,
        steps: list[ReplayStep],
        initial_state: Optional[GoalState] = None,
    ) -> ProofStepGraph:
        """
        Build a ProofStepGraph from a list of ReplaySteps.

        Goal node IDs are Goal.id (metavar names).
        Edge structure:
          focused_goal.id → tactic_node  (EDGE_INPUT)
          tactic_node → new_goal.id       (EDGE_OUTPUT) for each goal that
                                          appears in after but not in before.
        """
        pg = ProofStepGraph(theorem_name=name)

        def ensure_goal(goal: Goal, is_initial: bool = False) -> None:
            if goal.id not in pg.G:
                pg.add_goal_node(
                    node_id=goal.id,
                    target=goal.target,
                    variables=[str(v) for v in goal.variables],
                    case_name=goal.name,
                    is_initial=is_initial,
                )

        # Seed initial goals
        if initial_state:
            for g in initial_state.goals:
                ensure_goal(g, is_initial=True)
        elif steps:
            for g in steps[0].before.goals:
                ensure_goal(g, is_initial=True)

        for step_idx, step in enumerate(steps):
            tac_id = tactic_node_id(step_idx)
            pg.add_tactic_node(
                node_id=tac_id,
                tactic=step.tactic,
                used_constants=step.used_constants,
                step_idx=step_idx,
            )

            # Input: focused goal → tactic
            ensure_goal(step.focused_goal)
            pg.add_input_edge(step.focused_goal.id, tac_id)

            # Output: new goals produced by this tactic
            before_ids = {g.id for g in step.before.goals}
            for g in step.after.goals:
                if g.id not in before_ids:
                    ensure_goal(g, is_initial=False)
                    pg.add_output_edge(tac_id, g.id)

        return pg

    def trace_theorem(
        self,
        name: str,
        theorem_type: str,
        tactics: list[str],
        used_constants: Optional[list[list[str]]] = None,
    ) -> ProofStepGraph:
        """Convenience: replay then build graph."""
        steps = self.replay(theorem_type, tactics, used_constants)
        return self.build_graph(name, steps)
