#!/usr/bin/env python3
import argparse
import os
import sys
import time
from datetime import datetime, timezone

# Force line-buffered stdout so messages appear immediately even when the
# script is run under an IDE, redirected, or piped (Windows + PowerShell in
# particular love to switch to block-buffering, which makes the program look
# stuck during the blocking serial read).
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass  # Python < 3.7, very unlikely here

from BMV.bmv_handler import format_bmv_packet
from LORA.lora_transport import LoRaTransport, extract_hex_payload
from storage.event_csv_sink import write_event_csv
from telemetry_packet import MsgType, decode_packet


# ─────────────────────────────────────────────────────────────────────────────
# INFLUXDB WRITER
# ─────────────────────────────────────────────────────────────────────────────

def _flatten_event(event: dict, prefix: str = "", out: dict | None = None) -> dict:
    """Flatten a decoded packet so nested dicts become dotted-name fields.
    e.g. {'data': {'voltage': 12.5}} -> {'data_voltage': 12.5}.
    Lists/tuples of primitives become indexed fields: foo_0, foo_1, ..."""
    if out is None:
        out = {}
    for k, v in event.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}_{k}"
        if isinstance(v, dict):
            _flatten_event(v, prefix=key, out=out)
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    _flatten_event(item, prefix=f"{key}_{i}", out=out)
                else:
                    out[f"{key}_{i}"] = item
        else:
            out[key] = v
    return out


class InfluxWriter:
    """Writes decoded telemetry events to InfluxDB v2.

    Routes events to different buckets based on their `msg_type`, falling back
    to `default_bucket` if no specific mapping is configured. Numeric values
    are stored as fields; strings/enums become fields too. `msg_type` itself
    is promoted to a tag so it can be used for fast filtering inside a bucket.
    """

    def __init__(self, url, token, org, default_bucket, measurement,
                 bucket_map: dict | None = None):
        # Imported here so the script still runs without influxdb-client
        # installed when --influx-enable is not set.
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        self.default_bucket = default_bucket
        self.bucket_map = dict(bucket_map or {})
        self.measurement = measurement
        self.client = InfluxDBClient(url=url, token=token, org=org)
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        if self.bucket_map:
            mapping_str = ", ".join(f"{k}->{v}" for k, v in self.bucket_map.items())
            print(f"[influx] Connected to {url}  default_bucket={default_bucket}  "
                  f"routes={{{mapping_str}}}")
        else:
            print(f"[influx] Connected to {url}  bucket={default_bucket}")

    def _bucket_for(self, msg_type) -> str:
        # Try the enum name first, then the raw value, then string form.
        if isinstance(msg_type, MsgType):
            for key in (msg_type.name, msg_type.value):
                if key in self.bucket_map:
                    return self.bucket_map[key]
        if msg_type in self.bucket_map:
            return self.bucket_map[msg_type]
        if isinstance(msg_type, str) and msg_type in self.bucket_map:
            return self.bucket_map[msg_type]
        return self.default_bucket

    def write(self, event: dict, tags: dict | None = None):
        if not event:
            return

        # Flatten nested dicts so {data: {voltage: 12.5}} becomes
        # {data_voltage: 12.5} instead of being stringified into oblivion.
        flat = _flatten_event(event)

        numeric_fields: dict = {}
        string_fields: dict = {}
        point_tags = dict(tags or {})
        msg_type_value = None

        for k, v in flat.items():
            if v is None:
                continue

            # msg_type goes into TAGS (not fields) so InfluxDB can index it
            # for fast filtering — that's how you separate BMV / ASCII / etc.
            if k == "msg_type":
                msg_type_value = v
                if isinstance(v, MsgType):
                    point_tags["msg_type"] = v.name
                else:
                    point_tags["msg_type"] = str(v)
                continue

            if isinstance(v, bool):
                numeric_fields[k] = int(v)
            elif isinstance(v, (int, float)):
                numeric_fields[k] = float(v)
            elif isinstance(v, str):
                # Skip the noisy debug strings to avoid cluttering Data Explorer
                if k in ("payload_hex",):
                    continue
                string_fields[k] = v
            elif isinstance(v, MsgType):
                string_fields[k] = v.name
            else:
                string_fields[k] = str(v)

        all_fields = {**numeric_fields, **string_fields}
        if not all_fields:
            print(f"[influx] Skipping write: no usable fields in {event}")
            return

        if not numeric_fields:
            all_fields["received"] = 1.0
            print(f"[influx] No numeric fields in event, only strings: "
                  f"{list(string_fields.keys())}")

        bucket = self._bucket_for(msg_type_value)
        record = {
            "measurement": self.measurement,
            "tags": point_tags,
            "fields": all_fields,
            "time": datetime.now(timezone.utc),
        }
        try:
            self.write_api.write(bucket=bucket, record=record)
        except Exception as exc:
            print(f"[influx] Write failed (bucket={bucket}): {exc}")

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass


def make_influx_sink(writer: InfluxWriter, tags: dict):
    def _sink(event: dict):
        writer.write(event, tags=tags)
    return _sink


# ─────────────────────────────────────────────────────────────────────────────
# ASCII FALLBACK DECODER
# ─────────────────────────────────────────────────────────────────────────────
#
# Used when the binary `decode_packet` can't make sense of the bytes (e.g.
# bench tests where the sender just transmits "67.67"). Mirrors the formats
# supported by dragino_lora_influx.py: JSON, key=value, bare number, CSV,
# plain text.

import json
import re


def _clean_ascii(text: str) -> str:
    if not text:
        return ""
    return text.replace("\x00", "").replace("\ufffd", "").strip()


def decode_ascii_payload(raw: bytes) -> dict | None:
    """Try to decode `raw` as ASCII telemetry. Returns a decoded event dict
    on success, or None if the bytes don't look like usable text."""
    if not raw:
        return None

    hex_repr = raw.hex()

    try:
        text = _clean_ascii(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return None

    if not text:
        return None

    event: dict = {
        "msg_type": "ASCII",
        "payload": text,
        "payload_hex": hex_repr,
    }

    # 1. JSON object
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            for k, v in obj.items():
                try:
                    event[k.lower()] = float(v)
                except (TypeError, ValueError):
                    if isinstance(v, str):
                        event[k.lower()] = v
            return event
        except json.JSONDecodeError:
            pass

    # 2. key=value pairs  (e.g. "temp=24.5,hum=60")
    kv = re.findall(r"(\w+)=([-+]?[\d.]+)", text)
    if kv:
        for k, v in kv:
            try:
                event[k.lower()] = float(v)
            except ValueError:
                pass
        if len(event) > 3:  # got at least one real field beyond the defaults
            return event

    # 3. Bare number (e.g. "67.67")
    try:
        event["value"] = float(text)
        return event
    except ValueError:
        pass

    # 4. CSV numbers (e.g. "24.5,60.1,3.3")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 2:
        try:
            nums = [float(p) for p in parts]
            for i, v in enumerate(nums[:5]):
                event[f"value_{i}"] = v
            return event
        except ValueError:
            pass

    # 5. Plain text — return with payload field only, no numerics
    return event


# ─────────────────────────────────────────────────────────────────────────────
# DECODE / DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

def _looks_decoded(decoded) -> bool:
    """Heuristic: did the binary decoder actually recognise the packet?
    We accept only results with a proper MsgType enum — anything else
    (including a dict with a stray integer 'msg_type') is treated as
    'binary decoder didn't really know what this was' and we fall
    through to ASCII."""
    if not isinstance(decoded, dict):
        return False
    return isinstance(decoded.get("msg_type"), MsgType)


def decode_line(line, extract_payload, decoder, ascii_fallback=True):
    payload_hex = extract_payload(line)
    if not payload_hex:
        return None
    packet = bytes.fromhex(payload_hex)
    if not packet:
        return None

    binary_error = None
    if len(packet) > 2:
        try:
            decoded = decoder(packet)
        except ValueError as exc:
            binary_error = exc
            decoded = None

        if _looks_decoded(decoded):
            return decoded

    # Fall through to ASCII
    if ascii_fallback:
        ascii_decoded = decode_ascii_payload(packet)
        if ascii_decoded is not None:
            return ascii_decoded

    # Nothing worked — re-raise the original binary error if there was one,
    # so the caller's existing error message still surfaces.
    if binary_error is not None:
        raise binary_error
    return None


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
        try:
            sink(decoded)
        except Exception as exc:
            print(f"[rx] Sink error: {exc}")


def _now_str():
    return datetime.now().strftime("%H:%M:%S")


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
    ascii_fallback=True,
):
    print(f"[{log_prefix}] Starting receiver loop", flush=True)
    print(f"\n📡 Listening for LoRa data — press Ctrl-C to stop\n", flush=True)

    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 10  # bail out only after sustained failure

    while True:
        try:
            lines = transport.receive_hex_lines(recv_format=recv_format, wait=wait)
            consecutive_errors = 0  # reset on any successful poll
        except RuntimeError as exc:
            consecutive_errors += 1
            print(f"[rx] Modem poll failed ({consecutive_errors}/"
                  f"{MAX_CONSECUTIVE_ERRORS}): {exc}", flush=True)
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"[rx] Modem unresponsive for {MAX_CONSECUTIVE_ERRORS} "
                      f"polls in a row — giving up.", flush=True)
                raise
            # Back off briefly so we don't hammer a confused modem
            time.sleep(min(2.0 * consecutive_errors, 10.0))
            continue

        for line in lines:
            if show_raw:
                print(f"[rx-raw] {line}")

            try:
                decoded = decode_line(line, extract_payload, decoder,
                                      ascii_fallback=ascii_fallback)
            except ValueError as exc:
                print(f"[rx] Failed to parse/decode '{line}': {exc}")
                continue

            if decoded is None:
                if not show_raw:
                    print(f"[rx-raw] {line}")
                continue

            # Highly visible "we got something" line, before sinks/handlers run
            msg_type = decoded.get("msg_type")
            type_label = msg_type.name if isinstance(msg_type, MsgType) else str(msg_type)
            print(f"✅ [{_now_str()}] Received packet  type={type_label}", flush=True)

            dispatch_event(decoded, sinks=sinks)

            routed = route_packet(decoded, handlers=handlers)
            if routed is None:
                print(f"[rx] {decoded}")
            elif routed != "":
                print(routed)

        time.sleep(poll_interval)


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULTS
# ─────────────────────────────────────────────────────────────────────────────

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

# InfluxDB defaults — overridable via CLI args or env vars
DEFAULT_INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
DEFAULT_INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
DEFAULT_INFLUX_ORG = os.getenv("INFLUX_ORG", "my-org")
# Default bucket is used as a fallback for any msg_type without a dedicated
# bucket configured below.
DEFAULT_INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "lorawan-data")
DEFAULT_INFLUX_MEASUREMENT = os.getenv("INFLUX_MEASUREMENT", "telemetry")

# Per-msg-type bucket overrides
DEFAULT_INFLUX_BUCKET_BMV = os.getenv("INFLUX_BUCKET_BMV", "BMV-data")
DEFAULT_INFLUX_BUCKET_MPPT = os.getenv("INFLUX_BUCKET_MPPT", "CAN-data")
DEFAULT_INFLUX_BUCKET_BMS = os.getenv("INFLUX_BUCKET_BMS", "CAN-data")


# ─────────────────────────────────────────────────────────────────────────────
# WIRING
# ─────────────────────────────────────────────────────────────────────────────

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


def build_sinks(args, influx_writer=None):
    sinks = []

    if args.csv_path:
        sinks.append(lambda event: write_event_csv(args.csv_path, event))

    if influx_writer is not None:
        tags = {"source": "telemetry_receiver", "port": args.port}
        sinks.append(make_influx_sink(influx_writer, tags))

    return tuple(sinks)


def build_influx_writer(args):
    if not args.influx_enable:
        return None
    if not args.influx_token:
        print("[influx] --influx-enable set but no token provided "
              "(use --influx-token or INFLUX_TOKEN env var). Skipping InfluxDB.")
        return None

    # Map known msg_types to their dedicated buckets. Anything not in this
    # map (e.g. "ASCII" test packets) falls back to default_bucket.
    bucket_map = {
        "BMV":  args.influx_bucket_bmv,
        "MPPT": args.influx_bucket_mppt,
        "BMS":  args.influx_bucket_bms,
    }

    return InfluxWriter(
        url=args.influx_url,
        token=args.influx_token,
        org=args.influx_org,
        default_bucket=args.influx_bucket,
        measurement=args.influx_measurement,
        bucket_map=bucket_map,
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
    parser.add_argument("--no-ascii-fallback", action="store_true",
                        help="Disable the ASCII fallback decoder (binary packets only)")

    # InfluxDB options
    parser.add_argument("--influx-enable", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Write decoded events to InfluxDB v2 (on by default; "
                             "use --no-influx-enable to disable)")
    parser.add_argument("--influx-url", default=DEFAULT_INFLUX_URL,
                        help="InfluxDB v2 URL (env: INFLUX_URL)")
    parser.add_argument("--influx-token", default=DEFAULT_INFLUX_TOKEN,
                        help="InfluxDB v2 API token (env: INFLUX_TOKEN)")
    parser.add_argument("--influx-org", default=DEFAULT_INFLUX_ORG,
                        help="InfluxDB v2 organisation (env: INFLUX_ORG)")
    parser.add_argument("--influx-bucket", default=DEFAULT_INFLUX_BUCKET,
                        help="Default InfluxDB bucket — used for any msg_type "
                             "without a dedicated bucket (env: INFLUX_BUCKET)")
    parser.add_argument("--influx-bucket-bmv", default=DEFAULT_INFLUX_BUCKET_BMV,
                        help="InfluxDB bucket for BMV packets (env: INFLUX_BUCKET_BMV)")
    parser.add_argument("--influx-bucket-mppt", default=DEFAULT_INFLUX_BUCKET_MPPT,
                        help="InfluxDB bucket for MPPT (CAN) packets "
                             "(env: INFLUX_BUCKET_MPPT)")
    parser.add_argument("--influx-bucket-bms", default=DEFAULT_INFLUX_BUCKET_BMS,
                        help="InfluxDB bucket for BMS (CAN) packets "
                             "(env: INFLUX_BUCKET_BMS)")
    parser.add_argument("--influx-measurement", default=DEFAULT_INFLUX_MEASUREMENT,
                        help="InfluxDB measurement name (env: INFLUX_MEASUREMENT)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.transport != "lora":
        raise ValueError(f"Unsupported transport {args.transport}")

    influx_writer = build_influx_writer(args)

    try:
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
                sinks=build_sinks(args, influx_writer=influx_writer),
                log_prefix="receiver",
                ascii_fallback=not args.no_ascii_fallback,
            )
    finally:
        if influx_writer is not None:
            influx_writer.close()


if __name__ == "__main__":
    main()
