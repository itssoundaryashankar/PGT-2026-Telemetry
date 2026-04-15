#!/usr/bin/env python3
import argparse
import time

from BMV.bmv_handler import format_bmv_packet
from LORA.lora_transport import LoRaTransport, extract_hex_payload
from storage.event_csv_sink import write_event_csv
from telemetry_packet import MsgType, decode_packet


def decode_line(line, extract_payload, decoder):
    payload_hex = extract_payload(line)
    if not payload_hex:
        return None
    packet = bytes.fromhex(payload_hex)
    if len(packet) <= 2:
        return None
    return decoder(packet)


def route_packet(decoded, handlers=None):
    if not handlers:
        return None
    msg_type = decoded.get("msg_type")
    handler = handlers.get(msg_type)
    if handler is None and isinstance(msg_type, MsgType):
        handler = handlers.get(msg_type.value)
    if handler is None:
        return None
    return handler(decoded)


def dispatch_event(decoded, sinks=None):
    for sink in sinks or ():
        sink(decoded)


def run_receiver(
    *,
    transport,
    decoder,
    extract_payload,
    recv_format=0,
    poll_interval=1.0,
    wait=0.3,
    show_raw=False,
    handlers=None,
    sinks=None,
    log_prefix="receiver",
):
    print(f"[{log_prefix}] Starting receiver loop")

    while True:
        lines = transport.receive_hex_lines(recv_format=recv_format, wait=wait)
        for line in lines:
            if show_raw:
                print(f"[rx-raw] {line}")

            try:
                decoded = decode_line(line, extract_payload, decoder)
            except ValueError as exc:
                print(f"[rx] Failed to parse/decode '{line}': {exc}")
                continue

            if decoded is None:
                if not show_raw:
                    print(f"[rx-raw] {line}")
                continue

            dispatch_event(decoded, sinks=sinks)
            routed = route_packet(decoded, handlers=handlers)
            if routed is None:
                print(f"[rx] {decoded}")
            elif routed != "":
                print(routed)

        time.sleep(poll_interval)


DEFAULT_TRANSPORT = "lora"
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
DEFAULT_CSV_PATH = "received_events.csv"


def build_transport(args):
    return LoRaTransport(
        port=args.port,
        baud=args.baud,
        freq=args.freq,
        bw=args.bw,
        sf=args.sf,
        power=args.power,
        cr=args.cr,
        crc=args.crc,
        header=args.header,
        iq=args.iq,
        preamble=args.preamble,
        syncword=args.syncword,
        group=args.group,
        rx_timeout=args.rx_timeout,
        rx_ack=args.rx_ack,
    )


def build_handlers(_args):
    return {
        MsgType.BMV: format_bmv_packet,
    }


def build_sinks(args):
    if not args.csv_path:
        return ()
    return (
        lambda event: write_event_csv(args.csv_path, event),
    )


def build_parser():
    parser = argparse.ArgumentParser(description="Generic telemetry receiver")
    parser.add_argument("--transport", choices=("lora",), default=DEFAULT_TRANSPORT)
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
    parser.add_argument("--recv-format", type=int, choices=(0, 1), default=0, help="AT+RECV format 0=hex 1=text")
    parser.add_argument("--poll", type=float, default=1.0, help="Seconds between AT+RECV polls")
    parser.add_argument("--csv-path", default=DEFAULT_CSV_PATH, help="CSV output path for decoded events")
    parser.add_argument("--show-raw", action="store_true", help="Print raw modem receive lines")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.transport != "lora":
        raise ValueError(f"Unsupported transport {args.transport}")

    with build_transport(args) as transport:
        run_receiver(
            transport=transport,
            decoder=decode_packet,
            extract_payload=extract_hex_payload,
            recv_format=args.recv_format,
            poll_interval=args.poll,
            wait=0.3,
            show_raw=args.show_raw,
            handlers=build_handlers(args),
            sinks=build_sinks(args),
            log_prefix="receiver",
        )


if __name__ == "__main__":
    main()
