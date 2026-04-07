open Nat

theorem olymid_ref_base_7458 : ⌊(2005^3 : ℝ) / (2003 * 2004) - (2003^3 : ℝ) / (2004 * 2005)⌋ = 8 := by
  have h_main : (2005 : ℝ)^3 / (2003 * 2004) - (2003 : ℝ)^3 / (2004 * 2005) = 8 + (16 : ℝ) / 4016015 := by
    norm_num [pow_three, mul_assoc]
    <;> field_simp [mul_comm, mul_assoc, mul_left_comm]
    <;> ring_nf
    <;> norm_num
    <;> rfl
  
  have h_floor : ⌊(2005 : ℝ)^3 / (2003 * 2004) - (2003 : ℝ)^3 / (2004 * 2005)⌋ = 8 := by
    rw [h_main]
    -- Prove that 8 ≤ 8 + (16 : ℝ) / 4016015
    have h₁ : (8 : ℝ) ≤ 8 + (16 : ℝ) / 4016015 := by
      norm_num
    -- Prove that 8 + (16 : ℝ) / 4016015 < 9
    have h₂ : (8 + (16 : ℝ) / 4016015 : ℝ) < 9 := by
      norm_num
    -- Use the properties of the floor function to conclude that the floor is 8
    have h₃ : ⌊(8 + (16 : ℝ) / 4016015 : ℝ)⌋ = 8 := by
      rw [Int.floor_eq_iff]
      constructor <;> norm_num
    rw [h₃]
  
  exact h_floor

