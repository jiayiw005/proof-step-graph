from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from proof_graph.graph import ProofGraph, NODE_GOAL, NODE_TACTIC


def load_graphs(jsonl_path: Path) -> list[ProofGraph]:
    graphs = []
    with jsonl_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                graphs.append(ProofGraph.from_dict(json.loads(line)))
    return graphs


def compute_stats(graphs: list[ProofGraph]) -> pd.DataFrame:
    rows = [pg.stats() for pg in graphs]
    return pd.DataFrame(rows)


def print_summary(df: pd.DataFrame) -> None:
    print(f"{'='*55}")
    print(f"  Proof Graph Summary  ({len(df)} theorems)")
    print(f"{'='*55}")
    numeric = df.select_dtypes(include="number")
    for col in numeric.columns:
        print(f"  {col:<25} mean={df[col].mean():.2f}  max={df[col].max()}")
    print(f"  is_dag ratio: {df['is_dag'].mean():.2%}")
    print()


def plot_distributions(df: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = ["n_goals", "n_tactics", "n_initial_goals", "max_branching", "avg_branching"]

    fig, axes = plt.subplots(1, len(cols), figsize=(4 * len(cols), 4))
    for ax, col in zip(axes, cols):
        ax.hist(df[col].dropna(), bins=20, edgecolor="black")
        ax.set_title(col)
        ax.set_xlabel("value")
        ax.set_ylabel("count")
    fig.tight_layout()
    path = out_dir / "proof_graph_distributions.pdf"
    fig.savefig(path)
    print(f"[analyze] Saved → {path}")
    plt.close(fig)


def plot_branching_vs_depth(df: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(df["n_tactics"], df["max_branching"], alpha=0.5, s=20)
    ax.set_xlabel("proof depth (# tactics)")
    ax.set_ylabel("max branching factor")
    ax.set_title("Branching vs Depth")
    fig.tight_layout()
    path = out_dir / "proof_graph_branching_depth.pdf"
    fig.savefig(path)
    print(f"[analyze] Saved → {path}")
    plt.close(fig)


def tactic_frequency(graphs: list[ProofGraph], top_n: int = 20) -> Counter:
    counter: Counter = Counter()
    for pg in graphs:
        for _, d in pg.tactic_nodes():
            # Take the first token (tactic name) from the tactic string
            tac = d["tactic"].split()[0].rstrip(";").rstrip("<;>")
            counter[tac] += 1
    return counter


def plot_tactic_freq(graphs: list[ProofGraph], out_dir: Path, top_n: int = 20) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    freq = tactic_frequency(graphs, top_n)
    top = freq.most_common(top_n)
    names, counts = zip(*top) if top else ([], [])

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.barh(names, counts, edgecolor="black")
    ax.set_xlabel("frequency")
    ax.set_title(f"Top-{top_n} tactic names")
    ax.invert_yaxis()
    fig.tight_layout()
    path = out_dir / "proof_graph_tactic_freq.pdf"
    fig.savefig(path)
    print(f"[analyze] Saved → {path}")
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser(description="Analyze proof graphs from JSONL")
    p.add_argument("jsonl_file", help="JSONL file from trace_file.py")
    p.add_argument("--output-dir", default="proof_evals")
    p.add_argument("--no-plots", action="store_true")
    args = p.parse_args()

    jsonl_path = Path(args.jsonl_file)
    if not jsonl_path.exists():
        print(f"Error: {jsonl_path} not found", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)
    print(f"[analyze] Loading graphs from {jsonl_path}...")
    graphs = load_graphs(jsonl_path)
    print(f"[analyze] Loaded {len(graphs)} proof graphs\n")

    df = compute_stats(graphs)
    print_summary(df)

    # Save CSV
    csv_path = out_dir / f"{jsonl_path.stem}_stats.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)
    print(f"[analyze] Stats CSV → {csv_path}\n")

    if not args.no_plots:
        plot_distributions(df, out_dir)
        plot_branching_vs_depth(df, out_dir)
        plot_tactic_freq(graphs, out_dir)


if __name__ == "__main__":
    main()
