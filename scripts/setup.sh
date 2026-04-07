#!/usr/bin/env bash
# =============================================================================
# setup.sh — Set up the ProofStepGraph environment
#
# Run from the ProofStepGraph/ directory:
#   bash scripts/setup.sh
#
# What this does:
#   1. Build the PyPantograph REPL binary (Lean compilation)
#   2. Create a Python venv and install dependencies (via uv)
#   3. Build/fetch cache for the ProofStepGraph Lean project (Mathlib)
#
# Prerequisites:
#   - elan / lean (managed by elan) installed and on PATH
#   - lake (comes with lean4)
#   - uv (https://docs.astral.sh/uv/getting-started/installation/)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PYPANTOGRAPH_DIR="${ROOT_DIR}/PyPantograph"

cd "$ROOT_DIR"

echo "============================================"
echo "  ProofStepGraph Setup"
echo "============================================"
echo "  Root:         $ROOT_DIR"
echo "  PyPantograph: $PYPANTOGRAPH_DIR"
echo ""

# ── 1. Build PyPantograph REPL ───────────────────────────────────────────────
echo "[1/3] Building PyPantograph REPL (Lean compilation)..."

if [ ! -d "$PYPANTOGRAPH_DIR" ]; then
    echo "  ERROR: PyPantograph not found at $PYPANTOGRAPH_DIR"
    echo "  Expected directory: $PYPANTOGRAPH_DIR"
    echo "  Run: git submodule update --init"
    exit 1
fi

REPL_BIN="${PYPANTOGRAPH_DIR}/pantograph/pantograph-repl"
if [ -f "$REPL_BIN" ]; then
    echo "  pantograph-repl already built, skipping."
else
    echo "  Running uv build in $PYPANTOGRAPH_DIR ..."
    (cd "$PYPANTOGRAPH_DIR" && uv build)
    # After uv build the REPL is placed under pantograph/ by build-pantograph.py
    if [ ! -f "$REPL_BIN" ]; then
        echo ""
        echo "  NOTE: REPL binary not found after uv build."
        echo "  Trying lake build directly..."
        (cd "$PYPANTOGRAPH_DIR" && lake build)
        # Copy repl binary if built under .lake/build
        LAKE_BIN="${PYPANTOGRAPH_DIR}/.lake/build/bin/pantograph-repl"
        if [ -f "$LAKE_BIN" ]; then
            cp "$LAKE_BIN" "$REPL_BIN"
            echo "  Copied REPL binary from .lake/build/bin/"
        else
            echo "  WARNING: Could not locate pantograph-repl binary."
            echo "  Interactive mode will not work until the binary is available."
        fi
    fi
fi
echo ""

# ── 2. Python environment ────────────────────────────────────────────────────
echo "[2/3] Setting up Python environment with uv..."

if ! command -v uv &>/dev/null; then
    echo "  ERROR: uv not found. Install from https://docs.astral.sh/uv/"
    exit 1
fi

uv sync
echo "  Python env ready (use: uv run python ...)"
echo ""

# ── 3. Lean project build (optional, for Mathlib) ───────────────────────────
echo "[3/3] Building ProofStepGraph Lean project..."

if ! command -v lake &>/dev/null; then
    echo "  WARNING: lake not found, skipping Lean build."
    echo "  The Init-only mode (trace_file.py --imports Init) will still work"
    echo "  as long as PyPantograph's REPL is available."
else
    LEAN_VERSION=$(cat "${ROOT_DIR}/lean-toolchain" | tr -d '[:space:]')
    echo "  Lean toolchain: $LEAN_VERSION"
    elan toolchain install "$LEAN_VERSION" 2>/dev/null || true
    echo "  Fetching Mathlib cache..."
    lake exe cache get || echo "  (cache fetch failed — lake build may take a while)"
    echo "  Building..."
    lake build ProofStepGraph || echo "  (build failed — check lakefile.toml and internet access)"
fi
echo ""

echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "  Trace a Lean file (Init-only, fast):"
echo "    uv run python trace_file.py ProofStepGraph/Demo.lean"
echo ""
echo "  Trace against Mathlib:"
echo "    uv run python trace_file.py MyFile.lean \\"
echo "      --project /path/to/mathlib-project --imports Mathlib"
echo ""
echo "  Interactive replay mode:"
echo "    uv run python trace_file.py ProofStepGraph/Demo.lean --interactive"
echo ""
echo "  Analyze output:"
echo "    uv run python analyze_graphs.py data/Demo_graphs.jsonl"
echo ""
