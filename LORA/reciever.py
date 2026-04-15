#!/usr/bin/env python3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from telemetry_receiver import main as telemetry_receiver_main


def main(argv=None):
    telemetry_receiver_main(["--transport", "lora", *(argv or [])])


if __name__ == "__main__":
    main()
