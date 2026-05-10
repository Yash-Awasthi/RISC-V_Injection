/* fma_demo.c — Fused multiply-add as a single custom RISC-V instruction.
 *
 * Way-2 input file for customrv.py.  We don't ask the compiler to
 * understand FMA's mathematics; instead we use the explicit-marker
 * pattern: any call to __custom_fma() in this translation unit is
 * rewritten by the generated GIMPLE pass into IFN_RISCV_FMA, which
 * the RISC-V backend lowers to a single `fma` instruction.
 *
 * Build/integrate with:
 *   python3 customrv.py from-c examples/fma_demo.c --apply --build
 *
 * The script will:
 *   1. detect the __custom_fma marker → pattern_kind = marker
 *   2. allocate a free MATCH/MASK in custom-0
 *   3. emit 10 in-place patches + a new tree-ssa-fma.cc
 *   4. (with --apply) walk the toolchain tree interactively
 *   5. (with --build) rebuild + smoke + pattern test
 */

extern long __custom_fma(long a, long b, long c);

long fma_demo(long a, long b, long c)
{
    /* Mathematically a*b + c — but we DO NOT ask the compiler to
       prove that.  We simply mark the spot where one custom
       instruction should land. */
    return __custom_fma(a, b, c);
}
