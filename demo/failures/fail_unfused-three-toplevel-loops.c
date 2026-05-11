/*
 * fail_unfused-three-toplevel-loops.c — Failure case: SDPA written as
 * three separate top-level loop nests instead of one fused i-loop.
 *
 * Failure category : structural — phases not fused under a single outer loop
 * Matcher gate hit : per-loop "load bases < 3" reject path —
 *                    ";; attnrec: loop N rejected — need >=3 loads got K"
 *                    fires once per top-level nest, because no single nest
 *                    sees Q, K and V together.
 *
 * WHY THIS FAILS
 *   `attn_collect_load_bases` runs *once per outer loop nest* during the
 *   per-loop attempt in `execute()`.  The QK^T nest only touches Q and K,
 *   the softmax nest only touches the local S[][], and the SV nest only
 *   touches S and V.  None of the three nests independently satisfies the
 *   "≥ 3 distinct non-local load bases" requirement.
 *
 *   This is exactly the failure mode that motivated the design of
 *   `sdpa_test.c`: the three phases *must* live inside one outer i-loop
 *   so that one nest sees Q, K, V, and writes to O.
 *
 * HOW TO MAKE THIS PASS
 *   Fuse the three for-loops into a single `for (int i = 0; i < N; i++)`
 *   body, exactly the way `../sdpa_test.c` is written.  Three-base
 *   collection then succeeds inside one nest.
 */

#include <math.h>

#define N 64
#define D 32

void sdpa_unfused (float Q[N][D], float K[N][D], float V[N][D], float O[N][D])
{
    float scale = 1.0f / sqrtf ((float) D);
    static float S[N][N];   /* file-scope so each nest can "see" it as a base */

    /* Top-level nest #1 — only Q and K visible as load bases. */
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++)
        {
            float acc = 0.f;
            for (int d = 0; d < D; d++)
                acc += Q[i][d] * K[j][d];
            S[i][j] = acc * scale;
        }

    /* Top-level nest #2 — softmax, only S visible. */
    for (int i = 0; i < N; i++)
    {
        float sum = 0.f;
        for (int j = 0; j < N; j++) { S[i][j] = expf (S[i][j]); sum += S[i][j]; }
        for (int j = 0; j < N; j++) S[i][j] /= sum;
    }

    /* Top-level nest #3 — only S and V visible as load bases. */
    for (int i = 0; i < N; i++)
        for (int d = 0; d < D; d++)
        {
            float acc = 0.f;
            for (int j = 0; j < N; j++)
                acc += S[i][j] * V[j][d];
            O[i][d] = acc;
        }
}
