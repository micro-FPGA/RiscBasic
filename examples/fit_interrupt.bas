' RiscBasic example: MicroBlaze V MCS, FIT1 interrupt through the IO Module INTC
'
' MicroBlaze V MCS IP settings (Vivado, customize IP):
'   FIT1: Use FIT = on, Number of Clocks Between Strobes = 100000, Generate Interrupt = on
'   GPO1 and GPI1 for the LEDs and the button, UART on (as in include/CPU/MCS-V.bas)
' With an interrupt enabled, the MCS-V runs MicroBlaze V with low-latency interrupts: the
' core jumps to the vector the IO Module supplies. IntcInit points all vectors at the
' RiscBasic interrupt dispatcher (see MCS-V_INTC.bas).
'
' The FIT strobes every FIT1_CLOCKS clocks: 1000 interrupts per second at 100 MHz. The
' interrupt handler counts them and blinks the green LED; the main program prints the
' uptime once per second and shows the button on the red LED.

.include "include/CPU/MCS-V.bas"
.include "include/CPU/MCS-V_INTC.bas"
.include "include/boards/CR00107.bas"
.include "lib/uart.bas"

CONST CPU_HZ = 100_000_000                  ' the CLOCK of MCS-V.bas
#IF NOT DEFINED(FIT1_CLOCKS)
CONST FIT1_CLOCKS = 100_000                 ' must match "Number of Clocks Between Strobes"
#ENDIF
CONST TICK_HZ = CPU_HZ / FIT1_CLOCKS        ' interrupts per second
#IF CPU_HZ MOD FIT1_CLOCKS <> 0
#ERROR "FIT1_CLOCKS must divide the CPU clock evenly"
#ENDIF

DIM ticks   AS WORD                         ' FIT interrupts since start
DIM seconds AS WORD                         ' set by the interrupt handler
DIM ms      AS WORD                         ' ticks within the current second
DIM shown   AS WORD                         ' last second printed by the main loop
DIM lastbtn AS WORD                         ' button state last shown on the red LED

ON IRQ MEXT                                 ' IO Module INTC -> MicroBlaze V external interrupt
  IF IOM.FIT1_PENDING THEN
    IOM.ACK = FIT1_MASK                     ' first: the interrupt line stays active until ack
    ticks = ticks + 1
    ms = ms + 1
    IF ms = TICK_HZ THEN
      ms = 0
      seconds = seconds + 1
      GREEN_LED = NOT GREEN_LED             ' 1 s on, 1 s off
    END IF
  END IF
END IRQ

IntcInit                                    ' known INTC state, standard vectors
IOM.FIT1_ENABLE = 1                         ' IRQ_ENABLE is write-only: set in the shadow
IrqOn                                       ' mie.MEIE and mstatus.MIE

PRINT "RiscBasic FIT1 interrupt demo, ", DEC TICK_HZ, " interrupts per second", CR, LF

lastbtn = 2                                 ' not 0 or 1: show the button state at once
DO
  IF seconds <> shown THEN
    shown = seconds
    PRINT "uptime ", DEC shown, " s, ticks ", DEC ticks, CR, LF
  END IF
  IF BUTTON <> lastbtn THEN                 ' only on a change: interrupts stay on in the loop
    lastbtn = BUTTON
    ATOMIC RED_LED = (lastbtn = 0)          ' GPO1 shadow is shared with the handler
  END IF
LOOP
