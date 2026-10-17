"""Native Muse Code transport; authorization stays with Meta's CLI."""
from adapters.native import available, run as _run


def check_available():
    return available("muse")


def run(**kwargs):
    return _run("muse", **kwargs)
