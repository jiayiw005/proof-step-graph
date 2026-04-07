#!/usr/bin/env python3
"""
trace_goedel.py — Graph extraction for Goedel pass@32 verified proofs.

Reconstructs full Lean proofs from:
  - goedel_pass32_verified.jsonl  (which (item_idx, answer_idx) pairs passed)
  - gen_Goedel-Prover-V2-8B_pass32.json  (raw model outputs)

For each theorem with ≥1 passing, no-sorry answer: reconstructs the proof,
runs tactic_invocations, and streams ProofStepGraphs to JSONL.

Usage:
    PATH="$HOME/.elan/bin:$PATH" uv run python trace_goedel.py \\
        --verified data/goedel_pass32_verified.jsonl \\
        --gen     data/gen_Goedel-Prover-V2-8B_pass32.json \\
        --output  data/goedel_verified_graphs.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "PyPantograph"))
sys.path.insert(0, str(Path(__file__).parent))

from pantograph.server import Server, ServerError
from pantograph.data import CompilationUnit
from proof_step_graph.tracer import _build_graph_from_invocations

STARTUP_TIMEOUT = 600


# ─────────────────────── proof reconstruction ────────────────────────────────

_IMPORT_LINE_RE = re.compile(r'^import\s+\S+.*$', re.MULTILINE)


def reconstruct_proof(entry: dict, ans_idx: int) -> str | None:
    """
    Reconstruct a full Lean source string from the gen-file entry and answer.

    autoformalization ends with `...theorem ... := by` (no closing fence).
    The answer starts with tactic body and ends with ``` to close the block.
    """
    autof = entry.get("autoformalization", "")
    m = re.search(r'```lean4?\n(.*)', autof, re.DOTALL)
    if not m:
        return None
    header = m.group(1).rstrip()           # everything after ```lean4

    answer = entry["answers"][ans_idx]
    # answer may contain trailing ``` (closing the markdown fence)
    tactic_body = re.split(r'\n```', answer)[0]

    return (header + "\n" + tactic_body).strip()


def is_real_proof(proof: str) -> bool:
    """Return True if proof has genuine tactic content (not just sorry/empty)."""
    if "sorry" in proof:
        return False
    # Must have at least one tactic line beyond the theorem header
    tactic_lines = [
        l for l in proof.split("\n")
        if l.strip()
        and not l.startswith("import")
        and not l.startswith("open")
        and not re.match(r"\s*(theorem|lemma|def|abbrev)\b", l)
    ]
    return len(tactic_lines) >= 1


def strip_imports(proof: str) -> str:
    return _IMPORT_LINE_RE.sub("", proof).lstrip("\n")


# ─────────────────────── tactic_invocations via inheritEnv ───────────────────

def tactic_invocations_reuse_env(server: Server, source: str) -> list[CompilationUnit]:
    with tempfile.TemporaryDirectory() as tmpdir:
        inv_file = os.path.join(tmpdir, "invocations.json")
        result = server.run("frontend.process", {
            "file": source,
            "invocations": inv_file,
            "readHeader": False,
            "inheritEnv": True,
            "newConstants": False,
        })
        if "error" in result:
            raise ServerError(result)
        with open(inv_file) as f:
            data_units = json.load(f)
        return [
            CompilationUnit.parse(payload, invocations=data_unit["invocations"])
            for payload, data_unit in zip(result["units"], data_units["units"])
        ]


# ─────────────────────── server ──────────────────────────────────────────────

def make_server(project_path: str, request_timeout: int) -> Server:
    t0 = time.time()
    print(f"[trace_goedel] Starting Pantograph server with Mathlib…", flush=True)
    server = Server(imports=["Mathlib"], project_path=project_path, timeout=STARTUP_TIMEOUT)
    server.timeout = request_timeout
    print(f"[trace_goedel] Server ready in {time.time()-t0:.1f}s", flush=True)
    return server


# ─────────────────────── dataset builder ─────────────────────────────────────

def build_proof_list(verified: list, gen: list) -> list[tuple[str, str]]:
    """
    Return list of (theorem_name, full_proof) for items with a real passing proof.
    Picks the first passing answer per theorem that has no sorry and real tactics.
    """
    proofs = []
    n_only_sorry = 0

    for item in verified:
        if item["n_pass"] == 0:
            continue
        name = item["theorem_names"]
        entry = gen[item["item_idx"]]

        # Try passing answers in order; prefer no-sorry proofs
        best = None
        for a in item["answers"]:
            if not a["pass"]:
                continue
            proof = reconstruct_proof(entry, a["answer_idx"])
            if proof is None:
                continue
            if is_real_proof(proof):
                best = proof
                break

        if best is None:
            n_only_sorry += 1
            continue

        proofs.append((name, best))

    print(f"[trace_goedel] Theorems with real passing proof: {len(proofs)}")
    print(f"[trace_goedel] Skipped (only sorry/empty passing): {n_only_sorry}")
    return proofs


# ─────────────────────── main ────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--verified", required=True, help="goedel_pass32_verified.jsonl")
    p.add_argument("--gen",      required=True, help="gen_Goedel-Prover-V2-8B_pass32.json")
    p.add_argument("--output",   default=None)
    p.add_argument("--project",  default=None)
    p.add_argument("--timeout",  type=int, default=90)
    p.add_argument("--limit",    type=int, default=None)
    p.add_argument("--resume",   action="store_true")
    p.add_argument("--verbose",  action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    project_path = (
        Path(args.project).resolve() if args.project
        else Path(__file__).parent.resolve()
    )

    if args.output is None:
        out_dir = Path(__file__).parent / "data"
        out_dir.mkdir(exist_ok=True)
        output_path = out_dir / "goedel_verified_graphs.jsonl"
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    failures_path = output_path.with_suffix("").with_suffix(".failures.jsonl")

    # ── load data ─────────────────────────────────────────────────────────────
    print(f"[trace_goedel] Loading verified list…")
    with open(args.verified) as f:
        verified = [json.loads(l) for l in f if l.strip()]
    with open(args.gen) as f:
        gen = json.load(f)

    proofs = build_proof_list(verified, gen)

    if args.limit:
        proofs = proofs[:args.limit]
        print(f"[trace_goedel] Limited to {args.limit} proofs")

    # ── resume ────────────────────────────────────────────────────────────────
    done_names: set[str] = set()
    if args.resume and output_path.exists():
        with output_path.open() as f:
            for line in f:
                try:
                    done_names.add(json.loads(line)["theorem_name"])
                except Exception:
                    pass
        print(f"[trace_goedel] Resuming — {len(done_names)} already done")

    # ── server ────────────────────────────────────────────────────────────────
    server = make_server(str(project_path), args.timeout)
    n_restarts = 0

    n_ok = n_skip = n_fail = n_timeout = 0
    t_start = time.time()

    out_f  = output_path.open("a" if args.resume else "w")
    fail_f = failures_path.open("a" if args.resume else "w")

    try:
        for i, (name, proof) in enumerate(proofs):
            elapsed = time.time() - t_start
            rate = (n_ok + n_fail + n_timeout) / max(elapsed, 1)
            print(
                f"[{i+1}/{len(proofs)}] {name[:55]:<55}  "
                f"ok={n_ok} fail={n_fail} to={n_timeout} restart={n_restarts}  "
                f"{rate:.2f}/s",
                end="\r", flush=True,
            )

            if args.resume and name in done_names:
                n_skip += 1
                continue

            lean_src = strip_imports(proof)

            try:
                units = tactic_invocations_reuse_env(server, lean_src)

                graphs = []
                for unit in units:
                    if not unit.invocations:
                        continue
                    pg = _build_graph_from_invocations(name, unit.invocations)
                    if pg.G.number_of_nodes() > 0:
                        graphs.append(pg)

                if graphs:
                    pg = graphs[0]
                    out_f.write(json.dumps(pg.to_dict(), ensure_ascii=False) + "\n")
                    out_f.flush()
                    n_ok += 1
                    if args.verbose:
                        st = pg.stats()
                        print(f"\n  -> goals={st['n_goals']} tactics={st['n_tactics']} branch={st['max_branching']}")
                else:
                    _log(fail_f, name, "no_invocations", i)
                    n_fail += 1

            except ServerError as e:
                msg = str(e)
                if "timeout" in msg.lower():
                    _log(fail_f, name, f"timeout: {msg[:100]}", i)
                    n_timeout += 1
                else:
                    _log(fail_f, name, msg[:200], i)
                    n_fail += 1
                if server.proc is None:
                    print(f"\n[trace_goedel] Server died — restarting…", flush=True)
                    try:
                        server = make_server(str(project_path), args.timeout)
                        n_restarts += 1
                    except Exception as ex:
                        print(f"[trace_goedel] Restart failed: {ex}", file=sys.stderr)
                        break

            except Exception as e:
                _log(fail_f, name, str(e)[:200], i)
                n_fail += 1
                if server.proc is None:
                    print(f"\n[trace_goedel] Server died — restarting…", flush=True)
                    try:
                        server = make_server(str(project_path), args.timeout)
                        n_restarts += 1
                    except Exception as ex:
                        print(f"[trace_goedel] Restart failed: {ex}", file=sys.stderr)
                        break
    finally:
        out_f.close()
        fail_f.close()

    total_elapsed = time.time() - t_start
    print()
    print("=" * 60)
    print(f"Traced {len(proofs)} proofs in {total_elapsed:.1f}s")
    print(f"  success   : {n_ok}")
    print(f"  skipped   : {n_skip}")
    print(f"  failed    : {n_fail}")
    print(f"  timed out : {n_timeout}")
    print(f"  restarts  : {n_restarts}")
    print(f"  rate      : {(n_ok+n_fail+n_timeout)/max(total_elapsed,1):.2f}/s")
    print(f"\nOutput   → {output_path}")
    print(f"Failures → {failures_path}")


def _log(fail_f, name, reason, idx):
    fail_f.write(json.dumps({"theorem_name": name, "reason": reason, "index": idx}) + "\n")
    fail_f.flush()


if __name__ == "__main__":
    main()
