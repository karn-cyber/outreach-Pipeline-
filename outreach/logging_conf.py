"""Logging configured to print through Rich so the live demo reads cleanly."""
from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console()


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    # httpx is chatty at INFO; quiet it unless we're debugging.
    logging.getLogger("httpx").setLevel(logging.WARNING if not verbose else logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
