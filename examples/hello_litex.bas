' RiscBasic example for a LiteX SoC: PRINT, INPUT and, if the SoC has them, LEDs
'
' include/CPU/LITEX.bas is made from the LiteX build, see docs/LiteX.md:
'   python ../tools/litex2bas.py <build>/csr.json -o include/CPU/LITEX.bas

.include "include/CPU/LITEX.bas"
.include "lib/uart.bas"

DIM n   AS WORD
DIM key AS BYTE
DIM a   AS SWORD
DIM b   AS SWORD

PRINT CR, LF, "RiscBasic on LiteX", CR, LF
FOR n = 1 TO 5
  PRINT "count ", DEC n, " square ", DEC n * n, " hex ", HEX n, CR, LF
#IF DEFINED(LITEX_LEDS)
  LEDS.OUT = n
#ENDIF
NEXT

DO
  PRINT "a)dd  e)cho  q)uit > "
  INPUT CHR key
  PRINT CHR key, CR, LF
  SELECT CASE key
  CASE "a"
    INPUT "  first:  ", DEC a
    PRINT CR, LF
    INPUT "  second: ", DEC b
    PRINT CR, LF, "  sum = ", DEC a + b, CR, LF
  CASE "e"
    PRINT "  type, Esc ends: "
    DO
      INPUT CHR key
      IF key = 27 THEN EXIT DO
      PRINT CHR key
    LOOP
    PRINT CR, LF
  CASE "q"
    EXIT DO
  CASE ELSE
    PRINT "  unknown key", CR, LF
  END SELECT
LOOP
PRINT "bye", CR, LF
END
