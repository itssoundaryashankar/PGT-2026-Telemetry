#!/usr/bin/env python3
import argparse
import serial
import time

DEFAULT_PORT = "/dev/tty.usbserial-0001"
DEFAULT_BAUD = 9600
DEFAULT_FREQ = "868.100"
DEFAULT_BW = 0
DEFAULT_SF = 12
DEFAULT_POWER = 20
DEFAULT_CR = 1
DEFAULT_CRC = 0
DEFAULT_HEADER = 0
DEFAULT_IQ = 0
DEFAULT_PREAMBLE = 8
DEFAULT_SYNCWORD = 0
DEFAULT_GROUP = 0
DEFAULT_RX_TIMEOUT = 65535
DEFAULT_RX_ACK = 2
RESET_NOTICE = "TAKE EFFECT AFTER ATZ"
IGNORED_RX_LINES = {"OK", "NULL"}


def is_error_line(line):
    upper = line.upper()
    return "ERROR" in upper or "ERR" == upper or "UNKNOWN" in upper


def needs_reset(lines):
    return any(RESET_NOTICE in line.upper() for line in lines)


def is_ignored_rx_line(line):
    return line.strip().upper() in IGNORED_RX_LINES


def read_available_lines(ser, settle_time=0.2):
    time.sleep(settle_time)
    raw = ser.read_all().decode(errors="ignore")
    if not raw:
        return []
    return [line.strip() for line in raw.replace("\r", "\n").split("\n") if line.strip()]


def send_cmd(ser, cmd, wait=0.25):
    ser.write(f"{cmd.strip()}\r\n".encode())
    return read_available_lines(ser, wait)


def require_ok(ser, cmd, wait=0.25, allow_empty=False):
    lines = send_cmd(ser, cmd, wait)
    if any(is_error_line(line) for line in lines):
        raise RuntimeError(f"Modem rejected command: {cmd}")
    if not lines and not allow_empty:
        raise RuntimeError(f"No modem response for command: {cmd}")
    return lines


def try_probe_modem(ser):
    # Some units answer AT+CFG, others reject it even though they accept the
    # actual configuration commands. Treat this as informational only.
    lines = send_cmd(ser, "AT+CFG", wait=0.4)
    return [] if any(is_error_line(line) for line in lines) else lines


def configure_modem(ser, args):
    reset_required = False
    try_probe_modem(ser)
    reset_required |= needs_reset(require_ok(ser, f"AT+FRE={args.freq},{args.freq}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+BW={args.bw},{args.bw}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+SF={args.sf},{args.sf}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+POWER={args.power}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+CR={args.cr},{args.cr}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+CRC={args.crc},{args.crc}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+HEADER={args.header},{args.header}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+IQ={args.iq},{args.iq}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+PREAMBLE={args.preamble},{args.preamble}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+SYNCWORD={args.syncword}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+GROUPMOD={args.group},{args.group}"))
    reset_required |= needs_reset(require_ok(ser, f"AT+RXMOD={args.rx_timeout},{args.rx_ack}"))
    try_probe_modem(ser)
    return reset_required


def open_serial(port, baud):
    ser = serial.Serial(port, baud, timeout=0.5)
    time.sleep(0.5)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser


def configure_with_restart(args):
    ser = open_serial(args.port, args.baud)
    reset_required = configure_modem(ser, args)

    if not reset_required:
        return ser

    print("[receiver] Applying pending modem settings")
    send_cmd(ser, "ATZ", wait=0.5)
    ser.close()
    time.sleep(2.0)

    ser = open_serial(args.port, args.baud)
    try_probe_modem(ser)
    return ser


def main():
    parser = argparse.ArgumentParser(description="LoRa receiver for AT+RECV firmware")
    parser.add_argument("--port", default=DEFAULT_PORT, help="Serial device path")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Serial baud rate")
    parser.add_argument("--freq", default=DEFAULT_FREQ, help="TX/RX frequency in MHz, e.g. 868.100")
    parser.add_argument("--bw", type=int, default=DEFAULT_BW, help="Bandwidth enum 0-9")
    parser.add_argument("--sf", type=int, default=DEFAULT_SF, help="Spreading factor 5-12")
    parser.add_argument("--power", type=int, default=DEFAULT_POWER, help="TX power 0-22 dBm")
    parser.add_argument("--cr", type=int, default=DEFAULT_CR, help="Coding rate 1-4")
    parser.add_argument("--crc", type=int, default=DEFAULT_CRC, help="CRC 0=off 1=on")
    parser.add_argument("--header", type=int, default=DEFAULT_HEADER, help="Header 0=explicit 1=implicit")
    parser.add_argument("--iq", type=int, default=DEFAULT_IQ, help="IQ invert 0=standard 1=inverted")
    parser.add_argument("--preamble", type=int, default=DEFAULT_PREAMBLE, help="Preamble length")
    parser.add_argument("--syncword", type=int, default=DEFAULT_SYNCWORD, help="Sync word mode 0/1")
    parser.add_argument("--group", type=int, default=DEFAULT_GROUP, help="Group 0-255")
    parser.add_argument("--rx-timeout", type=int, default=DEFAULT_RX_TIMEOUT, help="RX window in seconds or 65535 always open")
    parser.add_argument("--rx-ack", type=int, default=DEFAULT_RX_ACK, help="ACK mode 0/1/2")
    parser.add_argument("--recv-format", type=int, choices=(0, 1), default=1, help="AT+RECV format 0=hex 1=text")
    parser.add_argument("--poll", type=float, default=1.0, help="Seconds between AT+RECV polls")
    args = parser.parse_args()

    with configure_with_restart(args) as ser:
        print(f"[receiver] Polling {args.port} for messages")
        while True:
            lines = require_ok(ser, f"AT+RECV={args.recv_format}", wait=0.3, allow_empty=True)
            for line in lines:
                if not is_ignored_rx_line(line):
                    print(f"[rx] {line}")
            time.sleep(args.poll)


if __name__ == "__main__":
    main()
