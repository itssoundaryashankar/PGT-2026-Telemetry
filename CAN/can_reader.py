#!/usr/bin/env python3
"""SocketCAN frame reader.

Mirrors the BMVReader interface so it drops into run_sender unchanged:
    reader.read_frame() -> dict
    reader.close()

Uses python-can with kernel-level CAN filters so the firehose never
reaches Python — only the IDs you actually care about cross the
syscall boundary.

Expected setup on the Pi (Waveshare RS485 CAN HAT or similar
MCP2515-based HAT):

    # /boot/firmware/config.txt:
    dtparam=spi=on
    dtoverlay=mcp2515-can0,oscillator=12000000,interrupt=25
    dtoverlay=spi-bcm2835-overlay

    # at runtime (or systemd-networkd / interfaces):
    sudo ip link set can0 up type can bitrate 500000
    sudo ifconfig can0 txqueuelen 1000

Verify with `ip -details link show can0` and `candump can0`.
"""

import can


class CANReader:
    def __init__(self, channel="can0", bitrate=500000, id_allowlist=None,
                 bustype="socketcan"):
        """
        channel:        SocketCAN interface name, usually 'can0'
        bitrate:        only used by some bustypes; SocketCAN reads what
                        the kernel was set to with `ip link`. Kept for
                        clarity and for non-socketcan backends.
        id_allowlist:   iterable of CAN IDs (ints) to forward. None or empty
                        means accept everything. Standard 11-bit IDs and
                        extended 29-bit IDs are both supported — extended
                        is auto-detected when ID > 0x7FF.
        bustype:        python-can interface name. Default 'socketcan'.
        """
        self.channel = channel
        self.bitrate = bitrate
        self.id_allowlist = tuple(id_allowlist) if id_allowlist else ()

        filters = self._build_filters(self.id_allowlist)

        # python-can swallowed `bustype` in favour of `interface` in newer
        # versions but still accepts both — pass `interface` for forward
        # compatibility.
        self.bus = can.interface.Bus(
            channel=channel,
            interface=bustype,
            bitrate=bitrate,
            can_filters=filters or None,
        )

    @staticmethod
    def _build_filters(ids):
        """Build python-can `can_filters` from an ID allowlist.

        Each filter matches exactly one CAN ID. Extended (29-bit) frames
        are auto-flagged by ID range.
        """
        if not ids:
            return []
        filters = []
        for can_id in ids:
            extended = can_id > 0x7FF
            filters.append({
                "can_id": can_id,
                "can_mask": 0x1FFFFFFF if extended else 0x7FF,
                "extended": extended,
            })
        return filters

    def read_frame(self):
        """Block until a CAN frame arrives, return it as a dict.

        The dict shape mirrors how the BMV reader returns data: a flat
        thing the normalizer can consume.

        Returns:
            {
                "can_id": int,       # 0..0x1FFFFFFF
                "extended": bool,
                "dlc": int,          # 0..8 (CAN 2.0; CAN-FD would go higher)
                "data": bytes,       # exactly dlc bytes
                "rx_timestamp": float,  # wall-clock seconds, from kernel
            }
        """
        while True:
            msg = self.bus.recv(timeout=None)  # block forever
            if msg is None:
                # Shouldn't happen with timeout=None, but guard anyway
                continue
            if msg.is_error_frame or msg.is_remote_frame:
                continue
            return {
                "can_id": msg.arbitration_id,
                "extended": bool(msg.is_extended_id),
                "dlc": msg.dlc,
                "data": bytes(msg.data),
                "rx_timestamp": msg.timestamp,
            }

    def close(self):
        try:
            self.bus.shutdown()
        except Exception:
            pass
