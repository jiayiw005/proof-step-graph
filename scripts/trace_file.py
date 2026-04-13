from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Allow running from anywhere — repo root is one level up from scripts/
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "PyPantograph"))
sys.path.insert(0, str(_ROOT))

from pantograph.server import Server

from proof_step_graph.tracer import StaticProofTracer, InteractiveProofTracer
from proof_step_graph.parse_lean import extract_theorems


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Trace Lean proofs to proof graphs via PyPantograph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("lean_file", help="Path to the .lean source file")
    p.add_argument("--output",      default=None,   help="Output JSONL path")
    p.add_argument("--project",     default=None,   help="Lean project path (for external imports)")
    p.add_argument("--imports",     default="Init", help="Comma-separated Lean modules")
    p.add_argument("--interactive", action="store_true",
                   help="Use interactive replay (slower, exact goal IDs)")
    p.add_argument("--timeout",     type=int, default=120, help="Server timeout in seconds")
    p.add_argument("--verbose",     action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    lean_file = Path(args.lean_file).resolve()
    if not lean_file.exists():
        print(f"Error: {lean_file} not found", file=sys.stderr)
        sys.exit(1)

    imports = [m.strip() for m in args.imports.split(",")]

    # Default output path
    if args.output is None:
        out_dir = _ROOT / "data"
        out_dir.mkdir(exist_ok=True)
        output_path = out_dir / f"{lean_file.stem}_graphs.jsonl"
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[trace_file] Source  : {lean_file}")
    print(f"[trace_file] Output  : {output_path}")
    print(f"[trace_file] Imports : {imports}")
    print(f"[trace_file] Mode    : {'interactive' if args.interactive else 'static'}")
    print()

    # ── start server ────────────────────────────────────────────────────────
    t0 = time.time()
    print("[trace_file] Starting Pantograph server...")
    server = Server(
        imports=imports,
        project_path=args.project,
        timeout=args.timeout,
    )
    print(f"[trace_file] Server ready in {time.time()-t0:.1f}s\n")

    # ── trace ────────────────────────────────────────────────────────────────
    if args.interactive:
        theorems = extract_theorems(lean_file.read_text())
        if not theorems:
            print("No theorems found — check the Lean file.", file=sys.stderr)
            sys.exit(1)
        print(f"[trace_file] Found {len(theorems)} theorem(s) to replay interactively")
        tracer = InteractiveProofTracer(server)
        graphs = []
        for thm in theorems:
            try:
                pg = tracer.trace_theorem(
                    name=thm.name,
                    theorem_type=thm.type_str,
                    tactics=thm.tactics,
                )
                graphs.append(pg)
                if args.verbose:
                    print(f"  {pg}")
            except Exception as e:
                print(f"  SKIP {thm.name}: {e}")
    else:
        # Extract theorems via heuristic parser for byte-offset-based name matching
        theorems = extract_theorems(lean_file.read_text())
        print(f"[trace_file] Static-tracing {lean_file.name} ({len(theorems)} theorems found by parser)...")
        tracer = StaticProofTracer(server)
        graphs = tracer.trace_file_named(lean_file, theorems)
        if args.verbose:
            for pg in graphs:
                print(f"  {pg}")

    print(f"\n[trace_file] Traced {len(graphs)} proof graph(s)")

    # ── save ─────────────────────────────────────────────────────────────────
    with output_path.open("w") as f:
        for pg in graphs:
            f.write(json.dumps(pg.to_dict(), ensure_ascii=False) + "\n")
    print(f"[trace_file] Saved graphs → {output_path}")

    # Summary stats
    stats_path = output_path.with_suffix("").with_suffix(".stats.json")
    all_stats = [pg.stats() for pg in graphs]
    stats_path.write_text(json.dumps(all_stats, indent=2, ensure_ascii=False))
    print(f"[trace_file] Stats       → {stats_path}")

    if all_stats:
        avg_goals   = sum(s["n_goals"]   for s in all_stats) / len(all_stats)
        avg_tactics = sum(s["n_tactics"] for s in all_stats) / len(all_stats)
        max_branch  = max(s["max_branching"] for s in all_stats)
        print(f"\nSummary over {len(all_stats)} theorems:")
        print(f"  avg goals/proof  : {avg_goals:.1f}")
        print(f"  avg tactics/proof: {avg_tactics:.1f}")
        print(f"  max branching    : {max_branch}")


if __name__ == "__main__":
    main()
