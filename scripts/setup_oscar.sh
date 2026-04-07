#!/usr/bin/env bash
# =============================================================================
# setup_oscar.sh — Set up ProofGraph on Brown's OSCAR cluster
#
# Run on OSCAR login node (or interactive session):
#   bash scripts/setup_oscar.sh
#
# Assumes:
#   - lean_setup.sh has already been run (elan + Mathlib ready)
#   - ProofGraph/ and PyPantograph/ have been rsync'd to $SCRATCH
#
# =============================================================================

set -euo pipefail

export SCRATCH_DIR="/users/${USER}/scratch"
export PROJECT_DIR="${SCRATCH_DIR}/proof_graph"
export PYPANTOGRAPH_DIR="${PROJECT_DIR}/PyPantograph"

# ── Lean/elan on scratch ─────────────────────────────────────────────────────
export ELAN_HOME="${SCRATCH_DIR}/.elan"
export XDG_CACHE_HOME="${SCRATCH_DIR}/.cache"
export PATH="${ELAN_HOME}/bin:${SCRATCH_DIR}/bin:${PATH}"

if [ -f /etc/pki/tls/certs/ca-bundle.crt ]; then
    export CURL_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt
elif [ -f /etc/ssl/cert.pem ]; then
    export CURL_CA_BUNDLE=/etc/ssl/cert.pem
fi

echo "============================================"
echo "  ProofGraph Setup on OSCAR"
echo "============================================"
echo "  User:          ${USER}"
echo "  Project:       ${PROJECT_DIR}"
echo "  PyPantograph:  ${PYPANTOGRAPH_DIR}"
echo ""

# ── 1. Verify directories ────────────────────────────────────────────────────
for d in "$PROJECT_DIR" "$PYPANTOGRAPH_DIR"; do
    if [ ! -d "$d" ]; then
        echo "ERROR: $d not found."
        echo "  Run: git submodule update --init"
        exit 1
    fi
done

# ── 2. Build PyPantograph REPL ───────────────────────────────────────────────
echo "[1/3] Building PyPantograph REPL..."
REPL_BIN="${PYPANTOGRAPH_DIR}/pantograph/pantograph-repl"
if [ -f "$REPL_BIN" ]; then
    echo "  REPL already built."
else
    cd "$PYPANTOGRAPH_DIR"
    lake build Pantograph
    LAKE_BIN="${PYPANTOGRAPH_DIR}/.lake/build/bin/pantograph-repl"
    if [ -f "$LAKE_BIN" ]; then
        cp "$LAKE_BIN" "$REPL_BIN"
        echo "  Copied REPL binary."
    else
        echo "  WARNING: could not build REPL binary."
    fi
fi
echo ""

# ── 3. Python env ────────────────────────────────────────────────────────────
echo "[2/3] Setting up Python environment..."
cd "$PROJECT_DIR"

if ! command -v uv &>/dev/null; then
    echo "  Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.cargo/bin:${PATH}"
fi

uv sync
echo "  Done."
echo ""

# ── 4. ProofGraph Lean project ───────────────────────────────────────────────
echo "[3/3] Building ProofGraph Lean project..."
cd "$PROJECT_DIR"

LEAN_VERSION=$(cat lean-toolchain | tr -d '[:space:]')
echo "  Toolchain: $LEAN_VERSION"
elan toolchain install "$LEAN_VERSION" || true
lake update
lake exe cache get || true
lake build ProofGraph || echo "  (lake build failed)"
echo ""

echo "============================================"
echo "  OSCAR Setup Complete!"
echo "============================================"
echo ""
echo "  Next: sbatch a job that calls"
echo "    uv run python trace_file.py <file> --project . --imports Mathlib"
echo ""
