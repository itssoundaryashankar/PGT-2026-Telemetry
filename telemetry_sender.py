#!/usr/bin/env python3
import argparse

from BMV.bmv_normalizer import normalize_bmv_frame
from BMV.bmv_policy import BMVTransmitPolicy
from BMV.bmv_reader import BMVReader
from LORA.lora_transport import LoRaTransport
from storage.csv_sink import write_telemetry_csv
from telemetry_packet import build_bmv_packet


DEFAULT_DEVICE = "bmv"
DEFAULT_TRANSPORT = "lora"
DEFAULT_BMV_PORT = "/dev/tty.usbserial-VE7ALZXZ"
DEFAULT_BMV_BAUD = 19200
DEFAULT_CSV_PATH = "bmv_data.csv"
DEFAULT_DEVICE_ID = 1

DEFAULT_LORA_PORT = "/dev/tty.usbserial-0002"
DEFAULT_LORA_BAUD = 9600
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

DEFAULT_VOLTAGE_DELTA_MV = 100
DEFAULT_CURRENT_DELTA_MA = 500
DEFAULT_POWER_DELTA_W = 10
DEFAULT_HEARTBEAT_SECONDS = 60


def process_reading(reader, normalizer, policy, packet_builder, device_id, sink=None):
    reading = normalizer(reader.read_frame(), device_id)
    if sink is not None:
        sink(reading)

    event_type = policy.classify(reading)
    if event_type is None:
        return None, None, None, None

    seq = policy.mark_sent(reading)
    packet = packet_builder(reading, event_type, seq)
    return reading, event_type, packet, seq


def run_sender(
    *,
    reader,
    normalizer,
    policy,
    packet_builder,
    device_id,
    log_prefix="telemetry",
    sink=None,
    transport=None,
):
    print(f"[{log_prefix}] Starting sender loop")

    try:
        while True:
            reading, event_type, packet, seq = process_reading(
                reader=reader,
                normalizer=normalizer,
                policy=policy,
                packet_builder=packet_builder,
                device_id=device_id,
                sink=sink,
            )
            if event_type is None:
                continue

            if transport is None:
                print(
                    f"[{log_prefix}] {event_type.name} seq={seq} "
                    f"fields={reading['fields']} hex={packet.hex()}"
                )
                continue

            transport.send_hex(packet.hex())
            print(
                f"[{log_prefix}] Sent {event_type.name} seq={seq} "
                f"fields={reading['fields']}"
            )
    finally:
        reader.close()


def build_lora_transport(args):
    return LoRaTransport(
        port=args.lora_port,
        baud=args.lora_baud,
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
        ack=args.ack,
        retries=args.retries,
    )


def build_bmv_sender_components(args):
    reader = BMVReader(args.bmv_port, args.bmv_baud)
    policy = BMVTransmitPolicy(
        voltage_delta_mv=args.voltage_delta_mv,
        current_delta_ma=args.current_delta_ma,
        power_delta_w=args.power_delta_w,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    sink = lambda reading: write_telemetry_csv(args.csv_path, reading)
    return {
        "reader": reader,
        "normalizer": normalize_bmv_frame,
        "policy": policy,
        "packet_builder": build_bmv_packet,
        "device_id": args.device_id,
        "log_prefix": "bmv",
        "sink": sink,
    }


SENDER_COMPONENT_BUILDERS = {
    "bmv": build_bmv_sender_components,
}


def build_parser():
    parser = argparse.ArgumentParser(description="Generic telemetry sender")
    parser.add_argument("--device", choices=tuple(SENDER_COMPONENT_BUILDERS), default=DEFAULT_DEVICE)
    parser.add_argument("--transport", choices=("lora",), default=DEFAULT_TRANSPORT)
    parser.add_argument("--dry-run", action="store_true", help="Build packets but do not send over the transport")
    parser.add_argument("--device-id", type=int, default=DEFAULT_DEVICE_ID, help="Telemetry device id")
    parser.add_argument("--csv-path", default=DEFAULT_CSV_PATH, help="CSV output path")

    parser.add_argument("--bmv-port", default=DEFAULT_BMV_PORT, help="BMV VE.Direct serial device")
    parser.add_argument("--bmv-baud", type=int, default=DEFAULT_BMV_BAUD, help="BMV serial baud rate")
    parser.add_argument("--voltage-delta-mv", type=int, default=DEFAULT_VOLTAGE_DELTA_MV)
    parser.add_argument("--current-delta-ma", type=int, default=DEFAULT_CURRENT_DELTA_MA)
    parser.add_argument("--power-delta-w", type=int, default=DEFAULT_POWER_DELTA_W)
    parser.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)

    parser.add_argument("--lora-port", default=DEFAULT_LORA_PORT, help="LoRa modem serial device")
    parser.add_argument("--lora-baud", type=int, default=DEFAULT_LORA_BAUD, help="LoRa modem baud rate")
    parser.add_argument("--freq", default=DEFAULT_FREQ, help="TX/RX frequency in MHz")
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
    parser.add_argument("--ack", type=int, choices=(0, 1, 2), default=DEFAULT_ACK, help="ACK mode 0/1/2")
    parser.add_argument("--retries", type=int, choices=range(0, 9), default=DEFAULT_RETRIES, help="Retransmissions 0-8")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    components = SENDER_COMPONENT_BUILDERS[args.device](args)

    print(f"[{components['log_prefix']}] Reading from device source")

    if args.dry_run:
        run_sender(**components)
        return

    if args.transport != "lora":
        raise ValueError(f"Unsupported transport {args.transport}")

    with build_lora_transport(args) as transport:
        run_sender(
            **components,
            transport=transport,
        )


if __name__ == "__main__":
    main()
