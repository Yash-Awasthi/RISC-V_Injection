# `docs/` — Deep-dive documentation set

This directory contains the long-form documentation for the `attn`
custom-instruction project. The numbering reflects a reading order
that goes from "I have never seen GIMPLE" to "I am evaluating this
as a research artefact". Every file is self-contained — you can
land on any of them via a search engine and still make sense of it.

---

## How to use this directory

There is no script to run here; this is pure prose. The intended
workflow is:

1. Open the top-level [`README.md`](../README.md) for the overview
   and the "How to run the program" recipe.
2. Pick an entry-point below that matches your background.
3. Cross-references between docs are explicit; follow them as you go.

If you want to *reproduce* the build, jump to
[`03-build-and-run.md`](03-build-and-run.md) and follow it
top-to-bottom.

---

## Files in this directory

| # | File | Audience | One-line summary |
|---|------|----------|------------------|
| 00 | [`00-background.md`](00-background.md) | Undergraduate | RISC-V, attention, the GCC pipeline, GIMPLE/SSA, custom instructions — from zero. |
| 01 | [`01-instruction-spec.md`](01-instruction-spec.md) | Undergraduate + supervisor | ISA-style specification of `attn`: encoding, semantics, ABI, worked decoding. |
| 02 | [`02-compiler-pass.md`](02-compiler-pass.md) | Undergraduate + supervisor | The `attnrec` pass — how it detects SDPA and emits the instruction. |
| 03 | [`03-build-and-run.md`](03-build-and-run.md) | Undergraduate | How to build the toolchain and verify each layer of the modification. |
| 04 | [`04-patches-and-files.md`](04-patches-and-files.md) | Undergraduate + supervisor | Every file that changed, the exact diff, and the *reason* for each change. |
| 05 | [`05-troubleshooting.md`](05-troubleshooting.md) | Implementer | Every ICE / build error encountered during development, with root cause and fix. |
| 06 | [`06-extending-toolchain.md`](06-extending-toolchain.md) | Researcher | A template for adding *any* new RISC-V custom instruction (the recipe behind `scripts/`). |
| 07 | [`07-research-context.md`](07-research-context.md) | Supervisor | Related work, novelty claim, limitations, and future research directions. |
| 08 | [`08-glossary.md`](08-glossary.md) | Undergraduate | Every acronym and term used in this repository, defined. |
| 09 | [`09-demo-walkthrough.md`](09-demo-walkthrough.md) | Undergraduate | File-by-file walkthrough of `demo/` and `demo/failures/`. |
| 10 | [`10-scripts-pipeline.md`](10-scripts-pipeline.md) | Researcher / Implementer | File-by-file reference for the `scripts/` generic pipeline. |

---

## Reading order by audience

**Undergraduate, "what is going on here"**

`00 → 01 → 02 → 03 → 09`. Read 04 only when you want to know what was
*actually* edited; read 05 only when you hit one of the errors.

**Research supervisor / reviewer, "is this a real contribution"**

`07 → 02 → 01 → 04 → 05`. Doc 07 positions the work; docs 02 and 04
substantiate it; doc 05 demonstrates depth of engagement with the
toolchain internals.

**Implementer, "I want to add my own custom instruction"**

`06 → 10 → 04 → 02 → 05`. Doc 06 is the template, doc 10 is the
file-by-file reference for the generic pipeline; the others are
reference material as you work.

**Just trying to reproduce the build**

`03 → 05` (the latter only if something fails).

---

## Cross-references with the rest of the repo

* The reference SDPA test program documented throughout these files
  lives in [`../demo/sdpa_test.c`](../demo/sdpa_test.c). The
  expected assembly output and the post-pass GIMPLE dump are next to
  it. A file-by-file walkthrough of `demo/` is in
  [`09-demo-walkthrough.md`](09-demo-walkthrough.md).
* The generic pipeline that *generalises* the recipe in
  [`06-extending-toolchain.md`](06-extending-toolchain.md) is
  implemented in [`../scripts/`](../scripts/) and documented
  file-by-file in [`10-scripts-pipeline.md`](10-scripts-pipeline.md).
  The two are kept consistent: any change to the recipe in doc 06
  should also be reflected in `scripts/lib/snippets.py`.
* Every concrete file path mentioned in [`04-patches-and-files.md`](04-patches-and-files.md)
  is verified against the actual contents of `../gcc/` and
  `../binutils/`. If you find a drift, please open an issue.

---

## Conventions used in these docs

* Code blocks are language-tagged (`bash`, `c`, `cpp`, `asm`, `make`)
  so they render correctly on GitHub and Markdown viewers.
* Long file paths are quoted in backticks; clickable links use
  Markdown link syntax.
* Section anchors follow GitHub's slugification rules so deep links
  (`docs/02-compiler-pass.md#7-…`) resolve correctly.
* Acronyms are expanded on first use, then defined again in
  [`08-glossary.md`](08-glossary.md).
