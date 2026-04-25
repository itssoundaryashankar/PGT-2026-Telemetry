import struct

def decode_0x356(data: bytes) -> dict:
    """Battery voltage / current / temperature."""
    voltage_cv = struct.unpack_from("<h", data, 0)[0]   # 0.01 V
    current_da = struct.unpack_from("<h", data, 2)[0]   # 0.1 A signed
    temp_dc    = struct.unpack_from("<h", data, 4)[0]   # 0.1 °C
    return {
        "voltage_v": voltage_cv / 100.0,
        "current_a": current_da / 10.0,
        "temp_c":    temp_dc / 10.0,
    }

def decode_0x355(data: bytes) -> dict:
    soc, soh = struct.unpack_from("<HH", data, 0)
    return {"soc_pct": soc, "soh_pct": soh}

DECODERS = {
    0x351: decode_0x351,
    0x355: decode_0x355,
    0x356: decode_0x356,
    0x359: decode_0x359,
}

def decode_bms_frame(can_id: int, data: bytes) -> dict | None:
    fn = DECODERS.get(can_id)
    return fn(data) if fn else None
