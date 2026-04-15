import serial
import time

PORT = "/dev/tty.usbserial-0001"   # <-- change this
BAUD = 115200                      # <-- common; sometimes 9600

def send_cmd(ser, cmd, wait=0.2):
    line = (cmd.strip() + "\r\n").encode()
    ser.write(line)
    time.sleep(wait)
    out = ser.read_all().decode(errors="ignore")
    if out:
        print(out.strip())
    return out

with serial.Serial(PORT, BAUD, timeout=0.5) as ser:
    time.sleep(0.5)

    # 1) sanity check
    send_cmd(ser, "AT")

    # 2) Example patterns (YOU MUST CHANGE based on your modem):
    #    - set frequency around 914.2 MHz (if your modem supports it)
    #    - set SF/BW/CR
    #    - transmit payload
    #
    # Common “test/p2p” style firmwares use commands like:
    # AT+MODE=TEST
    # AT+TEST=RFCFG,914200000,SF10,125,8,15,ON,OFF,OFF
    # AT+TEST=TXLRPKT,"48656C6C6F"     # hex "Hello"
    #
    send_cmd(ser, "AT+MODE=TEST")
    send_cmd(ser, "AT+TEST=RFCFG,914200000,SF10,125,8,15,ON,OFF,OFF")
    send_cmd(ser, 'AT+TEST=TXLRPKT,"48656C6C6F2D534F4C4152"')  # "Hello-SOLAR" in hex