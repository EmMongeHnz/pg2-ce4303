#!/usr/bin/env python3
"""Generate the printable ArUco ring for the manual turntable plate.

Usage:
    uv run hardware/aruco-ring/generate-ring.py --out-dir hardware/aruco-ring/out
    uv run hardware/aruco-ring/generate-ring.py --plate-diameter-mm 200 --marker-size-mm 26

Writes aruco-ring.pdf (US Letter, print at 100 %), aruco-ring.png (same page, with
DPI metadata) and ring-layout.json, which the pose estimator loads with --layout.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from astrea_common.aruco_ring import RingLayout, RenderPlate

MM_PER_INCH = 25.4
LETTER_MM = (215.9, 279.4)
TOP_MARGIN_MM = 15.0
SCALE_BAR_MM = 50.0


def ParseArgs() -> argparse.Namespace:
    defaults = RingLayout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=Path("hardware/aruco-ring/out"))
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--plate-diameter-mm", type=float, default=defaults.plateDiameterMm)
    parser.add_argument("--marker-count", type=int, default=defaults.markerCount)
    parser.add_argument("--marker-size-mm", type=float, default=defaults.markerSizeMm)
    parser.add_argument("--marker-center-radius-mm", type=float, default=defaults.markerCenterRadiusMm)
    parser.add_argument("--first-marker-id", type=int, default=defaults.firstMarkerId)
    parser.add_argument("--dictionary", default=defaults.dictionaryName)
    return parser.parse_args()


def DrawCaption(page: np.ndarray, layout: RingLayout, topPx: int, pxPerMm: float) -> None:
    left = round(20 * pxPerMm)
    lineHeight = round(7 * pxPerMm)
    scale = 0.11 * pxPerMm
    thickness = max(1, round(0.25 * pxPerMm))
    ids = layout.markerIds
    lines = [
        f"AstreaView ArUco ring - {layout.dictionaryName} ids {ids[0]}-{ids[-1]} - "
        f"plate {layout.plateDiameterMm:g} mm - marker {layout.markerSizeMm:g} mm",
        "Print at 100% (no fit-to-page). The bar below must measure exactly 50 mm.",
        "Glue on flat cardboard. Ticks every 10 deg, longest tick = 0 deg (marker 0).",
    ]
    for i, text in enumerate(lines):
        cv2.putText(page, text, (left, topPx + (i + 1) * lineHeight), cv2.FONT_HERSHEY_SIMPLEX, scale, 0, thickness, cv2.LINE_AA)
    barTop = topPx + (len(lines) + 1) * lineHeight
    barHeight = round(3 * pxPerMm)
    cv2.rectangle(page, (left, barTop), (left + round(SCALE_BAR_MM * pxPerMm) - 1, barTop + barHeight), 0, -1)


def Main() -> None:
    args = ParseArgs()
    layout = RingLayout(
        plateDiameterMm=args.plate_diameter_mm,
        markerCount=args.marker_count,
        markerSizeMm=args.marker_size_mm,
        markerCenterRadiusMm=args.marker_center_radius_mm,
        firstMarkerId=args.first_marker_id,
        dictionaryName=args.dictionary,
    )
    pxPerMm = args.dpi / MM_PER_INCH
    plate = RenderPlate(layout, pxPerMm)
    pageWidth, pageHeight = (round(side * pxPerMm) for side in LETTER_MM)
    if plate.shape[0] > pageWidth or plate.shape[0] + TOP_MARGIN_MM * pxPerMm > pageHeight:
        raise SystemExit("the plate does not fit on a US Letter page")
    page = np.full((pageHeight, pageWidth), 255, np.uint8)
    top = round(TOP_MARGIN_MM * pxPerMm)
    left = (pageWidth - plate.shape[1]) // 2
    page[top : top + plate.shape[0], left : left + plate.shape[1]] = plate
    DrawCaption(page, layout, top + plate.shape[0], pxPerMm)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    image = Image.fromarray(page)
    image.save(args.out_dir / "aruco-ring.png", dpi=(args.dpi, args.dpi))
    image.save(args.out_dir / "aruco-ring.pdf", resolution=args.dpi)
    (args.out_dir / "ring-layout.json").write_text(layout.ToJson() + "\n")
    print(f"wrote {args.out_dir}/aruco-ring.pdf, aruco-ring.png and ring-layout.json")


if __name__ == "__main__":
    Main()
