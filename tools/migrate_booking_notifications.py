"""Offline operator migration. Dry-run default; no mail is sent."""
import argparse
import json
import subprocess
from pathlib import Path

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage_faculty_twin.booking_migration import migrate_booking_receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_path", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.state_path.is_file():
        parser.error("existing booking state file required; refusing to create one")
    if args.apply:
        result = subprocess.run(["systemctl", "--user", "show", "sage-mate-app.service",
                                 "--property=ActiveState", "--value"], capture_output=True, text=True)
        if result.returncode or result.stdout.strip() not in {"inactive", "failed"}:
            parser.error("stop sage-mate-app.service before applying migration")
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(args.state_path.resolve()))
    try:
        print(json.dumps(migrate_booking_receipts(state, apply=args.apply), sort_keys=True))
    finally:
        state.close()


if __name__ == "__main__":
    main()
