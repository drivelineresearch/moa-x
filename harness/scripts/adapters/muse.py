"""Native Muse Code transport; authorization stays with Meta's CLI."""
from adapters.native import available, run as _run
from adapters.muse_catalog import list_models


def check_available():
    return available("muse")


def run(**kwargs):
    return _run("muse", **kwargs)
