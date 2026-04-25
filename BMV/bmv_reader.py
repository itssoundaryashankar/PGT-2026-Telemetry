#!/usr/bin/env python3
import serial


# VE.Direct protocol sends data in two alternating blocks, each ending in its
# own "Checksum" line:
#   Block A: V, I, P, CE, SOC, TTG, Alarm, Relay, AR, BMV, FW
#   Block B: H1..H17, MON
# We accumulate fields across consecutive blocks and only return once we have
# the fields the caller actually needs (the live measurements from Block A).

REQUIRED_KEYS = ("V", "I", "P")


class BMVReader:
    def __init__(self, serial_port, baudrate):
        self.serial = serial.Serial(port=serial_port, baudrate=baudrate, timeout=1)
        self.frame = {}

    def read_frame(self, required_keys=REQUIRED_KEYS):
        while True:
            line = self.serial.readline().decode(errors="ignore").strip()
            if not line:
                continue

            if "\t" in line:
                key, value = line.split("\t", 1)
                self.frame[key] = value

            if line.startswith("Checksum"):
                # End of a block. Only return if the accumulated frame
                # actually contains the live-measurement keys we need.
                # Otherwise keep accumulating into the next block.
                if all(k in self.frame for k in required_keys):
                    frame = dict(self.frame)
                    self.frame = {}
                    return frame
                # else: keep going, the next block will fill in the missing fields

    def close(self):
        if self.serial.is_open:
            self.serial.close()
