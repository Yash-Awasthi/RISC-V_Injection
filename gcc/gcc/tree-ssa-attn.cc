/* RISC-V attention-idiom recognizer — R4-type 4-register ABI.
   Instruction: attn rd, rs1, rs2, rs3
     rd  -> O                            output matrix, direct pointer
     rs1 -> &attn_ptrs { Q_ptr, K_ptr, V_ptr }
     rs2 -> &attn_dims { N, D, H }       uint64_t each
     rs3 -> &attn_cfg  { scale_bits, flags }   uint32_t each, scale_bits
                                          is 1/sqrt(D) as raw float32 bits
   Normative definition: docs/01-instruction-spec.md section 4 and
   demo/attn.h.  */

/* Known limitation.  This pass is experimental and off by default
   (-mattn-recognize).  The matcher scans the whole function rather
   than a single loop body, so it does not prove that the statements
   it collects execute in the order a real data dependency would
   require.  Operand roles (which base is Q, K, V, or O) are guessed
   positionally from scan order, not proven by data flow.  The pass
   never proves that the matched loop nest computes scaled
   dot-product attention; it only proves that the syntactic shape
   matches.  Do not enable this pass by default.  attn_emit_replacement
   still passes pre-ABI raw O/Q/K/V pointers instead of the block ABI
   in docs/01-instruction-spec.md section 4; it does not conform and
   is experimental.  */

#define INCLUDE_MEMORY
#include "config.h"
#include "system.h"
#include "coretypes.h"
#include "backend.h"
#include "tree.h"
#include "gimple.h"
#include "tree-pass.h"
#include "ssa.h"
#include "fold-const.h"
#include "gimple-iterator.h"
#include "gimple-pretty-print.h"
#include "cfgloop.h"
#include "tree-cfg.h"
#include "tree-ssa-loop.h"
#include "tree-ssa-loop-manip.h"
#include "tree-ssa-loop-niter.h"
#include "tree-scalar-evolution.h"
#include "gimple-fold.h"
#include "tree-data-ref.h"
#include "diagnostic-core.h"
#include "stor-layout.h"
#include "cfganal.h"
#include "tree-eh.h"
#include "tree-ssa.h"
#include "tree-into-ssa.h"
#include "builtins.h"
#include "config/riscv/riscv-protos.h"

namespace {

/* ------------------------------------------------------------------ */
/* Pass descriptor                                                      */
/* ------------------------------------------------------------------ */

const pass_data pass_data_recognize_attn =
{
  GIMPLE_PASS,
  "attnrec",
  OPTGROUP_LOOP,
  TV_TREE_LOOP,
  PROP_cfg | PROP_ssa,
  0, 0, 0, TODO_update_ssa
};

/* ------------------------------------------------------------------ */
/* Helper: extract base pointer from a memory reference tree           */
/* ------------------------------------------------------------------ */

static tree
attn_base_ptr (tree ref)
{
  while (handled_component_p (ref))
    ref = TREE_OPERAND (ref, 0);
  if (TREE_CODE (ref) == MEM_REF || TREE_CODE (ref) == TARGET_MEM_REF)
    {
      tree base = TREE_OPERAND (ref, 0);
      /* Walk SSA pointer arithmetic: _3 = Q + offset  =>  find Q.
         At O2 GCC hoists "ptr = base + idx*stride" out of loops,
         so the MEM_REF base is a derived SSA name, not the original
         function parameter.  Chase POINTER_PLUS_EXPR chains.  */
      unsigned limit = 8;
      while (TREE_CODE (base) == SSA_NAME && limit--)
        {
          gimple *def = SSA_NAME_DEF_STMT (base);
          if (!is_gimple_assign (def))
            break;
          tree_code code = gimple_assign_rhs_code (def);
          if (code == POINTER_PLUS_EXPR)
            base = gimple_assign_rhs1 (def);   /* left of + is the base */
          else if (code == SSA_NAME
                   || TREE_CODE_CLASS (code) == tcc_unary)
            base = gimple_assign_rhs1 (def);
          else
            break;
        }
      if (TREE_CODE (base) == SSA_NAME)  return base;
      if (TREE_CODE (base) == ADDR_EXPR) return base;
    }
  if (DECL_P (ref))
    return build_fold_addr_expr (ref);
  return NULL_TREE;
}

/* ------------------------------------------------------------------ */
/* Helper: detect multiply-add reduction phi in LOOP                   */
/* ------------------------------------------------------------------ */

static gphi *
attn_find_madd_reduction (class loop *loop)
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
      gimple *plus_stmt = SSA_NAME_DEF_STMT (be);
      if (!is_gimple_assign (plus_stmt)
          || gimple_assign_rhs_code (plus_stmt) != PLUS_EXPR)
        continue;
      tree a = gimple_assign_rhs1 (plus_stmt);
      tree b = gimple_assign_rhs2 (plus_stmt);
      tree other = (a == res) ? b : (b == res) ? a : NULL_TREE;
      if (!other || TREE_CODE (other) != SSA_NAME)
        continue;
      gimple *mult_stmt = SSA_NAME_DEF_STMT (other);
      if (is_gimple_assign (mult_stmt)
          && gimple_assign_rhs_code (mult_stmt) == MULT_EXPR)
        return phi;
    }
  return NULL;
}

/* ------------------------------------------------------------------ */
/* Helper: detect softmax (exp+div) anywhere in the function.          */
/* At O2, sqrtf(D) is folded to a float constant so we only check      */
/* for expf (softmax) and division (normalization).                    */
/* We scan ALL basic blocks in the function because softmax loops are  */
/* siblings of the QKT loop, not nested inside it.                    */
/* ------------------------------------------------------------------ */

static bool
attn_has_softmax_and_scale (class loop *outer ATTRIBUTE_UNUSED)
{
  bool has_exp = false;
  bool has_div = false;

  basic_block bb;
  FOR_EACH_BB_FN (bb, cfun)
    {
      for (gimple_stmt_iterator gsi = gsi_start_bb (bb);
           !gsi_end_p (gsi); gsi_next (&gsi))
        {
          gimple *stmt = gsi_stmt (gsi);
          if (is_gimple_call (stmt))
            {
              tree fn = gimple_call_fndecl (stmt);
              if (fn && (fndecl_built_in_p (fn, BUILT_IN_EXP)
                         || fndecl_built_in_p (fn, BUILT_IN_EXPF)))
                has_exp = true;
            }
          if (is_gimple_assign (stmt))
            {
              tree_code code = gimple_assign_rhs_code (stmt);
              if (code == RDIV_EXPR || code == TRUNC_DIV_EXPR)
                has_div = true;
            }
        }
    }
  return has_exp && has_div;
}



/* ------------------------------------------------------------------ */
/* Helper: collect distinct load base pointers across loop body        */
/* ------------------------------------------------------------------ */

/* Scan ALL basic blocks in function for load/store bases.
   At O2, GCC splits attention into separate loop nests (QKT nest +
   softmax nest + SV nest), so no single loop contains Q, K, V, O.
   We collect across the whole function and filter out S (local array). */
static void
attn_collect_load_bases (class loop *outer ATTRIBUTE_UNUSED,
                         auto_vec<tree> &bases)
{
  basic_block bb;
  FOR_EACH_BB_FN (bb, cfun)
    for (gimple_stmt_iterator gsi = gsi_start_bb (bb);
         !gsi_end_p (gsi); gsi_next (&gsi))
      {
        gimple *stmt = gsi_stmt (gsi);
        if (!is_gimple_assign (stmt)) continue;
        tree rhs = gimple_assign_rhs1 (stmt);
        if (!REFERENCE_CLASS_P (rhs)) continue;
        tree b = attn_base_ptr (rhs);
        if (!b) continue;
        /* Skip local/stack variables (ADDR_EXPR of a VAR_DECL
           with no DECL_EXTERNAL — these are S[], not Q/K/V).  */
        if (TREE_CODE (b) == ADDR_EXPR)
          {
            tree decl = TREE_OPERAND (b, 0);
            if (DECL_P (decl) && !DECL_EXTERNAL (decl)
                && !TREE_STATIC (decl))
              continue;
          }
        bool dup = false;
        for (unsigned i = 0; i < bases.length (); ++i)
          if (operand_equal_p (b, bases[i], 0)) { dup = true; break; }
        if (!dup)
          bases.safe_push (b);
      }
}

static tree
attn_collect_store_base (class loop *outer ATTRIBUTE_UNUSED)
{
  /* Find the store that writes to a non-local pointer (O array).  */
  basic_block bb;
  FOR_EACH_BB_FN (bb, cfun)
    for (gimple_stmt_iterator gsi = gsi_start_bb (bb);
         !gsi_end_p (gsi); gsi_next (&gsi))
      {
        gimple *stmt = gsi_stmt (gsi);
        if (!is_gimple_assign (stmt)) continue;
        tree lhs = gimple_assign_lhs (stmt);
        if (!REFERENCE_CLASS_P (lhs)) continue;
        tree b = attn_base_ptr (lhs);
        if (!b) continue;
        /* Skip local arrays (S[]).  */
        if (TREE_CODE (b) == ADDR_EXPR)
          {
            tree decl = TREE_OPERAND (b, 0);
            if (DECL_P (decl) && !DECL_EXTERNAL (decl)
                && !TREE_STATIC (decl))
              continue;
          }
        return b;
      }
  return NULL_TREE;
}

/* ------------------------------------------------------------------ */
/* Match result struct                                                  */
/* ------------------------------------------------------------------ */

struct attn_info
{
  tree q_ptr;
  tree k_ptr;
  tree v_ptr;
  tree o_ptr;
  tree n_iters;
  tree d_iters;
  class loop *outer;
};

/* ------------------------------------------------------------------ */
/* Main structural matcher                                             */
/* ------------------------------------------------------------------ */

static bool
attn_match (class loop *outer, attn_info *info)
{
  if (!outer->inner)
    {
      if (dump_file)
        fprintf (dump_file,
                 ";; attnrec: loop %d rejected — no inner loop\n",
                 outer->num);
      return false;
    }

  /* Find madd in any loop in the nest, not just direct inner.  */
  {
    bool found_madd = false;
    for (class loop *l = outer->inner; l && !found_madd; l = l->next)
      {
        if (attn_find_madd_reduction (l))
          found_madd = true;
        for (class loop *ll = l->inner; ll && !found_madd; ll = ll->next)
          if (attn_find_madd_reduction (ll))
            found_madd = true;
      }
    if (!found_madd)
      {
        if (dump_file)
          fprintf (dump_file,
                   ";; attnrec: loop %d rejected — no madd reduction\n",
                   outer->num);
        return false;
      }
  }

  if (!attn_has_softmax_and_scale (outer))
    {
      if (dump_file)
        fprintf (dump_file,
                 ";; attnrec: loop %d rejected — missing softmax/sqrt\n",
                 outer->num);
      return false;
    }

  auto_vec<tree> load_bases;
  attn_collect_load_bases (outer, load_bases);
  tree store_base = attn_collect_store_base (outer);

  if (dump_file)
    {
      fprintf (dump_file, ";; attnrec: loop %d load bases found: %u\n",
               outer->num, load_bases.length ());
      for (unsigned i = 0; i < load_bases.length (); i++)
        {
          fprintf (dump_file, ";;   base[%u]: ", i);
          print_generic_expr (dump_file, load_bases[i], TDF_SLIM);
          fprintf (dump_file, "\n");
        }
      fprintf (dump_file, ";;   store base: ");
      if (store_base)
        print_generic_expr (dump_file, store_base, TDF_SLIM);
      else
        fprintf (dump_file, "(none)");
      fprintf (dump_file, "\n");
    }

  if (load_bases.length () < 3 || !store_base)
    {
      if (dump_file)
        fprintf (dump_file,
                 ";; attnrec: loop %d rejected — need >=3 loads got %u\n",
                 outer->num, load_bases.length ());
      return false;
    }

  tree n = number_of_latch_executions (outer);
  tree d = number_of_latch_executions (outer->inner);
  if (n == chrec_dont_know)
    {
      if (dump_file)
        fprintf (dump_file,
                 ";; attnrec: loop %d rejected — trip count unknown\n",
                 outer->num);
      return false;
    }

  info->q_ptr   = load_bases[0];
  info->k_ptr   = load_bases[1];
  info->v_ptr   = load_bases[2];
  info->o_ptr   = store_base;
  info->n_iters = n;
  info->d_iters = (d == chrec_dont_know) ? integer_zero_node : d;
  info->outer   = outer;
  return true;
}

/* ------------------------------------------------------------------ */
/* Emit a call to __builtin_riscv_attn and wipe original loop body     */
/* ------------------------------------------------------------------ */

static void
attn_emit_replacement (const attn_info &mi)
{
  edge pre = loop_preheader_edge (mi.outer);
  gimple_stmt_iterator gsi = gsi_after_labels (pre->src);

  /* Known limitation: passes O, Q, K, V directly instead of building
     the attn_ptrs/attn_dims/attn_cfg blocks docs/01-instruction-spec.md
     section 4 requires for rs1/rs2/rs3.  No stack structs avoids
     vdef/vuse SSA issues in DCE, at the cost of not conforming.  */
  tree ptr_type = build_pointer_type (void_type_node);
  tree o_arg = fold_convert (ptr_type, mi.o_ptr);
  tree q_arg = fold_convert (ptr_type, mi.q_ptr);
  tree k_arg = fold_convert (ptr_type, mi.k_ptr);
  tree v_arg = fold_convert (ptr_type, mi.v_ptr);

  tree fndecl = riscv_builtin_decl_attn ();
  gcall *call = gimple_build_call (fndecl, 4, o_arg, q_arg, k_arg, v_arg);
  gimple_set_has_volatile_ops (call, true);
  gsi_insert_before (&gsi, call, GSI_SAME_STMT);

  if (dump_file)
    {
      fprintf (dump_file,
               ";; attnrec: replaced loop %d with __builtin_riscv_attn\n"
               ";;   pre-ABI direct pointers: O, Q, K, V (see known limitation)\n",
               mi.outer->num);
      print_gimple_stmt (dump_file, call, 2, TDF_SLIM);
    }
}


/* ------------------------------------------------------------------ */
/* Top-level per-loop entry point                                      */
/* ------------------------------------------------------------------ */

static bool
try_recognize_attention (class loop *outer)
{
  attn_info mi {};
  if (!attn_match (outer, &mi))
    return false;
  attn_emit_replacement (mi);
  return true;
}

/* ------------------------------------------------------------------ */
/* Pass class                                                          */
/* ------------------------------------------------------------------ */

class pass_recognize_attn : public gimple_opt_pass
{
public:
  pass_recognize_attn (gcc::context *ctxt)
    : gimple_opt_pass (pass_data_recognize_attn, ctxt) {}

  bool gate (function *) final override
  {
#ifdef TARGET_ATTN_RECOGNIZE
    /* Recognizing the idiom is useless without the instruction to
       replace it with; the builtin decl this pass calls only exists
       when TARGET_ATTN enabled it in riscv_init_builtins.  */
    return (TARGET_ATTN && TARGET_ATTN_RECOGNIZE
            && optimize >= 2 && flag_tree_loop_optimize);
#else
    return false;
#endif
  }

  unsigned int execute (function *fun) final override;
};

unsigned int
pass_recognize_attn::execute (function *fun)
{
  if (number_of_loops (fun) <= 1)
    return 0;

  bool changed = false;

  /* Only consider true top-level loops (direct children of loop 0).
     Matching inner loops causes the call to be inserted inside an
     outer loop iteration instead of replacing the whole nest.  */
  for (auto loop : loops_list (cfun, LI_FROM_INNERMOST))
    {
      /* Skip if not a direct child of the root (loop_depth != 1) */
      if (loop_depth (loop) != 1)
        continue;
      /* Must have inner loops (attention is always nested) */
      if (!loop->inner)
        continue;
      if (try_recognize_attention (loop))
        { changed = true; break; }
    }

  if (changed)
    {
      mark_virtual_operands_for_renaming (fun);
      return TODO_cleanup_cfg | TODO_update_ssa | TODO_remove_unused_locals;
    }
  return 0;
}

} // anon namespace

gimple_opt_pass *
make_pass_recognize_attn (gcc::context *ctxt)
{
  return new pass_recognize_attn (ctxt);
}
