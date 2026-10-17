"""Native DeepSeek Harness transport; separate from OpenCode Go billing."""
from adapters.native import available, run as _run


def check_available():
    return available("deepseek")


def run(**kwargs):
    return _run("deepseek", **kwargs)
