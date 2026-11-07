"""Filter expressions for the lists (pure, no Qt): `Restarts > 3`, `CPULim = 2*CPUReq`, `Status ~ crash and Namespace != kube-system`.

Text with no comparison in it is not an expression: the filter box keeps matching it as plain text in any column. An expression is
`comparison [and|or comparison ...]` (`and` binds tighter than `or`; `&&` and `||` work too; brackets group comparisons).

    comparison := operand OP operand          OP is = == != > >= < <= ~ (regular expression, case-insensitive) !~
    operand    := factor { (* | /) factor }   so CPULim = 2*CPUReq and MemUsed > MemLim/2 work
    factor     := number | 'text' | "text" | name

A *name* is a column (its heading or its attribute, without spaces, case-insensitive: `CPUReq`, `cpu_req`, `Restarts`, `Age`, `Node`),
`kind`, or a label (`label.app`, or just `app` when no column has that name). On the right of a comparison an unknown word is plain
text (`Status = Running`). Numbers carry the unit of the column they are compared with: CPU `500m` or `2`, memory `512Mi` or `2Gi`,
age `90s`, `5m`, `2h`, `1d`, `1w`; elsewhere a plain number. `=` and `!=` ignore case and accept `*` and `?` as wildcards
(`Name = web-*`). A value that is blank (no metrics, say) matches no comparison.

`parse(text)` returns an `Expression` or raises `FilterError` with a sentence for the user. `looks_like_expression(text)` says whether
the text should be parsed at all."""
import fnmatch
import re

from kubernetes.utils import parse_quantity

OPERATORS = ("!=", ">=", "<=", "==", "!~", "=", ">", "<", "~")
_TOKEN = re.compile(r"""\s*(?:
    (?P<op>!=|>=|<=|==|!~|=|>|<|~)
  | (?P<bool>&&|\|\|)
  | (?P<arith>[*/])
  | (?P<paren>[()])
  | (?P<num>\d+(?:\.\d+)?[A-Za-z]*)(?![\w.\-])
  | (?P<str>'[^']*'|"[^"]*")
  | (?P<name>[^\s=!<>~()/&|'"]+)
)""", re.X)
_DURATION = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class FilterError(ValueError):
    """The filter text could not be understood; the message is meant for the user."""


def normalize(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def looks_like_expression(text):
    """True when `text` has a comparison outside quotes (the only reason to parse it instead of matching it as text)."""
    outside = re.sub(r"'[^']*'|\"[^\"]*\"", "", str(text))
    return any(op in outside for op in ("=", ">", "<", "~"))


def _tokens(text):
    out, pos = [], 0
    while pos < len(text):
        if not text[pos:].strip():
            break
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise FilterError(f"Cannot read the filter near “{text[pos:pos + 12].strip()}”.")
        kind = match.lastgroup
        if kind == "name" and "*" in match.group(kind):
            out.extend(_split_star(match.group(kind)))
        else:
            out.append((kind, match.group(kind)))
        pos = match.end()
    return out


def _split_star(word):
    """`web-*` and `*-api` are wildcards in a name; a `*` touching a digit (`CPUReq*2`, `2*CPUReq`) is a multiplication."""
    pieces = re.split(r"(?<=\d)\*|\*(?=\d)", word)
    if len(pieces) == 1:
        return [("name", word)]
    out = []
    for i, piece in enumerate(pieces):
        if i:
            out.append(("arith", "*"))
        if piece:
            out.append(("num" if re.fullmatch(r"\d+(?:\.\d+)?[A-Za-z]*", piece) else "name", piece))
    return out


def parse_unit(text, unit):
    """A number written in the unit of the column it is compared with. Raises FilterError."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([A-Za-z]*)", text)
    if not match:
        raise FilterError(f"“{text}” is not a number.")
    number, suffix = float(match[1]), match[2]
    try:
        if unit == "age":
            if suffix == "min":
                suffix = "m"
            if suffix == "":
                raise FilterError(f"An age needs a unit: {text}s, {text}m, {text}h, {text}d or {text}w.")
            return number * _DURATION[suffix]
        if unit == "cpu":
            return float(parse_quantity(text))
        if unit == "mem":
            return float(parse_quantity(text)) / 2**20  # columns hold MiB
    except FilterError:
        raise
    except KeyError:
        raise FilterError(f"“{text}” is not an age: use s, m, h, d or w.") from None
    except (ValueError, ArithmeticError):
        raise FilterError(f"“{text}” is not a {'CPU' if unit == 'cpu' else 'memory'} amount (try 500m, 2, 512Mi, 2Gi).") from None
    if suffix:
        raise FilterError(f"“{text}” has a unit, but the other side of the comparison is a plain number.")
    return number


class _Parser:
    def __init__(self, text):
        self.tokens, self.pos = _tokens(text), 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def take(self):
        token = self.peek()
        self.pos += 1
        return token

    def word(self, *words):
        kind, value = self.peek()
        return kind == "name" and value.lower() in words

    def parse(self):
        if not self.tokens:
            raise FilterError("The filter is empty.")
        node = self.or_()
        if self.pos < len(self.tokens):
            raise FilterError(f"Unexpected “{self.peek()[1]}” in the filter.")
        return node

    def or_(self):
        node = self.and_()
        while self.word("or") or self.peek() == ("bool", "||"):
            self.take()
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.primary()
        while self.word("and") or self.peek() == ("bool", "&&"):
            self.take()
            node = ("and", node, self.primary())
        return node

    def primary(self):
        if self.peek() == ("paren", "("):
            self.take()
            node = self.or_()
            if self.take() != ("paren", ")"):
                raise FilterError("A bracket in the filter is not closed.")
            return node
        left = self.operand()
        kind, op = self.take()
        if kind != "op":
            raise FilterError("Expected a comparison such as >, =, != or ~ after “" + _show(left) + "”.")
        right = self.operand()
        if op in ("~", "!~") and right[0] in ("str", "name"):
            try:
                re.compile(right[1], re.I)
            except re.error as error:
                raise FilterError(f"The regular expression “{right[1]}” is not valid: {error}.") from None
        return ("cmp", op, left, right)

    def operand(self):
        node = self.factor()
        while self.peek()[0] == "arith":
            _kind, sign = self.take()
            node = ("mul" if sign == "*" else "div", node, self.factor())
        return node

    def factor(self):
        kind, value = self.take()
        if kind == "num":
            return ("num", value)
        if kind == "str":
            return ("str", value[1:-1])
        if kind == "name" and value.lower() not in ("and", "or"):
            return ("name", value)
        raise FilterError("Expected a column, a number or some text" + (f" instead of “{value}”." if value else " at the end of the filter."))


def _show(node):
    return {"num": lambda: node[1], "str": lambda: f"'{node[1]}'", "name": lambda: node[1]}.get(node[0], lambda: "…")()


class Expression:
    """A parsed filter. `matches(resolve)` is true for a row; `resolve(name)` returns `(value, unit)` for a column or label of that
    row (`unit` is "age", "cpu", "mem" or None), `None` when the name is nothing it knows, and a value of None for a blank cell."""

    def __init__(self, tree, text):
        self.tree, self.text = tree, text

    def matches(self, resolve):
        try:
            return self._test(self.tree, resolve)
        except FilterError:
            return False

    def _test(self, node, resolve):
        if node[0] == "and":
            return self._test(node[1], resolve) and self._test(node[2], resolve)
        if node[0] == "or":
            return self._test(node[1], resolve) or self._test(node[2], resolve)
        _tag, op, left, right = node
        unit = self._unit(left, resolve) or self._unit(right, resolve)
        a, b = self._value(left, resolve, unit), self._value(right, resolve, unit, bare_text=True)
        if a is None or b is None:
            return False
        return _compare(op, a, b)

    def _unit(self, node, resolve):
        if node[0] == "name":
            found = resolve(node[1])
            return found[1] if found else None
        if node[0] in ("mul", "div"):
            return self._unit(node[1], resolve) or self._unit(node[2], resolve)
        return None

    def _value(self, node, resolve, unit, bare_text=False):
        kind = node[0]
        if kind == "num":
            return parse_unit(node[1], unit)
        if kind == "str":
            return node[1]
        if kind == "name":
            found = resolve(node[1])
            if found is None:
                return node[1] if bare_text else None  # an unknown word on the right is just text
            return found[0]
        a, b = self._factor(node[1], resolve, unit), self._factor(node[2], resolve, unit)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return None
        if kind == "mul":
            return a * b
        return a / b if b else None


def _factor(self, node, resolve, unit):
    """An operand inside `*` or `/`: a bare number is a plain multiplier (`MemLim = 4*MemReq`), one with a unit is an amount (`2*500m`)."""
    if node[0] == "num" and re.fullmatch(r"\d+(?:\.\d+)?", node[1]):
        return float(node[1])
    return self._value(node, resolve, unit)


Expression._factor = _factor


def _compare(op, a, b):
    numbers = isinstance(a, (int, float)) and isinstance(b, (int, float))
    if op in ("~", "!~"):
        found = re.search(str(b), str(a), re.I) is not None
        return found if op == "~" else not found
    if numbers:
        return {"=": a == b, "==": a == b, "!=": a != b, ">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]
    a, b = str(a).casefold(), str(b).casefold()
    if op in ("=", "==", "!="):
        same = fnmatch.fnmatchcase(a, b) if any(c in b for c in "*?") else a == b
        return same if op != "!=" else not same
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]


def parse(text):
    return Expression(_Parser(text).parse(), text)


def _validate(expression, units):
    """Raise FilterError for what only shows up with the column units (a literal like 5x compared with an age, say). `units(name)`
    returns "age", "cpu", "mem", "" for any other known column, or None for a name that is not a column."""
    def resolve(name):
        unit = units(name)
        return None if unit is None else (0.0, unit or None)

    def walk(node):
        if node[0] in ("and", "or"):
            walk(node[1])
            walk(node[2])
            return
        _tag, _op, left, right = node
        unit = expression._unit(left, resolve) or expression._unit(right, resolve)
        expression._value(left, resolve, unit)
        expression._value(right, resolve, unit, bare_text=True)

    walk(expression.tree)


Expression.validate = lambda self, units: _validate(self, units)
