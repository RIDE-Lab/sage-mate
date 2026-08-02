#!/usr/bin/env python3
"""Select idle Ascend devices without assuming host-specific NPU IDs."""

from __future__ import annotations

import argparse
import glob
import re
import shutil
import subprocess
from pathlib import Path


RATE_PATTERN = re.compile(r"^(?P<name>.+?Usage Rate\(%\))\s*:\s*(?P<value>\d+)\s*$")


def _device_ids() -> list[int]:
    ids: list[int] = []
    for device_path in glob.glob("/dev/davinci[0-9]*"):
        match = re.fullmatch(r"davinci(\d+)", Path(device_path).name)
        if match:
            ids.append(int(match.group(1)))
    return sorted(set(ids))


def _holders(device_id: int) -> list[str]:
    result = subprocess.run(
        ["fuser", f"/dev/davinci{device_id}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return re.findall(r"\d+", f"{result.stdout} {result.stderr}")


def _usage_rates(device_id: int) -> dict[str, int]:
    result = subprocess.run(
        ["npu-smi", "info", "-t", "usages", "-i", str(device_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    rates: dict[str, int] = {}
    for raw_line in result.stdout.splitlines():
        match = RATE_PATTERN.match(raw_line.strip())
        if match:
            rates[match.group("name")] = int(match.group("value"))
    return rates


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a comma-separated set of currently idle Ascend device IDs."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--count", type=int)
    selection.add_argument("--devices", help="Validate this comma-separated device list.")
    parser.add_argument(
        "--max-hbm-usage-percent",
        type=int,
        default=10,
        help="Maximum baseline HBM percentage considered idle (default: 10).",
    )
    args = parser.parse_args()
    for command in ("fuser", "npu-smi"):
        if shutil.which(command) is None:
            raise SystemExit(f"Required command not found: {command}")
    if args.count is not None and args.count < 1:
        parser.error("--count must be positive")

    discovered = _device_ids()
    if args.devices is not None:
        if not re.fullmatch(r"\d+(,\d+)*", args.devices):
            parser.error("--devices must be a comma-separated list of numeric IDs")
        candidates = [int(value) for value in args.devices.split(",")]
        if len(candidates) != len(set(candidates)):
            parser.error("--devices contains duplicate IDs")
        requested_count = len(candidates)
    else:
        candidates = discovered
        requested_count = args.count
    assert requested_count is not None

    available: list[int] = []
    diagnostics: list[str] = []
    for device_id in candidates:
        if device_id not in discovered:
            diagnostics.append(f"NPU {device_id}: /dev/davinci{device_id} does not exist")
            continue
        holders = _holders(device_id)
        try:
            rates = _usage_rates(device_id)
        except RuntimeError as exc:
            diagnostics.append(f"NPU {device_id}: probe failed ({exc})")
            continue
        hbm = rates.get("HBM Usage Rate(%)", 100)
        aicore = rates.get("Aicore Usage Rate(%)", 100)
        if holders or hbm > args.max_hbm_usage_percent or aicore > 0:
            diagnostics.append(
                f"NPU {device_id}: busy (holders={','.join(holders) or 'none'}, "
                f"hbm={hbm}%, aicore={aicore}%)"
            )
            continue
        available.append(device_id)

    if len(available) < requested_count:
        detail = "\n".join(diagnostics) or "No /dev/davinciN devices were discovered."
        raise SystemExit(
            f"Need {requested_count} idle Ascend device(s), found {len(available)}.\n{detail}"
        )

    print(",".join(str(device_id) for device_id in available[:requested_count]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
