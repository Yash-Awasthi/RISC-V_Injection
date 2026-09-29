"""
Hardware Accelerator Design Service
Extracted from inspiration repos:
  gemmini, gemmini-rocc-tests, flash-attention, attention_acc,
  dl_accelerator, garuda-accelerator, coralnpu, tiny-npu,
  ml-accelerators, riscv-tinyml-accelerator, redmule, taidl,
  hardware-implementation-of-pipelined-parallel-transformer-attention-,
  integrated-matrix-extension, riscv-attached-matrix-extension,
  riscv-dot-product, hwpe-mac-engine, darkriscv, aladdin,
  dana, knu_iss, kohakutpu, neural-networks-on-silicon

Provides patterns for designing ML hardware accelerators that attach
to a RISC-V core via custom instructions (ROCC or matrix extensions).
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum
import math


class AcceleratorType(Enum):
    """Types of ML accelerators from inspiration repos."""
    GEMM = "gemm"              # Matrix multiply (gemmini pattern)
    ATTENTION = "attention"    # Transformer attention (flash-attention pattern)
    CONV = "conv"               # Convolution (NPU pattern)
    DOT_PRODUCT = "dot"         # Vector dot product (riscv-dot-product)
    MAC_ENGINE = "mac"          # Multiply-accumulate (hwpe-mac-engine)
    TPU = "tpu"                 # Tensor processing unit (kohakutpu)
    NPU = "npu"                 # Neural processing unit (coralnpu)


class AttachmentInterface(Enum):
    """How the accelerator attaches to the RISC-V core."""
    ROCC = "rocc"               # Rocket Custom Coprocessor
    MATRIX_EXT = "matrix"       # Matrix extension (integrated or attached)
    CUSTOM_CSR = "csr"          # Custom CSR-based interface
    MEMORY_MAPPED = "mmio"      # Memory-mapped I/O


@dataclass
class AcceleratorConfig:
    """Configuration for a hardware accelerator.
    Extracted from gemmini, coralnpu, and garuda-accelerator patterns.
    """
    name: str
    accel_type: AcceleratorType
    interface: AttachmentInterface
    # Compute parameters (gemmini-inspired)
    mesh_rows: int = 16         # Systolic array dimensions
    mesh_cols: int = 16
    pe_pipeline_stages: int = 1
    # Memory parameters
    scratchpad_kb: int = 256    # Scratchpad SRAM size
    accumulator_kb: int = 128   # Accumulator SRAM size
    # Data widths
    data_width_bits: int = 8    # Input data width (INT8, BF16, FP16)
    accumulator_width_bits: int = 32
    # Performance
    clock_ghz: float = 1.0
    latency_cycles: int = 1     # PE latency
    pipelined: bool = True
    # Custom instructions
    custom_instruction_opcodes: List[str] = field(default_factory=list)


@dataclass
class GEMMOp:
    """A single GEMM operation on the accelerator (gemmini pattern)."""
    m: int    # M dimension
    k: int    # K dimension (reduction)
    n: int    # N dimension
    a_addr: int = 0    # Scratchpad address of A matrix
    b_addr: int = 0    # Scratchpad address of B matrix
    c_addr: int = 0    # Accumulator address of C matrix
    bias: bool = False
    activation: str = ""   # relu, sigmoid, etc.


@dataclass
class PerformanceEstimate:
    """Performance estimate for an accelerator configuration."""
    peak_flops: float = 0.0       # Peak FLOPs/s
    peak_tops: float = 0.0        # Peak TOPS (INT8)
    utilizable_tops: float = 0.0  # Achievable TOPS at target utilization
    throughput_gops_s: float = 0.0
    power_watts: float = 0.0
    efficiency_gops_w: float = 0.0


class AcceleratorDesigner:
    """
    Designs and configures ML hardware accelerators.
    Extracted from gemmini, flash-attention, coralnpu, garuda-accelerator.
    """

    def __init__(self, config: AcceleratorConfig):
        self.config = config

    def estimate_performance(self, utilization: float = 0.7) -> PerformanceEstimate:
        """Estimate peak and achievable performance.

        Extracted from gemmini's analytical model and ml-accelerators patterns.
        For a systolic array of mesh_rows x mesh_cols processing data_width_bits:

        TOPS = mesh_rows * mesh_cols * 2 * clock_ghz / 1000
        (factor of 2 for multiply + accumulate)
        """
        r, c = self.config.mesh_rows, self.config.mesh_cols
        clk = self.config.clock_ghz

        # Each PE does 2 ops/cycle (multiply + add)
        ops_per_cycle = r * c * 2
        peak_gops = ops_per_cycle * clk  # GOPS (giga-ops/s)

        # For INT8, this is also TOPS
        peak_tops = peak_gops / 1000 if self.config.data_width_bits == 8 else peak_gops / 1000

        utilizable = peak_tops * utilization

        # Rough power estimate: each PE ~1mW at 1GHz (gemmini reference)
        pe_power_mw = 1.0 * clk  # mW per PE
        total_power = (r * c * pe_power_mw) / 1000  # watts
        efficiency = utilizable / total_power if total_power > 0 else 0

        return PerformanceEstimate(
            peak_flops=peak_gops * 1e9,
            peak_tops=peak_tops,
            utilizable_tops=utilizable,
            throughput_gops_s=peak_gops * utilization,
            power_watts=total_power,
            efficiency_gops_w=efficiency * 1000,
        )

    def estimate_gemm_latency(self, op: GEMMOp) -> int:
        """Estimate cycles for a GEMM operation on the systolic array.

        Extracted from gemmini's cycle model:
        cycles = (K / mesh_cols) * (M / mesh_rows) + M + K + fill_drain
        """
        r, c = self.config.mesh_rows, self.config.mesh_cols
        k_blocks = math.ceil(op.k / c)
        m_blocks = math.ceil(op.m / r)
        # Compute + fill/drain overhead
        compute_cycles = k_blocks * max(m_blocks, 1)
        fill_drain = r + c + self.config.latency_cycles
        total = compute_cycles + fill_drain
        if self.config.pipelined:
            total -= r  # pipelining overlaps fill
        return max(total, 1)

    def estimate_attention_latency(
        self, seq_len: int, d_model: int, num_heads: int = 1
    ) -> int:
        """Estimate cycles for a self-attention operation.

        Attention = QK^T (GEMM) + softmax + AV (GEMM)
        Extracted from flash-attention and attention_acc patterns.
        """
        # QK^T: (seq, d) x (d, seq) -> (seq, seq)
        qkt = self.estimate_gemm_latency(GEMMOp(
            m=seq_len, k=d_model, n=seq_len,
        ))
        # Softsoftmax: ~seq_len cycles (elementwise)
        softmax_cycles = seq_len
        # AV: (seq, seq) x (seq, d) -> (seq, d)
        av = self.estimate_gemm_latency(GEMMOp(
            m=seq_len, k=seq_len, n=d_model,
        ))
        return (qkt + softmax_cycles + av) * num_heads

    def generate_verilog_header(self) -> str:
        """Generate a Verilog module header for the accelerator.
        Extracted from darkriscv and hwpe-mac-engine patterns.
        """
        w = self.config.data_width_bits
        aw = self.config.accumulator_width_bits
        return f"""// Auto-generated accelerator module header
// Extracted from gemmini/hwpe-mac-engine patterns
// Type: {self.config.accel_type.value}, Interface: {self.config.interface.value}
// Mesh: {self.config.mesh_rows}x{self.config.mesh_cols}, Data: INT{w}, Acc: INT{aw}

module {self.config.name} #(
    parameter MESH_ROWS = {self.config.mesh_rows},
    parameter MESH_COLS = {self.config.mesh_cols},
    parameter DATA_WIDTH = {w},
    parameter ACC_WIDTH = {aw},
    parameter SPAD_KB = {self.config.scratchpad_kb},
    parameter ACC_KB = {self.config.accumulator_kb}
) (
    input  logic                        clk,
    input  logic                        rst_n,

    // RISC-V custom instruction interface ({self.config.interface.value})
    input  logic [31:0]                 inst,
    input  logic [DATA_WIDTH-1:0]      rs1,
    input  logic [DATA_WIDTH-1:0]      rs2,
    output logic [ACC_WIDTH-1:0]       rd,
    output logic                        inst_valid,

    // Memory interface (scratchpad)
    output logic [31:0]                 spad_addr,
    output logic                        spad_we,
    output logic [DATA_WIDTH-1:0]      spad_wdata,
    input  logic [DATA_WIDTH-1:0]      spad_rdata,

    // Interrupt
    output logic                        irq
);

    // Systolic array
    logic [ACC_WIDTH-1:0] pe_array [0:MESH_ROWS-1][0:MESH_COLS-1];

    // TODO: Implement PE array, scratchpad controller, and instruction decoder

endmodule
"""

    def generate_custom_instructions(self) -> List[Dict[str, Any]]:
        """Generate custom instruction definitions for ROCC or matrix extension.
        Extracted from gemmini-rocc-tests and riscv-attached-matrix-extension.
        """
        if self.config.interface == AttachmentInterface.ROCC:
            # ROCC uses custom0-3 opcodes (0x0B, 0x2B, 0x5B, 0x7B)
            rocc_opcodes = ["custom-0", "custom-1", "custom-2", "custom-3"]
            return [
                {
                    "name": f"{self.config.name}_compute",
                    "opcode": rocc_opcodes[i % 4],
                    "funct3": i,
                    "format": "R",
                    "description": f"Trigger compute on {self.config.name}",
                }
                for i in range(min(4, len(self.config.custom_instruction_opcodes) or 4))
            ]
        elif self.config.interface == AttachmentInterface.MATRIX_EXT:
            return [
                {
                    "name": "mma",
                    "opcode": "0x2C",  # Matrix opcode space
                    "format": "R4",
                    "description": "Matrix multiply-accumulate",
                },
                {
                    "name": "mld",
                    "opcode": "0x2C",
                    "funct3": 1,
                    "format": "I",
                    "description": "Load matrix to scratchpad",
                },
                {
                    "name": "mst",
                    "opcode": "0x2C",
                    "funct3": 2,
                    "format": "S",
                    "description": "Store matrix from accumulator",
                },
            ]
        return []

    def recommend_config(
        self,
        target_tops: float,
        max_mesh_size: int = 64,
    ) -> AcceleratorConfig:
        """Recommend an accelerator configuration for a target TOPS.

        Finds the smallest mesh that achieves the target performance.
        """
        best = None
        for size in range(4, max_mesh_size + 1, 4):
            trial = AcceleratorConfig(
                name=self.config.name,
                accel_type=self.config.accel_type,
                interface=self.config.interface,
                mesh_rows=size,
                mesh_cols=size,
                data_width_bits=self.config.data_width_bits,
                accumulator_width_bits=self.config.accumulator_width_bits,
                clock_ghz=self.config.clock_ghz,
            )
            designer = AcceleratorDesigner(trial)
            perf = designer.estimate_performance(utilization=0.7)
            if perf.peak_tops >= target_tops:
                best = trial
                break
        return best or self.config
