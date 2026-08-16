/*
 * fail_matmul-then-rownormalize.c — Failure case: ordinary matmul
 * followed by row normalization; every structural check the matcher
 * runs passes except the softmax signature.
 *
 * This is the "real numeric code" case plan.md's next-steps section
 * calls out directly: "a matmul-then-normalize routine, for instance"
 * — a function that shares several surface features with SDPA (a
 * nested multiply-accumulate loop, a division, three-plus non-local
 * load bases and a store) but computes neither attention scores nor
 * a softmax, and must not match.
 *
 * Failure category : signature — softmax/scale pair absent
 * Matcher gate hit  : `if (!attn_has_softmax_and_scale (outer)) return
 *                     false;` -> ";; attnrec: loop N rejected —
 *                     missing softmax/sqrt".
 *
 * WHY THIS FAILS (AND WHY IT ALMOST DOESN'T)
 *   Trace the same eight conditions demo/failures/README.md lists for
 *   a pass:
 *     1-3. The `i`-loop is a depth-1 candidate with a nested `j`-loop,
 *          and the `j`-loop's inner `k`-loop has a plain
 *          `acc += A[i][k] * Bm[k][j]` multiply-accumulate PHI —
 *          `attn_find_madd_reduction` finds it, same shape as QK^T.
 *     4.   FAILS HERE. `attn_has_softmax_and_scale` needs an
 *          `expf`/`exp` call *and* a division anywhere in the
 *          function. Row normalization only needs the division
 *          (`C[i][j] /= row_sum`) — there is no exponential anywhere
 *          in this file, because dividing by a row sum is not
 *          softmax. `has_exp` stays false and the whole-function AND
 *          fails, independent of where the division sits.
 *     5-7. `A`, `Bm`, and `C` are three distinct non-local load bases
 *          (`C` is read back for the row sum and for the in-place
 *          divide, not just written), and `C` is also the store base.
 *          Three loads and a store — the matcher's minimum — are
 *          present.
 *     8.   The outer loop's trip count is the compile-time constant
 *          `N`, so SCEV resolves it.
 *   Six of the eight conditions hold. This file is a clean, single-
 *   axis illustration that the softmax check is currently the only
 *   thing standing between "any matmul-and-divide kernel" and a
 *   false-positive `attn` emission — see
 *   fail_scattered-signature-known-false-positive.c in this same
 *   directory for a file where even that check gets satisfied by
 *   unrelated code.
 *
 * HOW TO MAKE THIS PASS (I.E., MATCH)
 *   Do not — this function is not attention and should never match.
 *   Turning it into SDPA would require replacing the row-sum division
 *   with a real softmax (subtract the row max, `expf` each entry, sum
 *   the exponentials, divide by that sum) and adding a second matmul
 *   against a V matrix, at which point it stops being "matmul then
 *   normalize" and becomes a different function.
 */

#define N 64
#define K 32
#define M 64

/* Ordinary matrix multiply (A * Bm -> C) followed by row-sum
   normalization. This is a real, common numeric kernel — for example
   turning raw scores into a row-stochastic matrix — and shares a
   multiply-accumulate reduction and a division with SDPA, but never
   computes a softmax. */
void
matmul_then_rownormalize (const float A[N][K], const float Bm[K][M],
                          float C[N][M])
{
    for (int i = 0; i < N; i++)
    {
        for (int j = 0; j < M; j++)
        {
            float acc = 0.f;
            for (int k = 0; k < K; k++)
                acc += A[i][k] * Bm[k][j];   /* madd reduction, same shape as QK^T */
            C[i][j] = acc;
        }

        float row_sum = 0.f;
        for (int j = 0; j < M; j++)
            row_sum += C[i][j];

        for (int j = 0; j < M; j++)
            C[i][j] /= row_sum;              /* normalize, but no expf anywhere */
    }
}
