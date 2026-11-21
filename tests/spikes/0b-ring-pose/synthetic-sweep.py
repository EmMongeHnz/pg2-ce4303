#!/usr/bin/env python3
"""Spike 0b, synthetic part: ring pose accuracy and stability against known poses.

Usage:
    uv run tests/spikes/0b-ring-pose/synthetic-sweep.py --out-dir /tmp/ring-sweep

Sweeps a full turn of azimuths for several elevations and distances, with an
object hiding the far side of the ring, random hand occlusions, blur and noise.
Writes sweep.csv, summary.md and a few annotated sample images.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import cv2
import numpy as np

from astrea_common.aruco_ring import RingLayout
from capture_service.ring_pose import AngleDifferenceDeg, RingPoseEstimator, RotationErrorDeg
from capture_service.synthetic_ring import SceneOptions, SyntheticRingRenderer, ViewPose

AZIMUTH_STEP_DEG = 10
ELEVATIONS_DEG = (20, 35, 50, 65)
DISTANCES_MM = (280, 350, 450)
JITTER_REPEATS = 10
GATE_AZIMUTH_ERROR_DEG = 1.0


def ParseArgs() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=Path("ring-sweep"))
    parser.add_argument("--layout", type=Path, default=None, help="ring-layout.json from generate-ring.py")
    parser.add_argument("--hands", type=int, default=1, help="random hand occluders per view")
    parser.add_argument("--noise", type=float, default=3.0, help="sensor noise sigma (gray levels)")
    return parser.parse_args()


def EvaluateView(renderer, estimator, pose: ViewPose, options: SceneOptions) -> dict:
    image = renderer.Render(pose, options)
    estimate = estimator.Estimate(image)
    row = {
        "azimuthDeg": pose.azimuthDeg,
        "elevationDeg": pose.elevationDeg,
        "distanceMm": pose.distanceMm,
        "markers": 0,
        "azimuthErrorDeg": math.nan,
        "rotationErrorDeg": math.nan,
        "translationErrorMm": math.nan,
        "reprojectionErrorPx": math.nan,
    }
    if estimate is None:
        return row
    rotation, translation = pose.PlateToCamera()
    row.update(
        markers=len(estimate.markerIds),
        azimuthErrorDeg=AngleDifferenceDeg(estimate.azimuthDeg, pose.azimuthDeg),
        rotationErrorDeg=RotationErrorDeg(estimate.rotation, rotation),
        translationErrorMm=float(np.linalg.norm(estimate.translationMm - translation)),
        reprojectionErrorPx=estimate.reprojectionErrorPx,
    )
    return row


def MeasureJitter(renderer, estimator, pose: ViewPose, args) -> float:
    """Std of the azimuth over repeated noisy renders of one static pose (pose stability)."""
    azimuths = []
    for seed in range(JITTER_REPEATS):
        estimate = estimator.Estimate(renderer.Render(pose, SceneOptions(noiseSigma=args.noise, seed=1000 + seed)))
        if estimate is not None:
            azimuths.append(AngleDifferenceDeg(estimate.azimuthDeg, pose.azimuthDeg))
    return float(np.std(azimuths)) if len(azimuths) > 1 else math.nan


def SaveSample(renderer, estimator, pose: ViewPose, options: SceneOptions, path: Path) -> None:
    gray = renderer.Render(pose, options)
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    detections = estimator.DetectMarkers(gray)
    estimate = estimator.EstimateFromDetections(detections)
    for markerId, corners in detections.items():
        cv2.polylines(image, [np.round(corners).astype(np.int32)], True, (0, 200, 0), 1, cv2.LINE_AA)
        cv2.putText(image, str(markerId), tuple(np.round(corners[0]).astype(int)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
    if estimate is not None:
        rvec, _ = cv2.Rodrigues(estimate.rotation)
        cv2.drawFrameAxes(image, estimator.cameraMatrix, estimator.distortion, rvec, estimate.translationMm, 40)
        text = f"az {estimate.azimuthDeg:.2f} (true {pose.azimuthDeg:.0f})  markers {len(estimate.markerIds)}"
        cv2.putText(image, text, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), image)


def Summarize(rows: list[dict], jitters: list[float]) -> str:
    detected = [row for row in rows if row["markers"] > 0]
    azimuthErrors = np.abs([row["azimuthErrorDeg"] for row in detected])
    rotationErrors = np.array([row["rotationErrorDeg"] for row in detected])
    translationErrors = np.array([row["translationErrorMm"] for row in detected])
    markers = np.array([row["markers"] for row in detected])
    lines = [
        "| Métrica | Valor |",
        "|---|---|",
        f"| Vistas evaluadas | {len(rows)} |",
        f"| Vistas con pose | {len(detected)} ({100 * len(detected) / len(rows):.1f} %) |",
        f"| Marcadores visibles (mín / mediana / máx) | {markers.min()} / {np.median(markers):.0f} / {markers.max()} |",
        f"| Error de azimut, mediana / p95 / máx (°) | {np.median(azimuthErrors):.3f} / {np.percentile(azimuthErrors, 95):.3f} / {azimuthErrors.max():.3f} |",
        f"| Error de rotación, mediana / máx (°) | {np.median(rotationErrors):.3f} / {rotationErrors.max():.3f} |",
        f"| Error de traslación, mediana / máx (mm) | {np.median(translationErrors):.2f} / {translationErrors.max():.2f} |",
        f"| Jitter de azimut con pose fija, máx std (°) | {np.nanmax(jitters):.3f} |",
        f"| Vistas con error de azimut ≥ {GATE_AZIMUTH_ERROR_DEG}° | {int(np.sum(azimuthErrors >= GATE_AZIMUTH_ERROR_DEG))} |",
    ]
    lines += ["", "| Subconjunto | Vistas | Error de azimut p95 / máx (°) | Vistas ≥ 1° |", "|---|---|---|---|"]
    subsets = [(f"elevación {e}°", lambda row, e=e: row["elevationDeg"] == e) for e in ELEVATIONS_DEG]
    subsets += [("≥ 5 marcadores", lambda row: row["markers"] >= 5), ("< 5 marcadores", lambda row: row["markers"] < 5)]
    for name, predicate in subsets:
        errors = np.abs([row["azimuthErrorDeg"] for row in detected if predicate(row)])
        if len(errors) == 0:
            continue
        failing = int(np.sum(errors >= GATE_AZIMUTH_ERROR_DEG))
        lines.append(f"| {name} | {len(errors)} | {np.percentile(errors, 95):.3f} / {errors.max():.3f} | {failing} |")
    return "\n".join(lines) + "\n"


def Main() -> None:
    args = ParseArgs()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    renderer = SyntheticRingRenderer(layout=RingLayout.Load(args.layout))
    estimator = RingPoseEstimator(renderer.layout, renderer.camera.Matrix())
    rows = []
    seed = 0
    for distance in DISTANCES_MM:
        for elevation in ELEVATIONS_DEG:
            for azimuth in range(0, 360, AZIMUTH_STEP_DEG):
                seed += 1
                options = SceneOptions(handOccluders=args.hands, noiseSigma=args.noise, seed=seed)
                rows.append(EvaluateView(renderer, estimator, ViewPose(azimuth, elevation, distance), options))
    jitters = [
        MeasureJitter(renderer, estimator, ViewPose(azimuth, elevation, 350), args)
        for elevation in ELEVATIONS_DEG
        for azimuth in (0, 95, 185, 275)
    ]
    with open(args.out_dir / "sweep.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for i, (azimuth, elevation) in enumerate([(0, 35), (125, 20), (250, 65)]):
        options = SceneOptions(handOccluders=args.hands, noiseSigma=args.noise, seed=i)
        SaveSample(renderer, estimator, ViewPose(azimuth, elevation, 350), options, args.out_dir / f"sample-{i}.png")
    summary = Summarize(rows, jitters)
    (args.out_dir / "summary.md").write_text(summary)
    print(summary)


if __name__ == "__main__":
    Main()
