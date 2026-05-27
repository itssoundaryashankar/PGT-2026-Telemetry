#!/usr/bin/env python3
import argparse
import threading

from BMV.bmv_normalizer import normalize_bmv_frame
from BMV.bmv_policy import BMVTransmitPolicy
from BMV.bmv_reader import BMVReader
from CAN.can_normalizer import make_normalizer as make_can_normalizer
from CAN.can_policy import CANTransmitPolicy
from CAN.can_reader import CANReader
from LORA.lora_transport import LoRaTransport
from storage.csv_sink import write_telemetry_csv
from telemetry_packet import build_bmv_packet, build_can_packet


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

# ── CAN defaults ────────────────────────────────────────────────────────────
DEFAULT_CAN_CHANNEL = "can0"
DEFAULT_CAN_BITRATE = 500000
DEFAULT_CAN_CSV_PATH = "can_data.csv"
DEFAULT_CAN_MIN_INTERVAL = 1.0
DEFAULT_CAN_HEARTBEAT = 60.0

# Allowlist of CAN IDs to forward, and how each ID is routed on the receiver
# (msg_type -> InfluxDB bucket via the receiver's bucket_map).
# EDIT THIS DICT to match your car.  Example values shown.
DEFAULT_CAN_ID_MAP = {
    # MPPT (solar charge controller) frames -> "MPPT" bucket
    0x600: "MPPT",
    0x601: "MPPT",
    0x602: "MPPT",
    # BMS (battery management) frames -> "BMS" bucket
    0x100: "BMS",
    0x101: "BMS",
    0x102: "BMS",
}


# ─────────────────────────────────────────────────────────────────────────────
# Generic per-device sender loop (unchanged signature; thread-safe via lock)
# ─────────────────────────────────────────────────────────────────────────────

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
    transport_lock=None,
    stop_event=None,
):
    """Read frames, decide what to send, and ship them over the transport.

    transport_lock: optional threading.Lock that must be held while calling
                    transport.send_hex. Required when multiple devices share
                    one transport.
    stop_event:     optional threading.Event; the loop exits when set.
    """
    print(f"[{log_prefix}] Starting sender loop")

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break

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

            if transport_lock is not None:
                with transport_lock:
                    transport.send_hex(packet.hex())
            else:
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


# ─────────────────────────────────────────────────────────────────────────────
# BMV component builder (unchanged behavior)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# CAN component builder
# ─────────────────────────────────────────────────────────────────────────────

def _parse_can_id_map(spec):
    """Parse a CLI arg like '0x100=BMS,0x600=MPPT' into a dict.

    Falls back to DEFAULT_CAN_ID_MAP if `spec` is empty / None.
    """
    if not spec:
        return dict(DEFAULT_CAN_ID_MAP)
    result = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(f"--can-id-map entry missing '=': {entry!r}")
        id_str, msg_type = entry.split("=", 1)
        result[int(id_str, 0)] = msg_type.strip().upper()
    return result


def build_can_sender_components(args):
    id_map = _parse_can_id_map(args.can_id_map)

    reader = CANReader(
        channel=args.can_channel,
        bitrate=args.can_bitrate,
        id_allowlist=list(id_map.keys()),
    )
    normalizer = make_can_normalizer(id_to_msg_type=id_map)
    policy = CANTransmitPolicy(
        min_interval_seconds=args.can_min_interval,
        heartbeat_seconds=args.can_heartbeat_seconds,
    )
    sink = lambda reading: write_telemetry_csv(args.can_csv_path, reading)
    return {
        "reader": reader,
        "normalizer": normalizer,
        "policy": policy,
        "packet_builder": build_can_packet,
        "device_id": args.device_id,
        "log_prefix": "can",
        "sink": sink,
    }


SENDER_COMPONENT_BUILDERS = {
    "bmv": build_bmv_sender_components,
    "can": build_can_sender_components,
}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(description="Generic telemetry sender")
    parser.add_argument(
        "--device", default=DEFAULT_DEVICE,
        help=(
            "Comma-separated list of devices to run. "
            f"Available: {','.join(SENDER_COMPONENT_BUILDERS)}. "
            "Multiple devices share one LoRa transport. Default: bmv."
        ),
    )
    parser.add_argument("--transport", choices=("lora",), default=DEFAULT_TRANSPORT)
    parser.add_argument("--dry-run", action="store_true", help="Build packets but do not send over the transport")
    parser.add_argument("--device-id", type=int, default=DEFAULT_DEVICE_ID, help="Telemetry device id")
    parser.add_argument("--csv-path", default=DEFAULT_CSV_PATH, help="CSV output path (BMV)")

    parser.add_argument("--bmv-port", default=DEFAULT_BMV_PORT, help="BMV VE.Direct serial device")
    parser.add_argument("--bmv-baud", type=int, default=DEFAULT_BMV_BAUD, help="BMV serial baud rate")
    parser.add_argument("--voltage-delta-mv", type=int, default=DEFAULT_VOLTAGE_DELTA_MV)
    parser.add_argument("--current-delta-ma", type=int, default=DEFAULT_CURRENT_DELTA_MA)
    parser.add_argument("--power-delta-w", type=int, default=DEFAULT_POWER_DELTA_W)
    parser.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)

    # CAN options
    parser.add_argument("--can-channel", default=DEFAULT_CAN_CHANNEL,
                        help="SocketCAN interface (default can0)")
    parser.add_argument("--can-bitrate", type=int, default=DEFAULT_CAN_BITRATE,
                        help="CAN bus bitrate (informational for SocketCAN)")
    parser.add_argument("--can-id-map", default="",
                        help="Comma-separated ID=msg_type pairs, e.g. "
                             "'0x100=BMS,0x600=MPPT'. Empty -> built-in default.")
    parser.add_argument("--can-min-interval", type=float, default=DEFAULT_CAN_MIN_INTERVAL,
                        help="Minimum seconds between transmissions for the "
                             "same CAN ID")
    parser.add_argument("--can-heartbeat-seconds", type=float, default=DEFAULT_CAN_HEARTBEAT,
                        help="Forward an unchanged CAN frame after this many "
                             "seconds of silence")
    parser.add_argument("--can-csv-path", default=DEFAULT_CAN_CSV_PATH,
                        help="CSV output path for raw CAN readings")

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


# ─────────────────────────────────────────────────────────────────────────────
# Multi-device runner
# ─────────────────────────────────────────────────────────────────────────────

def _parse_devices(spec):
    raw = [d.strip().lower() for d in spec.split(",") if d.strip()]
    if not raw:
        raise ValueError("--device must name at least one device")
    unknown = [d for d in raw if d not in SENDER_COMPONENT_BUILDERS]
    if unknown:
        raise ValueError(
            f"Unknown device(s): {unknown}. "
            f"Available: {sorted(SENDER_COMPONENT_BUILDERS)}"
        )
    # de-dupe while preserving order
    seen = set()
    devices = []
    for d in raw:
        if d not in seen:
            seen.add(d)
            devices.append(d)
    return devices


def _run_one(components, transport, transport_lock, stop_event):
    try:
        run_sender(
            **components,
            transport=transport,
            transport_lock=transport_lock,
            stop_event=stop_event,
        )
    except Exception as exc:
        print(f"[{components['log_prefix']}] Sender thread crashed: {exc}")
        stop_event.set()


def _run_all(devices, args, transport):
    transport_lock = threading.Lock() if transport is not None and len(devices) > 1 else None
    stop_event = threading.Event()

    if len(devices) == 1:
        components = SENDER_COMPONENT_BUILDERS[devices[0]](args)
        print(f"[{components['log_prefix']}] Reading from device source")
        run_sender(
            **components,
            transport=transport,
            transport_lock=transport_lock,
            stop_event=stop_event,
        )
        return

    threads = []
    for device in devices:
        components = SENDER_COMPONENT_BUILDERS[device](args)
        print(f"[{components['log_prefix']}] Reading from device source")
        t = threading.Thread(
            target=_run_one,
            args=(components, transport, transport_lock, stop_event),
            name=f"sender-{device}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    try:
        # Wait until any thread sets stop_event (e.g. on crash) or Ctrl-C
        while not stop_event.is_set():
            stop_event.wait(timeout=0.5)
    except KeyboardInterrupt:
        print("\n[sender] Ctrl-C received, stopping…")
        stop_event.set()
    finally:
        for t in threads:
            t.join(timeout=5.0)


def main(argv=None):
    args = build_parser().parse_args(argv)
    devices = _parse_devices(args.device)

    if args.dry_run:
        _run_all(devices, args, transport=None)
        return

    if args.transport != "lora":
        raise ValueError(f"Unsupported transport {args.transport}")

    with build_lora_transport(args) as transport:
        _run_all(devices, args, transport=transport)


if __name__ == "__main__":
    main()
