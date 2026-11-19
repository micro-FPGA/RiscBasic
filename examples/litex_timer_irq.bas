' RiscBasic on a LiteX SoC (VexRiscv): TIMER0 interrupt
'
' TIMER0 counts down from RELOAD and raises its interrupt at zero, TICK_HZ times
' per second. The handler counts the ticks; the main program prints the uptime
' once per second. If the SoC has LEDs, LED 0 changes every second.

.include "include/CPU/LITEX.bas"
.include "lib/litex_uart.bas"
.include "lib/litex_vexriscv_irq.bas"

#IF NOT DEFINED(TICK_HZ)
CONST TICK_HZ = 100                         ' interrupts per second
#ENDIF

DIM ticks   AS WORD                         ' interrupts since start
DIM seconds AS WORD                         ' set by the interrupt handler
DIM part    AS WORD                         ' ticks within the current second
DIM shown   AS WORD                         ' last second printed by the main loop

ON IRQ MEXT                                 ' every LiteX interrupt comes here
  IF TIMER0.EV_PENDING.0 THEN
    TIMER0.EV_PENDING = 1                   ' first: the interrupt stays active until cleared
    ticks = ticks + 1
    part = part + 1
    IF part = TICK_HZ THEN
      part = 0
      seconds = seconds + 1
#IF DEFINED(LITEX_LEDS)
      LEDS.OUT.0 = NOT LEDS.OUT.0
#ENDIF
    END IF
  END IF
END IRQ

TIMER0.EN         = 0
TIMER0.LOAD       = LITEX_CLOCK / TICK_HZ - 1
TIMER0.RELOAD     = LITEX_CLOCK / TICK_HZ - 1
TIMER0.EV_PENDING = 1                       ' forget an old event
TIMER0.EV_ENABLE  = 1
TIMER0.EN         = 1
IrqEnable IRQ_TIMER0
IrqOn

PRINT CR, LF, "RiscBasic timer interrupt demo, ", DEC TICK_HZ, " interrupts per second", CR, LF
DO
  IF seconds <> shown THEN
    shown = seconds
    PRINT "uptime ", DEC shown, " s, ticks ", DEC ticks, CR, LF
  END IF
LOOP
