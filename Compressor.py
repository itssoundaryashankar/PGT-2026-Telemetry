import struct
from datetime import datetime


class Compressor:
    def __init__(self, timestamp_format: str = "%m/%d/%Y %H:%M:%S", scale: int = 1000) -> None:
        self.timestamp_format = timestamp_format
        self.scale = scale
        self.packet_format = ">IHHH"

    def compress(self, ts_str: str, v1: float, v2: float, v3: float) -> bytes:
        epoch = int(datetime.strptime(ts_str, self.timestamp_format).timestamp())
        return struct.pack(
            self.packet_format,
            epoch,
            int(v1 * self.scale),
            int(v2 * self.scale),
            int(v3 * self.scale),
        )

    def decompress(self, data: bytes) -> tuple[str, float, float, float]:
        epoch, v1, v2, v3 = struct.unpack(self.packet_format, data)
        ts = datetime.fromtimestamp(epoch).strftime(self.timestamp_format)
        return ts, v1 / self.scale, v2 / self.scale, v3 / self.scale


default_compressor = Compressor()


def compress(ts_str: str, v1: float, v2: float, v3: float) -> bytes:
    return default_compressor.compress(ts_str, v1, v2, v3)


def decompress(data: bytes) -> tuple[str, float, float, float]:
    return default_compressor.decompress(data)
