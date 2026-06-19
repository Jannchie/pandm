"""Enable `python -m pandm` to run the CLI (handy when the `pandm` script isn't on PATH)."""

from .cli import app

app()
