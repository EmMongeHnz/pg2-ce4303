#!/usr/bin/env python3
"""M3 synthetic test: bunny -> synthetic depth views -> TSDF -> mesh -> distance to the original.

Usage:
    uv sync --extra reconstruction
    uv run tests/spikes/m3-synthetic/fetch-bunny.py
    uv run tests/spikes/m3-synthetic/run-bunny-test.py
    uv run tests/spikes/m3-synthetic/run-bunny-test.py --noise-sigma-mm 1.0 --elevations 35 50
    uv run tests/spikes/m3-synthetic/run-bunny-test.py --azimuth-error-deg 0.5

Capture geometry follows the real setup: 480x640 portrait intrinsics measured in spike 0a, the object
on the plate, one view every 10 degrees of azimuth at ~30 cm. The gate applies to the noise-free run
and to accuracy (reconstruction -> original): mean <= 2 voxels and p95 <= 4 voxels. Completeness is
reported only over faces some camera can see, because the underside of the object is never observed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from astrea_common.glb import WriteGlb
from astrea_common.mesh import ReadAsciiPly, TriangleMesh
from astrea_common.rgbd_frame import Intrinsics
from astrea_common.tsdf import GridSpec, TsdfVolume
from capture_service.synthetic_depth import MeshDepthRenderer
from capture_service.synthetic_ring import ViewPose
from coordinator.mesh_metrics import AccuracyStats, CompletenessStats
from coordinator.meshing import ComponentCount, ExtractMesh, LargestComponent

BUNNY_DIR = Path("out/datasets/stanford-bunny/extracted/bunny/reconstruction")
INTRINSICS = Intrinsics(fx=434.1, fy=434.1, cx=240.0, cy=320.0, width=480, height=640)
MEAN_LIMIT_VOXELS = 2.0
P95_LIMIT_VOXELS = 4.0
YUP_TO_ZUP = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])


def ParseArgs() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resolution", choices=["full", "res2", "res3", "res4"], default="full")
    parser.add_argument("--size-mm", type=float, default=120.0, help="largest dimension of the bunny")
    parser.add_argument("--views-per-ring", type=int, default=36)
    parser.add_argument("--elevations", type=float, nargs="+", default=[35.0])
    parser.add_argument("--distance-mm", type=float, default=300.0)
    parser.add_argument("--noise-sigma-mm", type=float, default=0.0)
    parser.add_argument("--azimuth-error-deg", type=float, default=0.0, help="std of a random azimuth error in the pose used to integrate each view")
    parser.add_argument("--out-dir", type=Path, default=Path("out/m3-bunny"))
    return parser.parse_args()


def LoadBunnyOnPlate(resolution: str, sizeMm: float) -> TriangleMesh:
    name = "bun_zipper.ply" if resolution == "full" else f"bun_zipper_{resolution}.ply"
    mesh = ReadAsciiPly(BUNNY_DIR / name)
    upright = mesh.Transformed(YUP_TO_ZUP, np.zeros(3))
    low, high = upright.vertices.min(axis=0), upright.vertices.max(axis=0)
    scale = sizeMm / (high - low).max()
    centerOnPlate = -scale * np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]])
    return upright.Transformed(np.eye(3), centerOnPlate, scale)


def ViewPoses(args: argparse.Namespace, heightMm: float) -> list[tuple[np.ndarray, np.ndarray]]:
    azimuths = np.arange(args.views_per_ring) * 360.0 / args.views_per_ring
    poses = [ViewPose(az, el, args.distance_mm, targetHeightMm=heightMm / 2) for el in args.elevations for az in azimuths]
    return [pose.PlateToCamera() for pose in poses]


def WithAzimuthError(rotation: np.ndarray, sigmaDeg: float, rng: np.random.Generator) -> np.ndarray:
    angle = np.radians(rng.normal(0.0, sigmaDeg))
    about = np.array([[np.cos(angle), -np.sin(angle), 0.0], [np.sin(angle), np.cos(angle), 0.0], [0.0, 0.0, 1.0]])
    return rotation @ about


def Main() -> None:
    args = ParseArgs()
    if not BUNNY_DIR.exists():
        sys.exit("bunny not found: run tests/spikes/m3-synthetic/fetch-bunny.py first")
    rng = np.random.default_rng(0)
    reference = LoadBunnyOnPlate(args.resolution, args.size_mm)
    print(f"reference: {len(reference.vertices)} vertices, {len(reference.faces)} faces, height {reference.vertices[:, 2].max():.1f} mm")

    start = time.monotonic()
    renderer = MeshDepthRenderer(reference, INTRINSICS)
    volume = TsdfVolume(GridSpec.Default())
    visibleFaces = np.zeros(len(reference.faces), dtype=bool)
    poses = ViewPoses(args, reference.vertices[:, 2].max())
    for rotation, translation in poses:
        view = renderer.Render(rotation, translation)
        depth = view.depthMm
        if args.noise_sigma_mm > 0:
            depth = np.where(depth > 0, depth + rng.normal(0.0, args.noise_sigma_mm, depth.shape), 0.0).astype(np.float32)
        estimatedRotation = WithAzimuthError(rotation, args.azimuth_error_deg, rng) if args.azimuth_error_deg > 0 else rotation
        volume.Integrate(depth, INTRINSICS, estimatedRotation, translation)
        visibleFaces[view.faceIds[view.faceIds >= 0]] = True
    print(f"rendered and integrated {len(poses)} views in {time.monotonic() - start:.1f} s")

    start = time.monotonic()
    raw = ExtractMesh(volume)
    mesh = LargestComponent(raw)
    print(f"mesh: {len(raw.faces)} faces in {ComponentCount(raw)} components, kept {len(mesh.faces)} in {time.monotonic() - start:.1f} s")

    voxelMm = volume.grid.voxelMm
    accuracy = AccuracyStats(mesh, reference)
    completeness = CompletenessStats(mesh, reference, np.flatnonzero(visibleFaces))
    print(f"visible faces: {100 * visibleFaces.mean():.1f} % of the reference")
    print(f"accuracy     recon->original: mean {accuracy.meanMm:.2f} mm ({accuracy.meanMm / voxelMm:.2f} vox), "
          f"p95 {accuracy.p95Mm:.2f} mm ({accuracy.p95Mm / voxelMm:.2f} vox), max {accuracy.maxMm:.2f} mm")
    print(f"completeness original->recon (visible faces): mean {completeness.meanMm:.2f} mm, "
          f"p95 {completeness.p95Mm:.2f} mm, max {completeness.maxMm:.2f} mm")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    WriteGlb(args.out_dir / "reconstruction.glb", mesh.vertices / 1000.0, mesh.faces)
    WriteGlb(args.out_dir / "reference.glb", reference.vertices / 1000.0, reference.faces)

    passed = accuracy.meanMm <= MEAN_LIMIT_VOXELS * voxelMm and accuracy.p95Mm <= P95_LIMIT_VOXELS * voxelMm
    print(f"gate (mean <= {MEAN_LIMIT_VOXELS} vox, p95 <= {P95_LIMIT_VOXELS} vox): {'PASS' if passed else 'FAIL'}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    Main()
