import asyncio
import csv
import os
import sys
from pathlib import Path
from datetime import datetime

import serial


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from Compressor import Compressor


PORT = "/dev/tty.usbserial-VE7ALZXZ"
BAUDRATE = 19200
CSV_PATH = "bmv_data.csv"


class BMVReader:
    def __init__(self, port: str, baudrate: int) -> None:
        self.serial = serial.Serial(port=port, baudrate=baudrate, timeout=0)
        self.buffer = bytearray()
        self.frame = {}
        self.compressor = Compressor()

    def on_serial_ready(self) -> None:
        chunk = self.serial.read(self.serial.in_waiting or 1)
        if not chunk:
            return

        self.buffer.extend(chunk)

        while b"\n" in self.buffer:
            raw_line, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            self.process_line(raw_line.decode(errors="ignore").strip())

    def process_line(self, line: str) -> None:
        if not line:
            return

        if "\t" in line:
            key, value = line.split("\t", 1)
            self.frame[key] = value

        if line.startswith("Checksum"):
            self.handle_frame()
            self.frame = {}

    def handle_frame(self) -> None:
        v = self.frame.get("V", "0")
        i = self.frame.get("I", "0")
        w = self.frame.get("P", "0")

        print("---- VE.Direct frame ----")
        write_to_csv(v, i, w)

        ts_now = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
        try:
            packed = self.compressor.compress(
                ts_now,
                float(v) / 1000,
                float(i) / 1000,
                float(w) / 1000,
            )
            print(f"Compressed: {packed.hex()}")
        except Exception as exc:
            print(f"Compression error: {exc}")

        print("-------------------------")

    def close(self) -> None:
        if self.serial.is_open:
            self.serial.close()


def write_to_csv(v, i, w):
    file_exists = os.path.exists(CSV_PATH)

    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists or os.stat(CSV_PATH).st_size == 0:
            writer.writerow(["timestamp", "voltage_v", "current_a", "power_w"])

        writer.writerow([datetime.utcnow().isoformat(), v, i, w])
        f.flush()
async def read_bmv() -> None:
    loop = asyncio.get_running_loop()
    reader = BMVReader(PORT, BAUDRATE)

    loop.add_reader(reader.serial.fileno(), reader.on_serial_ready)

    try:
        await asyncio.Future()
    finally:
        loop.remove_reader(reader.serial.fileno())
        reader.close()


if __name__ == "__main__":
    asyncio.run(read_bmv())
