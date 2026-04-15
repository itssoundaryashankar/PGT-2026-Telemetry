# Sending Data Between Devices

## Wi-Fi
1. Start the receiver:
```bash
python3 Wifi/reciever.py
```

2. Start the sender:
```bash
python3 Wifi/sender.py --ip <RECEIVER_IP> --hz 10 --text "hello world"
```

If both programs are running on the same machine, use `127.0.0.1` for `<RECEIVER_IP>`.

## LoRa
1. Find your serial devices:
```bash
ls /dev/tty.*
```

2. Start the generic telemetry receiver:
```bash
python3 telemetry_receiver.py --transport lora --port /dev/tty.usbserial-0001
```

3. Start the generic telemetry sender for the BMV device:
```bash
python3 telemetry_sender.py --device bmv --transport lora --bmv-port /dev/tty.usbserial-VE7ALZXZ --lora-port /dev/tty.usbserial-0001
```

## Dry Run
Build BMV telemetry packets without transmitting over LoRa:
```bash
python3 telemetry_sender.py --device bmv --transport lora --dry-run
```

## Notes
- `telemetry_sender.py` is now the main sender entrypoint.
- `telemetry_receiver.py` is now the main receiver entrypoint.
- `BMV/bmv_sender.py` and `LORA/reciever.py` still work as compatibility wrappers.
