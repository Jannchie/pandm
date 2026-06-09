"""pandm — beautiful, local-first experiment tracking."""

from importlib.metadata import PackageNotFoundError, version

from .sdk import Run, finish, init, log, log_image

try:
    # single source of truth is pyproject.toml; read it back from package metadata
    __version__ = version("pandm")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["init", "log", "log_image", "finish", "Run", "__version__"]
