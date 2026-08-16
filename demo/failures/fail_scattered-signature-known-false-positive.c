/*
 * fail_scattered-signature-known-false-positive.c — Correctness-hazard
 * exposure case: whole-function feature soup, no attention anywhere.
 *
 * STATUS — this file is NOT a verified reject like the other cases in
 * this directory.  It exists to give a concrete, compilable shape to the
 * exact hazard described in plan.md ("The pass that actually decides
 * this is attention"): every check in `attn_match` scans the *entire
 * function* rather than a single candidate loop, so a function that
 * merely scatters the required features anywhere in its body — not
 * composed into real SDPA — reads the same as `sdpa_test.c` to the
 * matcher. Tracing `tree-ssa-attn.cc` by hand against this file (no
 * toolchain build was run for this test; see demo/failures/README.md)
 * shows every one of the eight `attn_match` conditions holding, so the
 * expected *current* behaviour is a false positive: `attn` gets emitted
 * and the FIR loop below is silently discarded in favour of a bogus
 * call built from four pointers that have nothing to do with each
 * other. The correct behaviour — the thing this file's name asserts —
 * is that this function must NOT match, because it is not attention.
 * Until `attn_match` is rewritten to require the three phases to be
 * nested inside one outer loop in true data-dependency order (plan.md,
 * "Next steps" item 1), running this file through the sweep in
 * demo/failures/README.md is expected to print `FAIL` where every other
 * file in this directory prints `OK`. That `FAIL` is the correct,
 * expected outcome today — it is this file's entire purpose.
 *
 * Failure category : hazard — bag-of-features matcher, no structural proof
 * Matcher gate hit  : none — every early-return in `attn_match` is
 *                     satisfied, just not by parts that relate to
 *                     each other.
 *
 * WHY THE CURRENT MATCHER ACCEPTS THIS
 *   - The `i`/`t` FIR-filter nest is the only loop in the function
 *     with a nested loop, so it is the only depth-1 candidate
 *     `execute()` tries. Its inner `t`-loop has a plain
 *     `acc += A[i+t] * B[t]` multiply-accumulate PHI —
 *     `attn_find_madd_reduction` finds it, same as it would for QK^T
 *     or SV. This is an ordinary FIR filter, not a dot-product
 *     attention score.
 *   - `attn_has_softmax_and_scale` scans every basic block in the
 *     function, not just the FIR nest. The `expf` call and the
 *     division at the bottom of the function are a totally unrelated
 *     gain computation, but they satisfy the "has exp and has div"
 *     test regardless of where they sit.
 *   - `attn_collect_load_bases` also scans the whole function. `A`,
 *     `B`, `C`, and `out` are four distinct non-local bases spread
 *     across three unrelated loops (the FIR filter, an elementwise
 *     scale, and an elementwise bump) plus the closing gain line —
 *     more than the three the matcher requires — and `out` is also
 *     the only non-local store, so it becomes the presumed O.
 *   - The FIR nest's trip count is the compile-time constant `WIN`,
 *     so SCEV resolves it and the last gate passes too.
 *
 * HOW TO MAKE THIS REJECT FOR THE RIGHT REASON
 *   Fix the matcher, not this file: require that the madd reduction,
 *   the softmax pair, and the three-load/one-store bases all resolve
 *   to statements inside the same candidate outer loop, in an order
 *   consistent with QK^T -> softmax -> SV data flow, per plan.md
 *   option (a). Short of that, dropping automatic recognition for the
 *   explicit `__builtin_riscv_attn` path (plan.md option (b), also
 *   demonstrated in demo/attn.h and demo/sdpa_builtin.c) removes this
 *   hazard entirely, because the builtin takes its operands from the
 *   literal call site instead of guessing them from a whole-function
 *   scan.
 */

#include <math.h>

#define TAPS 32
#define WIN  64

/* Everything lives in one function on purpose — `attn_match` is run
   once per function (`FOR_EACH_BB_FN (bb, cfun)`), so the "whole
   function" it scans is exactly this function's body. Splitting the
   FIR filter into a callee would hide it from the softmax/base scans
   below and defeat the point of this file. */

/* Three unrelated pointer loops, an unrelated expf call, and an
   unrelated division, all in the same function — the exact scattered
   signature plan.md warns the whole-function scan cannot tell apart
   from real SDPA. Nothing here computes softmax(QK^T / sqrt(d))V. */
void
scattered_signature (const float *A, const float *B, const float *C,
                     float *out, int n)
{
    /* Unrelated pointer loop #1: FIR filter over A with kernel B.
       Multiply-accumulate reduction, not a dot-product attention
       score — but the only loop nest in the function, so it is the
       matcher's only depth-1 candidate. */
    float energy = 0.f;
    for (int i = 0; i < WIN; i++)
    {
        float acc = 0.f;
        for (int t = 0; t < TAPS; t++)
            acc += A[i + t] * B[t];
        energy += acc;
    }

    /* Unrelated pointer loop #2: elementwise scale of C into out. */
    for (int i = 0; i < n; i++)
        out[i] = C[i] * 2.0f;

    /* Unrelated pointer loop #3: elementwise bump of out. */
    for (int i = 0; i < n; i++)
        out[i] += 1.0f;

    /* Unrelated scalar gain — supplies the expf/division pair the
       whole-function softmax check looks for, with no relation to
       any of the three loops above. */
    float gain = expf (energy);
    out[0] += gain / (energy + 1.0f);
}
