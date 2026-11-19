# RiscBasic

A BASIC-like language for RISC-V soft cores, compiled directly to machine code: single pass, no linker, for cores with 8–16 KB of code memory. The compiler is one Python file with no dependencies.

- [Overview, usage and implementation notes](docs/README.md)
- [Reference Manual (PDF)](docs/RiscBasic-Reference-Manual.pdf), also as [Word document](docs/RiscBasic-Reference-Manual.docx)
- [Language specification](docs/RiscBasic-Spec.md)
- Compiler: [source/riscbasic.py](source/riscbasic.py), simulator: [source/rbsim.py](source/rbsim.py)
- Examples in [examples/](examples/), including:
  - [console_input.bas](examples/console_input.bas): a small command menu on the UART console with `INPUT CHR`, `INPUT DEC` and `INPUT HEX`
  - [strings.bas](examples/strings.bas): string functions and string expressions: `LINE INPUT`, `+`, `DEC$`, `HEX$`, functions that return a STRING
  - [fit_interrupt.bas](examples/fit_interrupt.bas): AMD MicroBlaze V MCS FIT interrupt demo
  - [i2c_detect.bas](examples/i2c_detect.bas): I2C bus scan that prints the address map like Linux `i2cdetect -y`
  - [MAC_EEPROM.bas](examples/MAC_EEPROM.bas): reads the 6-byte MAC address from an I2C EEPROM with `READ` / `WRITE`
  - [spi_read_id.bas](examples/spi_read_id.bas): reads the JEDEC ID (`9F`) and the manufacturer / device ID (`90`) of an SPI flash with bit-banged SPI
  - [hello_litex.bas](examples/hello_litex.bas): `PRINT` and `INPUT` on a [LiteX](https://github.com/enjoy-digital/litex) SoC, in simulation or on a board (Trenz TEL0025): see [RiscBasic on a LiteX SoC](docs/LiteX.md)
  - [timer_litex.bas](examples/timer_litex.bas): timer interrupt on a LiteX SoC with the VexRiscv CPU
  - [tinybasic_litex.bas](examples/tinybasic_litex.bas): the Tiny BASIC interpreter on a LiteX SoC

```
python source/riscbasic.py program.bas     # writes program.bin .hex .mem .lst .elf
python tests/run_tests.py                  # run the test suite
```

License: MIT
