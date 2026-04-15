#!/usr/bin/env python3
import argparse
import serial
import time

DEFAULT_PORT = "/dev/tty.usbserial-0002"
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
DEFAULT_ACK = 2
DEFAULT_RETRIES = 3
DEFAULT_SEND_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 2.0
DEFAULT_TEXT = "Hello-SOLAR"
RESET_NOTICE = "TAKE EFFECT AFTER ATZ"
SEND_FAILURE_MARKERS = ("NO ACK", "TIMEOUT", "FAILED", "FAIL")


def is_error_line(line):
    upper = line.upper()
    return "ERROR" in upper or "ERR" == upper or "UNKNOWN" in upper


def needs_reset(lines):
    return any(RESET_NOTICE in line.upper() for line in lines)


def send_failed(lines):
    upper_lines = [line.upper() for line in lines]
    return any(marker in line for line in upper_lines for marker in SEND_FAILURE_MARKERS)


def read_available_lines(ser, settle_time=0.2):
    time.sleep(settle_time)
    raw = ser.read_all().decode(errors="ignore")
    if not raw:
        return []
    return [line.strip() for line in raw.replace("\r", "\n").split("\n") if line.strip()]


def send_cmd(ser, cmd, wait=0.25):
    ser.write(f"{cmd.strip()}\r\n".encode())
    return read_available_lines(ser, wait)


def require_ok(ser, cmd, wait=0.25):
    lines = send_cmd(ser, cmd, wait)
    if any(is_error_line(line) for line in lines):
        raise RuntimeError(f"Modem rejected command: {cmd}")
    if not lines:
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

    print("[sender] Applying pending modem settings")
    send_cmd(ser, "ATZ", wait=0.5)
    ser.close()
    time.sleep(2.0)

    ser = open_serial(args.port, args.baud)
    try_probe_modem(ser)
    return ser


def send_wait_time(args):
    # Firmware docs say modem retransmissions are spaced 5 seconds apart.
    if args.ack == 0:
        return 1.0
    return 1.5 + (args.retries * 5.5)


def send_once(args, send_mode, payload):
    with configure_with_restart(args) as ser:
        lines = require_ok(
            ser,
            f"AT+SEND={send_mode},{payload},{args.ack},{args.retries}",
            wait=send_wait_time(args),
        )
        if args.ack != 0 and send_failed(lines):
            raise RuntimeError(f"Receiver acknowledgement failed: {' | '.join(lines)}")


def send_with_retries(args, send_mode, payload):
    last_error = None

    for attempt in range(1, args.send_attempts + 1):
        try:
            send_once(args, send_mode, payload)
            return attempt
        except (RuntimeError, serial.SerialException) as exc:
            last_error = exc
            if attempt == args.send_attempts:
                break
            print(
                f"[sender] Send attempt {attempt}/{args.send_attempts} failed: {exc}. "
                f"Retrying in {args.retry_delay:.1f}s..."
            )
            time.sleep(args.retry_delay)

    raise RuntimeError(
        f"Failed to send after {args.send_attempts} attempts: {last_error}"
    ) from last_error


def main():
    parser = argparse.ArgumentParser(description="LoRa sender for AT+SEND firmware")
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
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Text payload to send")
    parser.add_argument("--hex", help="Hex payload to send instead of text")
    parser.add_argument("--ack", type=int, choices=(0, 1, 2), default=DEFAULT_ACK, help="ACK mode 0/1/2")
    parser.add_argument("--retries", type=int, choices=range(0, 9), default=DEFAULT_RETRIES, help="Retransmissions 0-8")
    parser.add_argument(
        "--send-attempts",
        type=int,
        default=DEFAULT_SEND_ATTEMPTS,
        help="Application-level send attempts before failing",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEFAULT_RETRY_DELAY,
        help="Seconds to wait between application-level send attempts",
    )
    args = parser.parse_args()

    if args.hex:
        send_mode = 0
        payload = args.hex
    else:
        send_mode = 1
        payload = args.text

    if args.send_attempts < 1:
        raise ValueError("--send-attempts must be at least 1")
    if args.retry_delay < 0:
        raise ValueError("--retry-delay must be >= 0")

    attempts_used = send_with_retries(args, send_mode, payload)

    label = "hex" if send_mode == 0 else "text"
    if attempts_used == 1:
        print(f"[sender] Sent {label} payload on {args.port}: {payload}")
    else:
        print(
            f"[sender] Sent {label} payload on {args.port} after "
            f"{attempts_used} attempts: {payload}"
        )


if __name__ == "__main__":
    main()
