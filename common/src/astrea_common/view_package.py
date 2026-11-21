"""A captured keyframe (the unit of work for reconstruction) and its on-disk session layout.

A session directory holds `manifest.json` and `views/<index>.npz`. The same `ViewPackage` is what the
coordinator will send to workers, so it carries everything needed to integrate one view on its own:
RGB and averaged depth at the same resolution, the depth intrinsics and the plate->camera pose.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from astrea_common.rgbd_frame import Intrinsics

MANIFEST_NAME = "manifest.json"
VIEWS_DIR = "views"


@dataclass(frozen=True)
class ViewPackage:
    """`xCamera = rotation @ xPlate + translationMm`; `depthM` is metres, 0 meaning no measurement."""

    index: int
    rgb: np.ndarray
    depthM: np.ndarray
    intrinsics: Intrinsics
    rotation: np.ndarray
    translationMm: np.ndarray
    azimuthDeg: float
    elevationDeg: float
    reprojectionErrorPx: float


@dataclass
class SessionManifest:
    name: str
    layout: dict
    intrinsics: dict
    deviceType: str
    flipHorizontal: bool
    parameters: dict = field(default_factory=dict)
    viewCount: int = 0


def ViewPath(sessionDir: str | Path, index: int) -> Path:
    return Path(sessionDir) / VIEWS_DIR / f"{index:04d}.npz"


def WriteView(sessionDir: str | Path, view: ViewPackage) -> Path:
    path = ViewPath(sessionDir, view.index)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        index=view.index,
        rgb=view.rgb,
        depthM=view.depthM,
        intrinsics=json.dumps(asdict(view.intrinsics)),
        rotation=view.rotation,
        translationMm=view.translationMm,
        azimuthDeg=view.azimuthDeg,
        elevationDeg=view.elevationDeg,
        reprojectionErrorPx=view.reprojectionErrorPx,
    )
    return path


def ReadView(path: str | Path) -> ViewPackage:
    with np.load(path, allow_pickle=False) as data:
        return ViewPackage(
            index=int(data["index"]),
            rgb=data["rgb"],
            depthM=data["depthM"],
            intrinsics=Intrinsics(**json.loads(str(data["intrinsics"]))),
            rotation=data["rotation"],
            translationMm=data["translationMm"],
            azimuthDeg=float(data["azimuthDeg"]),
            elevationDeg=float(data["elevationDeg"]),
            reprojectionErrorPx=float(data["reprojectionErrorPx"]),
        )


def ListViewPaths(sessionDir: str | Path) -> list[Path]:
    return sorted((Path(sessionDir) / VIEWS_DIR).glob("*.npz"))


def WriteManifest(sessionDir: str | Path, manifest: SessionManifest) -> None:
    Path(sessionDir).mkdir(parents=True, exist_ok=True)
    (Path(sessionDir) / MANIFEST_NAME).write_text(json.dumps(asdict(manifest), indent=2))


def ReadManifest(sessionDir: str | Path) -> SessionManifest:
    return SessionManifest(**json.loads((Path(sessionDir) / MANIFEST_NAME).read_text()))
