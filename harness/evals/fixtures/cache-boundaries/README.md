This is an intentionally buggy known-answer fixture, not production code.
The public contract expires entries at `now >= expires` and preserves cached
values including `0`, `False`, and empty strings until expiry. Missing/expired
entries return `None`. No network or dependency is needed. The tests express
the intended contract; the implementation currently violates two cases.
