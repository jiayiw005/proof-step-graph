#!/usr/bin/env bash
# =============================================================================
# setup.sh — Set up the proof-step-graph environment
#
# Run from repo root:
#   bash scripts/setup.sh
#
# What this does:
#   1. Build the PyPantograph REPL binary (Lean compilation)
#   2. Create a Python venv and install dependencies (via uv)
#   3. Build/fetch Mathlib cache for the Lean project
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
echo "  proof-step-graph setup"
echo "============================================"
echo "  Root:         $ROOT_DIR"
echo "  PyPantograph: $PYPANTOGRAPH_DIR"
echo ""

# ── 1. Build PyPantograph REPL ───────────────────────────────────────────────
echo "[1/3] Building PyPantograph REPL (Lean compilation)..."

if [ ! -d "$PYPANTOGRAPH_DIR" ]; then
    echo "  ERROR: PyPantograph not found at $PYPANTOGRAPH_DIR"
    echo "  Run: git submodule update --init"
    exit 1
fi

REPL_BIN="${PYPANTOGRAPH_DIR}/pantograph/pantograph-repl"
if [ -f "$REPL_BIN" ]; then
    echo "  pantograph-repl already built, skipping."
else
    echo "  Running uv build in $PYPANTOGRAPH_DIR ..."
    (cd "$PYPANTOGRAPH_DIR" && uv build)
    if [ ! -f "$REPL_BIN" ]; then
        echo ""
        echo "  NOTE: REPL binary not found after uv build."
        echo "  Trying lake build directly..."
        (cd "$PYPANTOGRAPH_DIR" && lake build)
        LAKE_BIN="${PYPANTOGRAPH_DIR}/.lake/build/bin/pantograph-repl"
        if [ -f "$LAKE_BIN" ]; then
            cp "$LAKE_BIN" "$REPL_BIN"
            echo "  Copied REPL binary from .lake/build/bin/"
        else
            echo "  WARNING: Could not locate pantograph-repl binary."
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
echo "  Python env ready."
echo ""

# ── 3. Lean project build (Mathlib cache) ───────────────────────────────────
echo "[3/3] Fetching Mathlib cache..."

if ! command -v lake &>/dev/null; then
    echo "  WARNING: lake not found, skipping."
else
    LEAN_VERSION=$(cat "${ROOT_DIR}/lean-toolchain" | tr -d '[:space:]')
    echo "  Lean toolchain: $LEAN_VERSION"
    elan toolchain install "$LEAN_VERSION" 2>/dev/null || true
    lake exe cache get || echo "  (cache fetch failed — lake build may take a while)"
    lake build || echo "  (build failed — check lakefile.toml and internet access)"
fi
echo ""

echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "  Verify setup:"
echo "    uv run python trace_file.py your_file.lean --imports Init"
echo ""
