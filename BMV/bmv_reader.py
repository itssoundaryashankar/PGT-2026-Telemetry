#!/usr/bin/env python3
import serial


class BMVReader:
    def __init__(self, serial_port, baudrate):
        self.serial = serial.Serial(port=serial_port, baudrate=baudrate, timeout=1)
        self.frame = {}

    def read_frame(self):
        while True:
            line = self.serial.readline().decode(errors="ignore").strip()
            if not line:
                continue

            if "\t" in line:
                key, value = line.split("\t", 1)
                self.frame[key] = value

            if line.startswith("Checksum"):
                frame = dict(self.frame)
                self.frame = {}
                return frame

    def close(self):
        if self.serial.is_open:
            self.serial.close()
