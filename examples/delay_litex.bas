' RiscBasic example for a LiteX SoC: measure DELAY_MS with TIMER0, to find CPI
'
' TIMER0 counts down one step per clock cycle. With the right CPI
' (python ../tools/litex2bas.py ... --cpi n) 100 ms take CLOCK / 10 cycles.

.include "include/CPU/LITEX.bas"
.include "lib/uart.bas"

DIM before AS WORD
DIM after AS WORD
DIM n  AS WORD

TIMER0.EN     = 0
TIMER0.RELOAD = 0
TIMER0.LOAD   = &HFFFF_FFFF
TIMER0.EN     = 1

FOR n = 1 TO 3
  TIMER0.UPDATE_VALUE = 1 : before = TIMER0.VALUE
  DELAY_MS 100
  TIMER0.UPDATE_VALUE = 1 : after = TIMER0.VALUE
  PRINT "DELAY_MS 100: ", DEC before - after, " cycles", CR, LF
NEXT
PRINT "done", CR, LF
END
