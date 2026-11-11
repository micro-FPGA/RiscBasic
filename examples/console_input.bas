' Console input example: a small command menu on UART0
'
' Keys:  a = add two numbers,  l = set the LEDs (hex),  q = quit

TARGET RV32IC
CLOCK  100_000_000
CODE   AT &H0000 SIZE 14336
RAM    AT &H3800 SIZE 2048 

CONST CR = 13
CONST LF = 10

'
' AMD IOmodule support
'
DEVICE GPIO AT &H8000_0000
  REG OUT  AT +16  WRITEONLY
  REG IN   AT +32  READONLY
END DEVICE

DEVICE UART0 AT &H8000_0000
  REG RXDATA AT +0 READONLY     ' RX FIFO
  REG DATA   AT +4 : 8
  REG STAT   AT +8 READONLY
  BIT RXREADY = STAT.0          ' RX FIFO has data
  BIT TXREADY = NOT STAT.3
END DEVICE

CONSOLE UART0

DIM cmd AS BYTE
DIM a   AS SWORD
DIM b   AS SWORD

PRINT "RiscBasic console demo", CR, LF
DO
  PRINT "a)dd  l)eds  q)uit > "
  INPUT CHR cmd                       ' one key, no Enter needed
  PRINT CHR cmd, CR, LF               ' CHR does not echo, so echo the key here
  SELECT CASE cmd
  CASE "a"
    INPUT "  first:  ", DEC a         ' digits are echoed, Enter ends the number
    PRINT CR, LF
    INPUT "  second: ", DEC b
    PRINT CR, LF, "  sum = ", DEC a + b, CR, LF
  CASE "l"
    INPUT "  LEDs (hex): ", HEX GPIO.OUT
    PRINT CR, LF, "  GPIO.OUT = ", HEX GPIO.OUT, CR, LF
  CASE "q"
    EXIT DO
  CASE ELSE
    PRINT "  unknown key", CR, LF
  END SELECT
LOOP
PRINT "bye", CR, LF
END
