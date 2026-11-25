#!/usr/bin/env bash
# Checks "N presses give exactly N events": press ONE button N times (the autorotate button does not repeat)
# and compares what holo-reader saw with the driver statistics in sysfs.
#
# Usage: driver/holo-io/check-presses.sh [N]   (default 100; needs the module loaded)

set -euo pipefail

PRESSES="${1:-100}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATS=/sys/class/misc/holo-input/device/stats

Stat() {
    cat "$STATS/$1"
}

[[ -d "$STATS" ]] || { echo "check-presses: $STATS not found, is holo-io loaded?" >&2; exit 1; }

pressesBefore=$(Stat presses)
dropsBefore=$(Stat dropped)
echo "Press one button $PRESSES times..."
summary=$("$HERE/holo-reader" -p "$PRESSES" | tail -n 1)
readerPresses=$(sed -n 's/.*presses=\([0-9]*\).*/\1/p' <<< "$summary")
sysfsPresses=$(( $(Stat presses) - pressesBefore ))
dropped=$(( $(Stat dropped) - dropsBefore ))

echo "reader: $readerPresses presses | sysfs: $sysfsPresses presses | dropped: $dropped"
echo "irqs=$(Stat irqs) bounces_filtered=$(Stat bounces_filtered) presses=$(Stat presses) releases=$(Stat releases) repeats=$(Stat repeats)"
if [[ "$readerPresses" -eq "$PRESSES" && "$sysfsPresses" -eq "$PRESSES" && "$dropped" -eq 0 ]]; then
    echo "OK"
else
    echo "MISMATCH" >&2
    exit 1
fi
