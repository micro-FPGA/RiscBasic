# RiscBasic — Language Specification

**Draft 0.12** — 11 November 2026 (7 AG)
Language design: Antti Lukats (Identor #1). Drafting: Claude (Identor #9).

---

## 1. Goals

- As easy as BASIC, as efficient as assembler (the AVRBasic principle).
- Target: RISC-V soft and hard cores with **8 KB code memory** or less.
- **Single-pass, direct translation** from BASIC to machine code. No linker, no object files.
- Hardware is accessed through **objects**. Every hardware action can be written as one assignment: `DEST = SOURCE`.
- Readable output: every compile produces a listing with source line, address, machine code and disassembly side by side.
- The compiler runs on a **PC only** and is written in **Python**. It is not self-hosted on the target.

**Non-goals:** floating point, dynamic memory, string variables, an operating system.

---

## 2. Target and memory declarations

```basic
TARGET RV32IC          ' RV32I | RV32IC | RV32E | RV32EC, optional +M, +Zbb
CLOCK  25_000_000      ' required if DELAY_xx or baud math is used
CODE   AT &H0000 SIZE 8K
RAM    AT &H8000 SIZE 2K
STACK  256             ' top of RAM, grows down
```

- `CLOCK` is required whenever timing statements are used. A missing clock is a compile error, not a silent bug.
- Literals: `123`, `&HFF`, `0xFF`, `&B1010`. `_` is allowed as a separator: `&H4000_0000`.
- Comments start with `'` or `REM`.

---

## 3. Objects

Everything that can appear on either side of `=` is an object with a **kind**, a **width** and an **access method**.

| Kind | Example | Notes |
|---|---|---|
| CPU register | `R10`, `A0`, `SP` | `R0` / `ZERO` is read-only |
| Register bit | `R10.0` or `R10[0]` | single bit |
| Register field | `R10[7:4]` | bits 7 to 4, right-aligned when read |
| Variable | `count` | in RAM, or pinned to a register |
| Memory | `MEM8(addr)`, `MEM16(addr)`, `MEM32(addr)` | raw access; parentheses keep `[ ]` free for fields |
| Device register | `UART0.DATA` | memory-mapped |
| Device bit or field | `GPIO.OUT.3`, `TIMER.CTRL[2:0]` | read-modify-write, or set/clear registers if declared |
| Alias | `LED` | a name for any object above |
| Constant | `CONST BAUD = 115200` | compile-time only |

### 3.1 Register names

Both numeric and ABI names are accepted and are fully interchangeable.

| Numeric | ABI | Numeric | ABI |
|---|---|---|---|
| `R0` | `ZERO` | `R8` | `S0` / `FP` |
| `R1` | `RA` | `R9` | `S1` |
| `R2` | `SP` | `R10`–`R17` | `A0`–`A7` |
| `R3` | `GP` | `R18`–`R27` | `S2`–`S11` |
| `R4` | `TP` | `R28`–`R31` | `T3`–`T6` |
| `R5`–`R7` | `T0`–`T2` | | |

Reserved by the compiler:

- `S0`, `S1` and `A5` (`R8`, `R9`, `R15`) are compiler temporaries. Using them in user code is a compile error. They sit in x8–x15 so that the compiler's own code can use 16-bit RVC forms. `T0`–`T2` are free for user variables.
- `GP` (`R3`) points into RAM for one-instruction variable access (section 3.3). Writing it is a compile error.
- `SP` (`R2`) is the stack pointer. Writing it gives a warning.
- `RA` (`R1`) is the return address. Using it is legal, but the compiler warns if the program also uses `SUB` or `FUNCTION`.

On `RV32E` targets only `R0`–`R15` exist.

### 3.2 Devices

```basic
DEVICE GPIO AT &H4000_0000
  REG OUT  AT +0
  REG IN   AT +4  READONLY
  REG SET  AT +8  SETS OUT      ' write 1 = set bit in OUT
  REG CLR  AT +12 CLEARS OUT    ' write 1 = clear bit in OUT
END DEVICE

DEVICE UART0 AT &H4000_1000
  REG DATA   AT +0 : 8
  REG STATUS AT +4 READONLY
  BIT TXREADY = STATUS.0
END DEVICE

ALIAS LED    = GPIO.OUT.3
ALIAS BUTTON = GPIO.IN.0
```

- `: 8` / `: 16` gives the register width (default 32). It selects `SB` / `SH` / `SW` and `LBU` / `LHU` / `LW`.
- `SETS` and `CLEARS` tell the compiler it can change single bits with one store instead of read-modify-write.
- `BIT` and `FIELD` give names to parts of a register: `FIELD MODE = CTRL[2:0]`.

### 3.2.1 Inverted bits and fields (NOT)

`NOT` in a `BIT` or `FIELD` declaration makes it active-low. The program always sees the logical value; the compiler inverts on every read and write.

```basic
DEVICE UART1 AT &H4000_1100
  REG DATA   AT +0 : 8
  REG STATUS AT +4 READONLY
  BIT TXREADY = NOT STATUS.0     ' hardware bit 0 = 1 while busy
END DEVICE

DEVICE PORT AT &H4000_0000
  REG OUT AT +0
  REG SET AT +8  SETS OUT
  REG CLR AT +12 CLEARS OUT
  BIT  LED_N  = NOT OUT.5        ' LED lights when the pin is low
  FIELD SEL_N = NOT OUT[11:8]
END DEVICE

ALIAS RESET_N = NOT GPIO.OUT.7   ' NOT also works in ALIAS
```

- **Read:** `x = PORT.LED_N` gives 1 when the hardware bit is 0. A field reads as its bitwise inverse within the field width.
- **Write:** `PORT.LED_N = 1` writes 0 to the hardware bit. With `SETS` / `CLEARS` declared, the compiler swaps the two registers, so a 1 goes to `CLR` and a 0 to `SET`: still one store.
- **Conditions:** `IF`, `WHILE`, `WAIT UNTIL` and `LOOP UNTIL` test the hardware bit the other way round, at no extra cost.
- **PRINT:** a device whose `TXREADY` is declared with `NOT` waits while the hardware bit is 1.
- `NOT` applied twice cancels: `ALIAS X = NOT PORT.LED_N` is the raw pin.
- `NOT` needs a bit or field; a whole register cannot be declared inverted.

### 3.3 Variables

```basic
DIM count AS WORD IN R10    ' pinned to a register
DIM flags AS BYTE           ' in RAM
DIM buf(16) AS BYTE         ' array in RAM
```

Types: `BIT`, `BYTE`, `HALF` (16-bit), `WORD` (32-bit), and signed `SBYTE`, `SHALF`, `SWORD`.

- **RAM variables always live in RAM.** Every read is a load and every write is a store, so the listing is predictable. `count = count + 1` on a RAM variable is load, add, store.
- **Register variables** (`IN Rn`) always live in their register.
- Startup code sets `GP = RAM base + 2048`, so variables in the first 4 KB of RAM are reached with one `LW` / `SW` and a 12-bit offset.
- `BIT` variables in RAM take one byte each.

---

## 4. The assignment rule

`DEST = SOURCE` is translated by looking up the pair (destination kind, source kind). The compiler chooses the shortest correct sequence.

| Statement | Generated code (RV32I) |
|---|---|
| `R1.0 = 1` | `ori x1, x1, 1` |
| `R1.0 = 0` | `andi x1, x1, -2` |
| `R1.12 = 1` | `lui a5, 1` / `or x1, x1, a5` (bit above 10 does not fit in imm12) |
| `R10 = R11.3` | `srli x10, x11, 3` / `andi x10, x10, 1` |
| `R10[7:4] = 5` | `andi x10, x10, -241` / `ori x10, x10, 0x50` |
| `count = count + 1` (RAM) | `lw s0, ofs(gp)` / `addi s0, s0, 1` / `sw s0, ofs(gp)` |
| `GPIO.OUT.3 = 1` (SET declared) | `lui a5, 0x40000` / `li s0, 8` / `sw s0, 8(a5)` |
| `GPIO.OUT.3 = 1` (no SET) | `lw` / `ori` / `sw` (read-modify-write) |
| `LED = BUTTON` | read bit from `IN`, then store to `SET` or `CLR` depending on its value |
| `LED = count > 10` | comparison result (0 or 1) assigned as a bit |
| `UART0.DATA = "A"` | `li s0, 65` / `sb s0, 0(a5)` |

Rules:

- **Width:** a wider source is truncated to the destination width. A narrower source is zero-extended, or sign-extended for signed types. Truncation gives a warning unless the source is masked explicitly.
- **Bits:** any source assigned to a bit destination is true if non-zero.
- **Read-only destinations** (`R0`, `READONLY` registers, constants) are compile errors.
- **Atomic:** `ATOMIC GPIO.OUT.3 = 1` disables interrupts around a read-modify-write.
- **Device base addresses** are loaded once per statement. They are not cached between statements, so each statement's listing stands alone.

---

## 5. Expressions

- Operators: `+ - * / MOD`, `AND OR XOR NOT`, `SHL SHR SAR`, `= <> < > <= >=`.
- `*`, `/` and `MOD` use M-extension instructions if `+M` is in the target. Otherwise the compiler emits a small runtime routine the first time one is used. This **smart inclusion** replaces the linker.
- Constant expressions are always evaluated at compile time.

### 5.1 Shift and rotate commands

In addition to the `SHL` / `SHR` operators in expressions, four **commands** shift or rotate a register or RAM variable in place:

```basic
SHL dest [, count]     ' shift left, zeros in
SHR dest [, count]     ' shift right, zeros in (logical)
ROL dest [, count]     ' rotate left
ROR dest [, count]     ' rotate right
```

- `dest` is a CPU register, a register variable or a RAM variable. Device registers are not allowed; use an assignment instead.
- `count` defaults to 1. It is a constant, a CPU register or a register variable.
- Shifts and rotates work on the **width of the destination**: a `BYTE` rotates within 8 bits, a `HALF` within 16, a `WORD` or CPU register within 32.
- Variable counts are allowed for 32-bit destinations. For `BYTE` and `HALF` destinations, `count` must be a constant.
- RV32I has no rotate instructions, so `ROL` / `ROR` are built from two shifts and an `or`. With `+Zbb` in the target, the compiler uses `rol`, `ror` and `rori` directly.

| Command | Generated code (RV32I) |
|---|---|
| `SHL R10, 3` | `slli x10, x10, 3` |
| `SHR R10, R11` | `srl x10, x10, x11` |
| `SHL count, 2` (RAM `WORD`) | `lw s0, ofs(gp)` / `slli s0, s0, 2` / `sw s0, ofs(gp)` |
| `ROL R10, 4` | `slli s0, x10, 4` / `srli x10, x10, 28` / `or x10, x10, s0` |
| `ROL R10, 4` (`+Zbb`) | `rori x10, x10, 28` |
| `ROL R10, R11` | `sll s0, x10, x11` / `sub s1, zero, x11` / `srl x10, x10, s1` / `or x10, x10, s0` |
| `ROR flags` (RAM `BYTE`) | `lbu s0, ofs(gp)` / `srli s1, s0, 1` / `slli s0, s0, 7` / `or s0, s0, s1` / `sb s0, ofs(gp)` |
| `ROR x` (`BYTE` in `R12`) | as above on `x12`, then `andi x12, x12, 255` |

For RAM `BYTE` and `HALF` variables, the final `sb` / `sh` discards the bits above the variable's width, so no mask is needed.

---

## 6. Control flow

```basic
IF cond THEN ... ELSEIF cond THEN ... ELSE ... END IF
IF cond THEN statement            ' single-line form
WHILE cond ... WEND
DO ... LOOP [UNTIL cond]
FOR i = 0 TO 15 [STEP 1] ... NEXT
SELECT CASE x ... CASE 1, 2 ... CASE ELSE ... END SELECT
GOTO label
EXIT DO | EXIT FOR | EXIT WHILE
```

### 6.1 FOR loop counters

The counter of a `FOR` loop can be a RAM variable, a register variable or a CPU register directly:

```basic
FOR i = 0 TO 15 ... NEXT          ' RAM variable: load / add / store each pass
FOR ticks = 0 TO 99 ... NEXT      ' register variable (DIM ticks AS WORD IN A0)
FOR R12 = 0 TO 15 ... NEXT        ' CPU register, by number
FOR S2 = 10 TO 0 STEP -1 ... NEXT ' CPU register, by ABI name
```

- Register counters are the fastest form: the loop is `addi` plus one compare-and-branch.
- The same register rules apply as for assignments: `S0`, `S1`, `A5`, `GP` and `R0` cannot be loop counters.
- With constant bounds and a register counter, the end value is compared as an immediate where it fits in 12 bits.

---

## 7. Subroutines and interrupts

```basic
SUB Send(ch AS BYTE)            ' parameters in A0-A4, A6, A7
  WAIT UNTIL UART0.TXREADY
  UART0.DATA = ch
END SUB

FUNCTION Add(a AS WORD, b AS WORD) AS WORD
  RETURN a + b
END FUNCTION

ON IRQ TIMER0                    ' sets mtvec, saves only the registers it uses
  TIMER.STATUS.0 = 1
END IRQ
```

- Calling convention: arguments go in `A0`–`A4`, `A6`, `A7` (up to 7; on RV32E `A0`–`A4`, up to 5). `A5` is skipped because it is a compiler temporary. `A0` holds the return value.
- **Register variables are global.** A SUB or FUNCTION sees and may change every register variable, so no registers are saved by the callee except `RA`. This differs from the standard RISC-V ABI, where `S` registers are callee-saved.
- A global register variable that sits in an argument register (`A0`–`A4`, `A6`, `A7`) is saved by the caller around each call that uses that register, and restored afterwards. Inside a SUB, such a global is hidden by the parameter in the same register; using it there is a compile error.
- Recursion is allowed. Stack use is checked against `STACK` at compile time where the call depth is known.

---

## 8. Timing and waits

```basic
WAIT UNTIL UART0.TXREADY
DELAY_US 10
DELAY_MS 100
```

Delay loops are calculated from `CLOCK`. An optional `CPI n` declaration gives the cycles per instruction for multi-cycle soft cores.

---

## 9. PRINT

Deliberately minimal: constant strings, character constants, hex values and decimal values, to a device.

```basic
CONST CR = 13
CONST LF = 10
CONSOLE UART0                    ' default device for PRINT

PRINT "this is a constant", HEX R10, CR, LF
PRINT "count = ", DEC count
PRINT 13, 10
PRINT "key: ", CHR key, CR, LF   ' a character from a variable
PRINT UART1, "to the other UART", CR, LF
```

- `PRINT dev, items` sends to `dev`. `PRINT items` sends to the **console**: the device named in the last `CONSOLE` declaration before the `PRINT`. Without a `CONSOLE` declaration, a `PRINT` without a device is an error.
- `CONSOLE dev` may appear more than once; each applies to the `PRINT` statements after it.
- A device name directly after `PRINT`, followed by `,` or the end of the line, selects the device. Anything else is the first item.
- The device must declare a register `DATA` and a bit `TXREADY`.
- Items, separated by `,` (or `;`): string constants, character constants, `CHR expr`, `HEX expr`, `DEC expr`, and `BYTE` array names. Nothing else.
- A **`BYTE` array name** without an index prints the array as zero-terminated text, as filled by `LINE INPUT`: `PRINT "Hello, ", name`. Printing stops at the first 0 or at the end of the array, so an array without a 0 never prints past its end. Other array types are an error.
- **No line end is added.** Write `13, 10` (or `CR, LF`) where a line should end.
- A **character constant** is a constant expression from 0 to 255, including `CONST` names; it sends that one byte. A variable on its own is an error: use `CHR` for a character, `DEC` or `HEX` for a number.
- `CHR expr` sends the low 8 bits of `expr` as one character. A constant `CHR` (for example `CHR 65`) is the same as the character constant and is merged into strings. If the device's `DATA` register is wider than 8 bits, the compiler masks the value to 8 bits first (one instruction; not needed for `BYTE` sources).
- Adjacent text and character constants are **merged at compile time** into one string: `"done", 13, 10` is stored as `"done\r\n"` and sent with one call. Runs of one or two characters are sent with single-character calls instead, which is smaller. A zero byte cannot be part of a string, so it is sent on its own.
- `HEX` prints **digits only**, no prefix, with uppercase letters. The width follows the source: `BYTE` 2 digits, `HALF` 4, `WORD` and registers 8, with leading zeros.
- `DEC` prints without leading zeros. Signed types (`SBYTE`, `SHALF`, `SWORD`) print a `-` when negative; all other sources print as unsigned.
- `DEC` needs no multiply or divide instructions. It subtracts powers of ten from a small table, so it works on plain `RV32I` and stays small.
- String constants are stored once in code memory, zero-terminated.
- The print routines (character out, string out, hex out, decimal out) are each included once, on first use.

---

### 9.1 INPUT

Reads from a device: the console, or a device named the same way as in `PRINT`.

```basic
CONSOLE UART0
INPUT "Value? ", DEC v          ' prompt, then a decimal number
INPUT CHR c                     ' wait for one character
INPUT UART1, HEX addr           ' explicit device, hexadecimal number
IF UART0.RXREADY THEN INPUT CHR c   ' only if a character is waiting
```

- The device must declare `BIT RXREADY` (`NOT` allowed) and `REG RXDATA`, or receive through `REG DATA` when it has no separate receive register.
- Items, separated by `,`: `"prompt"` (sent before reading; the device must also be able to transmit), `CHR target`, `DEC target`, `HEX target`.
- `CHR` waits for one character and stores it (0–255). No echo.
- `DEC` reads an optional `-` and decimal digits; `HEX` reads hex digits `0`–`9`, `A`–`F`, `a`–`f`. Both echo the characters they accept if the device can transmit. The first other character (Enter, space, comma, ...) ends the number; it is not echoed and not stored. No digits gives 0. Values wrap at 32 bits.
- The target is anything assignable: a variable, array element, register, register bit or field, or device register. The usual assignment rules apply (width, `READONLY`).
- `INPUT` waits until a character arrives. To check without waiting, test `RXREADY` directly.
- No line editing: backspace is not supported in v0.11.

### 9.2 LINE INPUT

Reads a line of text into a `BYTE` array, with echo and backspace.

```basic
DIM name(20) AS BYTE
DIM n AS WORD
LINE INPUT "Name? ", name, n     ' text into name(), number of characters into n
LINE INPUT UART1, name           ' explicit device; the length target is optional
PRINT "Hello, ", name, 13, 10    ' print it back (section 9)
```

- Form: `LINE INPUT [dev,] ["prompt",] buffer [, length]`. The device is chosen as in `PRINT` and `INPUT`.
- `buffer` is a `BYTE` array. The text is **zero-terminated**, so a buffer of N bytes holds up to N−1 characters.
- Enter (CR) ends the line; it is not stored or echoed. LF is ignored, so terminals that send CR LF work too.
- Backspace (8) and DEL (127) remove the last character and erase it on the terminal (`8, 32, 8`).
- Other control characters (below 32) are ignored. Characters 32–255 are stored and echoed. When the buffer is full, further characters are ignored until Enter.
- `length`, if given, receives the number of characters stored. Any assignable target works.

## 10. Inline assembly

```basic
ASM
  csrrsi zero, mstatus, 8
END ASM
```

Variables and aliases can be used by name inside `ASM` blocks.

---

## 11. Compiler output

- Single pass. Forward jumps are backpatched at the end.
- Startup code: set `SP` and `GP`, clear RAM variables, jump to the first statement.
- Output formats: `.bin`, Intel `.hex`, `.mem` for `$readmemh` (BRAM initialisation on FPGAs), and `.elf` (ELF32 RISC-V executable with symbols and DWARF source-line information, for debuggers and simulators).
- Compile error if the code exceeds `CODE SIZE`. The size of each `SUB`, `FUNCTION` and runtime routine is reported.

### 11.1 Listing file (.LST)

A `.LST` file is **always written by default**, next to the source: `program.bas` gives `program.lst`. `--no-lst` turns it off.

- Each RiscBasic source line appears as a comment, with its line number, directly above the instructions generated for it.
- Each instruction line shows the address, the machine code in hex and the disassembly with ABI register names.
- Runtime routines, string constants and startup code are listed the same way, under their own headings.
- The file ends with a summary: code size per `SUB` / `FUNCTION` / runtime routine, total code used of `CODE SIZE`, and RAM used of `RAM SIZE`.

Example (RV32I):

```
; ---- program.bas ----
; 0031  R1.0 = 1
00000040  0010E093   ori   ra, ra, 1
; 0032  R10 = R11.3
00000044  0035D513   srli  a0, a1, 3
00000048  00157513   andi  a0, a0, 1
```

On an `RV32IC` target, compressed instructions show 4 hex digits instead of 8.

---

## 12. Example

```basic
TARGET RV32IC
CLOCK  25_000_000
CODE   AT &H0000 SIZE 8K
RAM    AT &H8000 SIZE 2K

DEVICE GPIO AT &H4000_0000
  REG OUT  AT +0
  REG IN   AT +4  READONLY
  REG SET  AT +8  SETS OUT
  REG CLR  AT +12 CLEARS OUT
END DEVICE

DEVICE UART0 AT &H4000_1000
  REG DATA   AT +0 : 8
  REG STATUS AT +4 READONLY
  BIT TXREADY = STATUS.0
END DEVICE

ALIAS LED    = GPIO.OUT.3
ALIAS BUTTON = GPIO.IN.0

DIM presses AS WORD          ' RAM
DIM ticks   AS WORD IN A0    ' register

presses = 0
ticks = 0
CONSOLE UART0

PRINT "RiscBasic ready", 13, 10

DO
  LED = BUTTON
  IF BUTTON THEN presses = presses + 1
  ticks = ticks + 1
  PRINT "presses ", DEC presses, " ticks ", HEX ticks, 13, 10
  DELAY_MS 100
LOOP
```

---

## 13. Decisions

| # | Question | Decision | Draft |
|---|---|---|---|
| 1 | Register names | Both numeric (`R0`–`R31`) and ABI names | 0.2 |
| 2 | Field syntax | `R10[7:4]`; single bit `R10.0` or `R10[0]` | 0.2 |
| 3 | RAM variables | Always load / modify / store in RAM | 0.2 |
| 4 | Where the compiler runs | PC only | 0.2 |
| 5 | Strings | Minimal `PRINT dev, "const", HEX expr` | 0.2 |
| 6 | `HEX` prefix | Digits only | 0.3 |
| 7 | Decimal output | `DEC expr` included | 0.3 |
| 8 | `FOR` counters in registers | Yes, register variables and CPU registers | 0.3 |
| 9 | Compiler language | Python | 0.3 |
| 10 | Listing file | `.LST` written by default, source lines as comments | 0.4 |
| 11 | Shift and rotate | `SHL`, `SHR`, `ROL`, `ROR` commands for registers and RAM variables | 0.4 |
| 12 | Active-low bits | `NOT` in `BIT`, `FIELD` and `ALIAS` declarations | 0.5 |
| 13 | Compiler temporaries | `S0`, `S1`, `A5` (in x8–x15, for RVC); `T0`–`T2` free for users; parameters skip `A5` | 0.6 |
| 14 | Callee-saved registers | None except `RA`: register variables are global, a SUB may change them | 0.6 |
| 15 | ELF output | `.elf` written by default, with symbols; `--no-elf` turns it off | 0.6 |
| 16 | PRINT line end | No automatic CR LF; character constants (0–255) as items, e.g. `13, 10` | 0.7 |
| 17 | Source-level debugging | DWARF line information for the `.bas` file in the `.elf`; `--no-debug` turns it off | 0.7 |
| 18 | Console | `CONSOLE dev` declaration; `PRINT` without a device uses it | 0.8 |
| 19 | Character values | `CHR expr` in `PRINT` sends the low byte of an expression | 0.9 |
| 20 | Input | `INPUT [dev,] "prompt", CHR / DEC / HEX target`; device needs `RXREADY` and `RXDATA` or `DATA` | 0.10 |
| 21 | Line input | `LINE INPUT [dev,] ["prompt",] buffer [, length]` into a zero-terminated `BYTE` array | 0.11 |
| 22 | Printing text buffers | A `BYTE` array name in `PRINT` prints it as zero-terminated text, bounded by its size | 0.12 |

## 14. Open questions

None at Draft 0.12.
