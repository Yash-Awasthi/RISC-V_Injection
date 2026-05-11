/*
 * fail_noexpf.c — Failure case: softmax replaced by a non-`expf` activation.
 *
 * Failure category : signature — no expf/exp built-in in the function
 * Matcher gate hit : `attn_has_softmax_and_scale` returns false →
 *                    ";; attnrec: loop N rejected — missing softmax/sqrt".
 *
 * WHY THIS FAILS
 *   The recogniser treats softmax as a *required signature*: somewhere in
 *   the function it must see (a) a call to BUILT_IN_EXPF or BUILT_IN_EXP,
 *   AND (b) a division (RDIV_EXPR / TRUNC_DIV_EXPR).  This pair is the
 *   syntactic fingerprint of `exp(x) / sum(exp)` that distinguishes
 *   attention from any other Q.K^T-style kernel.
 *
 *   In this file we have the *correct* three-phase structure, three
 *   distinct base pointers, a madd reduction and a known trip count —
 *   but the activation is ReLU instead of softmax, and the normalisation
 *   uses a multiplicative inverse precomputed outside the loop instead
 *   of a per-element divide.  No `expf`, no `/` ⇒ the signature check
 *   fails.
 *
 * HOW TO MAKE THIS PASS
 *   Restore the softmax: replace `relu(...)` with `expf(...)` and use
 *   `S[j] / sum` for normalisation (not `S[j] * inv_sum`).  Once both
 *   the call and the RDIV_EXPR appear in the IR, this check is happy.
 */

#include <math.h>

#define N 64
#define D 32

static inline float relu (float x) { return x > 0.f ? x : 0.f; }

void sdpa_relu (float Q[N][D], float K[N][D], float V[N][D], float O[N][D])
{
    float scale = 1.0f / sqrtf ((float) D);

    for (int i = 0; i < N; i++)
    {
        float S[N];

        /* Phase 1 — scaled QK^T (madd reduction present, fine). */
        for (int j = 0; j < N; j++)
        {
            float acc = 0.f;
            for (int d = 0; d < D; d++)
                acc += Q[i][d] * K[j][d];
            S[j] = acc * scale;
        }

        /* Phase 2 — ReLU + multiplicative normalisation.
           No expf() call, no RDIV_EXPR — attn_has_softmax_and_scale
           rejects the function here.                                  */
        float sum = 0.f;
        for (int j = 0; j < N; j++) { S[j] = relu (S[j]); sum += S[j]; }
        float inv_sum = 1.0f / (sum + 1e-6f);     /* hoisted out of the j-loop */
        for (int j = 0; j < N; j++)
            S[j] = S[j] * inv_sum;                /* MULT_EXPR, not RDIV_EXPR */

        /* Phase 3 — weighted V (also fine on its own). */
        for (int d = 0; d < D; d++)
        {
            float acc = 0.f;
            for (int j = 0; j < N; j++)
                acc += S[j] * V[j][d];
            O[i][d] = acc;
        }
    }
}
