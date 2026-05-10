/* batchnorm_demo.c — Whole-tensor batch normalisation as one custom
 * RISC-V instruction.
 *
 * Way-2 input file for customrv.py.  The mathematics
 *   y[i] = gamma * (x[i] - mu) / sqrt(var + eps) + beta
 * is left to whatever hardware sits behind the custom opcode; the
 * compiler pass merely rewrites the marker call into IFN_RISCV_BNORM.
 *
 * Three pointer arguments → R-type, three input registers.
 *
 * Build/integrate with:
 *   python3 customrv.py from-c examples/batchnorm_demo.c \
 *       --mnemonic bnorm --apply --build
 */

extern void __custom_bnorm(float *x, float *gamma_beta, float *out);

void batchnorm_demo(float *x, float *gamma_beta, float *out)
{
    __custom_bnorm(x, gamma_beta, out);
}
