#!/usr/bin/env python3
"""CAN bus reader for the telemetry sender.

Reads frames from a SocketCAN interface (e.g. `can0` on a Pi with a CAN HAT)
and groups them into device-type "snapshots". Each call to `read_frame`
blocks until a complete snapshot of either an MPPT or BMS device is ready,
then returns ('mppt' | 'bms', raw_frame_dict).

The grouping logic is per-device-type: each device emits several CAN frame
IDs that together describe a single state. We accumulate fields across IDs
and only return once a configurable "completion" set of IDs has been seen.
"""

import time
from CAN.bms_decoder import decode_bms_frame, BMS_IDS, BMS_COMPLETION_IDS
from CAN.mppt_decoder import decode_mppt_frame, MPPT_IDS, MPPT_COMPLETION_IDS


class CANReader:
    def __init__(self, interface="can0", bitrate=500000,
                 mppt_timeout=2.0, bms_timeout=2.0):
        # Imported lazily so the module can be imported on machines that
        # don't have python-can installed (e.g. the receiver-side Windows PC).
        import can
        self._can = can
        self.bus = can.interface.Bus(
            channel=interface,
            interface="socketcan",   # python-can >= 4.0 uses 'interface'
            bitrate=bitrate,
        )

        # Per-device accumulators
        self._mppt = {"fields": {}, "ids_seen": set(), "first_ts": None}
        self._bms = {"fields": {}, "ids_seen": set(), "first_ts": None}

        # If a snapshot is taking too long to complete (lost frames, device
        # offline), time it out and emit what we have.
        self.mppt_timeout = mppt_timeout
        self.bms_timeout = bms_timeout

    def read_frame(self):
        """Block until a complete MPPT or BMS snapshot is ready.
        Returns (kind, frame_dict) where kind is 'mppt' or 'bms'."""
        while True:
            msg = self.bus.recv(timeout=1.0)
            now = time.time()

            # Handle timeouts even if no frame came in — emits stale-but-
            # populated snapshots so the policy still sees data.
            for kind, state, timeout in (
                ("mppt", self._mppt, self.mppt_timeout),
                ("bms", self._bms, self.bms_timeout),
            ):
                if (state["fields"]
                        and state["first_ts"] is not None
                        and now - state["first_ts"] >= timeout):
                    out = dict(state["fields"])
                    self._reset(state)
                    return (kind, out)

            if msg is None:
                continue

            can_id = msg.arbitration_id
            data = bytes(msg.data)

            if can_id in MPPT_IDS:
                fields = decode_mppt_frame(can_id, data)
                if fields:
                    self._accumulate(self._mppt, can_id, fields, now)
                    if self._is_complete(self._mppt, MPPT_COMPLETION_IDS):
                        out = dict(self._mppt["fields"])
                        self._reset(self._mppt)
                        return ("mppt", out)

            elif can_id in BMS_IDS:
                fields = decode_bms_frame(can_id, data)
                if fields:
                    self._accumulate(self._bms, can_id, fields, now)
                    if self._is_complete(self._bms, BMS_COMPLETION_IDS):
                        out = dict(self._bms["fields"])
                        self._reset(self._bms)
                        return ("bms", out)
            # else: silently ignore frame IDs we don't care about

    @staticmethod
    def _accumulate(state, can_id, fields, now):
        state["fields"].update(fields)
        state["ids_seen"].add(can_id)
        if state["first_ts"] is None:
            state["first_ts"] = now

    @staticmethod
    def _is_complete(state, completion_ids):
        return completion_ids.issubset(state["ids_seen"])

    @staticmethod
    def _reset(state):
        state["fields"].clear()
        state["ids_seen"].clear()
        state["first_ts"] = None

    def close(self):
        try:
            self.bus.shutdown()
        except Exception:
            pass
