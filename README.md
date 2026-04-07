# proof-step-graph

Extract structured **proof step graphs** from Lean 4 tactic proofs via [PyPantograph](https://github.com/lenianiva/PyPantograph).

A proof step graph is a bipartite DAG where **goal nodes** and **tactic nodes** alternate:
- Goal -> Tactic edges represent a tactic being applied to a goal
- Tactic -> Goal edges represent new subgoals produced by a tactic

```
[initial goal] -> [tactic_0] -> [subgoal_a]
                              -> [subgoal_b] -> [tactic_1] -> ...
```

## Setup

**Prerequisites:** [elan](https://github.com/leanprover/elan) (Lean toolchain manager), [uv](https://docs.astral.sh/uv/) (Python package manager).

```bash
git clone --recurse-submodules <repo-url>
cd proof-step-graph
bash scripts/setup.sh
```

This builds the PyPantograph REPL, installs Python dependencies, and fetches the Mathlib cache.

## Usage

### Trace a single Lean file

```bash
# Against Init (fast, no Mathlib)
uv run python trace_file.py my_theorems.lean

# Against Mathlib
uv run python trace_file.py my_theorems.lean --project . --imports Mathlib

# Interactive replay (slower, exact goal IDs)
uv run python trace_file.py my_theorems.lean --interactive
```

Output: `data/<filename>_graphs.jsonl`

### Batch-trace a dataset

Input format (JSONL, one proof per line):

```jsonl
{"name": "Nat.add_comm", "proof": "import Mathlib\n\ntheorem Nat.add_comm (n m : Nat) : n + m = m + n := by\n  omega"}
{"name": "List.reverse_nil", "proof": "theorem List.reverse_nil : [].reverse = ([] : List Nat) := by rfl"}
```

- `proof` **(required)**: full Lean 4 source (imports + theorem + tactic block)
- `name` (optional): theorem name; auto-extracted from `proof` if omitted

Also accepts a JSON array of the same objects.

```bash
uv run python trace_dataset.py proofs.jsonl
uv run python trace_dataset.py proofs.jsonl --project . --timeout 120 --resume
```

Output: `data/<dataset>_graphs.jsonl` + `data/<dataset>_graphs.failures.jsonl`

`--resume` skips already-traced theorems, useful for long runs or crash recovery.

### Trace Mathlib

Walks `.lake/packages/mathlib/Mathlib/**/*.lean` and traces all theorems with server reuse. Requires `lake build` to have been run first (so Mathlib source is available).

```bash
# Trace a specific Mathlib subpackage
uv run python trace_mathlib.py --filter Topology
uv run python trace_mathlib.py --filter Algebra.Group

# Trace all of Mathlib (will take a long time)
uv run python trace_mathlib.py

# Resume after crash, limit to first 100 files
uv run python trace_mathlib.py --filter Topology --resume --limit 100
```

Output: `data/mathlib_<filter>_graphs.jsonl`

Each output entry includes a `source_file` field (e.g., `"Mathlib/Topology/Basic.lean"`) for provenance.

## Output format

Each line of the output JSONL is a serialized `ProofStepGraph`:

```json
{
  "theorem_name": "Nat.add_comm",
  "nodes": [
    {"id": "gc_abc123", "type": "goal", "target": "n + m = m + n", "variables": ["n : Nat", "m : Nat"], "case_name": null, "is_initial": true},
    {"id": "t0", "type": "tactic", "tactic": "omega", "used_constants": ["Nat.add_comm"], "step_idx": 0}
  ],
  "edges": [
    {"src": "gc_abc123", "dst": "t0", "type": "input"},
    {"src": "t0", "dst": "gc_def456", "type": "output"}
  ]
}
```

### Node types

| Type | Key fields |
|------|-----------|
| `goal` | `target` (type expression), `variables`, `case_name`, `is_initial` |
| `tactic` | `tactic` (source text), `used_constants`, `step_idx` |

### Edge types

| Type | Direction | Meaning |
|------|-----------|---------|
| `input` | goal -> tactic | Tactic applied to this goal |
| `output` | tactic -> goal | Tactic produced this subgoal |

## Python API

```python
from proof_step_graph import ProofStepGraph

# Load from JSONL
import json
with open("data/my_graphs.jsonl") as f:
    for line in f:
        pg = ProofStepGraph.from_dict(json.loads(line))
        print(pg.theorem_name, pg.stats())

        # Iterate nodes
        for node_id, data in pg.goal_nodes():
            print(data["target"])
        for node_id, data in pg.tactic_nodes():
            print(data["tactic"])

        # Access the underlying networkx DiGraph
        G = pg.G
```

## Project structure

```
proof-step-graph/
  proof_step_graph/       # Python package
    graph.py              #   ProofStepGraph data model (networkx DAG)
    tracer.py             #   StaticProofTracer, InteractiveProofTracer
    parse_lean.py         #   Lean source parser, goal block parser
  trace_file.py           # CLI: trace a single .lean file
  trace_dataset.py        # CLI: batch-trace a JSONL dataset
  trace_mathlib.py        # CLI: trace Mathlib source files
  proof_evals/            # Analysis & visualization notebooks
  scripts/
    setup.sh              # One-command setup
  PyPantograph/           # Git submodule — Lean 4 REPL server
  lakefile.toml           # Lean project config (pins Mathlib version)
  pyproject.toml          # Python project config
```

## How it works

1. **PyPantograph** starts a Lean 4 REPL server with the specified imports (e.g., Mathlib)
2. **Static tracing** (`trace_file.py`, `trace_dataset.py`): sends Lean source through `frontend.process` to get `TacticInvocation` records (before/after goal states per tactic step), then assembles these into a graph. Goal identity is content-based (hash of goal text).
3. **Interactive tracing** (`trace_file.py --interactive`): replays tactics one-by-one via `goal_start`/`goal_tactic`, giving exact goal identity via Lean metavar names. Slower but precise.
