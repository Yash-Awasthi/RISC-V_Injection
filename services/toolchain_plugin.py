"""
Compiler Toolchain Plugin Service
Extracted from inspiration repos:
  gcc-plugins, gcc-plugin-guide, gcc-python-plugin, gcc-passes,
  llvm-pass-skeleton, llvm-pass-tutorial, llvm-tutor, llvm-rocc-extension,
  llvm-guide, mlir-tutorial, mlir-beginner-friendly-tutorial,
  riscv-gnu-toolchain, riscv-gnu-toolchain-patch, riscv-llvm,
  pulp-riscv-gcc, pulp-riscv-gnu-toolchain, esp-gnu-toolchain,
  riscv-toolchain, riscv-toolchain-conventions,
  riscv-toolchain-conventions-vendor-relocs, xuantie-gnu-toolchain,
  custom-riscv-instruction-toolchain-and-gnu-toolchain-flow,
  customasm, bf16_custom_instruction_toolchain,
  riscv-isa-manual, riscv-opcodes, riscv-asm-manual,
  riscv-elf-psabi-doc, riscv-c-api-doc

Provides patterns for:
- Writing GCC plugins (plugins, passes, custom instruction registration)
- Writing LLVM passes (skeleton, tutorial, custom instruction lowering)
- MLIR dialect definition
- Custom assembler support (customasm pattern)
- Cross-toolchain build configuration
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum
import os


class ToolchainType(Enum):
    GCC = "gcc"
    LLVM = "llvm"
    MLIR = "mlir"
    CUSTOMASM = "customasm"
    BINUTILS = "binutils"


class PassType(Enum):
    """Compiler pass types."""
    ANALYSIS = "analysis"
    OPTIMIZATION = "optimization"
    TRANSFORMATION = "transformation"
    INSTRUCTION_SELECTION = "isel"
    REGISTER_ALLOCATION = "regalloc"
    CODE_GENERATION = "codegen"


@dataclass
class CustomInstructionReg:
    """Registration of a custom instruction in the toolchain.
    Extracted from customasm and gcc-python-plugin patterns.
    """
    mnemonic: str
    format: str          # R, I, S, B, U, J
    opcode: int
    funct3: Optional[int] = None
    funct7: Optional[int] = None
    operands: List[str] = field(default_factory=list)
    description: str = ""
    asm_pattern: str = ""    # e.g. "rd, rs1, rs2"
    binary_pattern: str = ""  # e.g. "funct7(7) rs2(5) rs1(5) funct3(3) rd(5) opcode(7)"


@dataclass
class CompilerPass:
    """A compiler pass definition.
    Extracted from gcc-passes and llvm-pass-tutorial patterns.
    """
    name: str
    pass_type: PassType
    toolchain: ToolchainType
    description: str = ""
    order: int = 0           # Execution order within its phase
    enabled_by_default: bool = True
    dependencies: List[str] = field(default_factory=list)


@dataclass
class ToolchainBuildConfig:
    """Configuration for building a RISC-V cross-toolchain.
    Extracted from riscv-gnu-toolchain and esp-gnu-toolchain patterns.
    """
    toolchain: ToolchainType
    arch: str = "rv64gc"
    abi: str = "lp64d"
    target_triple: str = "riscv64-unknown-elf"
    prefix: str = "/opt/riscv"
    jobs: int = 4
    enable_linux: bool = False
    enable_multilib: bool = True
    custom_instructions: List[CustomInstructionReg] = field(default_factory=list)
    extra_configure_flags: List[str] = field(default_factory=list)


class ToolchainPluginService:
    """
    Manages compiler toolchain plugins and custom instruction registration.
    Extracted from gcc-plugins, llvm-pass-tutorial, customasm, mlir-tutorial.
    """

    def __init__(self, config: ToolchainBuildConfig):
        self.config = config
        self.passes: List[CompilerPass] = []

    def add_pass(self, compiler_pass: CompilerPass):
        """Register a compiler pass."""
        self.passes.append(compiler_pass)
        self.passes.sort(key=lambda p: p.order)

    # ---- GCC plugin generation (from gcc-plugin-guide, gcc-python-plugin) ----

    def generate_gcc_plugin_c(self, passes: List[CompilerPass]) -> str:
        """Generate a GCC plugin C source file.

        Extracted from gcc-plugin-guide and gcc-plugins patterns.
        """
        pass_defs = "\n".join(
            f"static unsigned int pass_{p.name}_gate(void) {{ return 1; }}\n"
            f"static unsigned int pass_{p.name}_exec(void) {{ /* TODO: {p.description} */ return 0; }}\n"
            for p in passes if p.toolchain == ToolchainType.GCC
        )

        pass_structs = "\n".join(
            f"""const pass_data {p.name}_data = {{
    GIMPLE_PASS,                    /* type */
    "{p.name}",                     /* name */
    OPTGROUP_NONE,                  /* optinfo_flags */
    TV_NONE,                        /* tv_id */
    PROP_gimple_any,                /* properties_required */
    0,                              /* properties_provided */
    0,                              /* properties_destroyed */
    {1 if p.enabled_by_default else 0}, /* todo_flags_start */
    TODO_update_ssa | TODO_cleanup_cfg  /* todo_flags_finish */
}};
"""
            for p in passes if p.toolchain == ToolchainType.GCC
        )

        pass_refs = "\n".join(
            f"    register_pass(build_pass(&{p.name}_data, PASS_{p.pass_type.value.upper()},"
            f" \"{p.name}\", 1 /* ref_pass_instance_number */));"
            for p in passes if p.toolchain == ToolchainType.GCC
        )

        return f"""/* Auto-generated GCC plugin
 * Extracted from gcc-plugin-guide and gcc-plugins patterns
 * Target: {self.config.target_triple} ({self.config.arch})
 */

#include "gcc-plugin.h"
#include "plugin-version.h"
#include "tree.h"
#include "gimple.h"
#include "tree-pass.h"
#include "context.h"

int plugin_is_GPL_compatible;

{pass_defs}

{pass_structs}

static struct gimple_opt_pass {passes[0].name if passes else "custom"}_pass = {{
    GIMPLE_PASS,
    NULL,                       /* sub */
    NULL,                       /* next */
    0,                          /* static_pass_number */
    "{passes[0].name if passes else "custom"}", /* name */
    OPTGROUP_NONE,
    TV_NONE,
    PROP_gimple_any,
    0,
    0,
    0,
    0,
    0
}};

int plugin_init(struct plugin_name_args *plugin_info,
                struct plugin_gcc_version *version)
{{
    if (!plugin_default_version_check(version, &gcc_version))
        return 1;

    /* Register passes */
{pass_refs}

    return 0;
}}
"""

    def generate_gcc_custom_instr_header(self) -> str:
        """Generate GCC header for custom instructions.
        Extracted from riscv-gnu-toolchain-patch patterns.
        """
        instr_defs = "\n".join(
            f'  /* {ci.mnemonic}: {ci.description} */\n'
            f'  INSN_NAME("{ci.mnemonic}")'
            for ci in self.config.custom_instructions
        )

        return f"""/* Auto-generated custom instruction definitions for GCC
 * Extracted from riscv-gnu-toolchain-patch and customasm patterns
 * Target: {self.config.arch}, {self.config.abi}
 */

#ifndef RISCV_CUSTOM_INSTR_H
#define RISCV_CUSTOM_INSTR_H

/* Custom instruction mnemonics — use in inline asm or builtins */
{instr_defs}

/* Helper macros for inline asm usage */
#define CUSTOM_R_TYPE(mnemonic, rd, rs1, rs2) \\
    asm volatile(\\
        ".insn r 0x%(opcode)#x, %(funct3)d, %(funct7)d, %%0, %%1, %%2" \\
        : "=r"(rd) : "r"(rs1), "r"(rs2) \\
        : "memory")

#endif /* RISCV_CUSTOM_INSTR_H */
"""

    # ---- LLVM pass generation (from llvm-pass-skeleton, llvm-pass-tutorial) ----

    def generate_llvm_pass_cpp(self, compiler_pass: CompilerPass) -> str:
        """Generate an LLVM pass C++ source file.

        Extracted from llvm-pass-skeleton and llvm-tutor patterns.
        """
        return f"""// Auto-generated LLVM pass
// Extracted from llvm-pass-skeleton and llvm-tutor patterns
// Pass: {compiler_pass.name} ({compiler_pass.pass_type.value})
// Description: {compiler_pass.description}

#include "llvm/Pass.h"
#include "llvm/IR/Function.h"
#include "llvm/Support/raw_ostream.h"

using namespace llvm;

namespace {{

struct {compiler_pass.name.title().replace("_", "")} : Pass {{
  static char ID;
  {compiler_pass.name.title().replace("_", "")}() : Pass(ID) {{}}

  bool runOnFunction(Function &F) override {{
    bool Changed = false;
    errs() << "{compiler_pass.name}: " << F.getName() << "\\n";

    // TODO: Implement pass logic — {compiler_pass.description}
    //
    // Iterate instructions, transform as needed:
    //   for (auto &BB : F) {{
    //     for (auto &I : BB) {{
    //       // Check for custom RISC-V instructions and lower them
    //     }}
    //   }}

    return Changed;
  }}
}};

}} // namespace

char {compiler_pass.name.title().replace("_", "")}::ID = 0;
static RegisterPass<{compiler_pass.name.title().replace("_", "")}> X(
    "{compiler_pass.name}",
    "{compiler_pass.description}",
    false /* Only looks at CFG */,
    false /* Analysis Pass */
);
"""

    # ---- CustomASM generation (from customasm) ----

    def generate_customasm_def(self) -> str:
        """Generate a customasm instruction set definition file.

        Extracted from customasm's .cpuasm format.
        """
        lines = [
            f"# Auto-generated customasm instruction set definition",
            f"# Extracted from customasm inspiration repo patterns",
            f"# Architecture: {self.config.arch}",
            "",
            f"cpu {self.config.target_triple.replace("-", "_")}",
            f"end",
            "",
        ]

        for ci in self.config.custom_instructions:
            lines.append(f"# {ci.mnemonic}: {ci.description}")
            lines.append(f"insn {ci.mnemonic}")
            if ci.binary_pattern:
                lines.append(f"  {ci.binary_pattern}")
            else:
                # Generate from format
                parts = []
                parts.append(f"opcode({7})")
                if ci.format == "R":
                    if ci.funct7:
                        parts.insert(0, f"funct7({7})")
                    parts.insert(1, f"rs2(5)")
                    parts.insert(2, f"rs1(5)")
                    if ci.funct3:
                        parts.insert(3, f"funct3(3)")
                    parts.insert(4, f"rd(5)")
                elif ci.format == "I":
                    parts.insert(0, f"imm(12)")
                    parts.insert(1, f"rs1(5)")
                    if ci.funct3:
                        parts.insert(2, f"funct3(3)")
                    parts.insert(3, f"rd(5)")
                lines.append(f"  {' '.join(parts)}")
            lines.append(f"  {ci.asm_pattern}")
            lines.append("")

        return "\n".join(lines)

    # ---- MLIR dialect generation (from mlir-tutorial) ----

    def generate_mlir_dialect(self, dialect_name: str = "riscv_custom") -> str:
        """Generate an MLIR dialect definition for custom RISC-V instructions.

        Extracted from mlir-tutorial and mlir-beginner-friendly-tutorial.
        """
        ops = "\n".join(
            f"  def {ci.mnemonic.title().replace("_","")}Op : RiscvOp<\"{ci.mnemonic}\">, "
            f"Arguments<(ins AnyInt:$rs1, AnyInt:$rs2)>, Results<(outs AnyInt:$rd)> {{"
            f"  let summary = \"{ci.description}\";"
            f"  let assemblyFormat = \"$rd `,` $rs1 `,` $rs2 attr-dict\";"
            f"}}"
            for ci in self.config.custom_instructions
            if ci.format == "R"
        )

        return f"""// Auto-generated MLIR dialect for custom RISC-V instructions
// Extracted from mlir-tutorial and mlir-beginner-friendly-tutorial patterns
// Dialect: {dialect_name}

include "mlir/IR/OpBase.td"

def {dialect_name.title().replace("_","")}Dialect : Dialect {{
  let name = "{dialect_name}";
  let description = "Custom RISC-V instructions for {self.config.arch}";
  let cppNamespace = "{dialect_name}";
}}

class RiscvOp<string mnemonic, list<Trait> traits = []> :
  Op<{dialect_name.title().replace("_","")}Dialect, mnemonic, traits>;

{ops}
"""

    # ---- Build configuration ----

    def generate_build_script(self) -> str:
        """Generate a build script for the cross-toolchain.
        Extracted from riscv-gnu-toolchain Makefile patterns.
        """
        return f"""#!/bin/bash
# Auto-generated toolchain build script
# Extracted from riscv-gnu-toolchain and esp-gnu-toolchain patterns

set -e

ARCH={self.config.arch}
ABI={self.config.abi}
PREFIX={self.config.prefix}
TARGET={self.config.target_triple}
JOBS={self.config.jobs}

echo "Building {self.config.toolchain.value} toolchain for $ARCH ($ABI)"

case "{self.config.toolchain.value}" in
  gcc)
    git clone https://github.com/riscv/riscv-gnu-toolchain
    cd riscv-gnu-toolchain
    ./configure --prefix=$PREFIX --with-arch=$ARCH --with-abi=$ABI \\
        {"--enable-linux" if self.config.enable_linux else "--enable-multilib" if self.config.enable_multilib else ""}
    make -j$JOBS {"linux" if self.config.enable_linux else "elf"}
    ;;
  llvm)
    git clone https://github.com/llvm/llvm-project
    cd llvm-project
    cmake -G Ninja -B build \\
      -DCMAKE_BUILD_TYPE=Release \\
      -DLLVM_TARGETS_TO_BUILD=RISCV \\
      -DCMAKE_INSTALL_PREFIX=$PREFIX
    cmake --build build -j$JOBS
    cmake --install build
    ;;
esac

echo "Done. Toolchain installed to $PREFIX"
"""

    def get_multilib_variants(self) -> List[Dict[str, str]]:
        """Get recommended multilib variants for this architecture.
        Extracted from riscv-toolchain-conventions.
        """
        if "rv64" in self.config.arch:
            return [
                {"march": "rv64imac", "mabi": "lp64"},
                {"march": "rv64imafdc", "mabi": "lp64d"},
                {"march": "rv64gc", "mabi": "lp64d"},
            ]
        else:
            return [
                {"march": "rv32imac", "mabi": "ilp32"},
                {"march": "rv32imac", "mabi": "ilp32f"},
                {"march": "rv32imafc", "mabi": "ilp32f"},
                {"march": "rv32imafdc", "mabi": "ilp32d"},
                {"march": "rv32gc", "mabi": "ilp32d"},
            ]
