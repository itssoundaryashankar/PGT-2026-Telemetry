#!/usr/bin/env python3
import socket
import time

LISTEN_IP = "0.0.0.0"     # listen on all interfaces
LISTEN_PORT = 5005
BUF_SIZE = 65535

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((LISTEN_IP, LISTEN_PORT))
    print(f"[receiver] Listening on {LISTEN_IP}:{LISTEN_PORT}")

    count = 0
    last_print = time.time()

    while True:
        data, addr = sock.recvfrom(BUF_SIZE)
        count += 1

        now = time.time()
        if now - last_print >= 1.0:
            print(f"[receiver] pkts={count} last_from={addr} last_len={len(data)}")
            last_print = now

        # Print message (safe decode)
        try:
            msg = data.decode("utf-8", errors="replace")
        except Exception:
            msg = str(data)
        print(f"[receiver] {addr}: {msg}")

if __name__ == "__main__":
    main()