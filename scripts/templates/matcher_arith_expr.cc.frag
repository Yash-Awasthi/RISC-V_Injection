/* ------------------------------------------------------------------ */
/* Matcher: straight-line arithmetic idiom                             */
/*   result = OUTER_OP( INNER_OP(a, b), c )                            */
/*   For {{MNEMONIC}}: outer={{ARITH_OUTER_OP}}, inner={{ARITH_INNER_OP}} */
/*   inner_pos={{ARITH_INNER_POS}} (0=LHS of outer is the inner_op)    */
/* ------------------------------------------------------------------ */

static bool
try_recognize_{{MNEMONIC}}_in_function (function *fun)
{
  bool changed = false;
  basic_block bb;

  FOR_EACH_BB_FN (bb, fun)
    {
      for (gimple_stmt_iterator gsi = gsi_start_bb (bb);
           !gsi_end_p (gsi); /* advance below */)
        {
          gimple *stmt = gsi_stmt (gsi);
          if (!is_gimple_assign (stmt)
              || gimple_assign_rhs_code (stmt) != {{ARITH_OUTER_OP}})
            { gsi_next (&gsi); continue; }

          /* Pull operands of the outer expression. */
          tree outer_lhs_op = gimple_assign_rhs1 (stmt);   /* a/b candidate */
          tree outer_rhs_op = gimple_assign_rhs2 (stmt);   /* c             */
          tree result       = gimple_assign_lhs  (stmt);

          /* For inner_pos=0 we expect the INNER_OP on the LHS of OUTER_OP.
             For inner_pos=1 swap. */
          tree inner_ssa = ({{ARITH_INNER_POS}} == 0) ? outer_lhs_op : outer_rhs_op;
          tree c_operand = ({{ARITH_INNER_POS}} == 0) ? outer_rhs_op : outer_lhs_op;

          if (TREE_CODE (inner_ssa) != SSA_NAME)
            { gsi_next (&gsi); continue; }

          gimple *def = SSA_NAME_DEF_STMT (inner_ssa);
          if (!is_gimple_assign (def)
              || gimple_assign_rhs_code (def) != {{ARITH_INNER_OP}})
            { gsi_next (&gsi); continue; }

          tree a = gimple_assign_rhs1 (def);
          tree b = gimple_assign_rhs2 (def);

          if (dump_file)
            {
              fprintf (dump_file,
                       ";; {{MNEMONIC}}rec: matched %s of %s in bb %d\n",
                       "{{ARITH_OUTER_OP}}", "{{ARITH_INNER_OP}}", bb->index);
              print_gimple_stmt (dump_file, stmt, 2, TDF_SLIM);
            }

          /* Build IFN_{{IFN}} (a, b, c) and replace the outer stmt. */
          gcall *call = gimple_build_call_internal (IFN_{{IFN}}, 3,
                                                    a, b, c_operand);
          gimple_call_set_lhs (call, result);
          gsi_replace (&gsi, call, true);

          changed = true;
          /* gsi_replace already left the iterator on the new stmt;
             advance to avoid re-matching it. */
          gsi_next (&gsi);
        }
    }

  return changed;
}
