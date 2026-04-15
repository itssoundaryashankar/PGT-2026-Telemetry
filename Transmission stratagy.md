# Transmission Strategy

## Goal

Create one consistent path for telemetry data:

`device data -> normalized packet -> compressed binary -> LoRa hex -> receiver -> decode -> route/store`

This keeps BMV and future devices on the same protocol instead of building separate send and receive logic for each device.

## Current Repo State

- `Compressor.py` supports one fixed packet with 3 values.
- `BMV/bmv_v2.py` reads VE.Direct frames and compresses values, but it does not yet hand packets to LoRa.
- `LORA/sender.py` and `LORA/reciever.py` are modem tests, not a reusable transport layer.

## Recommended Architecture

### 1. Collect

Use one reader per device:

- `BMV/bmv_reader.py`
- `GPS/gps_reader.py`
- `ENV/env_reader.py`

Each reader should output a normalized dictionary like this:

```python
{
    "device_type": "bmv",
    "device_id": 1,
    "timestamp": 1713110400,
    "fields": {
        "voltage_mv": 13240,
        "current_ma": -840,
        "power_w": 111
    }
}
```

### 2. Encode

Convert the normalized data into a compact binary packet with a shared header:

```text
version      1 byte
msg_type     1 byte
device_id    1 byte
seq          2 bytes
timestamp    4 bytes
field_mask   2 bytes
payload      N bytes
crc16        2 bytes
```

Use `struct.pack` for this layer instead of JSON or CSV over LoRa.

### 3. Compress

For LoRa, compression should mostly mean:

- scale floats into integers
- avoid string timestamps
- omit optional fields with `field_mask`
- send only the values that matter

For BMV, a compact payload could be:

```text
voltage_mv   uint16
current_ma   int16
power_w      int16
```

This is usually better than generic compression libraries because LoRa payloads are already small.

### 4. Transport

Create a reusable `lora_transport.py` that:

- configures the modem once
- sends packet bytes as hex through `AT+TEST=TXLRPKT`
- receives modem responses
- extracts payload hex from received messages

Keep transport separate from packet encoding so the same packet can later be used over Wi-Fi or another link.

### 5. Decode

Receiver flow:

1. Read modem output
2. Extract hex payload
3. Convert hex to bytes
4. validate CRC
5. unpack header
6. decode payload based on `msg_type`
7. print, log, store, or forward the decoded telemetry

### 6. Transmission Model

Use an event-based transmission model instead of sending every sample immediately.

This is especially important for LoRa because airtime is limited, packets are small, and frequent transmissions can waste bandwidth and power.

The transmitter should decide whether to send based on events, not only on a fixed loop.

## Event-Based Transmission Model

The best model for this repo is:

- event-driven transmission for important changes
- periodic heartbeat transmission for system visibility
- optional batching when multiple readings change close together

### Core Idea

Each device reader can sample data often, but the radio should only transmit when one of these happens:

1. A value changes enough to matter
2. A critical threshold is crossed
3. A fault or alarm occurs
4. A maximum quiet period has passed

This reduces unnecessary LoRa traffic while still sending important telemetry quickly.

### Recommended Event Types

Use explicit event types in the packet or message metadata:

- `sample`
- `delta_update`
- `threshold_crossing`
- `alarm`
- `heartbeat`
- `device_status`

For example:

- `delta_update` when battery voltage changes by more than a configured amount

- `alarm` when the device reports a fault
- `heartbeat` every fixed interval even if nothing changes

### Example Trigger Rules For BMV

Transmit a packet if any of these conditions are true:

- voltage changes by at least `100 mV`
- current changes by at least `500 mA`
- power changes by at least `10 W`
- charge state changes
- an alarm or error flag appears
- no packet has been sent in the last `60 seconds`

These values are starting points and should be tuned during testing.

### Why This Model Fits LoRa

Benefits:

- lower airtime usage
- lower power consumption
- less repeated data
- faster delivery of meaningful state changes
- easier scaling when more devices are added

For LoRa, this is usually better than streaming every sample at a constant rate.

## Recommended Transmit Decision Flow

For each new normalized reading:

1. Read and normalize the device frame
2. Compare the new reading to the last transmitted reading
3. Check threshold and alarm rules
4. If a send condition is met, build and transmit a packet
5. If no send condition is met, keep the sample locally
6. If heartbeat timeout expires, send a heartbeat or state snapshot

Pseudo-flow:

```python
if alarm_active:
    send(event_type="alarm", payload=current_state)
elif threshold_crossed(current_state, last_sent_state):
    send(event_type="threshold_crossing", payload=current_state)
elif changed_enough(current_state, last_sent_state):
    send(event_type="delta_update", payload=current_state)
elif time_since_last_send >= HEARTBEAT_SECONDS:
    send(event_type="heartbeat", payload=current_state)
```

## Event Metadata In The Packet

To support the event model cleanly, include event information in the packet header.

A revised shared header could be:

```text
version      1 byte
msg_type     1 byte
event_type   1 byte
device_id    1 byte
seq          2 bytes
timestamp    4 bytes
field_mask   2 bytes
payload      N bytes
crc16        2 bytes
```

This lets the receiver know whether the payload is a normal update, a heartbeat, or an alarm without guessing.

## Best Packet Strategy For This Repo

For BMV and similar telemetry, use a compact typed binary protocol instead of a fixed compressor format like `>IHHH`.

A better structure for BMV packets is:

```python
HEADER = ">BBBBHIH"
# version, msg_type, event_type, device_id, seq, timestamp, field_mask

BMV_PAYLOAD = ">H h h H"
# voltage_mv, current_ma, power_w, 
```

This gives you:

- support for multiple device types
- support for event-based messaging
- support for negative current values
- no dependency on string timestamp parsing
- smaller packets
- easier extension later

Note: if you implement this with `struct`, remove spaces in the final format string if your Python version or style prefers that.

## Why The Current Compressor Needs To Change

`Compressor.py` currently uses `>IHHH`, which has a few problems:

- it only supports three values
- it assumes all values are unsigned
- negative current can break
- it depends on converting timestamps from strings
- it is difficult to extend to new device types

Instead of one generic fixed compressor, use protocol-specific packet builders and parsers.

## Important Notes From The Existing Code

### `Compressor.py`

- `HHH` does not allow negative values
- string timestamps add overhead that is not needed for LoRa

### `BMV/bmv_v2.py`

- `handle_frame()` already identifies `V`, `I`, and `P`
- this is the right place to normalize raw BMV values into packet fields
- `power` should be checked carefully before scaling because `P` may already be in watts
- CSV writing and LoRa sending should happen after normalized packet creation, not before

### `LORA/sender.py` and `LORA/reciever.py`

- these are useful modem connectivity tests
- they should be refactored into helper functions or a transport class
- they should not own packet format decisions

## Suggested Folder Layout

```text
BMV/
  bmv_reader.py

protocol/
  packet.py
  schemas.py
  crc.py

transport/
  lora_transport.py
  wifi_transport.py

apps/
  send_bmv_lora.py
  receive_lora.py
```

## Send Path

1. Read a full VE.Direct frame
2. Normalize units
3. Compare against the last transmitted state
4. Decide whether an event should be sent
5. Build binary packet
6. Append CRC
7. Convert to hex
8. Send through LoRa AT command

## Receive Path

1. Read modem output
2. Extract payload hex
3. Convert hex to bytes
4. Validate CRC
5. Decode header and payload
6. Route output to terminal, CSV, or another storage target

## Recommended First Implementation Steps

Build these three parts first:

1. `protocol/packet.py`
   Handles pack and unpack of telemetry packets.
2. `transport/lora_transport.py`
   Handles modem setup, send, receive, and parsing of radio payload text.
3. Refactor `BMV/bmv_v2.py`
   Make `handle_frame()` return a normalized telemetry dictionary instead of only printing compressed data.
4. Add an event decision layer
   Compare current readings to the last transmitted state and decide whether to send `delta_update`, `alarm`, or `heartbeat`.

## Summary

The key idea is to separate:

- device reading
- event decision logic
- packet encoding
- LoRa transport
- packet decoding

If you keep those layers separate, you can sample devices continuously, transmit only meaningful changes over LoRa, and decode BMV and other device telemetry cleanly on the receiver side.
