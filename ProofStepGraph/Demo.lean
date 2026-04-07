-- Demo.lean — Sample theorems for ProofStepGraph tracing.
--
-- These are deliberately simple so they can be traced against `Init` (no Mathlib).
-- They exercise the key graph structures we care about:
--   • Linear proofs (one goal, no branching)
--   • Branching (cases / induction splits into sibling goals)
--   • Deep proofs (many sequential tactics)
--   • Multi-initial-goal proofs (∀ intro splits)

-- ── Linear: no branching ─────────────────────────────────────────────────────

-- Note: `obtain` is not traced by Pantograph; use explicit And.left/And.right
-- Renamed to avoid clash with Lean stdlib's And.comm
theorem my_and_comm (p q : Prop) (h : p ∧ q) : q ∧ p := by
  constructor
  · exact h.2
  · exact h.1

theorem impl_trans (p q r : Prop) (hpq : p → q) (hqr : q → r) : p → r := by
  intro hp
  apply hqr
  apply hpq
  exact hp

-- ── Branching: cases ─────────────────────────────────────────────────────────

-- Note: `cases h with | inl | inr` on Prop types is not traced by Pantograph;
-- use Or.elim with explicit intro steps instead.
-- Renamed to avoid clash with Lean stdlib's Or.comm
theorem my_or_comm (p q : Prop) (h : p ∨ q) : q ∨ p := by
  apply Or.elim h
  · intro hp
    exact Or.inr hp
  · intro hq
    exact Or.inl hq

theorem nat_zero_or_succ (n : Nat) : n = 0 ∨ ∃ m, n = Nat.succ m := by
  cases n with
  | zero      => exact Or.inl rfl
  | succ m    => exact Or.inr ⟨m, rfl⟩

-- ── Induction: deep branching ─────────────────────────────────────────────────

theorem nat_add_zero (n : Nat) : n + 0 = n := by
  induction n with
  | zero      => rfl
  | succ n ih => simp [Nat.succ_add, ih]

theorem nat_add_comm (n m : Nat) : n + m = m + n := by
  induction n with
  | zero =>
    rw [Nat.zero_add]
  | succ n ih =>
    rw [Nat.succ_add, Nat.add_succ, ih]

-- ── Multi-step linear with rewrites ──────────────────────────────────────────

theorem list_append_nil (α : Type) (l : List α) : l ++ [] = l := by
  induction l with
  | nil        => rfl
  | cons hd tl ih =>
    rw [List.cons_append, ih]

-- ── Conjunction with manual construction ─────────────────────────────────────

theorem and_intro_manual (p q : Prop) (hp : p) (hq : q) : p ∧ q ∧ p := by
  constructor
  · exact hp
  · constructor
    · exact hq
    · exact hp
