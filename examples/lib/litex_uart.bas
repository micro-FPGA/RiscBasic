'
' LiteX UART: console on UART0 of include/CPU/LITEX.bas
'
' PRINT uses the registers (DATA, TXREADY). INPUT needs this handler: the
' LiteX UART keeps a received character until the RX event is cleared.
'
CONST CR = 13
CONST LF = 10

CONSOLE UART0

FUNCTION UART0Get AS BYTE
DIM c AS WORD
  WAIT UNTIL UART0.RXREADY
  c = UART0.RXTX
  UART0.EV_PENDING = 2          ' clear the RX event: takes the character from the FIFO
  RETURN c
END FUNCTION
