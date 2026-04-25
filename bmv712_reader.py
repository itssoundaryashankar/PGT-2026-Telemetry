#!/usr/bin/env python3
"""
BMV-712 VE.Direct reader for solar car telemetry.

Reads VE.Direct text frames from a BMV-712 over serial (USB-VE.Direct cable
or direct UART), validates the frame checksum, and appends a compact JSON
record to an output file that your LoRa transmit process consumes.

Designed to run on a Raspberry Pi 4B.

Usage:
    python3 bmv712_reader.py --port /dev/ttyUSB0 --out /var/tmp/telemetry.jsonl
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import serial  # pip install pyserial


# --- VE.Direct protocol constants ---------------------------------------------
# Each frame is a series of "<Name>\t<Value>\r\n" lines, terminated by a
# "Checksum\t<byte>" line. The sum of every byte in the frame (including the
# trailing checksum byte) modulo 256 must equal 0.
VE_DIRECT_BAUD = 19200
FRAME_END_KEY = b"Checksum"

# Fields we care about from the BMV-712. The BMV emits more, but these are the
# core battery-monitor values for a solar car.
#   V    -> main battery voltage (mV)
#   VS   -> auxiliary/starter voltage (mV) -- BMV-712 has a 2nd input
#   I    -> current (mA, signed; negative = discharge)
#   P    -> instantaneous power (W, signed)
#   CE   -> consumed amp hours (mAh, signed)
#   SOC  -> state of charge (per mille, i.e. 0.1%)
#   TTG  -> time-to-go (minutes, -1 if not discharging)
#   T    -> battery temperature (deg C, if temp sensor connected)
#   Alarm/Relay/AR -> alarm + relay state
NUMERIC_FIELDS = {
    "V":   ("v_main_mv",   int),
    "VS":  ("v_aux_mv",    int),
    "I":   ("i_ma",        int),
    "P":   ("p_w",         int),
    "CE":  ("consumed_mah", int),
    "SOC": ("soc_permille", int),
    "TTG": ("ttg_min",     int),
    "T":   ("batt_temp_c", int),
    "H1":  ("deepest_discharge_mah", int),
    "H2":  ("last_discharge_mah",    int),
    "H17": ("discharged_kwh_x100",   int),  # kWh * 100
    "H18": ("charged_kwh_x100",      int),  # kWh * 100
}
PASSTHROUGH_FIELDS = {
    "Alarm": "alarm",
    "Relay": "relay",
    "AR":    "alarm_reason",
}


# --- Frame parsing ------------------------------------------------------------
class VEDirectParser:
    """Stateful parser that consumes bytes and yields validated frames."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, chunk: bytes):
        """Feed bytes from the serial port; yield dicts for each valid frame."""
        self._buf.extend(chunk)
        while True:
            frame, consumed = self._extract_frame(self._buf)
            if frame is None:
                # Cap buffer growth in case we never see a checksum (e.g. junk
                # on the line). Anything older than 4KB is unrecoverable.
                if len(self._buf) > 4096:
                    self._buf = self._buf[-1024:]
                return
            del self._buf[:consumed]
            if frame:
                yield frame

    @staticmethod
    def _extract_frame(buf: bytearray):
        """
        Try to pull one validated frame from the front of buf.

        Returns (frame_dict_or_None, bytes_consumed). frame_dict is None when
        no complete frame is available yet, {} when a frame was found but
        the checksum failed (caller should still advance past it).
        """
        # Frames start with \r\n. Find the Checksum line, which is the last
        # tab-separated key/value pair in a frame, followed by a single
        # checksum byte.
        idx = buf.find(b"\r\n" + FRAME_END_KEY + b"\t")
        if idx < 0:
            return None, 0

        # The checksum byte is the single byte right after "Checksum\t".
        cs_value_pos = idx + 2 + len(FRAME_END_KEY) + 1  # after "\r\nChecksum\t"
        if cs_value_pos >= len(buf):
            return None, 0  # haven't received the checksum byte yet

        end = cs_value_pos + 1  # frame ends immediately after the checksum byte
        frame_bytes = bytes(buf[:end])

        # Validate: sum of every byte mod 256 == 0
        if sum(frame_bytes) % 256 != 0:
            logging.warning("VE.Direct checksum mismatch, dropping frame")
            return {}, end

        return VEDirectParser._parse_text(frame_bytes[:idx]), end

    @staticmethod
    def _parse_text(text_bytes: bytes) -> dict:
        """Parse the human-readable portion of a frame into a dict."""
        out = {}
        for line in text_bytes.split(b"\r\n"):
            if not line or b"\t" not in line:
                continue
            try:
                key, val = line.split(b"\t", 1)
                key = key.decode("ascii", errors="replace").strip()
                val = val.decode("ascii", errors="replace").strip()
            except Exception:
                continue
            if key in NUMERIC_FIELDS:
                out_key, caster = NUMERIC_FIELDS[key]
                try:
                    out[out_key] = caster(val)
                except ValueError:
                    # BMV emits "---" for fields with no data (e.g. TTG when
                    # not discharging). Store as None so downstream knows.
                    out[out_key] = None
            elif key in PASSTHROUGH_FIELDS:
                out[PASSTHROUGH_FIELDS[key]] = val
        return out


# --- Output handling ----------------------------------------------------------
class RollingWriter:
    """
    Append JSON lines to a file, rotating when it exceeds max_bytes so the
    LoRa-side process never has to chew through an unbounded file.
    """

    def __init__(self, path: Path, max_bytes: int = 1_000_000):
        self.path = path
        self.max_bytes = max_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict):
        line = json.dumps(record, separators=(",", ":")) + "\n"
        # Rotate if needed BEFORE writing, so the new line lands in the fresh file.
        try:
            if self.path.exists() and self.path.stat().st_size + len(line) > self.max_bytes:
                rotated = self.path.with_suffix(self.path.suffix + ".1")
                if rotated.exists():
                    rotated.unlink()
                self.path.rename(rotated)
        except OSError as e:
            logging.warning("Rotate failed (%s); continuing", e)

        # O_APPEND ensures atomic appends even if multiple processes write.
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)


# --- Main loop ----------------------------------------------------------------
def open_serial(port: str) -> serial.Serial:
    return serial.Serial(
        port=port,
        baudrate=VE_DIRECT_BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=2.0,  # blocking read with timeout, lets us check for signals
    )


def run(port: str, out_path: Path, source_id: str):
    parser = VEDirectParser()
    writer = RollingWriter(out_path)

    stop = {"flag": False}
    def handle_sig(signum, _frame):
        logging.info("Caught signal %s, exiting", signum)
        stop["flag"] = True
    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    backoff = 1.0
    while not stop["flag"]:
        try:
            ser = open_serial(port)
            logging.info("Opened %s at %d baud", port, VE_DIRECT_BAUD)
            backoff = 1.0
            try:
                while not stop["flag"]:
                    chunk = ser.read(256)
                    if not chunk:
                        continue
                    for frame in parser.feed(chunk):
                        record = {
                            "ts": time.time(),
                            "src": source_id,
                            **frame,
                        }
                        writer.write(record)
            finally:
                ser.close()
        except serial.SerialException as e:
            logging.error("Serial error: %s; retrying in %.1fs", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        except Exception as e:
            logging.exception("Unexpected error: %s", e)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def main():
    ap = argparse.ArgumentParser(description="BMV-712 VE.Direct telemetry reader")
    ap.add_argument("--port", default="/dev/ttyUSB0",
                    help="Serial device the BMV-712 is connected to")
    ap.add_argument("--out", default="/var/tmp/telemetry.jsonl",
                    help="Output JSON-lines file the LoRa sender reads")
    ap.add_argument("--source-id", default="bmv712",
                    help="Tag written into each record's 'src' field")
    ap.add_argument("--log-level", default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    run(args.port, Path(args.out), args.source_id)


if __name__ == "__main__":
    sys.exit(main() or 0)
