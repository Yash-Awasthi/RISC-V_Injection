/* attn.c — smoke-test fixture for the generic build/test pipeline
   (see ../README.md, ../customrv.py, ../04_build.sh, ../05_test.sh).

   Exercises the primary, non-experimental path: a direct call to
   __builtin_riscv_attn, gated on plain -mattn. This is deliberately
   independent of demo/sdpa_builtin.c and demo/attn.h so the generic
   pipeline's single-file convention (./05_test.sh attn) keeps
   working without reaching into demo/.

   ./05_test.sh attn compiles this with -mattn -O2 -S and greps the
   output for `attn`; compiling it without -mattn is a compile-time
   error, since the builtin is registered only when TARGET_ATTN is
   set (AVAIL (attn, TARGET_ATTN) in riscv-builtins.cc). */

static char out_block[8];
static char qkv_block[24];
static char dims_block[24];
static char cfg_block[8];

void
attn_smoke (void)
{
  __builtin_riscv_attn (out_block, qkv_block, dims_block, cfg_block);
}
