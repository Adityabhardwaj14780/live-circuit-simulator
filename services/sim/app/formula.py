"""
Strict, dependency-free formula parser.

Grammar (recursive descent, left-associative binary operators, unary sign, parentheses):

    expression := term (('+' | '-') term)*
    term       := factor (('*' | '/') factor)*
    factor     := ('+' | '-') factor | primary
    primary    := NUMBER | IDENT | '(' expression ')'

Allowed: numeric literals, identifiers ``a`` and ``b``, ``+ - * / ( )`` and whitespace.
Everything else is rejected with FORMULA_DISALLOWED_TOKEN.

`eval`/`exec` are never used: the parser produces an explicit AST that is interpreted by
``evaluate``. Rejected expressions never reach the netlist generator (see engine.py), so they can
never be passed to ngspice either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .errors import Codes, FormulaError

ALLOWED_IDENTS = ("a", "b")
MAX_EXPRESSION_LENGTH = 200
MAX_TOKENS = 256
MAX_DEPTH = 32
MAX_ABS_LITERAL = 1.0e9

# AST node shapes
Ast = tuple  # ('num', float) | ('ident', str) | ('bin', op, Ast, Ast) | ('neg', Ast)


@dataclass(frozen=True)
class Token:
    kind: str  # 'number' | 'ident' | 'op' | 'lparen' | 'rparen'
    text: str
    position: int


def tokenize(expression: str) -> list[Token]:
    """Split into tokens, rejecting everything outside the allowed alphabet.

    Deliberately permissive about *format* but strict about *alphabet*: a single unexpected
    character (letters other than a/b, `^`, `**`, `;`, quotes, brackets, `$`, backticks, `_`,
    commas, Unicode tricks) aborts the whole parse.
    """
    if not isinstance(expression, str):
        raise FormulaError("expression must be a string", Codes.FORMULA_SYNTAX_ERROR)
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise FormulaError(
            f"expression is {len(expression)} characters, limit is {MAX_EXPRESSION_LENGTH}",
            Codes.FORMULA_SYNTAX_ERROR,
        )

    tokens: list[Token] = []
    i = 0
    length = len(expression)
    while i < length:
        char = expression[i]
        if char.isspace():
            i += 1
            continue

        if char.isdigit() or (char == "." and i + 1 < length and expression[i + 1].isdigit()):
            start = i
            seen_dot = False
            while i < length and (expression[i].isdigit() or expression[i] == "."):
                if expression[i] == ".":
                    if seen_dot:
                        raise FormulaError(f"malformed number at position {start}", Codes.FORMULA_SYNTAX_ERROR, start)
                    seen_dot = True
                i += 1
            # optional exponent, e.g. 1e3 / 2.5E-2
            if i < length and expression[i] in "eE":
                lookahead = i + 1
                if lookahead < length and expression[lookahead] in "+-":
                    lookahead += 1
                if lookahead < length and expression[lookahead].isdigit():
                    i = lookahead
                    while i < length and expression[i].isdigit():
                        i += 1
                else:
                    raise FormulaError(f"malformed exponent at position {i}", Codes.FORMULA_SYNTAX_ERROR, i)
            tokens.append(Token("number", expression[start:i], start))
            continue

        if char.isalpha() or char == "_":
            start = i
            while i < length and (expression[i].isalnum() or expression[i] == "_"):
                i += 1
            word = expression[start:i]
            if word not in ALLOWED_IDENTS:
                raise FormulaError(
                    f'identifier "{word}" is not allowed; only "a" and "b" are',
                    Codes.FORMULA_DISALLOWED_TOKEN,
                    start,
                )
            tokens.append(Token("ident", word, start))
            continue

        if char in "+-*/":
            tokens.append(Token("op", char, i))
            i += 1
            continue
        if char == "(":
            tokens.append(Token("lparen", char, i))
            i += 1
            continue
        if char == ")":
            tokens.append(Token("rparen", char, i))
            i += 1
            continue

        raise FormulaError(
            f'character "{char}" at position {i} is not allowed',
            Codes.FORMULA_DISALLOWED_TOKEN,
            i,
        )

    if not tokens:
        raise FormulaError("expression is empty", Codes.FORMULA_SYNTAX_ERROR)
    if len(tokens) > MAX_TOKENS:
        raise FormulaError(f"expression has {len(tokens)} tokens, limit is {MAX_TOKENS}", Codes.FORMULA_SYNTAX_ERROR)
    return tokens


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def expect(self, kind: str) -> Token:
        token = self.peek()
        if token is None or token.kind != kind:
            position = token.position if token else len(self.tokens)
            raise FormulaError(f"expected {kind} at position {position}", Codes.FORMULA_SYNTAX_ERROR, position)
        self.pos += 1
        return token

    def parse_expression(self, depth: int) -> Ast:
        if depth > MAX_DEPTH:
            raise FormulaError(f"expression nests deeper than {MAX_DEPTH}", Codes.FORMULA_SYNTAX_ERROR)
        node = self.parse_term(depth)
        while True:
            token = self.peek()
            if token is not None and token.kind == "op" and token.text in "+-":
                self.pos += 1
                node = ("bin", token.text, node, self.parse_term(depth))
            else:
                return node

    def parse_term(self, depth: int) -> Ast:
        node = self.parse_factor(depth)
        while True:
            token = self.peek()
            if token is not None and token.kind == "op" and token.text in "*/":
                self.pos += 1
                node = ("bin", token.text, node, self.parse_factor(depth))
            else:
                return node

    def parse_factor(self, depth: int) -> Ast:
        if depth > MAX_DEPTH:
            raise FormulaError(f"expression nests deeper than {MAX_DEPTH}", Codes.FORMULA_SYNTAX_ERROR)
        token = self.peek()
        if token is not None and token.kind == "op" and token.text in "+-":
            self.pos += 1
            operand = self.parse_factor(depth + 1)
            return ("neg", operand) if token.text == "-" else ("pos", operand)
        return self.parse_primary(depth)

    def parse_primary(self, depth: int) -> Ast:
        token = self.peek()
        if token is None:
            raise FormulaError("unexpected end of expression", Codes.FORMULA_SYNTAX_ERROR)
        if token.kind == "number":
            self.pos += 1
            return ("num", float(token.text))
        if token.kind == "ident":
            self.pos += 1
            return ("ident", token.text)
        if token.kind == "lparen":
            self.pos += 1
            inner = self.parse_expression(depth + 1)
            self.expect("rparen")
            return inner
        raise FormulaError(f'unexpected "{token.text}" at position {token.position}', Codes.FORMULA_SYNTAX_ERROR, token.position)


def parse(expression: str) -> Ast:
    """Tokenize + parse; raises FormulaError with a stable code on any violation."""
    tokens = tokenize(expression)
    parser = _Parser(tokens)
    ast = parser.parse_expression(0)
    leftover = parser.peek()
    if leftover is not None:
        raise FormulaError(
            f'unexpected trailing "{leftover.text}" at position {leftover.position}',
            Codes.FORMULA_SYNTAX_ERROR,
            leftover.position,
        )
    return ast


def identifiers(ast: Ast) -> set[str]:
    """Identifiers referenced by the expression (used to decide which inputs must be wired)."""
    kind = ast[0]
    if kind == "ident":
        return {ast[1]}
    if kind == "num":
        return set()
    if kind in ("neg", "pos"):
        return identifiers(ast[1])
    return identifiers(ast[2]) | identifiers(ast[3])


def evaluate(ast: Ast, values: dict[str, float]) -> float:
    """Interpret the AST. Division by zero raises FormulaError(DIVIDE_BY_ZERO)."""
    kind = ast[0]
    if kind == "num":
        return float(ast[1])
    if kind == "ident":
        name = ast[1]
        if name not in values:
            raise FormulaError(f'input "{name}" has no value', Codes.UPSTREAM_ERROR)
        return float(values[name])
    if kind == "neg":
        return -evaluate(ast[1], values)
    if kind == "pos":
        return evaluate(ast[1], values)
    if kind == "bin":
        op = ast[1]
        left = evaluate(ast[2], values)
        right = evaluate(ast[3], values)
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0.0 or abs(right) < 1.0e-12:
                raise FormulaError(
                    f"division by zero: denominator is {right:g}",
                    Codes.DIVIDE_BY_ZERO,
                )
            return left / right
        raise FormulaError(f"unsupported operator {op!r}", Codes.FORMULA_SYNTAX_ERROR)  # pragma: no cover
    raise FormulaError(f"unsupported AST node {kind!r}", Codes.FORMULA_SYNTAX_ERROR)  # pragma: no cover


def to_ngspice(ast: Ast, net_of_ident: dict[str, str]) -> str:
    """Render the AST as an ngspice behavioural-source expression.

    Every identifier becomes a net voltage reference ``V(nX)``; generated net names are internal
    identifiers, so no user text ever reaches the deck.
    """
    kind = ast[0]
    if kind == "num":
        return _format_number(float(ast[1]))
    if kind == "ident":
        net = net_of_ident.get(ast[1])
        if net is None:
            raise FormulaError(f'input "{ast[1]}" is not wired', Codes.MISSING_INPUT)
        return f"V({net})"
    if kind == "neg":
        return f"(-{to_ngspice(ast[1], net_of_ident)})"
    if kind == "pos":
        return to_ngspice(ast[1], net_of_ident)
    return f"({to_ngspice(ast[2], net_of_ident)} {ast[1]} {to_ngspice(ast[3], net_of_ident)})"


def _format_number(value: float) -> str:
    if value.is_integer() and abs(value) < 1.0e15:
        return str(int(value))
    return repr(value)


def format_value(value: float) -> str:
    """Compact float formatting for netlists and messages."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1.0e15:
        return str(int(value))
    return repr(value)


def safe_ascii(expression: str) -> str:
    """Error messages must survive JSON round-trips unchanged; strip control characters."""
    return "".join(ch for ch in expression if ch.isprintable())


def describe_tokens(tokens: Iterable[Token]) -> str:  # pragma: no cover - debug helper
    return " ".join(f"{t.kind}:{t.text}" for t in tokens)
