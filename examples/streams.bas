' RiscBasic example

.include "include/CPU/MCS-V.bas"
.include "include/boards/CR00107.bas"
.include "lib/uart.bas"

DIM I AS BYTE          ' RAM
DIM J AS BYTE          ' RAM
DIM I2CAddr AS BYTE    ' RAM
DIM ackerr AS BYTE      ' RAM: 1 if a device did not acknowledge (GetAck)

DEVICE GPIO1 AT &H8000_0000     ' IO Module GPO2 / GPI2
  REG OUT  AT +20  WRITEONLY
  REG IN   AT +36  READONLY
END DEVICE

'
' I2C bitbang defs
'
' SDA must be open-drain in the FPGA design: SDA_OUT = 0 pulls the line low, SDA_OUT = 1
' releases it (the pull-up makes it high), so a device can pull SDA low for its ACK and its
' data bits. A push-pull output would fight the device on every ACK and read.
'
ALIAS SDA_OUT = GPIO1.OUT.0
ALIAS SCL_OUT = GPIO1.OUT.1
ALIAS SDA_IN = GPIO1.IN.0


SUB I2CDelay
'
' Half-bit time: SCL low for one delay, high for one delay (about 50 kHz)
'
  DELAY_US 10
END SUB
'
' Stream support functions. Every routine starts and ends with SCL low,
' except Start (starts from the idle bus) and Stop (leaves the bus idle).
'
SUB I2CWrite(wrval AS BYTE)
  DIM temp AS BYTE       ' RAM
  
  FOR temp = 0 to 7
    SDA_OUT = wrval.7 ' output high bit, while SCL is low
    SHL wrval         ' shift left
    I2CDelay
    SCL_OUT = 1: I2CDelay   ' device samples SDA while SCL is high
    SCL_OUT = 0
  NEXT
END SUB

FUNCTION I2CRead AS BYTE
  DIM temp AS BYTE       ' RAM
  DIM rdval AS BYTE       ' RAM

  SDA_OUT = 1             ' release SDA: the device drives it
  FOR temp = 0 to 7
    SHL rdval
    I2CDelay
    SCL_OUT = 1: I2CDelay
    rdval.0 = SDA_IN      ' read I2C SDA bus value while SCL is high
    SCL_OUT = 0
  NEXT
  RETURN rdval
END FUNCTION

SUB I2CGetAck             ' after a written byte: the device acknowledges by pulling SDA low
  SDA_OUT = 1: I2CDelay
  SCL_OUT = 1: I2CDelay
  IF SDA_IN THEN ackerr = 1
  SCL_OUT = 0
END SUB

SUB I2CACK                ' after a read byte: more bytes follow
  SDA_OUT = 0: I2CDelay
  SCL_OUT = 1: I2CDelay
  SCL_OUT = 0
END SUB

SUB I2CNACK               ' after the last read byte
  SDA_OUT = 1: I2CDelay
  SCL_OUT = 1: I2CDelay
  SCL_OUT = 0
END SUB

SUB I2CStart              ' SDA falls while SCL is high
  SDA_OUT = 1: SCL_OUT = 1: I2CDelay
  SDA_OUT = 0:              I2CDelay
  SCL_OUT = 0
END SUB

SUB I2CRepeatedStart      ' from SCL low: release SDA, then SCL, then a START
  SDA_OUT = 1: I2CDelay
  SCL_OUT = 1: I2CDelay
  SDA_OUT = 0: I2CDelay
  SCL_OUT = 0
END SUB

SUB I2CStop               ' SDA rises while SCL is high
  SDA_OUT = 0: I2CDelay
  SCL_OUT = 1: I2CDelay
  SDA_OUT = 1: I2CDelay
END SUB

PRINT "RiscBasic ready", 13, 10
PRINT "Read/Write Demo for I2C devices", 13, 10
'
' I2C Read Demo
'
I2CAddr = &HFA
ackerr = 0

WRITE I2C, Start, &B1010_0100, GetAck, I2CAddr, GetAck, RepeatedStart, &B1010_0101, GetAck
IF ackerr THEN PRINT "No ACK from the device", 13, 10

PRINT "Value read: "

FOR J = 0 to 5 
  READ I2C, I, ACK
  PRINT HEX I
NEXT
I2CStop












