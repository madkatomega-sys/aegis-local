"""The math worker's computations, without the MCP server or the container around them."""

import pytest

import mathcore
from mathcore import MathError


def test_solve_quadratic():
    assert mathcore.solve(["x^2 - 4 = 0"], ["x"])["result"] == "[{x: -2}, {x: 2}]"


def test_simplify_identity():
    assert mathcore.simplify("sin(x)^2 + cos(x)^2")["result"] == "1"


def test_calculus():
    assert mathcore.differentiate("x^3", "x")["result"] == "3*x**2"
    assert mathcore.integrate("exp(-x^2)", "x", "-oo", "oo")["result"] == "sqrt(pi)"
    assert mathcore.integrate("2*x", "x")["result"] == "x**2"


def test_ode():
    assert mathcore.solve_ode("f(t).diff(t, 2) + f(t) = 0", "f", "t")["result"] == "Eq(f(t), C1*sin(t) + C2*cos(t))"


def test_evaluate_with_values():
    assert mathcore.evaluate("a*b", {"a": 2, "b": 3.5}, digits=5)["result"] == "7.0000"


def test_units():
    assert mathcore.convert_units("9.81 m/s^2 * 3 s", "km/h")["magnitude"].startswith("105.94")
    assert mathcore.check_dimensions("1 kg * m / s^2", "1 N")["consistent"] is True
    assert mathcore.check_dimensions("1 kg", "1 m")["consistent"] is False


@pytest.mark.parametrize(
    ("fn", "args"),
    [
        (mathcore.simplify, ("x +* 2",)),
        (mathcore.solve, ([], ["x"])),
        (mathcore.integrate, ("x", "x", "0", None)),
        (mathcore.convert_units, ("3", "m")),
        (mathcore.convert_units, ("3 kg", "m")),
        (mathcore.evaluate, ("x", None, 0)),
        (mathcore.simplify, ("x" * 5000,)),
    ],
)
def test_bad_input_raises_math_error(fn, args):
    with pytest.raises(MathError):
        fn(*args)
