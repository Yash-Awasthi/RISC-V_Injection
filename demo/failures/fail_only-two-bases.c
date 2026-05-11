/*
 * fail_only-two-bases.c — Failure case: only two distinct load base
 * pointers visible to the matcher (V folded into K, or K folded into Q).
 *
 * Failure category : structural — fewer than 3 non-local load bases
 * Matcher gate hit : `if (load_bases.length () < 3 || !store_base)` →
 *                    ";; attnrec: loop N rejected — need >=3 loads got 2".
 *
 * WHY THIS FAILS
 *   `attn_collect_load_bases` walks every load in the function and pushes
 *   the *unique* base pointers (after stripping POINTER_PLUS_EXPR chains
 *   and ignoring local stack arrays).  attnrec needs to see Q, K *and* V
 *   as three distinct base pointers — that is what makes the function
 *   recognisable as scaled-dot-product *attention* and not just a tiled
 *   matmul or a GEMM.
 *
 *   In this file, the caller has aliased V to K (a common bug, or a
 *   "weight tying" trick from a research paper) by passing the same
 *   pointer for both arguments at the API level, *and* the function
 *   only ever loads from two of the three — so after base collection
 *   there are exactly two distinct non-local bases.  Even though the
 *   loop structure, softmax and madd are all perfect, the base count
 *   trips the gate.
 *
 * HOW TO MAKE THIS PASS
 *   Load from three genuinely distinct parameters Q, K, V — and store
 *   to a fourth, O.  Aliasing K and V (or omitting V entirely) is
 *   exactly the sort of "almost attention" the matcher is meant to
 *   reject, because the resulting kernel does not compute SDPA.
 */

#include <math.h>

#define N 64
#define D 32

/* Only two input arrays: K is reused where V should be.  The matcher
   sees two distinct non-local load bases (Q and K), not the three it
   requires.                                                          */
void sdpa_two_bases (float Q[N][D], float K[N][D], float O[N][D])
{
    float scale = 1.0f / sqrtf ((float) D);

    for (int i = 0; i < N; i++)
    {
        float S[N];

        for (int j = 0; j < N; j++)
        {
            float acc = 0.f;
            for (int d = 0; d < D; d++)
                acc += Q[i][d] * K[j][d];          /* madd reduction OK */
            S[j] = acc * scale;
        }

        float sum = 0.f;
        for (int j = 0; j < N; j++) { S[j] = expf (S[j]); sum += S[j]; }
        for (int j = 0; j < N; j++) S[j] /= sum;     /* softmax OK     */

        /* Re-uses K instead of a separate V matrix.  This is the
           tell-tale sign of "almost SDPA": all the right ops, all the
           right shapes, but only two distinct memory regions in play. */
        for (int d = 0; d < D; d++)
        {
            float acc = 0.f;
            for (int j = 0; j < N; j++)
                acc += S[j] * K[j][d];              /* should be V[j][d] */
            O[i][d] = acc;
        }
    }
}
