
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


compressor = Compressor()

def read_bmv():
    ser = serial.Serial(
        port='/dev/ttyUSB0',
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
            v = 0
            i = 0
            w = 0
            for k in ["V", "I", "P", "SOC", "TTG"]:
                if k in data:
                    # print(k, data[k])
                    if(k=="V"):
                        v = data[k]
                    if(k=="I"):
                        i = data[k]
                    if(k=="P"):
                        w = data[k]
            write(v, i, w)
            print("-------------------------")
            data = {}

def write_to_csv(v, i, w):
    with open("bmv_data.csv", "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "voltage_v", "current_a", "power_w"])

        while True:
            voltage_v = v
            current_a = i
            power_w = w

            writer.writerow([datetime.utcnow().isoformat(), voltage_v, current_a, power_w])
            f.flush()
            time.sleep(1)

def write(v, i, w):
    write_to_csv(v, i, w)
    ts_now = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
    packed = compressor.compress(ts_now, float(v), float(i), float(w))
    

read_bmv()
