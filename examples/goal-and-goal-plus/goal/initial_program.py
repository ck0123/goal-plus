"""SWE-bench instance: sympy__sympy-20212 (issue 19572).

Original issue: `0**-oo` returns `0` but should return `zoo` (ComplexInfinity).
The documentation for sympy.Pow states that 0**-oo should yield ComplexInfinity.

Upstream bug location: sympy/core/power.py, `Pow.__new__`. The original code
handles `e is S.ComplexInfinity` and `e is S.Zero` before the `b is S.Zero`
branch, but lacks the special case for `(S.Zero, S.NegativeInfinity)`. So
0**-oo falls through and is treated like 0**finite, returning 0.

Upstream gold patch (2 lines added in power.py):

    if evaluate:
        if b is S.Zero and e is S.NegativeInfinity:
            return S.ComplexInfinity

This fixture reproduces that decision logic with a self-contained set of
singletons so the evaluator can run without importing sympy. Candidates must
modify `evaluate_power` so that `evaluate_power(ZERO, NEG_INFINITY)` returns
`COMPLEX_INFINITY` while leaving the other cases unchanged.

References:
- https://github.com/sympy/sympy/issues/19572
- https://github.com/sympy/sympy/pull/20212
"""


class _Symbol:
    """Singleton-style stand-in for sympy's `S.*` special values."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Symbol) and other.name == self.name

    def __hash__(self) -> int:
        return hash(("swe20212_symbol", self.name))


ZERO = _Symbol("0")
ONE = _Symbol("1")
NEG_INFINITY = _Symbol("-oo")
INFINITY = _Symbol("oo")
COMPLEX_INFINITY = _Symbol("zoo")
NAN = _Symbol("nan")


def evaluate_power(base, exponent):
    """Return the evaluated result of `base ** exponent` for symbolic special values.

    BUGGY (mirrors sympy/core/power.py before the fix for issue 19572):
    the `(ZERO, NEG_INFINITY)` case is not handled and falls through to the
    generic `base is ZERO` branch, which returns `ZERO`. The correct behavior
    is to return `COMPLEX_INFINITY` for that case.

    The candidate should fix this without changing the behavior of any other
    input combination.
    """
    if exponent is ZERO:
        return ONE
    if exponent is COMPLEX_INFINITY:
        return NAN
    if base is ONE:
        return ONE
    if base is ZERO:
        if exponent is NEG_INFINITY:
            return COMPLEX_INFINITY
        # BUG: missing the special case `exponent is NEG_INFINITY` here,
        # which should return COMPLEX_INFINITY. Currently returns ZERO for
        # every exponent that reaches this branch (including -oo).
        return ZERO
    return _Symbol(f"{base.name}**{exponent.name}")


if __name__ == "__main__":
    print(f"0 ** -oo = {evaluate_power(ZERO, NEG_INFINITY)!r}")
    print(f"0 ** 0   = {evaluate_power(ZERO, ZERO)!r}")
    print(f"0 ** oo  = {evaluate_power(ZERO, INFINITY)!r}")
    print(f"1 ** -oo = {evaluate_power(ONE, NEG_INFINITY)!r}")
