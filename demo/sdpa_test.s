        # ================================================================
        # sdpa_test.s — Annotated Assembly Output
        # Source  : sdpa_test.c
        # Compiler: riscv64-unknown-elf-gcc (GCC 15.2.0, modified)
        # Command : riscv64-unknown-elf-gcc -mattn -O2
        #               -S sdpa_test.c -o sdpa_test.s
        #
        # KEY RESULT:
        #   Line 24: attn a3,a0,a1,a2
        #   The attnrec GCC pass recognized the attention loop nest
        #   and emitted this custom R4-type instruction automatically.
        #
        # BINARY ENCODING (verified by objdump):
        #   attn a3,a0,a1,a2  =  0x60b5068b
        #   [rs3=a2][f2=00][rs2=a1][rs1=a0][f3=000][rd=a3][0001011]
        #
        # BASELINE (without -mattn): 110 lines, 7 branches
        # WITH -mattn             : 111 lines, 7 branches + attn insn
        # ================================================================

        .file   "sdpa_test.c"
        .option nopic
        .attribute arch, "rv64i2p1_m2p0_a2p1_f2p2_d2p2_c2p0_zicsr2p0_zifencei2p0_zmmul1p0_zaamo1p0_zalrsc1p0_zca1p0_zcd1p0"
        .attribute unaligned_access, 0
        .attribute stack_align, 16
        .text
        .align  1
        .globl  sdpa
        .type   sdpa, @function

sdpa:
        # ── FUNCTION PROLOGUE ────────────────────────────────────────
        # Grow stack frame: 336 bytes total
        #   sp+328 : return address (ra)
        #   sp+320 : s0
        #   sp+312 : s1
        #   sp+304 : s2
        #   sp+296 : s3
        #   sp+288 : s4
        #   sp+280 : s5
        #   sp+272 : s6
        #   sp+264 : fs0  (float callee-saved)
        #   sp+256 : fs1  (float callee-saved)
        #   sp+0   : S[64] local array = 256 bytes (64 floats x 4)
        addi    sp,sp,-336
        sd      ra,328(sp)          # save return address
        sd      s0,320(sp)          # save callee-saved integer regs
        sd      s1,312(sp)
        sd      s2,304(sp)
        sd      s3,296(sp)
        sd      s4,288(sp)
        sd      s5,280(sp)
        sd      s6,272(sp)
        fsd     fs0,264(sp)         # save callee-saved float regs
        fsd     fs1,256(sp)

        # ── ARGUMENT CAPTURE ─────────────────────────────────────────
        # Function args arrive in: a0=Q, a1=K, a2=V, a3=O
        # Preserve K and V across expf() calls (which clobber a-regs)
        mv      s6,a1               # s6 = K pointer (preserved)
        mv      s5,a2               # s5 = V pointer (preserved)
        # a0 = Q, a3 = O used directly below

        # ════════════════════════════════════════════════════════════
        # THE CUSTOM INSTRUCTION — emitted by the attnrec GCC pass
        # ════════════════════════════════════════════════════════════
        # The attnrec pass (tree-ssa-attn.cc, pass #179) analyzed the
        # loop nest in sdpa_test.c and confirmed:
        #   [1] inner madd reduction (dot product phi pattern)
        #   [2] expf call + fdiv in function body (softmax signature)
        #   [3] 3 distinct load bases: Q(a0), K(a1), V(a2)
        #   [4] 1 store base: O(a3)
        #   [5] trip count N=64 statically known
        #
        # It emitted IFN_RISCV_ATTN at GIMPLE level, lowered to:
        attn    a3,a0,a1,a2         # rd=O, rs1=Q, rs2=K, rs3=V
        #                             encoding: 0x60b5068b
        #                             R4-type, custom-0 opcode 0x0b
        # ════════════════════════════════════════════════════════════

        # ── POST-FUSION POINTER SETUP ────────────────────────────────
        # The original loop body follows as reference implementation.
        # It remains because GCC cannot prove hardware equivalence yet.
        mv      s3,a3               # s3 = O pointer (for S*V store loop)
        addi    s0,a0,128           # s0 = Q + 128 = end of Q[0] row
        li      a5,8192             #   |
        addi    a5,a5,128           #   | a5 = 8192+128 = 8320
        add     s4,a0,a5            # s4 = Q + 8320 = end-of-Q sentinel
        addi    s2,a2,128           # s2 = V + 128 = end of V row 0 (D floats)
        lui     a5,%hi(.LC0)        #   |
        flw     fs1,%lo(.LC0)(a5)   # fs1 = 1/sqrt(32) = 0.17677669...
        #                             loaded from .rodata constant .LC0

        # ── LEADER 1: Outer i-loop header ────────────────────────────
        # Iterates i = 0..63 over query rows
        # s0 tracks Q[i] row end pointer; incremented at .L2 bottom
.L2:
        mv      a2,s6               # a2 = K (reset to base for each i)
        mv      a3,sp               # a3 = &S[0]  (stack scratch)
        addi    a1,s0,-128          # a1 = Q[i] row base (s0-128)

        # ── LEADER 2: QK^T middle j-loop header ──────────────────────
        # Iterates j = 0..63 over key rows
        # For each j: compute dot(Q[i], K[j]) then store to S[j]
.L10:
        mv      a5,a1               # a5 = Q[i][0] pointer
        mv      a4,a2               # a4 = K[j][0] pointer
        fmv.s.x fa5,zero            # fa5 = 0.0  (dot product accumulator)

        # ── LEADER 3: QK^T innermost d-loop ──────────────────────────
        # Iterates d = 0..31  (head dimension D=32)
        # Computes: acc += Q[i][d] * K[j][d]  — fused multiply-add
.L3:
        flw     fa3,0(a5)           # fa3 = Q[i][d]
        flw     fa4,0(a4)           # fa4 = K[j][d]
        fmadd.s fa5,fa3,fa4,fa5    # fa5 = Q[i][d]*K[j][d] + fa5
        addi    a5,a5,4             # Q pointer += 4 bytes (next float)
        addi    a4,a4,4             # K pointer += 4 bytes
        bne     a5,s0,.L3           # branch back if d < D
                                    # (s0 = end of Q[i] row)
        fmul.s  fa5,fa5,fs1         # S[j] = acc * (1/sqrt(32))
        fsw     fa5,0(a3)           # store S[j] to stack
        addi    a3,a3,4             # advance S pointer to S[j+1]
        addi    a2,a2,128           # K pointer to next row K[j+1]
        addi    a5,sp,256           # a5 = sp+256 = end of S[] (64*4=256)
        bne     a3,a5,.L10          # branch back if j < N

        # ── LEADER 4: Softmax pass 1 setup ───────────────────────────
        # Prepare for exp+accumulate pass over S[]
        mv      s1,sp               # s1 = &S[0]
        fmv.s.x fs0,zero            # fs0 = 0.0  (sum accumulator)

        # ── LEADER 5: Softmax exp+sum loop ───────────────────────────
        # S[j] = exp(S[j]);   sum += S[j]   for j = 0..N-1
        # expf is a libm call — clobbers a-registers, so K/V were saved
.L4:
        flw     fa0,0(s1)           # fa0 = S[j]
        call    expf                # fa0 = expf(S[j])  [libm]
        fsw     fa0,0(s1)           # S[j] = expf(S[j])  overwrite
        fadd.s  fs0,fs0,fa0         # sum += S[j]
        addi    s1,s1,4             # advance S pointer
        addi    a5,sp,256           # a5 = end of S[]
        bne     s1,a5,.L4           # loop if j < N

        # ── LEADER 6: Softmax normalize loop ─────────────────────────
        # S[j] = S[j] / sum   for j = 0..N-1
        # After this, S[] is a probability distribution (sums to 1.0)
        mv      a5,sp               # a5 = &S[0]
.L5:
        flw     fa5,0(a5)           # fa5 = S[j]
        fdiv.s  fa5,fa5,fs0         # fa5 = S[j] / sum
        fsw     fa5,0(a5)           # S[j] = normalized
        addi    a5,a5,4             # advance pointer
        addi    a4,sp,256           # a4 = end of S[]
        bne     a5,a4,.L5           # loop if j < N

        # ── LEADER 7: S*V outer d-loop setup ─────────────────────────
        # Set pointers for output accumulation
        mv      a3,s5               # a3 = V[0][0] base pointer
        mv      a2,s3               # a2 = O[i][0] output pointer

        # ── LEADER 8: S*V outer d-loop header ────────────────────────
        # Iterates d = 0..31  (output head dimension)
        # For each d: acc = sum_j S[j] * V[j][d]  → O[i][d]
.L6:
        mv      a4,a3               # a4 = V[0][d] column pointer
        mv      a5,sp               # a5 = &S[0]
        fmv.s.x fa5,zero            # fa5 = 0.0  (accumulator)

        # ── LEADER 9: S*V inner j-loop ───────────────────────────────
        # Iterates j = 0..63  (sum over sequence length)
        # acc += S[j] * V[j][d]  — second fused multiply-add reduction
.L7:
        flw     fa3,0(a5)           # fa3 = S[j]  (from stack)
        flw     fa4,0(a4)           # fa4 = V[j][d]
        fmadd.s fa5,fa3,fa4,fa5    # fa5 = S[j]*V[j][d] + fa5
        addi    a5,a5,4             # advance S pointer
        addi    a4,a4,128           # V[j][d] → V[j+1][d]  (row stride=128B)
        addi    a1,sp,256           # a1 = end of S[]
        bne     a5,a1,.L7           # loop if j < N
        fsw     fa5,0(a2)           # O[i][d] = acc  (store result)
        addi    a2,a2,4             # advance O pointer to O[i][d+1]
        addi    a3,a3,4             # advance V column pointer
        bne     a3,s2,.L6           # loop if d < D  (s2 = V base + 128)

        # ── LEADER 10: Outer i-loop increment ────────────────────────
        # Advance row pointers and loop back to .L2
        addi    s3,s3,128           # O[i] → O[i+1]  (row stride = D*4 = 128B)
        addi    s0,s0,128           # Q[i] end → Q[i+1] end
        bne     s0,s4,.L2           # loop if i < N  (s4 = end of Q)

        # ── FUNCTION EPILOGUE ────────────────────────────────────────
        # Restore all callee-saved registers and return
        ld      ra,328(sp)
        ld      s0,320(sp)
        ld      s1,312(sp)
        ld      s2,304(sp)
        ld      s3,296(sp)
        ld      s4,288(sp)
        ld      s5,280(sp)
        ld      s6,272(sp)
        fld     fs0,264(sp)
        fld     fs1,256(sp)
        addi    sp,sp,336           # restore stack pointer
        jr      ra                  # return to caller

        .size   sdpa, .-sdpa

        # ── READ-ONLY DATA ───────────────────────────────────────────
        .section        .srodata.cst4,"aM",@progbits,4
        .align  2
.LC0:
        .word   1043662067          # IEEE 754 float32: 1/sqrt(32)
        #                             = 0x3D3504F3
        #                             = 0.1767766922712326049804...
        #                             GCC folded sqrtf(32) at compile time

        .ident  "GCC: (g5115c7e447f-dirty) 15.2.0"
        .section        .note.GNU-stack,"",@progbits
