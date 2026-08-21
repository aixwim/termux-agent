"""Termux Agent - CLI coding agent for Termux, like opencode."""

# Keep this aligned with ``project.version`` in pyproject.toml. Importing
# importlib.metadata adds substantial startup latency on Android/Termux and can
# report the version of an older installed wheel when running a source checkout.
__version__ = "1.1.0"
