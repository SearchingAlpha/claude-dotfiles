"""shepherd — tracks, reviews and a live board for parallel Claude Code sessions."""

VERSION = "0.1.0"
SCHEMA = 1


class ShepherdError(Exception):
    """A user-facing refusal: printed as-is by the CLI, exit code 1."""
