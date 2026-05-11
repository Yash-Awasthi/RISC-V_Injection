/*
 * fail_extra-op-in-inner-loop.c — Failure case: extra operation inside
 * the inner d-loop breaks the pure madd-reduction PHI shape.
 *
 * Failure category : signature — madd PHI poisoned by an extra dependency
 * Matcher gate hit : `attn_find_madd_reduction` walks the latch-edge def
 *                    of the reduction PHI; the "other" SSA name feeding
 *                    PLUS_EXPR must be the result of a *single* MULT_EXPR.
 *                    Inserting a second operation between the multiply
 *                    and the accumulator breaks that direct PHI ↔ MULT
 *                    chain, so the helper returns NULL →
 *                    ";; attnrec: loop N rejected — no madd reduction".
 *
 * WHY THIS FAILS
 *   The recogniser is intentionally syntactic.  It does *not* normalise
 *   the IR or perform algebraic identities; it just asks "does the
 *   def-use graph of the reduction match `acc = acc + (a*b)`?".  If
 *   the inner loop adds an extra `+ b[d]` term, an early-exit branch,
 *   or a saturating clamp on each iteration, the PHI's incoming SSA
 *   name is the result of a PLUS_EXPR (or a PHI of a PHI), not a
 *   MULT_EXPR, and the matcher walks past it.
 *
 *   Here we add a per-iteration bias term inside the d-loop:
 *       acc += Q[i][d] * K[j][d] + bias[d];
 *   At -O2 GCC keeps the bias load and the FMA on separate statements,
 *   so the PHI back-edge feeds from `tmp2 = tmp1 + bias`, not from a
 *   MULT_EXPR.  No madd → rejected.
 *
 * HOW TO MAKE THIS PASS
 *   Hoist any per-d additive constant out of the inner loop (the bias
 *   only depends on d, so adding it once *outside* the j-loop is
 *   semantically identical).  Restore the inner body to a single
 *   `acc += Q[i][d] * K[j][d];` and the madd signature lights up.
 */

#include <math.h>

#define N 64
#define D 32

void sdpa_with_inner_bias (float Q[N][D], float K[N][D],
                           float V[N][D], float O[N][D],
                           const float bias[D])
{
    float scale = 1.0f / sqrtf ((float) D);

    for (int i = 0; i < N; i++)
    {
        float S[N];

        for (int j = 0; j < N; j++)
        {
            float acc = 0.f;
            for (int d = 0; d < D; d++)
                acc += Q[i][d] * K[j][d] + bias[d];   /* extra op in inner */
            S[j] = acc * scale;
        }

        float sum = 0.f;
        for (int j = 0; j < N; j++) { S[j] = expf (S[j]); sum += S[j]; }
        for (int j = 0; j < N; j++) S[j] /= sum;

        for (int d = 0; d < D; d++)
        {
            float acc = 0.f;
            for (int j = 0; j < N; j++)
                acc += S[j] * V[j][d];
            O[i][d] = acc;
        }
    }
}
