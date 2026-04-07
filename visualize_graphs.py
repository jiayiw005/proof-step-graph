from __future__ import annotations

import argparse
import json
import math
import os
import sys
import webbrowser
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import networkx as nx
from pyvis.network import Network

sys.path.insert(0, str(Path(__file__).parent))
from proof_graph.graph import ProofGraph, NODE_GOAL, NODE_TACTIC, EDGE_INPUT, EDGE_OUTPUT


# ─────────────────────── colours / styles ────────────────────────────────────

GOAL_COLOR   = "#4C9BE8"   # blue
TACTIC_COLOR = "#F4A261"   # orange
INITIAL_COLOR = "#2A9D8F"  # teal — initial goals
EDGE_INPUT_COLOR  = "#888888"
EDGE_OUTPUT_COLOR = "#E76F51"


def _truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


# ─────────────────────── hierarchical layout ─────────────────────────────────

def _hierarchical_pos(G: nx.DiGraph, root_nodes: list[str]) -> dict[str, tuple[float, float]]:
    """
    Assign (x, y) positions using BFS levels from root_nodes.
    Roots are at y=0, their children at y=-1, etc.
    Within each level, nodes are spread evenly on x.
    """
    level: dict[str, int] = {}
    queue = list(root_nodes)
    for r in queue:
        level[r] = 0

    while queue:
        nxt = []
        for n in queue:
            for child in G.successors(n):
                if child not in level:
                    level[child] = level[n] + 1
                    nxt.append(child)
        queue = nxt

    # Assign any unreached nodes (disconnected) at a level below max
    if level:
        max_level = max(level.values()) + 1
    else:
        max_level = 0
    for n in G.nodes():
        if n not in level:
            level[n] = max_level

    # Group nodes by level
    by_level: dict[int, list[str]] = {}
    for n, lv in level.items():
        by_level.setdefault(lv, []).append(n)

    pos: dict[str, tuple[float, float]] = {}
    for lv, nodes in by_level.items():
        for i, n in enumerate(nodes):
            x = (i - (len(nodes) - 1) / 2.0) * 2.0
            y = -float(lv) * 1.5
            pos[n] = (x, y)
    return pos


# ─────────────────────── pyvis HTML ──────────────────────────────────────────

def save_html(pg: ProofGraph, path: Path, max_label: int = 40) -> None:
    net = Network(
        height="750px", width="100%",
        directed=True,
        bgcolor="#1a1a2e",
        font_color="white",
    )
    # Hierarchical layout (left-to-right)
    net.set_options("""
    {
      "layout": {
        "hierarchical": {
          "enabled": true,
          "direction": "LR",
          "sortMethod": "directed",
          "nodeSpacing": 130,
          "levelSeparation": 200
        }
      },
      "physics": { "enabled": false },
      "edges": {
        "arrows": { "to": { "enabled": true, "scaleFactor": 0.8 } },
        "smooth": { "type": "cubicBezier" }
      },
      "nodes": {
        "font": { "size": 13 },
        "borderWidth": 2
      },
      "interaction": {
        "navigationButtons": true,
        "keyboard": true,
        "hover": true
      }
    }
    """)

    for node_id, data in pg.G.nodes(data=True):
        ntype = data.get("type")
        if ntype == NODE_GOAL:
            target = _truncate(data.get("target", ""), max_label)
            case = data.get("case_name")
            label = f"case {case}\n{target}" if case else target
            color = INITIAL_COLOR if data.get("is_initial") else GOAL_COLOR
            shape = "ellipse"
            title = (
                f"<b>Goal</b><br>"
                f"<code>{data.get('target', '')}</code><br>"
                + (f"case: {case}<br>" if case else "")
                + (f"vars: {'; '.join(data.get('variables', []))}<br>" if data.get('variables') else "")
                + ("🟢 initial" if data.get("is_initial") else "")
            )
        else:
            tac = _truncate(data.get("tactic", ""), max_label)
            label = tac
            color = TACTIC_COLOR
            shape = "box"
            used = data.get("used_constants", [])
            title = (
                f"<b>Tactic [{data.get('step_idx', '?')}]</b><br>"
                f"<code>{data.get('tactic', '')}</code><br>"
                + (f"uses: {', '.join(used[:5])}" if used else "")
            )
        net.add_node(
            node_id,
            label=label,
            color=color,
            shape=shape,
            title=title,
            size=20 if ntype == NODE_GOAL else 15,
        )

    for src, dst, data in pg.G.edges(data=True):
        etype = data.get("type")
        color = EDGE_OUTPUT_COLOR if etype == EDGE_OUTPUT else EDGE_INPUT_COLOR
        net.add_edge(src, dst, color=color, width=2)

    # Write HTML with title
    html = net.generate_html()
    header = f"<title>ProofGraph: {pg.theorem_name}</title>"
    html = html.replace("<head>", f"<head>\n{header}\n", 1)
    path.write_text(html)


# ─────────────────────── matplotlib static ───────────────────────────────────

def _dot_layout(G: nx.DiGraph) -> dict[str, tuple[float, float]]:
    """Use graphviz dot for a proper DAG layout (top-to-bottom, source at top)."""
    try:
        # pydot_layout returns coords with y increasing upward (dot's convention).
        # matplotlib also has y increasing upward, so no flip needed —
        # source nodes (roots) get the highest y and appear at the top.
        pos = nx.drawing.nx_pydot.pydot_layout(G, prog="dot")
        if pos:
            return pos
    except Exception:
        pass
    # Fallback: BFS hierarchical
    roots = [n for n in G.nodes() if G.in_degree(n) == 0]
    return _hierarchical_pos(G, roots)


def save_static(pg: ProofGraph, path: Path, max_label: int = 40) -> None:
    G = pg.G
    if G.number_of_nodes() == 0:
        return

    pos = _dot_layout(G)

    goal_nodes   = [n for n, d in G.nodes(data=True) if d.get("type") == NODE_GOAL and not d.get("is_initial")]
    init_nodes   = [n for n, d in G.nodes(data=True) if d.get("type") == NODE_GOAL and d.get("is_initial")]
    tactic_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == NODE_TACTIC]
    input_edges  = [(u, v) for u, v, d in G.edges(data=True) if d.get("type") == EDGE_INPUT]
    output_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("type") == EDGE_OUTPUT]

    # Size: scale with graph, wider for larger ones
    n = G.number_of_nodes()
    xs = [x for x, y in pos.values()]
    ys = [y for x, y in pos.values()]
    x_span = max(xs) - min(xs) + 1
    y_span = max(ys) - min(ys) + 1
    fig_w = max(10, min(x_span / 60, 40))
    fig_h = max(6,  min(y_span / 60, 60))

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_facecolor("#1a1a2e")
    fig.patch.set_facecolor("#1a1a2e")
    ax.axis("off")

    node_size = max(300, 1200 - n * 6)

    nx.draw_networkx_nodes(G, pos, nodelist=init_nodes,   node_color=INITIAL_COLOR, node_shape="o", ax=ax, node_size=int(node_size * 1.2))
    nx.draw_networkx_nodes(G, pos, nodelist=goal_nodes,   node_color=GOAL_COLOR,    node_shape="o", ax=ax, node_size=node_size)
    nx.draw_networkx_nodes(G, pos, nodelist=tactic_nodes, node_color=TACTIC_COLOR,  node_shape="s", ax=ax, node_size=int(node_size * 0.85))

    nx.draw_networkx_edges(G, pos, edgelist=input_edges,  edge_color=EDGE_INPUT_COLOR,
                           ax=ax, arrows=True, arrowsize=12, width=1.2,
                           connectionstyle="arc3,rad=0.05")
    nx.draw_networkx_edges(G, pos, edgelist=output_edges, edge_color=EDGE_OUTPUT_COLOR,
                           ax=ax, arrows=True, arrowsize=12, width=1.2,
                           connectionstyle="arc3,rad=0.05")

    font_size = max(5, 9 - int(n / 20))
    label_len = max(15, max_label - int(n / 5))
    goal_labels   = {n: _truncate(G.nodes[n].get("target", n), label_len) for n in goal_nodes + init_nodes}
    tactic_labels = {n: _truncate(G.nodes[n].get("tactic", n), label_len) for n in tactic_nodes}
    nx.draw_networkx_labels(G, pos, labels=goal_labels,   font_size=font_size, font_color="white", ax=ax)
    nx.draw_networkx_labels(G, pos, labels=tactic_labels, font_size=font_size, font_color="#1a1a2e", ax=ax)

    legend_items = [
        mpatches.Patch(color=INITIAL_COLOR, label="initial goal"),
        mpatches.Patch(color=GOAL_COLOR,    label="goal"),
        mpatches.Patch(color=TACTIC_COLOR,  label="tactic"),
        mpatches.Patch(color=EDGE_INPUT_COLOR,  label="goal→tactic"),
        mpatches.Patch(color=EDGE_OUTPUT_COLOR, label="tactic→goal"),
    ]
    ax.legend(handles=legend_items, loc="upper left", fontsize=8,
              facecolor="#2a2a4e", labelcolor="white", framealpha=0.8)
    ax.set_title(pg.theorem_name, color="white", fontsize=13, pad=12)

    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


# ─────────────────────── main ────────────────────────────────────────────────

def load_graphs(jsonl_path: Path, names: list[str] | None = None) -> list[ProofGraph]:
    graphs = []
    with jsonl_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            pg = ProofGraph.from_dict(json.loads(line))
            if names is None or pg.theorem_name in names:
                graphs.append(pg)
    return graphs


def main() -> None:
    p = argparse.ArgumentParser(
        description="Visualize proof graphs as interactive HTML or static PNG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("jsonl_file")
    p.add_argument("--output-dir", default="proof_evals/viz")
    p.add_argument("--names", default=None, help="Comma-separated theorem names")
    p.add_argument("--static", action="store_true", help="Only static matplotlib PNG")
    p.add_argument("--html",   action="store_true", help="Only interactive HTML")
    p.add_argument("--open",   action="store_true", help="Open HTML in browser")
    p.add_argument("--max-label", type=int, default=40)
    args = p.parse_args()

    jsonl_path = Path(args.jsonl_file)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    names = [n.strip() for n in args.names.split(",")] if args.names else None
    graphs = load_graphs(jsonl_path, names)

    if not graphs:
        print(f"No graphs found in {jsonl_path}" + (f" matching {names}" if names else ""))
        sys.exit(1)

    do_html   = not args.static
    do_static = not args.html

    opened = []
    for pg in graphs:
        safe = pg.theorem_name.replace("/", "_")
        stats = pg.stats()
        print(f"[viz] {pg.theorem_name}: {stats['n_goals']} goals, {stats['n_tactics']} tactics, branch={stats['max_branching']}")

        if do_html:
            html_path = out_dir / f"{safe}.html"
            save_html(pg, html_path, args.max_label)
            print(f"      HTML  → {html_path}")
            opened.append(html_path)

        if do_static:
            png_path = out_dir / f"{safe}.png"
            save_static(pg, png_path, args.max_label)
            print(f"      PNG   → {png_path}")

    if args.open and opened:
        for p in opened:
            webbrowser.open(f"file://{p.resolve()}")


if __name__ == "__main__":
    main()
