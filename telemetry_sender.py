#!/usr/bin/env python3
import argparse
import sys

# Make stdout line-buffered so logs appear immediately on Windows / piped runs.
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

from BMV.bmv_normalizer import normalize_bmv_frame
from BMV.bmv_policy import BMVTransmitPolicy
from BMV.bmv_reader import BMVReader
from LORA.lora_transport import LoRaTransport
from storage.csv_sink import write_telemetry_csv
from telemetry_packet import build_bmv_packet


DEFAULT_DEVICE = "all"
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

# CAN defaults
DEFAULT_CAN_INTERFACE = "can0"
DEFAULT_CAN_BITRATE = 500000
DEFAULT_MPPT_DEVICE_ID = 2
DEFAULT_BMS_DEVICE_ID = 3
DEFAULT_MPPT_HEARTBEAT_SECONDS = 60
DEFAULT_BMS_HEARTBEAT_SECONDS = 60
DEFAULT_CSV_PATH_MPPT = "mppt_data.csv"
DEFAULT_CSV_PATH_BMS = "bms_data.csv"


# ─────────────────────────────────────────────────────────────────────────────
# CORE LOOP — supports both single-stream (BMV) and multi-stream (CAN) readers
# ─────────────────────────────────────────────────────────────────────────────

def _process_reading(components, raw_frame, transport, log_prefix):
    """Run a single raw frame through normalizer -> policy -> packet builder
    -> transport. Returns nothing; side-effects only (sink writes, prints,
    LoRa transmits)."""
    normalizer = components["normalizer"]
    policy = components["policy"]
    packet_builder = components["packet_builder"]
    device_id = components["device_id"]
    sink = components.get("sink")
    sub_prefix = components.get("log_prefix", log_prefix)

    reading = normalizer(raw_frame, device_id)
    if sink is not None:
        sink(reading)

    event_type = policy.classify(reading)
    if event_type is None:
        return

    seq = policy.mark_sent(reading)
    packet = packet_builder(reading, event_type, seq)

    if transport is None:
        print(
            f"[{sub_prefix}] {event_type.name} seq={seq} "
            f"fields={reading['fields']} hex={packet.hex()}",
            flush=True,
        )
        return

    transport.send_hex(packet.hex())
    print(
        f"[{sub_prefix}] Sent {event_type.name} seq={seq} "
        f"fields={reading['fields']}",
        flush=True,
    )


def run_sender(
    *,
    reader,
    streams=None,
    # The kwargs below are a convenience for single-stream callers (BMV).
    # If `streams` is None, these are used to build a one-entry streams dict.
    normalizer=None,
    policy=None,
    packet_builder=None,
    device_id=None,
    log_prefix="telemetry",
    sink=None,
    transport=None,
):
    """Run a sender loop.

    Two operating modes:

    1. Single-stream (legacy BMV):
       run_sender(reader=..., normalizer=..., policy=..., packet_builder=...,
                  device_id=..., sink=..., transport=...)
       The reader's read_frame() returns a raw frame dict.

    2. Multi-stream (CAN):
       run_sender(reader=..., streams={"mppt": {...}, "bms": {...}},
                  transport=..., log_prefix="can")
       The reader's read_frame() returns (kind, raw_frame_dict). Each kind
       has its own normalizer/policy/packet_builder/device_id/sink in its
       components dict.
    """
    if streams is None:
        # Wrap single-stream args into the streams shape under a default kind
        if normalizer is None or policy is None or packet_builder is None:
            raise ValueError(
                "run_sender needs either a `streams` dict or the full set of "
                "single-stream kwargs (normalizer/policy/packet_builder/...)"
            )
        streams = {
            "_default": {
                "normalizer": normalizer,
                "policy": policy,
                "packet_builder": packet_builder,
                "device_id": device_id,
                "sink": sink,
                "log_prefix": log_prefix,
            }
        }
        single_stream = True
    else:
        single_stream = False

    print(f"[{log_prefix}] Starting sender loop", flush=True)
    for kind, comps in streams.items():
        if kind != "_default":
            print(f"[{log_prefix}]   stream: {kind} -> "
                  f"device_id={comps.get('device_id')}, "
                  f"policy={type(comps['policy']).__name__}", flush=True)

    try:
        while True:
            result = reader.read_frame()

            if single_stream:
                # Legacy reader returns raw frame directly
                kind = "_default"
                raw_frame = result
            else:
                # Multi-stream reader returns (kind, frame)
                if result is None:
                    continue
                kind, raw_frame = result

            comps = streams.get(kind)
            if comps is None:
                # Unknown kind — log once but don't crash
                print(f"[{log_prefix}] Unknown stream kind {kind!r}, skipping",
                      flush=True)
                continue

            _process_reading(comps, raw_frame, transport, log_prefix)
    finally:
        reader.close()


# ─────────────────────────────────────────────────────────────────────────────
# TRANSPORT WIRING
# ─────────────────────────────────────────────────────────────────────────────

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
# COMPONENT BUILDERS — one per --device choice
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


def build_can_sender_components(args):
    """Build a multi-stream sender that reads MPPT and BMS frames off the
    same CAN bus and emits separate packet streams for each."""
    # Imports are local so non-Pi machines can still import this module
    # without python-can installed.
    from CAN.can_reader import CANReader
    from CAN.can_normalizer import normalize_mppt_frame, normalize_bms_frame
    from CAN.can_policy import GenericTransmitPolicy

    # MsgType / packet builders for the new device kinds. These live in
    # telemetry_packet, but to keep this refactor self-contained we resolve
    # them lazily and fall back to the BMV packet shape if they don't yet
    # exist (so the sender can run even before the receiver-side changes
    # are deployed).
    try:
        from telemetry_packet import (
            MsgType,
            build_mppt_packet,
            build_bms_packet,
        )
        mppt_change = MsgType.MPPT
        mppt_heartbeat = MsgType.MPPT
        bms_change = MsgType.BMS
        bms_heartbeat = MsgType.BMS
    except ImportError:
        # Fallback: reuse BMV machinery as a placeholder so the sender
        # is testable end-to-end before MsgType.MPPT/BMS are added.
        print("[can] WARNING: MsgType.MPPT/BMS not found in telemetry_packet; "
              "falling back to BMV packet builder. Add them to telemetry_packet "
              "before deploying.", flush=True)
        from telemetry_packet import MsgType
        mppt_change = mppt_heartbeat = MsgType.BMV
        bms_change = bms_heartbeat = MsgType.BMV
        build_mppt_packet = build_bmv_packet
        build_bms_packet = build_bmv_packet

    reader = CANReader(
        interface=args.can_interface,
        bitrate=args.can_bitrate,
    )

    mppt_policy = GenericTransmitPolicy(
        deltas={
            "pv_voltage_v":      args.mppt_pv_voltage_delta_v,
            "pv_current_a":      args.mppt_pv_current_delta_a,
            "pv_power_w":        args.mppt_pv_power_delta_w,
            "battery_voltage_v": args.mppt_batt_voltage_delta_v,
        },
        event_type_change=mppt_change,
        event_type_heartbeat=mppt_heartbeat,
        heartbeat_seconds=args.mppt_heartbeat_seconds,
    )
    bms_policy = GenericTransmitPolicy(
        deltas={
            "battery_voltage_v": args.bms_voltage_delta_v,
            "battery_current_a": args.bms_current_delta_a,
            "soc_pct":           args.bms_soc_delta_pct,
        },
        event_type_change=bms_change,
        event_type_heartbeat=bms_heartbeat,
        heartbeat_seconds=args.bms_heartbeat_seconds,
    )

    mppt_sink = lambda r: write_telemetry_csv(args.csv_path_mppt, r)
    bms_sink = lambda r: write_telemetry_csv(args.csv_path_bms, r)

    return {
        "reader": reader,
        "log_prefix": "can",
        "streams": {
            "mppt": {
                "normalizer": normalize_mppt_frame,
                "policy": mppt_policy,
                "packet_builder": build_mppt_packet,
                "device_id": args.mppt_device_id,
                "sink": mppt_sink,
                "log_prefix": "mppt",
            },
            "bms": {
                "normalizer": normalize_bms_frame,
                "policy": bms_policy,
                "packet_builder": build_bms_packet,
                "device_id": args.bms_device_id,
                "sink": bms_sink,
                "log_prefix": "bms",
            },
        },
    }


SENDER_COMPONENT_BUILDERS = {
    "bmv": build_bmv_sender_components,
    "can": build_can_sender_components,
}


# ─────────────────────────────────────────────────────────────────────────────
# 'all' mode: probe hardware, run whatever's available
# ─────────────────────────────────────────────────────────────────────────────

class LockedTransport:
    """Thread-safe wrapper around LoRaTransport. Multiple sender threads share
    one underlying modem; this serializes send_hex calls so two threads can't
    interleave AT commands. Pass-through for any other attribute access so
    `with build_lora_transport(args) as t` semantics still work."""

    def __init__(self, inner):
        import threading
        self._inner = inner
        self._lock = threading.Lock()

    def send_hex(self, hex_str):
        with self._lock:
            return self._inner.send_hex(hex_str)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _try_build_bmv_components(args):
    """Try to build BMV components. Returns the dict if the BMV hardware is
    reachable, or None (with a warning) if not."""
    try:
        return build_bmv_sender_components(args)
    except Exception as exc:
        print(f"[all] BMV unavailable, skipping: {exc}", flush=True)
        return None


def _try_build_can_components(args):
    """Try to build CAN components. Returns the dict if the CAN interface is
    reachable, or None (with a warning) if not."""
    try:
        return build_can_sender_components(args)
    except Exception as exc:
        print(f"[all] CAN unavailable, skipping: {exc}", flush=True)
        return None


def build_all_sender_components(args):
    """Marker for 'all' mode — actual orchestration happens in main().
    Returning a sentinel here keeps the dispatch table simple."""
    return {"_all_mode": True, "log_prefix": "all"}


SENDER_COMPONENT_BUILDERS["all"] = build_all_sender_components


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(description="Generic telemetry sender")
    parser.add_argument("--device", choices=tuple(SENDER_COMPONENT_BUILDERS), default=DEFAULT_DEVICE)
    parser.add_argument("--transport", choices=("lora",), default=DEFAULT_TRANSPORT)
    parser.add_argument("--dry-run", action="store_true", help="Build packets but do not send over the transport")
    parser.add_argument("--device-id", type=int, default=DEFAULT_DEVICE_ID, help="Telemetry device id (BMV)")
    parser.add_argument("--csv-path", default=DEFAULT_CSV_PATH, help="CSV output path (BMV)")

    # BMV
    parser.add_argument("--bmv-port", default=DEFAULT_BMV_PORT, help="BMV VE.Direct serial device")
    parser.add_argument("--bmv-baud", type=int, default=DEFAULT_BMV_BAUD, help="BMV serial baud rate")
    parser.add_argument("--voltage-delta-mv", type=int, default=DEFAULT_VOLTAGE_DELTA_MV)
    parser.add_argument("--current-delta-ma", type=int, default=DEFAULT_CURRENT_DELTA_MA)
    parser.add_argument("--power-delta-w", type=int, default=DEFAULT_POWER_DELTA_W)
    parser.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)

    # CAN bus
    parser.add_argument("--can-interface", default=DEFAULT_CAN_INTERFACE,
                        help="SocketCAN interface name (e.g. can0)")
    parser.add_argument("--can-bitrate", type=int, default=DEFAULT_CAN_BITRATE,
                        help="CAN bus bitrate (default 500000 for EG4/most BMS)")
    parser.add_argument("--csv-path-mppt", default=DEFAULT_CSV_PATH_MPPT,
                        help="CSV output path for MPPT data")
    parser.add_argument("--csv-path-bms", default=DEFAULT_CSV_PATH_BMS,
                        help="CSV output path for BMS data")

    # MPPT
    parser.add_argument("--mppt-device-id", type=int, default=DEFAULT_MPPT_DEVICE_ID,
                        help="Telemetry device id for MPPT")
    parser.add_argument("--mppt-pv-voltage-delta-v", type=float, default=2.0,
                        help="MPPT: PV voltage change to trigger transmit (V)")
    parser.add_argument("--mppt-pv-current-delta-a", type=float, default=0.5,
                        help="MPPT: PV current change to trigger transmit (A)")
    parser.add_argument("--mppt-pv-power-delta-w", type=float, default=20.0,
                        help="MPPT: PV power change to trigger transmit (W)")
    parser.add_argument("--mppt-batt-voltage-delta-v", type=float, default=0.2,
                        help="MPPT: battery voltage change to trigger transmit (V)")
    parser.add_argument("--mppt-heartbeat-seconds", type=int, default=DEFAULT_MPPT_HEARTBEAT_SECONDS)

    # BMS
    parser.add_argument("--bms-device-id", type=int, default=DEFAULT_BMS_DEVICE_ID,
                        help="Telemetry device id for BMS")
    parser.add_argument("--bms-voltage-delta-v", type=float, default=0.2,
                        help="BMS: battery voltage change to trigger transmit (V)")
    parser.add_argument("--bms-current-delta-a", type=float, default=1.0,
                        help="BMS: battery current change to trigger transmit (A)")
    parser.add_argument("--bms-soc-delta-pct", type=float, default=1.0,
                        help="BMS: SOC change to trigger transmit (%)")
    parser.add_argument("--bms-heartbeat-seconds", type=int, default=DEFAULT_BMS_HEARTBEAT_SECONDS)

    # LoRa
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


def _run_in_thread(name, components, transport):
    """Wrap run_sender so an exception in one thread doesn't bring others down.
    Returns the Thread object (already started)."""
    import threading

    def _target():
        try:
            run_sender(**components, transport=transport)
        except Exception as exc:
            print(f"[{name}] thread crashed: {exc}", flush=True)

    t = threading.Thread(target=_target, name=name, daemon=True)
    t.start()
    return t


def _run_all(args, transport):
    """Build whatever device components we can, run each in its own thread,
    block until any thread dies (typically only on Ctrl-C)."""
    threads = []
    components_built = []

    bmv = _try_build_bmv_components(args)
    if bmv is not None:
        components_built.append(("bmv", bmv))
    can = _try_build_can_components(args)
    if can is not None:
        components_built.append(("can", can))

    if not components_built:
        raise RuntimeError(
            "No telemetry sources available. Tried BMV serial port and CAN "
            "interface, neither could be opened. Check --bmv-port and "
            "--can-interface, or run with an explicit --device flag."
        )

    print(f"[all] Starting {len(components_built)} source(s): "
          f"{[name for name, _ in components_built]}", flush=True)

    for name, comps in components_built:
        threads.append(_run_in_thread(name, comps, transport))

    # Block on threads. Daemon threads exit when main exits (Ctrl-C),
    # so we just join with a timeout in a loop to stay responsive to signals.
    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        print("[all] Interrupted, shutting down", flush=True)


def main(argv=None):
    args = build_parser().parse_args(argv)
    components = SENDER_COMPONENT_BUILDERS[args.device](args)

    print(f"[{components['log_prefix']}] Reading from device source", flush=True)

    is_all_mode = components.get("_all_mode", False)

    if args.dry_run:
        if is_all_mode:
            raise ValueError("--dry-run is not supported with --device all "
                             "(use --device bmv or --device can)")
        run_sender(**components)
        return

    if args.transport != "lora":
        raise ValueError(f"Unsupported transport {args.transport}")

    with build_lora_transport(args) as transport:
        if is_all_mode:
            _run_all(args, LockedTransport(transport))
        else:
            run_sender(**components, transport=transport)


if __name__ == "__main__":
    main()
