#!/usr/bin/env python3
"""
RISC-V Custom Instruction Injection Toolkit
Main entry point wiring all services together.

Services:
- accelerator_designer: ML hardware accelerator design (gemmini, flash-attention, etc.)
- compiler_patterns: Custom instruction compilation (LLVM, GCC, SPIKE)
- simulation_manager: Multi-backend simulation (gem5, QEMU, spike, renode)
- instruction_encoder: Binary instruction encoding/decoding
- toolchain_plugin: Toolchain integration plugins
- attn_model: Attention module for custom ISA extensions
"""

import sys
import json
from pathlib import Path

# Ensure services/ is importable
sys.path.insert(0, str(Path(__file__).parent / "services"))
sys.path.insert(0, str(Path(__file__).parent / "backend" / "services"))
sys.path.insert(0, str(Path(__file__).parent / "tools"))


def main():
    """Entry point for the RISC-V injection toolkit."""
    print("=" * 60)
    print("  RISC-V Custom Instruction Injection Toolkit")
    print("=" * 60)
    print()

    # List available services
    services = [
        ("accelerator_designer", "ML hardware accelerator design"),
        ("compiler_patterns", "Custom instruction compilation"),
        ("simulation_manager", "Multi-backend simulation"),
        ("instruction_encoder", "Binary instruction encoding"),
        ("toolchain_plugin", "Toolchain integration"),
        ("attn_model", "Attention module ISA extensions"),
    ]

    print("Available services:")
    for i, (name, desc) in enumerate(services, 1):
        print(f"  {i}. {name} — {desc}")

    print()
    print("Usage:")
    print("  from services.accelerator_designer import AcceleratorDesigner")
    print("  from services.simulation_manager import SimulationManager")
    print("  from services.compiler_patterns import CompilerPatterns")
    print("  from instruction_encoder import InstructionEncoder")
    print()

    # Quick demo: encode a sample instruction
    try:
        from instruction_encoder import InstructionEncoder
        encoder = InstructionEncoder()
        print("InstructionEncoder: ready")
    except ImportError:
        print("InstructionEncoder: not available (missing dependencies)")
    except Exception as e:
        print(f"InstructionEncoder: {e}")

    # Demo: instruction simulator
    try:
        from instruction_simulator import InstructionSimulator, CUSTOM_INSTRUCTIONS
        sim = InstructionSimulator()
        print(f"InstructionSimulator: ready ({len(CUSTOM_INSTRUCTIONS)} custom instructions loaded)")
        print()
        print("Custom instructions:")
        for instr in CUSTOM_INSTRUCTIONS:
            print(f"  {instr.name:12s} — {instr.description}")
            print(f"               Category: {instr.category}, Latency: {instr.latency_cycles} cycles")
        print()

        # Quick simulation demo
        sim.set_register(1, 0x0F0F0F0F)
        sim.set_register(2, 0xF0F0F0F0)
        print("Registers set:")
        print(sim.format_register_file())
        print()
        print(sim.get_performance_stats())
    except ImportError:
        print("InstructionSimulator: not available (missing dependencies)")
    except Exception as e:
        print(f"InstructionSimulator: {e}")

    # Demo: instruction codec (encode/decode/analyze)
    print()
    print("-" * 60)
    try:
        from instruction_codec import InstructionCodec
        codec = InstructionCodec()
        print("InstructionCodec: ready")
        print()

        # Encode some common instructions
        # ADDI x1, x0, 42  → addi x1, x0, 42
        addi = codec.encode_i(imm=42, rs1=0, funct3=0b000, rd=1)
        decoded = codec.decode(addi)
        print(f"  Encoded: 0x{addi:08x}")
        print(f"  Decoded: {decoded.assembly}")
        print(f"  Category: {decoded.category}, Mnemonic: {decoded.mnemonic}")
        print()

        # ADD x3, x1, x2
        add = codec.encode_r(funct7=0, rs2=2, rs1=1, funct3=0b000, rd=3)
        decoded_add = codec.decode(add)
        print(f"  Encoded: 0x{add:08x}")
        print(f"  Decoded: {decoded_add.assembly}")
        print()

        # Analyze injection surface
        test_program = [addi, add, codec.encode_s(imm=16, rs2=3, rs1=1, funct3=0b010)]
        analysis = codec.analyze_injection_surface(test_program)
        print("  Injection Surface Analysis:")
        print(f"    Instructions: {analysis['total_instructions']}")
        print(f"    Categories: {analysis['categories']}")
        print(f"    Memory accesses: {len(analysis['memory_accesses'])}")
        print(f"    Control flow: {len(analysis['control_flow'])}")
        print(f"    System calls: {len(analysis['system_calls'])}")
        print(f"    Potential vectors: {len(analysis['potential_vectors'])}")
    except ImportError:
        print("InstructionCodec: not available (missing dependencies)")
    except Exception as e:
        print(f"InstructionCodec: {e}")


if __name__ == "__main__":
    main()
