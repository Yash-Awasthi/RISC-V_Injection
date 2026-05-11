/*
 * fail_no-madd-reduction.c — Failure case: dot product replaced by a
 * non-multiplicative reduction.
 *
 * Failure category : signature — no `acc += a * b` phi anywhere in the nest
 * Matcher gate hit : `attn_find_madd_reduction` returns NULL for every
 *                    loop in the nest →
 *                    ";; attnrec: loop N rejected — no madd reduction".
 *
 * WHY THIS FAILS
 *   `attn_find_madd_reduction` walks every PHI of a loop header and looks
 *   for the shape
 *       acc_phi = PHI(0, acc_next)
 *       acc_next = acc_phi + tmp
 *       tmp = a * b                       (MULT_EXPR feeding PLUS_EXPR)
 *   It is the syntactic signature of a fused multiply-add reduction,
 *   the unmistakable inner kernel of QK^T and SV.
 *
 *   Here the inner-d loop computes a sum of *absolute differences*
 *   (`acc += fabsf(Q[i][d] - K[j][d])`) — a perfectly valid distance
 *   metric, but its def-use graph is PLUS_EXPR(ABS_EXPR(MINUS_EXPR)),
 *   never PLUS_EXPR(MULT_EXPR).  The reduction phi exists but its
 *   "other" operand is not a MULT_EXPR, so the matcher walks past it
 *   and returns NULL.
 *
 * HOW TO MAKE THIS PASS
 *   Restore the inner loop to a true multiply-accumulate dot product:
 *   `acc += Q[i][d] * K[j][d];`.  The PHI-fed MULT_EXPR signature
 *   then lights up and the check passes.
 */

#include <math.h>

#define N 64
#define D 32

void sdpa_l1_distance (float Q[N][D], float K[N][D],
                       float V[N][D], float O[N][D])
{
    float scale = 1.0f / sqrtf ((float) D);

    for (int i = 0; i < N; i++)
    {
        float S[N];

        /* Phase 1 — L1 distance instead of dot product.
           Inner loop is acc += |Q - K|, not acc += Q * K.
           No MULT_EXPR feeding the reduction PHI ⇒ matcher rejects. */
        for (int j = 0; j < N; j++)
        {
            float acc = 0.f;
            for (int d = 0; d < D; d++)
                acc += fabsf (Q[i][d] - K[j][d]);     /* ABS, not MULT */
            S[j] = -acc * scale;                       /* negate so big => small */
        }

        /* Phase 2 — full softmax (expf + RDIV present, so the softmax
           check on its own would still pass). */
        float sum = 0.f;
        for (int j = 0; j < N; j++) { S[j] = expf (S[j]); sum += S[j]; }
        for (int j = 0; j < N; j++) S[j] /= sum;

        /* Phase 3 — note that this loop's inner reduction *is* an
           acc += S*V mul-add, so a madd phi does still exist in the
           function — but `attn_match` searches the nest of `outer`,
           not the function, and once Phase 1's inner d-loop fails to
           expose a madd, the matcher walks the sibling l-loops looking
           for one.  In this file none of them expose a Q*K-style
           inner-most reduction at the *correct* depth, and the matcher
           rejects.  (Swap the order or replace fabsf with `*` to
           confirm the difference.)                                   */
        for (int d = 0; d < D; d++)
        {
            float acc = 0.f;
            for (int j = 0; j < N; j++)
                acc += S[j] * V[j][d];
            O[i][d] = acc;
        }
    }
}
