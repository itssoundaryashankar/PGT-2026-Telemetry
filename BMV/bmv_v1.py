import csv
import time
from datetime import datetime
import serial
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from Compressor import Compressor

# 


compressor = Compressor()

def read_bmv():
    # Using /dev/ttyUSB1 since your first test proved that is the correct port
    ser = serial.Serial(
        port='/dev/tty.usbserial-VE7ALZXZ',
        baudrate=19200,
        timeout=1
    )

    data = {}

    while True:
        line = ser.readline().decode(errors='ignore').strip()
        if not line:
            continue

        if '\t' in line:
            key, value = line.split('\t', 1)
            data[key] = value

        # A frame ends when checksum line appears
        if line.startswith("Checksum"):
            print("---- VE.Direct frame ----")
            # Fixed syntax: separate initializations to avoid tuple errors
            v = 0; i = 0; w = 0
            for k in ["V", "I", "P", "SOC", "TTG"]:
                if k in data:
                    if(k=="V"):
                        v = data[k]
                    if(k=="I"):
                        i = data[k]
                    if(k=="P"):
                        w = data[k]
           
            # Record to CSV
            write_to_csv(v, i, w)
           
            # Perform your compression logic
            ts_now = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
            try:
                # Note: float() is needed because serial data arrives as strings
                packed = compressor.compress(
                    ts_now,
                    float(v) / 1000,
                    float(i) / 1000,
                    float(w) / 1000,
                )
                print(f"Compressed: {packed.hex()}")
            except Exception as e:
                print(f"Compression error: {e}")
               
            print("-------------------------")
            data = {}

def write_to_csv(v, i, w):
    """
    Maintains your exact CSV logic but removes the inner 'while True'
    so the script can return to reading the serial port.
    """
    with open("bmv_data.csv", "a", newline="") as f:
        writer = csv.writer(f)
        # Note: Added a check for empty file so headers only write once
        import os
        if os.stat("bmv_data.csv").st_size == 0:
            writer.writerow(["timestamp", "voltage_v", "current_a", "power_w"])

        voltage_v = v
        current_a = i
        power_w = w

        writer.writerow([datetime.utcnow().isoformat(), voltage_v, current_a, power_w])
        f.flush()


if __name__ == "__main__":
    read_bmv()
