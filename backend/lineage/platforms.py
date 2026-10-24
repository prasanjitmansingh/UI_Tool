"""The independent platforms the UI can switch between."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .abinitio import build_abinitio_graph
from .builder import build_graph
from .graph import Graph


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    target: str  # where the tables live
    loader: str  # what loads them
    build: Callable[[Path], Graph]


PLATFORMS: dict[str, Platform] = {
    "mainframe": Platform("mainframe", "Mainframe", "Teradata", "JCL jobs", build_graph),
    "abinitio": Platform("abinitio", "Ab Initio", "Hadoop / Hive", "Ab Initio graphs", build_abinitio_graph),
}
