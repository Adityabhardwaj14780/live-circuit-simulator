"""Strict formula parser tests — correctness and, above all, rejection of unsafe input."""

from __future__ import annotations

import pytest

from app.errors import Codes, FormulaError
from app.formula import evaluate, identifiers, parse, to_ngspice, tokenize


@pytest.mark.parametrize(
    ("expression", "values", "expected"),
    [
        ("1", {}, 1.0),
        ("2 + 3", {}, 5.0),
        ("2+3*4", {}, 14.0),
        ("(2+3)*4", {}, 20.0),
        ("a", {"a": 7.0}, 7.0),
        ("a + b", {"a": 1.5, "b": 2.5}, 4.0),
        ("a * b + 1", {"a": 4.0, "b": 6.0}, 25.0),
        ("-a + b", {"a": 2.0, "b": 5.0}, 3.0),
        ("a / b", {"a": 22.0, "b": 7.0}, 22.0 / 7.0),
        ("((a))", {"a": 3.0}, 3.0),
        ("2.5e2 / 5", {}, 50.0),
        ("1e-3 * b", {"b": 1000.0}, 1.0),
        ("a - b - 1", {"a": 10.0, "b": 3.0}, 6.0),
    ],
)
def test_valid_expressions(expression: str, values: dict[str, float], expected: float) -> None:
    assert evaluate(parse(expression), values) == pytest.approx(expected)


def test_evaluation_is_left_associative_and_precedence_aware() -> None:
    assert evaluate(parse("10 - 3 - 2"), {}) == 5.0
    assert evaluate(parse("2 + 3 * 4"), {}) == 14.0
    assert evaluate(parse("8 / 4 / 2"), {}) == 1.0


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "a; b",
        "a ^ b",
        "a ** b",
        "a % b",
        "a .. b",
        "a[0]",
        "`id`",
        "$(whoami)",
        "a ? b : 1",
        "sqrt(a)",
        "A + B",
        "a & b",
        "a | b",
        "a \\n b",
        "a + b\n.control\nshell ls",
        "a,",
        "a bad",
        "\x00",
    ],
)
def test_unsafe_or_invalid_expressions_are_rejected(expression: str) -> None:
    with pytest.raises(FormulaError) as info:
        parse(expression)
    assert info.value.code in (Codes.FORMULA_DISALLOWED_TOKEN, Codes.FORMULA_SYNTAX_ERROR)


@pytest.mark.parametrize("expression", ["", "   ", "a +", "(a + b", "a + b)", "* a", "1 2"])
def test_malformed_expressions_raise_syntax_errors(expression: str) -> None:
    with pytest.raises(FormulaError) as info:
        parse(expression)
    assert info.value.code == Codes.FORMULA_SYNTAX_ERROR


def test_division_by_zero_is_a_formula_error() -> None:
    with pytest.raises(FormulaError) as info:
        evaluate(parse("a / b"), {"a": 1.0, "b": 0.0})
    assert info.value.code == Codes.DIVIDE_BY_ZERO


def test_tiny_denominator_is_treated_as_zero() -> None:
    with pytest.raises(FormulaError) as info:
        evaluate(parse("1 / b"), {"b": 1e-15})
    assert info.value.code == Codes.DIVIDE_BY_ZERO


def test_missing_identifier_raises_upstream_error() -> None:
    with pytest.raises(FormulaError) as info:
        evaluate(parse("a + b"), {"a": 1.0})
    assert info.value.code == Codes.UPSTREAM_ERROR


def test_limits_are_enforced() -> None:
    with pytest.raises(FormulaError):
        parse("1 + " * 200 + "1")
    with pytest.raises(FormulaError):
        parse("((((((((((((((((((((((((((((((((1)))))))))))))))))))))))))))))))))" * 2)


def test_identifiers_and_ngspice_rendering() -> None:
    ast = parse("a * b + 1")
    assert identifiers(ast) == {"a", "b"}
    assert identifiers(parse("7")) == set()
    rendered = to_ngspice(ast, {"a": "n1", "b": "n2"})
    assert rendered == "((V(n1) * V(n2)) + 1)"
    # rendered expression only ever contains generated net names
    assert "V(n1)" in rendered and "V(n2)" in rendered


def test_rendering_requires_wired_inputs() -> None:
    with pytest.raises(FormulaError) as info:
        to_ngspice(parse("a + b"), {"a": "n1"})
    assert info.value.code == Codes.MISSING_INPUT


def test_tokenizer_reports_positions() -> None:
    tokens = tokenize("a + 12")
    assert [(token.kind, token.text, token.position) for token in tokens] == [
        ("ident", "a", 0),
        ("op", "+", 2),
        ("number", "12", 4),
    ]
