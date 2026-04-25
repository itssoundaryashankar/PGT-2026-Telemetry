#!/usr/bin/env python3
"""Decoders for TPEE MPPT CAN frames.

PLACEHOLDER — TPEE does not publish a public CAN spec. The frame IDs and
scaling factors below are guesses based on common Chinese-vendor MPPT
protocols (Voltronic / Phocos / TI-style). You'll need to verify these
against your actual MPPTs by:

  1. Bringing up the CAN interface:
       sudo ip link set can0 up type can bitrate 500000
  2. Capturing live frames:
       candump can0
  3. Cross-referencing IDs with known MPPT states (idle/charging/fault)
  4. Updating the decoders below to match observed scaling

Until you've done that, this module is wired up but every decoder returns
an empty dict, so the CAN reader will simply skip MPPT frames.
"""

import struct


# ─────────────────────────────────────────────────────────────────────────────
# REPLACE THESE WITH REAL VALUES ONCE YOU'VE CAPTURED FRAMES
# ─────────────────────────────────────────────────────────────────────────────

# Common TPEE-style frame IDs — VERIFY against candump output before trusting.
ID_PV_MEASUREMENTS  = 0x18FF50E5   # PV voltage / current / power (guess)
ID_BATT_MEASUREMENTS = 0x18FF51E5  # Battery voltage / current (guess)
ID_STATUS           = 0x18FF52E5   # Charging state, fault flags (guess)


def decode_pv_measurements(data: bytes) -> dict:
    """PLACEHOLDER. Once you know the layout, fill this in. Likely shape:
       bytes 0-1: PV voltage in 0.1 V
       bytes 2-3: PV current in 0.01 A
       bytes 4-5: PV power in 1 W"""
    if len(data) < 6:
        return {}
    # Example decode — REPLACE with real scaling once verified
    # pv_v, pv_a, pv_w = struct.unpack_from("<hhh", data, 0)
    # return {
    #     "pv_voltage_v": pv_v / 10.0,
    #     "pv_current_a": pv_a / 100.0,
    #     "pv_power_w":   float(pv_w),
    # }
    return {}


def decode_batt_measurements(data: bytes) -> dict:
    """PLACEHOLDER. Likely shape:
       bytes 0-1: battery voltage in 0.1 V
       bytes 2-3: battery current in 0.01 A signed (positive = charging)"""
    if len(data) < 4:
        return {}
    return {}


def decode_status(data: bytes) -> dict:
    """PLACEHOLDER. Likely shape:
       byte 0: charging state (0=idle, 1=bulk, 2=absorption, 3=float)
       bytes 1-2: fault flags"""
    if len(data) < 3:
        return {}
    return {}


_DECODERS = {
    ID_PV_MEASUREMENTS:  decode_pv_measurements,
    ID_BATT_MEASUREMENTS: decode_batt_measurements,
    ID_STATUS:           decode_status,
}

# Set of all CAN IDs this decoder handles
MPPT_IDS = frozenset(_DECODERS.keys())

# IDs whose presence signals a complete snapshot. Currently empty so that
# the placeholder decoders don't accidentally trigger emissions.  Add IDs
# here once decoding is verified.
MPPT_COMPLETION_IDS = frozenset()


def decode_mppt_frame(can_id: int, data: bytes) -> dict:
    """Decode a single MPPT CAN frame. Returns a flat dict of named fields,
    or empty dict if the frame ID isn't recognized."""
    decoder = _DECODERS.get(can_id)
    if decoder is None:
        return {}
    try:
        return decoder(data)
    except struct.error:
        return {}
