/* ------------------------------------------------------------------ */
/* Matcher: explicit-marker idiom                                      */
/*                                                                     */
/* Looks for calls to the magic function {{MARKER_FN}}(...) in the IR  */
/* and rewrites each one into IFN_{{IFN}}(...) with the same args.     */
/* This is the universal fallback used by way-2 (C-file driven) when   */
/* the analyser cannot map the user's code to a more specific pattern. */
/*                                                                     */
/* The user just declares the marker as a normal extern function in    */
/* their C source and calls it where they want one custom instruction  */
/* to land.  No intrinsics, no inline asm — just an unresolved symbol  */
/* that this pass eliminates before linking.                           */
/* ------------------------------------------------------------------ */

static bool
{{MNEMONIC}}_call_matches_marker (gimple *stmt)
{
  if (!is_gimple_call (stmt))
    return false;
  tree fndecl = gimple_call_fndecl (stmt);
  if (!fndecl)
    return false;
  const char *name = IDENTIFIER_POINTER (DECL_NAME (fndecl));
  return name && strcmp (name, "{{MARKER_FN}}") == 0;
}

static bool
try_recognize_{{MNEMONIC}}_in_function (function *fun)
{
  bool changed = false;
  basic_block bb;

  FOR_EACH_BB_FN (bb, fun)
    {
      for (gimple_stmt_iterator gsi = gsi_start_bb (bb);
           !gsi_end_p (gsi); )
        {
          gimple *stmt = gsi_stmt (gsi);
          if (!{{MNEMONIC}}_call_matches_marker (stmt))
            { gsi_next (&gsi); continue; }

          /* Build IFN_{{IFN}}(arg0, arg1, ...) with the same arity. */
          unsigned nargs = gimple_call_num_args (stmt);
          auto_vec<tree> args (nargs);
          for (unsigned i = 0; i < nargs; ++i)
            args.quick_push (gimple_call_arg (stmt, i));

          gcall *call = gimple_build_call_internal_vec (IFN_{{IFN}}, args);
          tree lhs = gimple_call_lhs (stmt);
          if (lhs)
            gimple_call_set_lhs (call, lhs);

          if (dump_file)
            {
              fprintf (dump_file,
                       ";; {{MNEMONIC}}rec: rewrote {{MARKER_FN}} call "
                       "in bb %d\n", bb->index);
              print_gimple_stmt (dump_file, stmt, 2, TDF_SLIM);
            }

          gsi_replace (&gsi, call, true);
          changed = true;
          gsi_next (&gsi);
        }
    }

  return changed;
}
