from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LeanTheorem:
    name: str
    type_str: str           # the proposition (everything between `:` and `:= by`)
    tactics: list[str]      # individual tactic strings from the by-block
    source_range: tuple[int, int] = field(default=(0, 0))  # (start_line, end_line)
    byte_offset: int = field(default=0)  # byte offset of the `theorem`/`lemma` keyword


# ─────────────────────── goal string parsing ─────────────────────────────────

def parse_goal_block(goal_str: str) -> list[dict]:
    """
    Parse a multi-goal pretty-printed string (as produced by Lean/Pantograph)
    into a list of individual goal dicts with keys:
      - case_name : str | None
      - variables : list[str]   (local context lines)
      - target    : str         (the ⊢ expression)

    Pantograph separates goals with `\ncase` (a new `case` line), not `\n\n`.
    We split on that boundary.
    """
    goals = []
    # Split on transitions to a new `case` block: newline followed by "case "
    raw_chunks = re.split(r'\n(?=case )', goal_str.strip())
    # Also handle double-newline separation (used by some tactic output)
    chunks_flat = []
    for chunk in raw_chunks:
        sub = chunk.split("\n\n")
        chunks_flat.extend(sub)

    for chunk in chunks_flat:
        chunk = chunk.strip()
        if not chunk or chunk == "no goals":
            continue
        lines = chunk.splitlines()
        case_name = None
        if lines and lines[0].startswith("case "):
            case_name = lines[0][len("case "):].strip()
            lines = lines[1:]
        target = None
        variables = []
        for i, line in enumerate(lines):
            stripped = line.lstrip("|⊢ ").lstrip("⊢ ")
            if line.startswith("⊢ ") or line.startswith("| "):
                target = stripped
                variables = [l.strip() for l in lines[:i] if l.strip()]
                break
        if target is None:
            # fallback: last line is target
            target = lines[-1].strip() if lines else chunk
            variables = [l.strip() for l in lines[:-1] if l.strip()]
        goals.append({
            "case_name": case_name,
            "variables": variables,
            "target": target,
        })
    return goals


# ─────────────────────── Lean source parser ──────────────────────────────────

# Matches `theorem Foo (args) : Type :=` or `lemma Foo (args) : Type :=`
_DECL_RE = re.compile(
    r'^(?:private\s+|protected\s+|noncomputable\s+)*'
    r'(?:theorem|lemma)\s+'
    r'(?P<name>[A-Za-z_][A-Za-z0-9_.\']*)',
    re.MULTILINE,
)


def _find_by_block(source: str, decl_start: int) -> Optional[tuple[int, int, list[str]]]:
    """
    Starting from `decl_start`, find the `:= by` or `by` and extract tactics.
    Returns (by_start, by_end, tactics) or None.
    """
    # Find `:= by` after the declaration
    assign_match = re.search(r':=\s*by\b', source[decl_start:])
    if not assign_match:
        # Try just `by` at end of type signature
        assign_match = re.search(r'\bby\b', source[decl_start:])
    if not assign_match:
        return None

    by_start = decl_start + assign_match.end()
    # Collect indented lines until a line with less indentation than the first tactic
    rest = source[by_start:]
    lines = rest.splitlines()

    # Find indentation of first non-empty tactic line
    base_indent = None
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("--"):
            continue
        base_indent = len(line) - len(stripped)
        break

    if base_indent is None:
        return None

    tactics = []
    by_end = by_start
    for line in lines:
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            by_end += len(line) + 1
            continue
        # End of by-block: a line with less indentation that looks like a new declaration
        if indent < base_indent and re.match(r'^(theorem|lemma|def |#|end |namespace )', stripped):
            break
        # If indented enough, collect as a tactic
        if indent >= base_indent:
            if stripped:
                tactics.append(stripped)
        by_end += len(line) + 1

    return by_start, by_end, tactics


def _extract_type(source: str, name_end: int, by_pos: int) -> str:
    """Extract the type string between `name_end` and the `:= by` marker."""
    segment = source[name_end:by_pos]
    # Strip leading arg list (simple heuristic: drop everything before the last `:`)
    # Try to find `: Type := by` pattern
    colon_match = re.search(r':\s*(.+?)\s*:=', segment, re.DOTALL)
    if colon_match:
        return colon_match.group(1).strip()
    # Fallback
    return segment.strip().lstrip(':').strip()


def extract_theorems(source: str) -> list[LeanTheorem]:
    """
    Extract all theorem/lemma declarations from a Lean 4 source string.
    Returns a list of LeanTheorem with name, type_str, and tactic list.
    """
    results: list[LeanTheorem] = []
    lines = source.splitlines(keepends=True)

    for match in _DECL_RE.finditer(source):
        name = match.group("name")
        decl_start = match.start()
        name_end = match.end()

        # Look for `:= by` in the next ~200 chars
        lookahead = source[decl_start: decl_start + 2000]
        assign_match = re.search(r':=\s*by\b', lookahead)
        if not assign_match:
            continue

        by_absolute = decl_start + assign_match.start()
        type_str = _extract_type(source, name_end, by_absolute)

        result = _find_by_block(source, decl_start + assign_match.start())
        if result is None:
            continue
        _, _, tactics = result

        # Compute line range
        start_line = source[:decl_start].count('\n') + 1
        by_end = decl_start + assign_match.end()
        end_line = source[:by_end].count('\n') + 1 + len(tactics)

        # Convert char offset → UTF-8 byte offset (Pantograph uses byte offsets)
        byte_off = len(source[:decl_start].encode("utf-8"))
        results.append(LeanTheorem(
            name=name,
            type_str=type_str,
            tactics=tactics,
            source_range=(start_line, end_line),
            byte_offset=byte_off,
        ))

    return results
