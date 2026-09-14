"""Configuration and defaults for local cycling analysis."""

from pathlib import Path

DEFAULT_STORAGE_ROOT = Path(".cycling")
DEFAULT_DOWNLOADS_DIR = Path("downloads/strava")
DEFAULT_SAMPLES_DIR = Path("samples")
DEFAULT_CATALOG_FILE = "catalog.duckdb"

NORMALIZER_VERSION = "1"
ALGORITHM_VERSION = "mvp-2"

SUPPORTED_EXTENSIONS = {".fit", ".tcx", ".fit.gz", ".tcx.gz"}
