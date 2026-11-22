#!/usr/bin/env python3
"""M3 synthetic test: bunny -> synthetic depth views -> TSDF -> mesh -> distance to the original.

Usage:
    uv sync --extra reconstruction
    uv run tests/spikes/m3-synthetic/fetch-bunny.py
    uv run tests/spikes/m3-synthetic/run-bunny-test.py
    uv run tests/spikes/m3-synthetic/run-bunny-test.py --noise-sigma-mm 1.0 --elevations 35 50
    uv run tests/spikes/m3-synthetic/run-bunny-test.py --azimuth-error-deg 0.5
    uv run tests/spikes/m3-synthetic/run-bunny-test.py --voxel-mm 0.65 --weights --azimuth-error-deg 3 --refine-poses icp
    uv run tests/spikes/m3-synthetic/run-bunny-test.py --tilt-error-deg 1 --translation-error-mm 1 --refine-poses turntable

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
from astrea_common.rotations import RotationAboutAxis
from astrea_common.rgbd_frame import Intrinsics
from astrea_common.tsdf import GridSpec, TsdfVolume
from astrea_common.view_package import ViewPackage
from astrea_common.view_preprocess import ComputeViewWeights, PreprocessParams
from capture_service.ring_pose import RotationErrorDeg
from capture_service.synthetic_depth import MeshDepthRenderer
from capture_service.synthetic_ring import ViewPose
from coordinator.mesh_metrics import AccuracyStats, CompletenessStats
from coordinator.meshing import ComponentCount, ExtractMesh, LargestComponent
from coordinator.postprocess import TAUBIN_ITERATIONS, BuildLods
from coordinator.reconstruct import REFINE_MODES, ReconstructSettings, RefineViews

BUNNY_DIR = Path("out/datasets/stanford-bunny/extracted/bunny/reconstruction")
INTRINSICS = Intrinsics(fx=434.1, fy=434.1, cx=240.0, cy=320.0, width=480, height=640)
MEAN_LIMIT_VOXELS = 2.0
P95_LIMIT_VOXELS = 4.0
GRID_MARGIN_MM = 8.0
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
    parser.add_argument("--tilt-error-deg", type=float, default=0.0, help="std of a random tilt (about a horizontal axis) in the pose of each view")
    parser.add_argument("--translation-error-mm", type=float, default=0.0, help="std of a random translation error in the pose of each view")
    parser.add_argument("--voxel-mm", type=float, default=None, help="fit the grid to the bunny at this voxel size instead of the default 192^3 cube")
    parser.add_argument("--weights", action="store_true", help="weight observations by incidence angle and depth")
    parser.add_argument("--no-bilinear", action="store_true", help="sample depth at the nearest pixel")
    parser.add_argument("--refine-poses", choices=REFINE_MODES, default="none")
    parser.add_argument("--taubin-iterations", type=int, default=TAUBIN_ITERATIONS)
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


def WithTiltError(rotation: np.ndarray, sigmaDeg: float, rng: np.random.Generator) -> np.ndarray:
    heading = rng.uniform(0.0, 2.0 * np.pi)
    horizontalAxis = np.array([np.cos(heading), np.sin(heading), 0.0])
    return rotation @ RotationAboutAxis(horizontalAxis, np.radians(rng.normal(0.0, sigmaDeg)))


def MeasuredPose(rotation: np.ndarray, translation: np.ndarray, args: argparse.Namespace, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    if args.azimuth_error_deg > 0:
        rotation = WithAzimuthError(rotation, args.azimuth_error_deg, rng)
    if args.tilt_error_deg > 0:
        rotation = WithTiltError(rotation, args.tilt_error_deg, rng)
    if args.translation_error_mm > 0:
        translation = translation + rng.normal(0.0, args.translation_error_mm, 3)
    return rotation, translation


def GridFor(args: argparse.Namespace, reference: TriangleMesh) -> GridSpec:
    if args.voxel_mm is None:
        return GridSpec.Default()
    return GridSpec.FitToBounds(reference.vertices.min(axis=0), reference.vertices.max(axis=0), args.voxel_mm, GRID_MARGIN_MM)


def RefinedPoses(args: argparse.Namespace, depths: list[np.ndarray], truth: list, measured: list) -> list:
    views = [ViewPackage(i, np.zeros((*d.shape, 3), np.uint8), d / 1000.0, INTRINSICS, r, t, 0.0, 0.0, 0.0) for i, (d, (r, t)) in enumerate(zip(depths, measured))]
    settings = ReconstructSettings(voxelMm=args.voxel_mm or 1.3, refinePoses=args.refine_poses)
    refined, report = RefineViews([views], PreprocessParams(), settings)
    before = np.mean([RotationErrorDeg(m[0], t[0]) for m, t in zip(measured, truth)])
    after = np.mean([RotationErrorDeg(v.rotation, t[0]) for v, t in zip(refined, truth)])
    print(f"pose refinement ({args.refine_poses}): mean rotation error {before:.2f} -> {after:.2f} deg; turntable {report['turntable'][0]['message']}")
    return [(v.rotation, v.translationMm) for v in refined]


def ReportLods(volume: TsdfVolume, reference: TriangleMesh, outDir: Path, taubinIterations: int) -> None:
    start = time.monotonic()
    lods = BuildLods(volume, taubinIterations=taubinIterations)
    print(f"LODs built in {time.monotonic() - start:.1f} s")
    voxelMm = volume.grid.voxelMm
    for name, lod in lods.items():
        accuracy = AccuracyStats(lod.mesh, reference)
        glb = lod.ToGlb()
        compressed = lod.ToCompressedGlb()
        (outDir / f"lod-{name}.glb").write_bytes(glb)
        print(f"lod-{name}: {len(lod.mesh.faces)} faces, {ComponentCount(lod.mesh)} component(s), "
              f"accuracy mean {accuracy.meanMm:.2f} mm ({accuracy.meanMm / voxelMm:.2f} vox) p95 {accuracy.p95Mm:.2f} mm, "
              f"GLB {len(glb) / 1024:.0f} KiB, zstd {len(compressed) / 1024:.0f} KiB")


def Main() -> None:
    args = ParseArgs()
    if not BUNNY_DIR.exists():
        sys.exit("bunny not found: run tests/spikes/m3-synthetic/fetch-bunny.py first")
    rng = np.random.default_rng(0)
    reference = LoadBunnyOnPlate(args.resolution, args.size_mm)
    print(f"reference: {len(reference.vertices)} vertices, {len(reference.faces)} faces, height {reference.vertices[:, 2].max():.1f} mm")

    start = time.monotonic()
    renderer = MeshDepthRenderer(reference, INTRINSICS)
    visibleFaces = np.zeros(len(reference.faces), dtype=bool)
    truth = ViewPoses(args, reference.vertices[:, 2].max())
    depths, measured = [], []
    for rotation, translation in truth:
        view = renderer.Render(rotation, translation)
        depth = view.depthMm
        if args.noise_sigma_mm > 0:
            depth = np.where(depth > 0, depth + rng.normal(0.0, args.noise_sigma_mm, depth.shape), 0.0).astype(np.float32)
        depths.append(depth)
        measured.append(MeasuredPose(rotation, translation, args, rng))
        visibleFaces[view.faceIds[view.faceIds >= 0]] = True
    print(f"rendered {len(truth)} views in {time.monotonic() - start:.1f} s")
    poses = RefinedPoses(args, depths, truth, measured) if args.refine_poses != "none" else measured

    start = time.monotonic()
    volume = TsdfVolume(GridFor(args, reference))
    for depth, (rotation, translation) in zip(depths, poses):
        weights = ComputeViewWeights(depth, INTRINSICS) if args.weights else None
        volume.Integrate(depth, INTRINSICS, rotation, translation, weights=weights, bilinear=not args.no_bilinear)
    print(f"integrated {len(poses)} views into a {volume.grid.shape} grid at {volume.grid.voxelMm:.2f} mm in {time.monotonic() - start:.1f} s")

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

    ReportLods(volume, reference, args.out_dir, args.taubin_iterations)

    passed = accuracy.meanMm <= MEAN_LIMIT_VOXELS * voxelMm and accuracy.p95Mm <= P95_LIMIT_VOXELS * voxelMm
    print(f"gate (mean <= {MEAN_LIMIT_VOXELS} vox, p95 <= {P95_LIMIT_VOXELS} vox): {'PASS' if passed else 'FAIL'}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    Main()
