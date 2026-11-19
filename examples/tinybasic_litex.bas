' Tiny BASIC interpreter on a LiteX SoC, console on UART0 (115200 8N1)
'
' The interpreter is lib/tinybasic.bas; this file only sets up the console.
' Memory comes from include/CPU/LITEX.bas (made by tools/litex2bas.py, see docs/LiteX.md):
' the code is in the main RAM or the ROM, variables and BASIC program in the 8K SRAM.

CONST PSIZE = 4096                 ' program text; the rest of the SRAM is variables and stack

.include "include/CPU/LITEX.bas"
.include "lib/uart.bas"

FUNCTION KeyHit AS WORD            ' a character is waiting
  RETURN UART0.RXREADY
END FUNCTION

.include "lib/tinybasic.bas"
