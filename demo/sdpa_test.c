/*
 * sdpa_test.c — Scaled Dot-Product Attention
 *               with automatic custom RISC-V instruction fusion
 *
 * Author  : Yash Awasthi
 * Compiler: riscv64-unknown-elf-gcc (GCC 15.2.0, modified)
 *
 * Compile command:
 *   riscv64-unknown-elf-gcc \
 *       -mattn -O2 \
 *       -S sdpa_test.c -o sdpa_test.s
 *
 * ─────────────────────────────────────────────────────────────────
 * WHAT THIS FILE DEMONSTRATES
 * ─────────────────────────────────────────────────────────────────
 * The GCC middle-end pass "attnrec" (tree-ssa-attn.cc, pass #179)
 * automatically recognizes the scaled dot-product attention pattern
 * in standard C loops and replaces it with a single custom instruction:
 *
 *     attn  rd, rs1, rs2, rs3
 *
 * No intrinsics. No builtins. No inline assembly.
 * Plain C in, custom RISC-V instruction out.
 *
 * ─────────────────────────────────────────────────────────────────
 * INSTRUCTION ENCODING
 * ─────────────────────────────────────────────────────────────────
 * Format  : R4-type (32-bit, 4 register operands)
 * Opcode  : 0x0b  (custom-0 slot)
 * funct3  : 0x0
 * funct2  : 0x00
 * MATCH   : 0x0000000b
 * MASK    : 0x0600707f
 *
 * Operands: see docs/01-instruction-spec.md section 4 for the
 * rd/rs1/rs2/rs3 block-pointer ABI (this is the single normative
 * definition; it is not repeated here).
 *
 * Binary example — attn a3, a0, a1, a2:
 *   0x60b5068b
 *   bit layout:
 *   [rs3=a2=01100][f2=00][rs2=a1=01011][rs1=a0=01010][f3=000][rd=a3=01101][0001011]
 *
 * ─────────────────────────────────────────────────────────────────
 * MATHEMATICS
 * ─────────────────────────────────────────────────────────────────
 *   Attention(Q, K, V) = softmax( Q x K^T / sqrt(D) ) x V
 *
 *   Step 1: S[i][j] = dot(Q[i], K[j]) x (1/sqrt(D))   scaled QK^T
 *   Step 2: S[i]    = softmax(S[i])                    row-wise softmax
 *   Step 3: O[i][d] = sum_j S[i][j] x V[j][d]         weighted values
 *
 * ─────────────────────────────────────────────────────────────────
 * WHY THE FUSED LOOP STRUCTURE IS REQUIRED
 * ─────────────────────────────────────────────────────────────────
 * All three phases must be inside ONE outer i-loop. If they are
 * written as three separate top-level loops, GCC compiles them as
 * three independent loop nests. The attnrec matcher then finds only
 * 2 distinct load bases per nest (not the 3 required: Q, K, V) and
 * rejects each nest individually.
 *
 * The fused form gives the matcher exactly what it needs:
 *   - inner madd reduction     (dot product over D)
 *   - expf + division          (softmax signature)
 *   - 3 distinct load bases    (Q, K, V)
 *   - 1 store base             (O)
 *   - known trip count         (N=64, statically analyzable by SCEV)
 *
 * ─────────────────────────────────────────────────────────────────
 * WHY THE ORIGINAL LOOPS REMAIN IN THE BINARY
 * ─────────────────────────────────────────────────────────────────
 * GCC emits the attn instruction because the pattern matched.
 * But it cannot remove the original loop body because it has no
 * proof the hardware actually computes the correct result.
 *
 * GCC is a compiler, not a theorem prover.
 *
 * To remove the loops, a hardware simulator (Spike) must:
 *   1. Implement attn in riscv-isa-sim/riscv/insns/attn.h
 *   2. Run both versions on identical inputs
 *   3. Confirm bit-exact output match (equivalence proof)
 *
 * Only after that can dead-code elimination remove the loop body.
 * This is standard hardware-software co-design practice.
 * The loop body remaining is NOT a bug — it is an incomplete pipeline.
 *
 * ─────────────────────────────────────────────────────────────────
 * THE 1/sqrt(D) SCALE FACTOR
 * ─────────────────────────────────────────────────────────────────
 * At -O2, GCC folds sqrtf(32) at compile time to the float constant:
 *   1.7677669227123260498046875e-1  (= 1/sqrt(32))
 * stored as IEEE 754 bits 0x3D3504F3 in .rodata section (.LC0).
 * No runtime sqrt is computed. It is a compile-time constant multiply.
 */

#include <math.h>

#define N 64    /* sequence length — number of tokens         */
#define D 32    /* head dimension  — embedding size per head  */

/*
 * sdpa() — Single-head Scaled Dot-Product Attention
 *
 * Parameters:
 *   Q[N][D] — query  matrix (N tokens x D dims, row-major float32)
 *   K[N][D] — key    matrix (N tokens x D dims, row-major float32)
 *   V[N][D] — value  matrix (N tokens x D dims, row-major float32)
 *   O[N][D] — output matrix (written by attn instruction in hardware)
 *
 * With -mattn -O2, the attnrec pass replaces this entire function body
 * at the GIMPLE level with:
 *
 *   .RISCV_ATTN ((void*)O, (void*)Q, (void*)K, (void*)V)
 *
 * which the RISC-V backend lowers to:
 *
 *   attn  a3, a0, a1, a2
 */
void sdpa (float Q[N][D], float K[N][D], float V[N][D], float O[N][D])
{
    /*
     * scale = 1/sqrt(D)
     * GCC folds sqrtf(32) to float constant 0.17677669...
     * Loaded once into register fs1 before the outer loop.
     * Stored in .rodata: .word 1043662067  (= 0x3D3504F3)
     */
    float scale = 1.0f / sqrtf ((float)D);

    /* Outer loop — iterates over N=64 query rows.
     * attnrec sees this as the top-level loop (loop_depth == 1)
     * and runs all four detection checks on it.                 */
    for (int i = 0; i < N; i++)
    {
        /*
         * S[N] — local attention score row, on stack.
         * 64 floats = 256 bytes at sp..sp+255.
         * Excluded from base-pointer collection by attnrec
         * because it is a non-static non-external local decl.
         */
        float S[N];

        /* ── Phase 1: QK^T with 1/sqrt(D) scaling ─────────────────
         *
         * S[j] = dot(Q[i], K[j]) * scale   for j = 0..N-1
         *
         * Inner d-loop is the madd reduction.
         * attnrec check 1: finds phi + PLUS_EXPR(MULT_EXPR) pattern.
         *
         * Assembly (.L3):
         *   flw     fa3, 0(a5)          Q[i][d]
         *   flw     fa4, 0(a4)          K[j][d]
         *   fmadd.s fa5, fa3, fa4, fa5  acc += Q*K  (fused multiply-add)
         *   addi    a5, a5, 4           advance Q pointer
         *   addi    a4, a4, 4           advance K pointer
         *   bne     a5, s0, .L3         loop while d < D
         *   fmul.s  fa5, fa5, fs1       acc * scale  (1/sqrt(32))
         *   fsw     fa5, 0(a3)          S[j] = scaled dot product
         * ─────────────────────────────────────────────────────── */
        for (int j = 0; j < N; j++)
        {
            float acc = 0.f;
            for (int d = 0; d < D; d++)
                acc += Q[i][d] * K[j][d];
            S[j] = acc * scale;
        }

        /* ── Phase 2: Row-wise softmax ─────────────────────────────
         *
         * Converts raw attention scores to a probability distribution.
         * Every row sums to 1.0 after normalization.
         *
         * Pass 2a — exp and accumulate:
         *   S[j] = expf(S[j])     for all j
         *   sum  += S[j]
         *
         * Pass 2b — normalize:
         *   S[j] = S[j] / sum     for all j
         *
         * attnrec check 2: scans ALL basic blocks for BUILT_IN_EXPF
         * call and RDIV_EXPR. Both found here — check passes.
         *
         * Assembly (.L4 — exp pass):
         *   flw     fa0, 0(s1)          load S[j]
         *   call    expf                S[j] = exp(S[j])
         *   fsw     fa0, 0(s1)          store back
         *   fadd.s  fs0, fs0, fa0       sum += S[j]
         *   addi    s1, s1, 4
         *   bne     s1, a5, .L4
         *
         * Assembly (.L5 — divide pass):
         *   flw     fa5, 0(a5)          load S[j]
         *   fdiv.s  fa5, fa5, fs0       S[j] / sum
         *   fsw     fa5, 0(a5)          store normalized
         *   addi    a5, a5, 4
         *   bne     a5, a4, .L5
         * ─────────────────────────────────────────────────────── */
        float sum = 0.f;
        for (int j = 0; j < N; j++)
            { S[j] = expf (S[j]); sum += S[j]; }
        for (int j = 0; j < N; j++)
            S[j] /= sum;

        /* ── Phase 3: Weighted sum over value matrix ───────────────
         *
         * O[i][d] = sum_j S[j] * V[j][d]   for d = 0..D-1
         *
         * Second madd reduction in the function.
         * attnrec check 3: function has >= 2 loops with madd phi.
         *
         * Assembly (.L7):
         *   flw     fa3, 0(a5)          S[j]   (from stack)
         *   flw     fa4, 0(a4)          V[j][d]
         *   fmadd.s fa5, fa3, fa4, fa5  acc += S[j]*V[j][d]
         *   addi    a5, a5, 4           next S element
         *   addi    a4, a4, 128         next V row (stride=D*4=128B)
         *   bne     a5, a1, .L7         loop while j < N
         *   fsw     fa5, 0(a2)          O[i][d] = acc
         * ─────────────────────────────────────────────────────── */
        for (int d = 0; d < D; d++)
        {
            float acc = 0.f;
            for (int j = 0; j < N; j++)
                acc += S[j] * V[j][d];
            O[i][d] = acc;
        }
    }
}
