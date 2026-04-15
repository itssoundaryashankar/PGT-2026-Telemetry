#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "${REPO_ROOT}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python interpreter not found: ${PYTHON_BIN}" >&2
  exit 1
fi

if ! "${PYTHON_BIN}" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller is not installed for ${PYTHON_BIN}." >&2
  echo "Install it with:" >&2
  echo "  ${PYTHON_BIN} -m pip install --user pyinstaller" >&2
  exit 1
fi

"${PYTHON_BIN}" -m PyInstaller \
  --clean \
  --onefile \
  --name telemetry_receiver \
  --paths "${REPO_ROOT}" \
  --hidden-import BMV.bmv_handler \
  --hidden-import LORA.lora_transport \
  --hidden-import storage.event_csv_sink \
  --hidden-import telemetry_packet \
  telemetry_receiver.py

"${PYTHON_BIN}" -m PyInstaller \
  --clean \
  --onefile \
  --name telemetry_sender \
  --paths "${REPO_ROOT}" \
  --hidden-import BMV.bmv_normalizer \
  --hidden-import BMV.bmv_policy \
  --hidden-import BMV.bmv_reader \
  --hidden-import LORA.lora_transport \
  --hidden-import storage.csv_sink \
  --hidden-import telemetry_packet \
  telemetry_sender.py

echo "Built executables in ${REPO_ROOT}/dist"
