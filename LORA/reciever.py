import serial
import time

PORT="/dev/tty.usbserial-0001"
BAUD=115200

with serial.Serial(PORT, BAUD, timeout=0.2) as ser:
    time.sleep(0.5)

    ser.write(b"AT+MODE=TEST\r\n")
    time.sleep(0.2)
    ser.write(b"AT+TEST=RXLRPKT\r\n")   # firmware-dependent

    print("Listening...")
    while True:
        data = ser.read(1024)
        if data:
            print(data.decode(errors="ignore"), end="")