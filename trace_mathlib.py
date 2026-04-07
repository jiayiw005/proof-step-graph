from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "PyPantograph"))
sys.path.insert(0, str(Path(__file__).parent))

from pantograph.server import Server, ServerError
from pantograph.data import CompilationUnit

from proof_step_graph.tracer import _build_graph_from_invocations
from proof_step_graph.parse_lean import extract_theorems


# ─────────────────────── file discovery ──────────────────────────────────────

MATHLIB_PATHS = [
    Path(".lake/packages/mathlib/Mathlib"),
    Path("lake-packages/mathlib/Mathlib"),
]


def find_mathlib_root() -> Path:
    for p in MATHLIB_PATHS:
        if p.is_dir():
            return p
    print("ERROR: Mathlib source not found. Run `lake build` first.", file=sys.stderr)
    print(f"  Searched: {[str(p) for p in MATHLIB_PATHS]}", file=sys.stderr)
    sys.exit(1)


def discover_files(mathlib_root: Path, filter_prefix: str | None) -> list[Path]:
    """Find all .lean files under mathlib_root, optionally filtered by subpath."""
    if filter_prefix:
        # e.g. "Topology" -> Mathlib/Topology/**/*.lean
        # e.g. "Algebra.Group" -> Mathlib/Algebra/Group/**/*.lean
        subpath = mathlib_root / filter_prefix.replace(".", "/")
        if subpath.is_file():
            return [subpath]
        if not subpath.is_dir():
            # Try as a .lean file
            lean_file = subpath.with_suffix(".lean")
            if lean_file.is_file():
                return [lean_file]
            print(f"ERROR: {subpath} not found (tried as dir and .lean file)", file=sys.stderr)
            sys.exit(1)
        root = subpath
    else:
        root = mathlib_root

    files = sorted(root.rglob("*.lean"))
    return files


# ─────────────────────── server ──────────────────────────────────────────────

STARTUP_TIMEOUT = 600


def make_server(project_path: str, request_timeout: int) -> Server:
    t0 = time.time()
    print(f"[trace_mathlib] Starting Pantograph server with Mathlib...", flush=True)
    server = Server(
        imports=["Mathlib"],
        project_path=project_path,
        timeout=STARTUP_TIMEOUT,
    )
    server.timeout = request_timeout
    print(f"[trace_mathlib] Server ready in {time.time()-t0:.1f}s", flush=True)
    return server


# ─────────────────────── resume ──────────────────────────────────────────────

def load_done_files(output_path: Path) -> set[str]:
    """Collect all source_file values already in the output JSONL."""
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
                src = obj.get("source_file", "")
                if src:
                    done.add(src)
            except json.JSONDecodeError:
                pass
    return done


# ─────────────────────── tracing ─────────────────────────────────────────────

def trace_lean_file(
    server: Server,
    lean_file: Path,
    mathlib_root: Path,
) -> list[dict]:
    """
    Trace a single .lean file. Returns list of serialized ProofStepGraph dicts,
    each annotated with source_file.
    """
    # Parse source to get theorem names + byte offsets
    source = lean_file.read_text()
    theorems = extract_theorems(source)

    # Build offset -> name map
    offset_to_name: dict[int, str] = {}
    for t in theorems:
        offset_to_name[t.byte_offset] = t.name

    # Get tactic invocations via Pantograph
    units: list[CompilationUnit] = server.tactic_invocations(str(lean_file))

    # Relative path for output annotation (e.g. "Mathlib/Topology/Basic.lean")
    rel_path = str(lean_file.relative_to(mathlib_root.parent))

    results = []
    for idx, unit in enumerate(units):
        if not unit.invocations:
            continue
        # Match unit to theorem name by byte offset
        name = None
        for byte_off, thm_name in offset_to_name.items():
            if unit.i_begin <= byte_off < unit.i_end:
                name = thm_name
                break
        if name is None:
            name = f"theorem_{idx}"

        pg = _build_graph_from_invocations(name, unit.invocations)
        if pg.G.number_of_nodes() > 0:
            d = pg.to_dict()
            d["source_file"] = rel_path
            results.append(d)

    return results


# ─────────────────────── main ────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Trace Mathlib .lean files to proof step graphs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  uv run python trace_mathlib.py --filter Topology\n"
               "  uv run python trace_mathlib.py --filter Algebra.Group --resume\n"
               "  uv run python trace_mathlib.py --limit 50\n",
    )
    p.add_argument("--filter",  default=None,
                   help="Mathlib subpath to trace (e.g. Topology, Algebra.Group)")
    p.add_argument("--output",  default=None,
                   help="Output JSONL path (default: data/mathlib_<filter>_graphs.jsonl)")
    p.add_argument("--project", default=".",
                   help="Lean project root (default: .)")
    p.add_argument("--timeout", type=int, default=120,
                   help="Per-request Pantograph timeout (default: 120s)")
    p.add_argument("--limit",   type=int, default=None,
                   help="Only trace first N files")
    p.add_argument("--resume",  action="store_true",
                   help="Skip files already in the output")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    mathlib_root = find_mathlib_root()
    files = discover_files(mathlib_root, args.filter)

    if args.limit:
        files = files[:args.limit]

    if not files:
        print("No .lean files found.", file=sys.stderr)
        sys.exit(1)

    # Output path
    if args.output:
        output_path = Path(args.output)
    else:
        out_dir = Path("data")
        out_dir.mkdir(exist_ok=True)
        tag = args.filter.replace(".", "_").replace("/", "_") if args.filter else "all"
        output_path = out_dir / f"mathlib_{tag}_graphs.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    failures_path = output_path.with_suffix("").with_suffix(".failures.jsonl")

    print(f"[trace_mathlib] Mathlib root : {mathlib_root}")
    print(f"[trace_mathlib] Filter       : {args.filter or '(all)'}")
    print(f"[trace_mathlib] Files        : {len(files)}")
    print(f"[trace_mathlib] Output       : {output_path}")
    print(f"[trace_mathlib] Timeout      : {args.timeout}s per request")
    print()

    # Resume
    done_files: set[str] = set()
    if args.resume:
        done_files = load_done_files(output_path)
        print(f"[trace_mathlib] Resuming — {len(done_files)} files already traced")

    # Server
    server = make_server(args.project, args.timeout)
    n_restarts = 0

    # Counters
    n_files_ok = 0
    n_files_skip = 0
    n_files_fail = 0
    n_graphs = 0
    t_start = time.time()

    out_f  = output_path.open("a" if args.resume else "w")
    fail_f = failures_path.open("a" if args.resume else "w")

    try:
        for i, lean_file in enumerate(files):
            rel_path = str(lean_file.relative_to(mathlib_root.parent))

            elapsed = time.time() - t_start
            rate = n_files_ok / max(elapsed, 1)
            print(
                f"[{i+1}/{len(files)}] {rel_path:<60}  "
                f"ok={n_files_ok} fail={n_files_fail} graphs={n_graphs} restart={n_restarts}  "
                f"{rate:.2f} files/s",
                end="\r", flush=True,
            )

            # Skip if already traced
            if args.resume and rel_path in done_files:
                n_files_skip += 1
                continue

            try:
                results = trace_lean_file(server, lean_file, mathlib_root)
                for d in results:
                    out_f.write(json.dumps(d, ensure_ascii=False) + "\n")
                out_f.flush()
                n_files_ok += 1
                n_graphs += len(results)

                if args.verbose and results:
                    print(f"\n  -> {len(results)} graphs from {rel_path}")

            except ServerError as e:
                msg = str(e)[:200]
                fail_f.write(json.dumps({"file": rel_path, "error": msg}) + "\n")
                fail_f.flush()
                n_files_fail += 1

                if server.proc is None:
                    print(f"\n[trace_mathlib] Server died — restarting...", flush=True)
                    try:
                        server = make_server(args.project, args.timeout)
                        n_restarts += 1
                    except Exception as ex:
                        print(f"[trace_mathlib] Restart failed: {ex}", file=sys.stderr)
                        break

            except Exception as e:
                fail_f.write(json.dumps({"file": rel_path, "error": str(e)[:200]}) + "\n")
                fail_f.flush()
                n_files_fail += 1

                if server.proc is None:
                    print(f"\n[trace_mathlib] Server died — restarting...", flush=True)
                    try:
                        server = make_server(args.project, args.timeout)
                        n_restarts += 1
                    except Exception as ex:
                        print(f"[trace_mathlib] Restart failed: {ex}", file=sys.stderr)
                        break

    finally:
        out_f.close()
        fail_f.close()

    # Summary
    total_elapsed = time.time() - t_start
    print()
    print("=" * 60)
    print(f"Traced {len(files)} Mathlib files in {total_elapsed:.1f}s")
    print(f"  files ok  : {n_files_ok}")
    print(f"  skipped   : {n_files_skip}  (already done)")
    print(f"  failed    : {n_files_fail}")
    print(f"  restarts  : {n_restarts}")
    print(f"  graphs    : {n_graphs}")
    print(f"  rate      : {n_files_ok / max(total_elapsed, 1):.2f} files/s")
    print()
    print(f"Output   -> {output_path}")
    print(f"Failures -> {failures_path}")


if __name__ == "__main__":
    main()
