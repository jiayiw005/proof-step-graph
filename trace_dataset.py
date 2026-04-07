from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).parent / "PyPantograph"))
sys.path.insert(0, str(Path(__file__).parent))

from pantograph.server import Server, ServerError
from pantograph.data import CompilationUnit
from proof_graph.tracer import _build_graph_from_invocations


# ─────────────────────── proof pre-processing ────────────────────────────────

_THEOREM_NAME_RE = re.compile(
    r'(?:^|\n)\s*(?:private\s+|protected\s+)?(?:theorem|lemma|example)\s+(\w+)',
)
_IMPORT_LINE_RE = re.compile(r'^import\s+\S+.*$', re.MULTILINE)


def extract_theorem_name(proof_text: str) -> str | None:
    m = _THEOREM_NAME_RE.search(proof_text)
    return m.group(1) if m else None


def strip_imports(proof_text: str) -> str:
    """Remove `import ...` lines so we can reuse the server's pre-loaded env."""
    return _IMPORT_LINE_RE.sub("", proof_text).lstrip("\n")


# ─────────────────────── fast tactic_invocations via inheritEnv ───────────────

def tactic_invocations_reuse_env(server: Server, source: str) -> list[CompilationUnit]:
    """
    Like server.tactic_invocations() but uses the server's already-loaded
    environment (inheritEnv=True, readHeader=False).  Avoids re-importing
    Mathlib per proof — reduces per-proof cost from ~30s to ~1s.

    `source` should have `import ...` lines stripped; `open` and theorem body
    are kept as-is.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        invocation_file = os.path.join(tmpdir, "invocations.json")
        result = server.run("frontend.process", {
            "file": source,
            "invocations": invocation_file,
            "readHeader": False,
            "inheritEnv": True,
            "newConstants": False,
        })
        if "error" in result:
            raise ServerError(result)
        with open(invocation_file) as f:
            data_units = json.load(f)
        return [
            CompilationUnit.parse(payload, invocations=data_unit["invocations"])
            for payload, data_unit in zip(result["units"], data_units["units"])
        ]


# ─────────────────────── resume support ──────────────────────────────────────

def load_done_names(output_path: Path) -> set[str]:
    done: set[str] = set()
    if not output_path.exists():
        return done
    with output_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                name = obj.get("theorem_name", "")
                if name:
                    done.add(name)
            except json.JSONDecodeError:
                pass
    return done


# ─────────────────────── server factory ──────────────────────────────────────

STARTUP_TIMEOUT = 600  # seconds — Mathlib startup can take ~3-4 min


def make_server(project_path: str, request_timeout: int) -> Server:
    """
    Start a Pantograph server with Mathlib, then lower the per-request timeout.
    Server.__init__ uses `timeout` for the initial ready-signal wait too,
    so we pass STARTUP_TIMEOUT there and reset to request_timeout afterward.
    """
    t0 = time.time()
    print(f"[trace_dataset] Starting Pantograph server with Mathlib…", flush=True)
    server = Server(
        imports=["Mathlib"],
        project_path=project_path,
        timeout=STARTUP_TIMEOUT,
    )
    server.timeout = request_timeout  # per-request timeout from here on
    print(f"[trace_dataset] Server ready in {time.time()-t0:.1f}s", flush=True)
    return server


# ─────────────────────── main ────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Batch-trace a JSON proof dataset to proof graphs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("dataset_json", help="JSON file — list of {model, proof} dicts")
    p.add_argument("--output",   default=None)
    p.add_argument("--project",  default=None,
                   help="Lean project root (default: ProofGraph dir)")
    p.add_argument("--timeout",  type=int, default=90,
                   help="Per-request Pantograph timeout in seconds (default: 90)")
    p.add_argument("--limit",    type=int, default=None,
                   help="Only trace first N proofs")
    p.add_argument("--resume",   action="store_true",
                   help="Skip proofs already in the output file")
    p.add_argument("--verbose",  action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    dataset_path = Path(args.dataset_json).resolve()
    if not dataset_path.exists():
        print(f"Error: {dataset_path} not found", file=sys.stderr)
        sys.exit(1)

    project_path = (
        Path(args.project).resolve() if args.project
        else Path(__file__).parent.resolve()
    )

    # Output paths
    if args.output is None:
        out_dir = Path(__file__).parent / "data"
        out_dir.mkdir(exist_ok=True)
        output_path = out_dir / f"{dataset_path.stem}_graphs.jsonl"
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    failures_path = output_path.with_suffix("").with_suffix(".failures.jsonl")

    print(f"[trace_dataset] Dataset : {dataset_path}")
    print(f"[trace_dataset] Output  : {output_path}")
    print(f"[trace_dataset] Project : {project_path}")
    print(f"[trace_dataset] Timeout : {args.timeout}s per request")
    print()

    # ── load dataset ──────────────────────────────────────────────────────────
    print("[trace_dataset] Loading dataset…")
    with dataset_path.open() as f:
        dataset = json.load(f)
    print(f"[trace_dataset] {len(dataset)} proofs loaded")

    if args.limit:
        dataset = dataset[:args.limit]
        print(f"[trace_dataset] Limited to first {args.limit} proofs")

    # ── resume support ────────────────────────────────────────────────────────
    done_names: set[str] = set()
    if args.resume:
        done_names = load_done_names(output_path)
        print(f"[trace_dataset] Resuming — {len(done_names)} already traced")

    # ── start server ──────────────────────────────────────────────────────────
    server = make_server(str(project_path), args.timeout)
    n_restarts = 0

    # ── counters ──────────────────────────────────────────────────────────────
    n_ok = 0
    n_skip = 0
    n_fail = 0
    n_timeout = 0
    t_start = time.time()

    out_f  = output_path.open("a" if args.resume else "w")
    fail_f = failures_path.open("a" if args.resume else "w")

    try:
        for i, entry in enumerate(dataset):
            proof_text = entry.get("proof", "")
            name = extract_theorem_name(proof_text) or f"proof_{i}"

            elapsed = time.time() - t_start
            processed = n_ok + n_fail + n_timeout
            rate = processed / max(elapsed, 1)
            print(
                f"[{i+1}/{len(dataset)}] {name[:55]:<55}  "
                f"ok={n_ok} fail={n_fail} to={n_timeout} restart={n_restarts}  "
                f"{rate:.2f}/s",
                end="\r", flush=True,
            )

            if args.resume and name in done_names:
                n_skip += 1
                continue

            # Strip imports — server already has Mathlib in env (inheritEnv=True)
            lean_src = strip_imports(proof_text)

            try:
                units = tactic_invocations_reuse_env(server, lean_src)

                graphs_for_proof = []
                for unit in units:
                    if not unit.invocations:
                        continue
                    pg = _build_graph_from_invocations(name, unit.invocations)
                    if pg.G.number_of_nodes() > 0:
                        graphs_for_proof.append(pg)

                if graphs_for_proof:
                    pg = graphs_for_proof[0]
                    out_f.write(json.dumps(pg.to_dict(), ensure_ascii=False) + "\n")
                    out_f.flush()
                    n_ok += 1
                    if args.verbose:
                        st = pg.stats()
                        print(f"\n  -> goals={st['n_goals']} tactics={st['n_tactics']} branch={st['max_branching']}")
                else:
                    _log_fail(fail_f, name, "no_invocations", i)
                    n_fail += 1

            except ServerError as e:
                msg = str(e)
                if "timeout" in msg.lower():
                    _log_fail(fail_f, name, f"timeout: {msg[:100]}", i)
                    n_timeout += 1
                else:
                    _log_fail(fail_f, name, msg[:200], i)
                    n_fail += 1

                # Server process was killed by Pantograph's timeout/error handling.
                # Restart it to recover (costs ~2min but prevents cascading failures).
                if server.proc is None:
                    print(f"\n[trace_dataset] Server died — restarting…", flush=True)
                    try:
                        server = make_server(str(project_path), args.timeout)
                        n_restarts += 1
                    except Exception as restart_err:
                        print(f"[trace_dataset] Restart failed: {restart_err}", file=sys.stderr)
                        break

            except Exception as e:
                _log_fail(fail_f, name, str(e)[:200], i)
                n_fail += 1
                # If server proc is dead, restart
                if server.proc is None:
                    print(f"\n[trace_dataset] Server died — restarting…", flush=True)
                    try:
                        server = make_server(str(project_path), args.timeout)
                        n_restarts += 1
                    except Exception as restart_err:
                        print(f"[trace_dataset] Restart failed: {restart_err}", file=sys.stderr)
                        break

    finally:
        out_f.close()
        fail_f.close()

    # ── summary ───────────────────────────────────────────────────────────────
    total_elapsed = time.time() - t_start
    processed = n_ok + n_fail + n_timeout
    print()
    print("=" * 60)
    print(f"Traced {len(dataset)} proof entries in {total_elapsed:.1f}s")
    print(f"  success   : {n_ok}")
    print(f"  skipped   : {n_skip}  (already done)")
    print(f"  failed    : {n_fail}  (no invocations / server error)")
    print(f"  timed out : {n_timeout}  (>{args.timeout}s)")
    print(f"  restarts  : {n_restarts}")
    print(f"  rate      : {processed / max(total_elapsed, 1):.2f} proofs/s")
    print()
    print(f"Output    → {output_path}")
    print(f"Failures  → {failures_path}")


def _log_fail(fail_f, name: str, reason: str, index: int) -> None:
    fail_f.write(json.dumps({"theorem_name": name, "reason": reason, "index": index}) + "\n")
    fail_f.flush()


if __name__ == "__main__":
    main()
