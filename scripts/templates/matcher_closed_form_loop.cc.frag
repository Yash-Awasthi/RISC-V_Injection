/* ------------------------------------------------------------------ */
/* Matcher: closed-form replacement of a reduction loop                */
/*   acc = 0; for (i = 0; i < n; ++i) acc REDUCTION_OP= i;             */
/*   For {{MNEMONIC}}: reduction={{LOOP_REDUCTION_OP}}, step_is_iv={{LOOP_STEP_IS_IV}} */
/* ------------------------------------------------------------------ */

/* Find a phi at LOOP->header whose back-edge value is RESULT op SOMETHING,
   where SOMETHING is the loop induction variable.  Returns the phi or NULL. */
static gphi *
{{MNEMONIC}}_find_iv_reduction (class loop *loop)
{
  for (gphi_iterator gpi = gsi_start_phis (loop->header);
       !gsi_end_p (gpi); gsi_next (&gpi))
    {
      gphi *phi = gpi.phi ();
      tree  res = PHI_RESULT (phi);
      if (virtual_operand_p (res))
        continue;

      tree be = PHI_ARG_DEF_FROM_EDGE (phi, loop_latch_edge (loop));
      if (TREE_CODE (be) != SSA_NAME)
        continue;
      gimple *upd = SSA_NAME_DEF_STMT (be);
      if (!is_gimple_assign (upd)
          || gimple_assign_rhs_code (upd) != {{LOOP_REDUCTION_OP}})
        continue;

      tree a = gimple_assign_rhs1 (upd);
      tree b = gimple_assign_rhs2 (upd);
      tree other = (a == res) ? b : (b == res) ? a : NULL_TREE;
      if (!other || TREE_CODE (other) != SSA_NAME)
        continue;

      /* Check that 'other' evolves as the loop IV.  Use SCEV. */
      tree scev = analyze_scalar_evolution (loop, other);
      if (scev == chrec_dont_know || scev == NULL_TREE)
        continue;
      /* A polynomial chrec at this loop with step 1 and start 0 means IV. */
      if (TREE_CODE (scev) != POLYNOMIAL_CHREC)
        continue;
      tree start = CHREC_LEFT (scev);
      tree step  = CHREC_RIGHT (scev);
      if (!integer_zerop (start) || !integer_onep (step))
        continue;

      return phi;
    }
  return NULL;
}

static bool
try_recognize_{{MNEMONIC}}_loop (class loop *loop)
{
  gphi *phi = {{MNEMONIC}}_find_iv_reduction (loop);
  if (!phi)
    return false;

  tree n = number_of_latch_executions (loop);
  if (n == NULL_TREE || n == chrec_dont_know)
    {
      if (dump_file)
        fprintf (dump_file,
                 ";; {{MNEMONIC}}rec: loop %d trip count unknown\n",
                 loop->num);
      return false;
    }

  /* Trip count is the count of latch executions; for "i<n" loops with
     step 1 from 0, that's exactly n.  We pass the same expression
     SCEV gave us to the IFN. */
  tree result = PHI_RESULT (phi);

  edge pre = loop_preheader_edge (loop);
  gimple_stmt_iterator gsi = gsi_last_bb (pre->src);
  if (!gsi_end_p (gsi) && stmt_ends_bb_p (gsi_stmt (gsi)))
    /* Insert before the terminator. */
    ;
  else
    gsi = gsi_after_labels (pre->src);

  gcall *call = gimple_build_call_internal (IFN_{{IFN}}, 1, n);
  /* Make a fresh SSA name for the IFN's result and let later passes
     forward-propagate it into uses of 'result'.  Simpler: replace
     phi result via rewriting—but for v1 we just emit the call and
     set its lhs equal to phi-result type via a temp.  */
  tree tmp = make_ssa_name (TREE_TYPE (result));
  gimple_call_set_lhs (call, tmp);
  gsi_insert_before (&gsi, call, GSI_SAME_STMT);

  if (dump_file)
    {
      fprintf (dump_file,
               ";; {{MNEMONIC}}rec: replaced loop %d with IFN_{{IFN}}\n",
               loop->num);
      print_gimple_stmt (dump_file, call, 2, TDF_SLIM);
    }

  /* NOTE: we do NOT delete the original loop body; that is left to
     dead-code elimination (or future verified-removal work).  We DO
     replace uses of the original reduction result with our tmp.  */
  replace_uses_by (result, tmp);

  return true;
}

static bool
try_recognize_{{MNEMONIC}}_in_function (function *fun)
{
  if (number_of_loops (fun) <= 1)
    return false;

  bool changed = false;
  for (auto loop : loops_list (cfun, LI_FROM_INNERMOST))
    {
      if (try_recognize_{{MNEMONIC}}_loop (loop))
        { changed = true; break; }
    }
  return changed;
}
