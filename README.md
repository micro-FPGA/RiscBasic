# RiscBasic

A BASIC-like language for RISC-V soft cores, compiled directly to machine code: single pass, no linker, for cores with 8–16 KB of code memory. The compiler is one Python file with no dependencies.

- [Overview, usage and implementation notes](docs/README.md)
- [Reference Manual (PDF)](docs/RiscBasic-Reference-Manual.pdf), also as [Word document](docs/RiscBasic-Reference-Manual.docx)
- [Language specification](docs/RiscBasic-Spec.md)
- Compiler: [source/riscbasic.py](source/riscbasic.py), simulator: [source/rbsim.py](source/rbsim.py)
- Examples, including an AMD MicroBlaze V MCS FIT interrupt demo: [examples/](examples/)

```
python source/riscbasic.py program.bas     # writes program.bin .hex .mem .lst .elf
python tests/run_tests.py                  # run the test suite
```

License: MIT
