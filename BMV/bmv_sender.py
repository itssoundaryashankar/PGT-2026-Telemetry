#!/usr/bin/env python3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from telemetry_sender import main as telemetry_sender_main


def main(argv=None):
    telemetry_sender_main(["--device", "bmv", "--transport", "lora", *(argv or [])])


if __name__ == "__main__":
    main()
