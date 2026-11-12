# RiscBasic

A BASIC-like language for RISC-V, compiled directly to machine code: single pass, no linker, for cores with 8 KB of code memory or less.

Language design: Antti Lukats (Identor #1). Compiler: Claude (Identor #9). 10 November 2026 (7 AG).

- `RiscBasic-Spec.md`: the language specification (Draft 0.25)
- `riscbasic.py`: the compiler (Python 3, no dependencies)
- `rbsim.py`: a small RV32I/M/C/Zbb simulator used for testing
- `mem2elf.py`: converts a `.mem` file into an ELF executable
- `tests/run_tests.py`: the test suite
- `tests/check_gnu.py`: cross-check against the GNU RISC-V toolchain (objdump, objcopy, readelf)
- `examples/example.bas`: the example from spec section 12
- `examples/include_demo.bas`: uses `.include "lib/uart0.bas"` for the console declarations
- `examples/conditional.bas`: `#IF` builds for two boards, with and without debug output

## Usage

```
python riscbasic.py program.bas            # writes program.bin .hex .mem .lst .elf
python riscbasic.py program.bas -o build/x # other output name
python riscbasic.py program.bas --no-lst   # no listing (--no-elf: no ELF, --no-debug: no DWARF)
python riscbasic.py program.bas -I lib     # also search lib/ for .include files
python riscbasic.py program.bas -D DEBUG -D BOARD=2   # CONSTs for #IF (default value 1)
python mem2elf.py program.mem --base 0 --arch rv32imc   # .mem -> .elf
python rbsim.py program.bin                # run in the test simulator
python tests/run_tests.py                  # run all tests
python tests/check_gnu.py                  # compare with GNU objdump (needs a RISC-V toolchain)
```

Outputs:

- `.bin`: raw image from the `CODE` address
- `.hex`: Intel HEX
- `.mem`: one 32-bit word per line, for `$readmemh`
- `.lst`: source lines as comments, address, machine code, disassembly, and a size summary at the end
- `.elf`: ELF32 RISC-V executable (v0.6)

## ELF output

The `.elf` file loads directly into GDB, OpenOCD, Spike, Renode or a simulator testbench, and `objdump -d` disassembles it with all names. It contains:

- one loadable segment with the code and constant data at the `CODE` address, entry point `__start`
- `.bss` describing the RAM variables (the startup code clears it; nothing is loaded there)
- a symbol table: `__start`, every SUB and FUNCTION and runtime routine as a function with its size, IRQ handlers, user labels, generated labels, strings, and RAM variables with their sizes (`SUB.local` for variables declared inside a SUB). `$x` / `$d` mapping symbols tell disassemblers where code ends and data begins.
- DWARF 3 debug information (v0.8): a line table that maps every instruction to its line in the `.bas` file, and a compile unit that names the source file and its directory. Startup code, runtime routines and data are marked as having no source line, so stepping does not land in them. A `main` symbol marks where the BASIC program starts, after the startup code.
- the ISA string in `.riscv.attributes` (for example `rv32i2p1_m2p0_c2p0_zicsr2p0`) and the RVC / RVE flags in the header

`mem2elf.py` makes an ELF from a `.mem` file alone. A `.mem` file has no addresses or names, so give `--base` and `--arch`. The result has the code and `__start`, but no other symbols. When the source is available, use the compiler's own `.elf`.

`tests/check_gnu.py` compiles every test program for every target and checks it with GNU binutils: each instruction decoded by `objdump -M no-aliases` must match the compiler's listing in address, encoding, mnemonic and registers; the DWARF line table decoded by `readelf` must match the compiler's own address-to-line map; `objcopy -O binary` must give the `.bin` back; `readelf -a` and the DWARF dumps must give no warnings; and `mem2elf` must reproduce the `.mem` contents. Result with GNU binutils 2.45: 106 programs, 16,726 instructions and 2,247 line rows, all match.

### Source-level debugging

With the DWARF information, debuggers show the `.bas` file and step one BASIC line at a time. Keep the `.bas` file in the directory where it was compiled (or tell the debugger where it is).

GDB, for example `riscv-none-elf-gdb program.elf` connected to the target:

```
break main
break program.bas:34
next
list
x/wd &PRESSES
```

XSDB (Vitis), after `connect`, `target` and `dow program.elf`:

```
bpadd -file program.bas -line 34
con
nxt
```

Variable names are upper case in the ELF (RiscBasic names are not case sensitive). Variables are available as addresses (`&PRESSES`), not as typed variables: the DWARF information covers source lines, not types. GDB on Windows may print a warning about converting `main` from the host encoding; it also does this for GCC-built programs and does not affect debugging.

## Compressed instructions and branch shrinking

On `RV32IC` and `RV32EC` targets, every instruction that has a 16-bit RVC form is emitted compressed. The listing shows these with 4 hex digits and the RVC mnemonic (`c.li`, `c.addi`, `c.mv`, `c.j`, ...).

Code is generated in one pass. Branches, jumps, `%hi`/`%lo` address loads and alignment padding stay relocatable until the end. Then a final layout step (v0.3):

1. Shrinks each `beq`/`bne` against zero to `c.beqz`/`c.bnez` (±256 bytes), and each `jal zero`/`jal ra` to `c.j`/`c.jal` (±2 KB), when the target is in range.
2. Recomputes all addresses and repeats until nothing changes. Shrinking only shortens distances, so this ends. Alignment padding can grow by 2 bytes, so a branch pushed out of range by it goes back to 32-bit and stays there.
3. Turns a conditional branch beyond ±4 KB into an inverted branch over a `jal` (8 bytes), on every target. Before v0.3 this was a compile error.

Data and the trap vector are aligned to 4 bytes with `c.nop`.

Spec example (section 12):

| | RV32I | RV32IC | saved |
|---|---|---|---|
| program | 216 | 154 | 29% |
| runtime routines | 376 | 226 | 40% |
| data (strings, table) | 76 | 76 | – |
| **total** | **668** | **456** | **32%** |

Steps over all test programs (RV32IC): branch shrinking saved 3.3%, runtime calls through `RA` (v0.4) 6.3%, and compiler temporaries in x8–x15 (v0.5) a further 4.2%. Every call and jump is 16-bit, and so is every test against zero. The branches that stay 32-bit compare two registers (`blt`, `bgeu`, `beq a, b`); RVC has no such forms.

## Status and differences from the spec

Implemented: everything in Draft 0.25 sections 2–11, including active-low `NOT` bits and fields (3.2.1), except for the points below.

1. **Compressed instructions**: see above.
2. **Recursion.** Parameters and `DIM` inside a SUB are not saved across a recursive call (locals are static RAM). Recursion works only if the SUB does not need its parameters after the recursive call.
3. **Stack checking** is limited to: RAM variables + `STACK` must fit in `RAM SIZE`. Call depth is not analysed.
4. **`CASE`** takes constants, ranges and `IS` comparisons (`CASE 1, 5 TO 9, IS > 100`). The values must be constants.
5. **IRQ names.** `ON IRQ` takes a number or `MSOFT` (3), `MTIMER` (7), `MEXT` (11). The spec's `ON IRQ TIMER0` needs `CONST TIMER0 = 7`. Interrupts are enabled with inline ASM (`csrsi mstatus, 8`).
6. **`FOR`** compares the counter signed, so `FOR S2 = 10 TO 0 STEP -1` stops correctly.
7. **`DELAY_US` / `DELAY_MS`** assume 2 instructions per loop pass times `CPI`. Calibrate `CPI` on real hardware.
8. **Generated code** can differ slightly from the spec's example tables in the choice of temporary.

## Internals

- Compiler temporaries: `s0` and `s1` carry values, `a5` holds device base addresses (v0.5). All three are in x8–x15, so `c.lw`, `c.sw`, `c.andi`, `c.beqz` and the other 3-bit-register RVC forms apply to compiler code. `t0`–`t2` are free for user variables.
- Runtime routines (`__mul`, `__divu`, `__div`, and per-device `__putc_`, `__puts_`, `__putbuf_`, `__puthex_`, `__getc_`, `__getdec_`, `__gethex_`, `__getline_`, and for strings `__sbegin`, `__sapp_chr`, `__sapp_str`, `__sapp_dec`, `__sapp_hex`, `__strlen`, `__strcmp`, `__sapp_mid`, `__lenmid`, `__putmid_`, `__instr`, `__val`, `__putdec_`) are emitted once, after the program, only if used. They are called with `jal ra` (2 bytes as `c.jal`) and return with `ret`. They take arguments in `s0`/`s1`, save every other register they use, and save `RA` when they call another routine.
- `RA` is therefore not a safe place for a value: SUB and FUNCTION calls, PRINT and software multiply/divide overwrite it. SUBs and IRQ handlers save `RA` on entry. The compiler warns if a program uses `RA` (`R1`) directly and also makes calls.
- Register variables are global: a SUB may change them, and only `RA` is saved by the callee (spec section 7). A global register variable in an argument register is saved by the caller around calls that use that register.
- Strings are built with a small state block in RAM (`__sbstate`: current position and end). When a string `FUNCTION` is called while another string is being built, the caller saves and restores that state around the call. Overlapping assignments build in a scratch buffer: `__strtmp` for the main program and `__tmp_NAME` for each string FUNCTION, each sized to the largest target it needs.
- RAM variables are addressed relative to `GP = RAM base + 2048`, so the first 4 KB of RAM are one instruction away.
- `DEC` uses a power-of-ten table, so it needs no multiply or divide.
- Interrupts: one dispatcher at `mtvec` saves the caller-saved registers, reads `mcause` and calls the matching `ON IRQ` block.
