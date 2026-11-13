' RiscBasic example
'
' NOTE will not work on CH32V003 the libs are not ready!!!
'
.include "include/CPU/CH32V003.bas"
.include "include/boards/CH32V003_EVAL.bas"
.include "lib/uart.bas"

DIM presses AS WORD          ' RAM
DIM ticks   AS WORD IN A0    ' register

presses = 0
ticks = 0
PRINT UART0, "RiscBasic ready", 13, 10

DO
  LED = NOT BUTTON
  IF NOT BUTTON THEN 
    REPEAT 
      DELAY_MS 50  
    UNTIL BUTTON 
    presses = presses + 1
  END IF

  DELAY_MS 100

  ticks = ticks + 1
  PRINT UART0, "presses ", DEC presses, " ticks ", HEX ticks, 13, 10

LOOP
