# RiscBasic — Language Specification

**Draft 0.26** — 12 November 2026 (7 AG)
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
- `:` separates statements on one line: `s = 0 : FOR i = 1 TO 4 : s = s + i : NEXT`. Blocks may open and close on the same line. A `:` inside `( )` or `[ ]` (as in `R10[7:4]`) and the width in `REG ... : 8` are not separators. `name:` at the start of a line is a label, unless `name` is a SUB or FUNCTION (then it is a call). `REM` after a `:` comments out the rest of the line.

### 2.1 Include files

```basic
.include "lib/uart0.bas"      ' device declarations, CONSOLE, shared SUBs
```

- The lines of the named file replace the `.include` line, as if they were typed there. The program is still one source and one compile: no linker, no object files.
- The directive stands alone on its line. A `'` comment may follow; a `:` separator may not. `.include` is not case sensitive.
- A relative name is searched first in the directory of the file that contains the `.include`, then in the directories given with `-I DIR` on the command line, in order. An absolute path is used as it is.
- Each file is included **once**. A second `.include` of the same file is skipped, so a library can include what it needs without double declarations. A file that includes itself, directly or through other files, is a compile error.
- Includes nest: an included file may include further files.
- Errors and warnings name the included file and its own line: `lib/uart0.bas:18: error: ...`.

### 2.2 Conditional compilation

```basic
#IF NOT DEFINED(BOARD)          ' default when the command line has no -D BOARD=...
CONST BOARD = 1
#ENDIF

#IF BOARD = 1
  .include "board1.bas"
#ELSEIF BOARD = 2
  .include "board2.bas"
#ELSE
  #ERROR "BOARD must be 1 or 2"
#ENDIF

#IF DEFINED(DEBUG)
PRINT "x = ", DEC x, CR, LF
#ENDIF
```

- `#IF cond`, `#ELSEIF cond`, `#ELSE`, `#ENDIF` (also `#END IF`) choose which lines are compiled. Lines in a branch that is off are not compiled at all: they may hold other definitions of the same names, or text that is not BASIC.
- The condition is a constant expression: numbers, `CONST` names defined **above** the `#IF` line (also in included files), operators, and `DEFINED(name)`, which is 1 if a `CONST` of that name exists. It is true when it is not 0. An unknown name is an error, so a typing error does not quietly switch code off; test with `DEFINED(name)` when a `CONST` may be missing.
- `NOT` works as in the rest of the language: on a comparison or `DEFINED()` it is a logical NOT; on a number it inverts all bits, so `NOT 1` is `&HFFFFFFFE`, which is true. Write `#IF DEBUG = 0` rather than `#IF NOT DEBUG`.
- `#IF` blocks nest. An `#IF` opened in a file must be closed in the same file.
- `.include` inside a branch that is off is not read; the file does not even need to exist.
- `#ERROR "text"` stops the compile with that message, when its branch is on.
- `-D NAME` or `-D NAME=value` on the command line defines `CONST NAME` before the first line (default value 1): `python riscbasic.py prog.bas -D DEBUG -D BOARD=2`.
- Each directive stands alone on its line, starting with `#`; a `'` comment may follow it.
- The listing shows the directive lines, and the lines of a branch that is off with `(off)`: `; 0012  (off) CONST BOARD = 1`.

---

## 3. Objects

Everything that can appear on either side of `=` is an object with a **kind**, a **width** and an **access method**.

| Kind | Example | Notes |
|---|---|---|
| CPU register | `R10`, `A0`, `SP` | `R0` / `ZERO` is read-only |
| Register bit | `R10.0` or `R10[0]` | single bit |
| Register field | `R10[7:4]` | bits 7 to 4, right-aligned when read |
| Variable | `count` | in RAM, or pinned to a register |
| Memory | `MEM[addr]`, `MEM8[addr]`, `MEM16[addr]`, `MEM32[addr]` | raw access to any 32-bit address (3.4) |
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
- `READONLY` registers cannot be written. `WRITEONLY` registers are read through a RAM copy (section 3.2.2).
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

### 3.2.2 Write-only registers (WRITEONLY)

Some output registers cannot be read back: a read returns 0, garbage, or the input pins. `WRITEONLY` makes the compiler keep a copy of such a register in RAM (a *shadow*):

```basic
DEVICE GPIO AT &H4000_0000
  REG OUT AT +0 WRITEONLY
  REG SET AT +4 SETS OUT
  REG CLR AT +8 CLEARS OUT
  BIT LED = OUT.3
END DEVICE

LED = 1                    ' set bit 3 in the shadow, then write the whole shadow to OUT
PRINT HEX GPIO.OUT         ' reads the shadow, not the hardware
```

- Every write goes to the shadow and to the register. Every read, including the read-modify-write behind bit and field assignments, `IF`/`WAIT` tests, `NOT` bits and `INPUT` targets, uses the shadow.
- Bit and field changes on a `WRITEONLY` register are done in the shadow, and the whole shadow is then written to the register (not through `SET`/`CLR`).
- Writing a register declared `SETS` or `CLEARS` for a `WRITEONLY` register also updates the shadow, so the shadow stays correct.
- The shadow is one RAM variable of the register's width, created only when the register is used. It appears in the ELF symbol table as `DEVICE.REG.shadow`.
- The shadow starts at 0 (RAM is cleared at startup). If the hardware resets to another value, write the whole register once at the start of the program.
- Raw `MEM[addr]` access does not use the shadow.
- `READONLY` and `WRITEONLY` together are an error.

### 3.3 Variables

```basic
DIM count AS WORD IN R10    ' pinned to a register
DIM flags AS BYTE           ' in RAM
DIM buf(16) AS BYTE         ' array in RAM
```

Types: `BIT`, `BYTE`, `HALF` (16-bit), `WORD` (32-bit), and signed `SBYTE`, `SHALF`, `SWORD`. Text: `STRING * n` (section 5.2).

- **RAM variables always live in RAM.** Every read is a load and every write is a store, so the listing is predictable. `count = count + 1` on a RAM variable is load, add, store.
- **Register variables** (`IN Rn`) always live in their register.
- Startup code sets `GP = RAM base + 2048`, so variables in the first 4 KB of RAM are reached with one `LW` / `SW` and a 12-bit offset.
- `BIT` variables in RAM take one byte each.

### 3.4 Memory access (MEM)

```basic
MEM[&H4400_0000] = &H1234_5678     ' write a 32-bit word anywhere in the 4 GB address space
v = MEM[&HFFFF_FFFC]               ' read the top word
MEM[base + 4 * i] = v              ' the address can be any expression
MEM8[p] = 13 : x = MEM16[p + 2]    ' byte and halfword access
MEM[p][15:8] = &HAB                ' a field of a memory word (read-modify-write)
IF MEM[p].31 THEN ...              ' a bit
```

- `MEM[addr]` is a 32-bit word; `MEM8`, `MEM16` and `MEM32` give the width. Reads of `MEM8` and `MEM16` are zero-extended.
- The address is a full 32-bit value: a constant or any expression. Parentheses work too: `MEM32(addr)` is the same as `MEM32[addr]`. The first `[ ]` after `MEM` is the address; a second `[hi:lo]` or `.n` selects bits.
- A constant address costs no extra instruction when it is within 2 KB of `GP` (RAM) or of address 0 (also the top 2 KB, `&HFFFF_F800` to `&HFFFF_FFFF`); otherwise one `LUI` is added. A computed address is evaluated into a register first.
- The address is not checked for alignment. A misaligned `MEM16` or `MEM` access does what the core does with it (trap or split access).
- `MEM`, `MEM8`, `MEM16` and `MEM32` are reserved words.

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

### 5.2 Strings

RiscBasic has no heap. Every string is a fixed-size, zero-terminated `BYTE` buffer.

```basic
DIM name AS STRING * 20               ' 20 bytes: up to 19 characters and the 0

FUNCTION Hex8(v AS WORD) AS STRING * 12
  RETURN "0x" + HEX$(v)
END FUNCTION

FUNCTION Greet(who AS STRING) AS STRING * 32
  RETURN "Hello, " + who + "!"
END FUNCTION

name = "item " + DEC$(n) + " at " + Hex8(addr) + CHR$(33)
PRINT Greet(name), 13, 10
```

- **`DIM name AS STRING * n`** is the same as `DIM name(n) AS BYTE`, but marks the array as text. Any `BYTE` array can be used as a string.
- **String expressions** join terms with `+`. A term is `"text"`, a `STRING` or `BYTE` array, a string `FUNCTION` call, a `STRING` parameter, `CHR$(expr)`, `DEC$(expr)` or `HEX$(expr)`. `DEC$` and `HEX$` format like `DEC` and `HEX` in `PRINT`; `CHR$` gives one character.
- **Assignment** `buffer = string expression` builds the text in the buffer. It is cut at the buffer size and always zero-terminated. `s = "x" + s` works: when the buffer itself may be read during the build, the text is built in a scratch buffer and then copied.
- **`FUNCTION f(...) AS STRING * n`** returns text in its own result buffer of n bytes. `RETURN expression` sets it and returns. A string `FUNCTION` that ends without `RETURN`, or leaves with `EXIT FUNCTION`, returns empty text.
- **`STRING` parameters** (`who AS STRING`) pass a pointer to existing text: a literal, a buffer, a string `FUNCTION` result or another `STRING` parameter. They are read-only. An argument is a single term, not a `+` expression.
- **`PRINT`** takes string expressions as items: `PRINT Greet(name) + "?", 13, 10`. It prints the terms one after another and needs no buffer.
- Strings cannot be used as numbers, and numbers cannot be assigned to strings: use `DEC$`, `HEX$` or `CHR$`.
- **`LEN(string expression)`** is the number of characters: up to the 0, or the end of the buffer. For joined strings it adds up the lengths of the terms, without building them. Text of known length (`"abc"`, `CHR$`, `HEX$`) is counted at compile time; `LEN(DEC$(x))` is not supported.
- **Comparison** `=`, `<>`, `<`, `>`, `<=`, `>=` between two strings: `IF name = "Bob" THEN`. Byte by byte, unsigned; a shorter prefix is less (`"ab" < "abc"`). Each side is one term: text, a `STRING` or `BYTE` array (read up to its size), a string `FUNCTION` call or a `STRING` parameter; compare a joined (`+`) string by assigning it first. Both sides calling the same string `FUNCTION` is an error (one result buffer). A one-character text against a number stays a number comparison: `IF c = "A"` with a `BYTE` `c` compares with 65.
- **`LEFT$(s, n)`**, **`RIGHT$(s, n)`** and **`MID$(s, start [, n])`** give the first n, the last n, or n characters from position `start` (counting from 1; without n, to the end). Out-of-range values give shorter or empty text, never an error: `MID$(s, 0)` and a `start` past the end are empty. They are string terms: usable in assignments, `RETURN`, `PRINT`, `LEN`, comparisons and as `STRING` arguments. The source is a single term (text, `STRING` or `BYTE` array, string `FUNCTION`, `STRING` parameter); nest them through a variable. `PRINT` and `LEN` work on a substring directly; for a comparison or an argument it is copied into a scratch buffer, so only one side of a comparison can be a substring, and the substring of a `STRING` parameter must be assigned to a `STRING` first. With constant text and numbers the result is computed at compile time.
- **`INSTR([start,] s, find)`** is the position (from 1) of `find` in `s`, searching from `start` (default 1); 0 if not found. An empty `find` gives `start` (or 0 past the end).
- **`VAL(s)`** converts text to a signed number: leading spaces are skipped, then an optional `+` or `-`, then decimal digits, or hex / binary digits after `&H` / `0x` / `&B` / `0b` (as in RiscBasic literals). The first character that is not a digit of that base ends the number: `VAL("42abc")` is 42. No digits gives 0; values wrap at 32 bits. `s` is a single term, including `LEFT$` / `RIGHT$` / `MID$`: `n = VAL(MID$(line, 3))`. `VAL` of constant text is computed at compile time.
- Each string `FUNCTION`, and the main program, has its own scratch buffer for overlapping builds, so a function that needs scratch space can be called while the main program is building in its scratch buffer.
- A string `FUNCTION` result is a single static buffer: each call overwrites the previous result. Using results one after another (`Pad(7) + "/" + Pad(42)`) and nesting (`Greet(Greet("x"))`) work; a recursive string `FUNCTION` does not.
- Do not build strings in an interrupt handler while the main program may be building one; the build state is shared.

## 6. Control flow

```basic
IF cond THEN ... ELSEIF cond THEN ... ELSE ... END IF
IF cond THEN statement [ELSE statement]   ' single-line form
WHILE cond ... WEND
DO ... LOOP [UNTIL cond]
REPEAT ... UNTIL cond              ' body runs at least once
FOR i = 0 TO 15 [STEP 1] ... NEXT
SELECT CASE x ... CASE 1, 2 ... CASE 5 TO 9, 12 ... CASE IS > 100 ... CASE ELSE ... END SELECT
GOTO label
EXIT DO | EXIT FOR | EXIT WHILE | EXIT REPEAT
```

The single-line `IF` takes the statements after `THEN` and optionally after `ELSE`, separated by `:`: `IF ok THEN PRINT "a" : PRINT "b" ELSE PRINT "c" : PRINT "d"`. Everything after `THEN` up to its `ELSE` belongs to `THEN`, everything after the `ELSE` belongs to `ELSE`, so an `END IF` on such a line is an error. Either part can itself be a single-line `IF`. An `ELSE` belongs to the nearest `IF` that has no `ELSE` yet: in `IF a THEN IF b THEN x = 1 ELSE x = 2`, the `ELSE` runs when `a` is true and `b` is false.

`CASE` takes a list of constants and constant ranges: `CASE 1 TO 5`, `CASE 7, 10 TO 12, 20`, `CASE "a" TO "z"`. A range includes both ends and works for negative and unsigned values; `CASE 5 TO 1` (low end last) is an error. `CASE IS op value` compares with `=`, `<>`, `<`, `>`, `<=` or `>=`: `CASE IS < 0, 5 TO 9, IS > 100`. The comparison is signed if the `SELECT` expression is signed (`SBYTE`, `SHALF`, `SWORD`), otherwise unsigned, as for all comparisons. The first matching `CASE` runs; there is no fall-through.

`REPEAT ... UNTIL cond` runs the body, then tests `cond` at the end and repeats until it is true. It is the same as `DO ... LOOP UNTIL cond`, written Pascal-style; `UNTIL` on its own line closes the innermost `REPEAT`.

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
- **`SUB BoardInit`**: if the program has a SUB of this name, the startup code calls it once, after the stack, `GP` and RAM clearing are set up and before the `main` label, where the BASIC program starts. It may be defined anywhere, also in an included file or inside an `#IF` branch (a board file can bring its own). It must be a `SUB` without parameters. Interrupts are not yet enabled when it runs. A program that also calls it with `CALL` runs it twice.

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
- Output formats: `.bin`, Intel `.hex`, `.mem` for `$readmemh` (BRAM initialisation on FPGAs), and `.elf` (ELF32 RISC-V executable with symbols and DWARF source-line information, for debuggers and simulators). The DWARF line table lists every included file, so a debugger can set breakpoints in them (`break lib/uart0.bas:18`).
- Compile error if the code exceeds `CODE SIZE`. The size of each `SUB`, `FUNCTION` and runtime routine is reported.

### 11.1 Listing file (.LST)

A `.LST` file is **always written by default**, next to the source: `program.bas` gives `program.lst`. `--no-lst` turns it off.

- Each RiscBasic source line appears as a comment, with its line number, directly above the instructions generated for it. Lines from an included file carry the file name too: `; lib/uart0.bas:0018  PRINT CR, LF`.
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
| 23 | Write-only registers | `WRITEONLY` keeps a RAM shadow; reads and bit changes use it | 0.13 |
| 24 | REPEAT loop | `REPEAT ... UNTIL cond` and `EXIT REPEAT` | 0.14 |
| 25 | CASE ranges | `CASE low TO high`, mixed with single values | 0.15 |
| 26 | CASE IS | `CASE IS op value`; signed or unsigned like the SELECT expression | 0.16 |
| 27 | Single-line ELSE | `IF c THEN s ELSE s`; ELSE binds to the nearest IF without one | 0.17 |
| 28 | Statement separator | `:` between statements; single-line IF takes the rest of the line (QBasic rules) | 0.18 |
| 29 | Strings | `STRING * n` buffers, string expressions with `+`, `CHR$` / `DEC$` / `HEX$`, `FUNCTION ... AS STRING * n`, `STRING` parameters | 0.19 |
| 30 | LEN and string comparison | `LEN(s)`; `=` `<>` `<` `>` `<=` `>=` on strings, unsigned byte order | 0.20 |
| 31 | Substrings and search | `LEFT$`, `RIGHT$`, `MID$` (from 1), `INSTR` (from 1, 0 = not found) | 0.21 |
| 32 | VAL | Text to signed number: decimal, `&H` / `0x`, `&B` / `0b` | 0.22 |
| 33 | Include files | `.include "file.bas"`: textual, each file once, search the including file's directory then `-I` | 0.23 |
| 34 | Conditional compilation | `#IF` / `#ELSEIF` / `#ELSE` / `#ENDIF` on CONSTs defined above, `DEFINED(name)`, `#ERROR`, `-D NAME=value` | 0.24 |
| 35 | Memory access | `MEM[addr]` (32-bit) and `MEM8` / `MEM16` / `MEM32` with `[ ]` or `( )`; full 32-bit address; fields and bits also with a computed address | 0.25 |
| 36 | Board initialization | a `SUB BoardInit` (no parameters) is called by the startup code before `main` | 0.26 |

## 14. Open questions

None at Draft 0.26.
