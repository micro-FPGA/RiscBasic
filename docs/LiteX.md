# RiscBasic on a LiteX SoC

[LiteX](https://github.com/enjoy-digital/litex) builds a SoC (CPU, memories, UART, timer, ...)
for 250+ FPGA boards and for a Verilator simulation. RiscBasic programs run on it without any
change to LiteX: a script turns the description of the SoC into a RiscBasic include file, and
the LiteX tools load the image.

Files:

| File | Use |
|---|---|
| `tools/litex2bas.py` | makes `examples/include/CPU/LITEX.bas` from the `csr.json` of the SoC |
| `examples/include/boards/TEL0025.bas` | LED names of the Trenz TEL0025 |
| `examples/hello_litex.bas` | `PRINT`, `INPUT` and LEDs |
| `examples/delay_litex.bas` | measures `DELAY_MS` with `TIMER0` |
| `examples/include/CPU/LITEX_VEXRISCV_IRQ.bas` | interrupts on the VexRiscv CPU: `IrqEnable`, `IrqDisable`, `IrqOn`, `IrqOff` |
| `examples/timer_litex.bas` | `TIMER0` interrupt with `ON IRQ MEXT` |
| `examples/tinybasic_litex.bas` | the Tiny BASIC interpreter (`lib/tinybasic.bas`) on the LiteX console |
| `tests/check_litex.py` | checks the script and compiles the LiteX examples |

`LITEX.bas` is not in the repository: it belongs to one LiteX build. It is used like the other
CPU files:

```basic
.include "include/CPU/LITEX.bas"
.include "lib/uart.bas"
```

## The include file

Every LiteX build can write `csr.json` (option `--csr-json=<file>`): the memory regions, the
address of each register and the clock. The addresses change with the options of the SoC, so
the include is made again after each new build:

```
python tools/litex2bas.py build/<board>/csr.json -o examples/include/CPU/LITEX.bas
```

It declares `TARGET` (from the CPU: `RV32I+M` for the default VexRiscv), `CLOCK`, `CODE`, `RAM`
and one `DEVICE` for each module of the SoC: `UART0`, `TIMER0`, `CTRL`, `LEDS`, ... with all its
registers. For each device a `CONST LITEX_<name>` is defined, to test with
`#IF DEFINED(LITEX_LEDS)`, and `CONST IRQ_<name>` with its interrupt number if it has one.
`CONST LITEX_CLOCK` is the clock in Hz.

| Option | Meaning |
|---|---|
| `--code main_ram` | the program is loaded into the main RAM by the LiteX BIOS (default) |
| `--code rom` | the program takes the place of the BIOS in the ROM |
| `--ram sram` | region for variables and stack (default `sram`, 8 KB) |
| `--target RV32IC+M` | `TARGET`, when the CPU is not in the table of the script |
| `--code-size`, `--stack`, `--cpi` | `CODE` size, `STACK` and `CPI` |

The UART: `PRINT` uses the registers directly (`TXREADY` is `NOT TXFULL.0`). The LiteX UART keeps
a received character until the RX event is cleared, so `INPUT` goes through the `Get` handler
`UART0Get`, which is part of the include.

## Simulation (no board needed)

Needs LiteX and Verilator.

```
litex_sim --cpu-type=vexriscv --integrated-main-ram-size=0x10000 --csr-json=build/sim/csr.json --output-dir=build/sim --no-compile-gateware
python tools/litex2bas.py build/sim/csr.json -o examples/include/CPU/LITEX.bas
python source/riscbasic.py examples/hello_litex.bas
litex_sim --cpu-type=vexriscv --integrated-main-ram-size=0x10000 --ram-init=examples/hello_litex.bin --output-dir=build/sim
```

The BIOS starts, finds the image in the main RAM and runs it:

```
Executing booted program at 0x40000000
--============== Liftoff! ==============--
RiscBasic on LiteX
count 1 square 1 hex 00000001
...
a)dd  e)cho  q)uit >
```

Without the BIOS: make the include with `--code rom`, compile, and give the image with
`--rom-init=examples/hello_litex.bin` in place of `--ram-init`.

## Trenz TEL0025 (Lattice Certus-NX)

Needs LiteX, LiteX-Boards, Lattice Radiant and openFPGALoader. `--load` writes the FPGA only,
not the flash.

```
python3 -m litex_boards.targets.trenz_tel0025 --build --load --integrated-main-ram-size=0x10000 --csr-json=build/tel0025/csr.json --output-dir=build/tel0025
python tools/litex2bas.py build/tel0025/csr.json -o examples/include/CPU/LITEX.bas
python source/riscbasic.py examples/hello_litex.bas
litex_term /dev/ttyUSBx --kernel=examples/hello_litex.bin --kernel-adr=0x40000000
```

`litex_term` is the console (115200 baud) and sends the image when the BIOS asks for it: type
`serialboot` at the BIOS prompt. A new program needs only the last two lines, not a new FPGA
build; load the FPGA again to get back to the BIOS.

`--integrated-main-ram-size=0x10000` gives 64 KB of main RAM inside the FPGA, enough for any
RiscBasic program. Without it the main RAM is the 32 MB HyperRAM of the board; programs ran
from it too, but in a later test the HyperRAM failed the memory test of the BIOS (`Memtest KO`)
and no program started. Check the BIOS messages if a program does not start.

On the TEL0025 the FT2232H has the console on channel A and JTAG on channel B. If `--load` does
not find the FPGA, load with `openFPGALoader -c ft2232_b build/tel0025/gateware/trenz_tel0025.bit`.

Program in the ROM, without the BIOS (one FPGA build for each program):

```
python tools/litex2bas.py build/tel0025/csr.json --code rom -o examples/include/CPU/LITEX.bas
python source/riscbasic.py examples/hello_litex.bas
python3 -m litex_boards.targets.trenz_tel0025 --build --load --integrated-main-ram-size=0x10000 --integrated-rom-init=examples/hello_litex.bin --output-dir=build/tel0025_rom
```

Other boards work the same way with their own LiteX target.

## Interrupts (VexRiscv)

`ON IRQ` works on LiteX with the default VexRiscv CPU without a change to the compiler:
VexRiscv has `mtvec`, `mepc`, `mcause` and `mstatus` as in the RISC-V standard, and the
interrupt return of RiscBasic without `mret` (specification, section 7) works on it.

All interrupts of the SoC (timer, UART, ...) arrive as the machine external interrupt, so a
program has one handler, `ON IRQ MEXT`, that tests the `EV_PENDING` register of each module.
Three switches must be on:

```basic
.include "include/CPU/LITEX_VEXRISCV_IRQ.bas"

ON IRQ MEXT
  IF TIMER0.EV_PENDING.0 THEN
    TIMER0.EV_PENDING = 1        ' clear the event first
    ticks = ticks + 1
  END IF
END IRQ

TIMER0.EV_ENABLE = 1             ' 1: the module raises its interrupt
IrqEnable IRQ_TIMER0             ' 2: VexRiscv lets this interrupt through (its CSR 0xBC0)
IrqOn                            ' 3: mie.MEIE and mstatus.MIE
```

`examples/timer_litex.bas` is the complete program: 100 interrupts per second from
`TIMER0`, uptime printed once per second (`-D TICK_HZ=10000` for another rate). Tested in
`litex_sim` and on the TEL0025: 10 s of uptime in 10.00 s, and 10000 interrupts per second for
30 s while the main program prints.

The Efinix Sapphire SoC is a VexRiscv too, but with the CLINT timer (`ON IRQ MTIMER`, see
`include/CPU/SAPPHIRE_CLINT.bas`). The default VexRiscv of LiteX has no CLINT: the LiteX timer
is a module of the SoC and its interrupt is an external interrupt.

`include/CPU/LITEX_VEXRISCV_IRQ.bas` is for VexRiscv only. Other LiteX CPUs enable their interrupts in
another way (PicoRV32 has its own instructions; SERV in LiteX has no interrupts); they are
not tested.

## Tiny BASIC

`examples/tinybasic_litex.bas` is the Tiny BASIC interpreter of `lib/tinybasic.bas` on the LiteX
console. It needs about 11 KB of code and 5 KB of RAM (4 KB of it for the BASIC program), so it
fits the 8 KB SRAM of a default LiteX SoC. It is compiled and loaded like the other examples:

```
python source/riscbasic.py examples/tinybasic_litex.bas
litex_sim --cpu-type=vexriscv --integrated-main-ram-size=0x10000 --ram-init=examples/tinybasic_litex.bin --output-dir=build/sim
```

```
RiscBasic Tiny BASIC, 4020 bytes free
> 10 FOR I=1 TO 5
> 20 PRINT I;" squared is ";I*I
> 30 NEXT I
> RUN
1 squared is 1
...
5 squared is 25
OK
```

Tested in `litex_sim`; not yet on a board. `DELAY` is 1.5 times too long on VexRiscv (see the
notes).

## Notes

- `CODE` in `main_ram` and variables in `sram`: the BIOS no longer runs when the program has
  started, so its RAM is free.
- `DELAY_US` / `DELAY_MS`: the default VexRiscv takes 3 clock cycles for one pass of the delay
  loop, where the compiler counts 2. This would be `CPI 1.5`; `CPI` is a whole number, so with
  the default (`CPI 1`) a delay is 1.5 times too long (measured on the TEL0025 with
  `examples/delay_litex.bas`: `DELAY_MS 100` takes 150 ms). `TIMER0` gives exact times.
- Other CPUs than VexRiscv are not tested.
