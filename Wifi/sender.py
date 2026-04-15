#!/usr/bin/env python3
import socket
import time
import argparse

DEFAULT_PORT = 5005

def main():
    parser = argparse.ArgumentParser(description="UDP sender")
    parser.add_argument("--ip", required=True, help="Receiver IP address (e.g., 192.168.1.23)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Receiver UDP port (default 5005)")
    parser.add_argument("--hz", type=float, default=5.0, help="Send rate in Hz (default 5)")
    parser.add_argument("--text", default="hello from sender", help="Base message text")
    args = parser.parse_args()

    dest = (args.ip, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    seq = 0
    period = 1.0 / args.hz if args.hz > 0 else 0.0

    print(f"[sender] Sending to {dest} at {args.hz} Hz. Ctrl+C to stop.")
    try:
        while True:
            ts_ms = int(time.time() * 1000)
            payload = f"{seq},{ts_ms},{args.text}"
            sock.sendto(payload.encode("utf-8"), dest)
            seq += 1
            if period > 0:
                time.sleep(period)
    except KeyboardInterrupt:
        print("\n[sender] Stopped.")

if __name__ == "__main__":
    main()