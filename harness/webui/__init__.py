"""Local-first Web UI for MoA-X."""

__all__ = ["create_app"]


def create_app(*args, **kwargs):
    """Load Flask only when the application factory is actually requested."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)
