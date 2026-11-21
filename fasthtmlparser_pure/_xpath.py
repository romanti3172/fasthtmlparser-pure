"""XPath 1.0 over the flat tag list: the root, elements, comments, text nodes (runs of source text between tags) and
attributes (positions in the opening tag).

All axes (except namespace), predicates, |, the operators and the XPath 1.0 functions are supported, plus
ends-with(), lower-case() and upper-case(). HTML rules apply: names are case-insensitive, the string-value of an
element is its visible text, attribute values and text are decoded. Variables are not supported.
"""

import math

from . import _attrs as at
from . import _decode as dec
from . import _text
from ._chars import SPACE, SPACE_RUNS, equal_fold, is_alpha, is_digit, lower, quote, trim_space, upper, \
    utf16_length
from ._css import element_at
from ._parser import COMMENT, NAMES, is_raw_text
from ._query import Query


class XPathError(ValueError):
    """An invalid XPath expression, or one that cannot be evaluated."""


# value types
TYPE_NODE_SET = 0
TYPE_NUMBER = 1
TYPE_STRING = 2
TYPE_BOOLEAN = 3
TYPE_NAMES = ("node-set", "number", "string", "boolean")

# expressions
E_LITERAL = 0  # value
E_NUMBER = 1  # value
E_AND = 2  # l, r
E_OR = 3  # l, r
E_COMPARE = 4  # cmp, l, r
E_ARITH = 5  # arith, l, r
E_NEGATE = 6  # e
E_UNION = 7  # l, r
E_ATTR_EQUALS = 8  # name, value, negate: @name = 'literal' compared in place
E_ATTR_EXISTS = 9  # name: [@name]
E_PATH = 10  # start (expr or None), absolute, steps; attr: a bare @name
E_FILTER = 11  # primary, preds
E_FUNCTION = 12  # fn, args
# fn, l (prefix), name, r (suffix), value (needle): contains/starts-with/ends-with(concat(l, @name, r), value),
# tested on the attribute in place (prefix and suffix may be empty)
E_ATTR_TEST = 13

OP_EQ = 0
OP_NEQ = 1
OP_LT = 2
OP_LE = 3
OP_GT = 4
OP_GE = 5

ARITH_ADD = 0
ARITH_SUB = 1
ARITH_MUL = 2
ARITH_DIV = 3
ARITH_MOD = 4

# node tests
TK_NAME = 0
TK_PREFIX = 1  # dc:*
TK_WILDCARD = 2  # *
TK_NODE = 3  # node()
TK_TEXT = 4  # text()
TK_COMMENT = 5  # comment()
TK_PI = 6  # processing-instruction(): never matches
TK_CONTAINER = 7  # internal: the root and elements (// before a child or attribute step)

AXIS_CHILD = 0
AXIS_DESCENDANT = 1
AXIS_DESCENDANT_OR_SELF = 2
AXIS_SELF = 3
AXIS_PARENT = 4
AXIS_ANCESTOR = 5
AXIS_ANCESTOR_OR_SELF = 6
AXIS_FOLLOWING_SIBLING = 7
AXIS_PRECEDING_SIBLING = 8
AXIS_FOLLOWING = 9
AXIS_PRECEDING = 10
AXIS_ATTRIBUTE = 11

NAN = float("nan")
INF = float("inf")


class Expr:
    __slots__ = ("op", "type", "pos", "value", "l", "r", "cmp", "arith", "e", "name", "negate", "start", "absolute",
                 "steps", "primary", "preds", "fn", "args", "attr")

    def __init__(self, op, type_, pos, **fields):
        self.op = op
        self.type = type_
        self.pos = pos  # depends on the context position or size
        self.value = self.l = self.r = self.cmp = self.arith = self.e = self.name = self.negate = None
        self.start = self.absolute = self.steps = self.primary = self.preds = self.fn = self.args = self.attr = None
        for k, v in fields.items():
            setattr(self, k, v)


class Step:
    __slots__ = ("axis", "test", "preds", "positional")

    def __init__(self, axis, test, preds):
        self.axis = axis
        self.test = test
        self.preds = preds
        self.positional = any(p.type == TYPE_NUMBER or p.pos for p in preds)


class NodeTest:
    __slots__ = ("k", "name", "q")

    def __init__(self, k, name=""):
        self.k = k
        self.name = name
        self.q = Query(name) if k == TK_NAME else None


ANY_NODE_TEST = NodeTest(TK_NODE)
CONTAINERS_TEST = NodeTest(TK_CONTAINER)


# ----- the compiler -----

T_END = 0
T_SLASH = 1
T_DOUBLE_SLASH = 2
T_LPAREN = 3
T_RPAREN = 4
T_LBRACKET = 5
T_RBRACKET = 6
T_DOT = 7
T_DOT_DOT = 8
T_AT = 9
T_COMMA = 10
T_COLON_COLON = 11
T_PIPE = 12
T_PLUS = 13
T_MINUS = 14
T_EQ = 15
T_NEQ = 16
T_LT = 17
T_LE = 18
T_GT = 19
T_GE = 20
T_STAR = 21  # name test *
T_MULTIPLY = 22  # operator *
T_AND = 23
T_OR = 24
T_DIV = 25
T_MOD = 26
T_NAME = 27
T_LITERAL = 28
T_NUMBER = 29
T_DOLLAR = 30

SINGLE = {"(": T_LPAREN, ")": T_RPAREN, "[": T_LBRACKET, "]": T_RBRACKET, "@": T_AT, ",": T_COMMA, "|": T_PIPE,
          "+": T_PLUS, "-": T_MINUS, "=": T_EQ, "$": T_DOLLAR}
NODE_TYPES = frozenset(("node", "text", "comment", "processing-instruction"))
OPERATORS = {"and": T_AND, "or": T_OR, "div": T_DIV, "mod": T_MOD}
OPERAND_ENDS = frozenset((T_RPAREN, T_RBRACKET, T_DOT, T_DOT_DOT, T_STAR, T_NAME, T_LITERAL, T_NUMBER))
AXES = {
    "child": AXIS_CHILD, "descendant": AXIS_DESCENDANT, "descendant-or-self": AXIS_DESCENDANT_OR_SELF,
    "self": AXIS_SELF, "parent": AXIS_PARENT, "ancestor": AXIS_ANCESTOR, "ancestor-or-self": AXIS_ANCESTOR_OR_SELF,
    "following-sibling": AXIS_FOLLOWING_SIBLING, "preceding-sibling": AXIS_PRECEDING_SIBLING,
    "following": AXIS_FOLLOWING, "preceding": AXIS_PRECEDING, "attribute": AXIS_ATTRIBUTE,
}
MANY = 1 << 30
# name: (min args, max args, type)
FUNCTIONS = {
    "last": (0, 0, TYPE_NUMBER), "position": (0, 0, TYPE_NUMBER), "count": (1, 1, TYPE_NUMBER),
    "name": (0, 1, TYPE_STRING), "local-name": (0, 1, TYPE_STRING), "string": (0, 1, TYPE_STRING),
    "concat": (2, MANY, TYPE_STRING), "starts-with": (2, 2, TYPE_BOOLEAN), "ends-with": (2, 2, TYPE_BOOLEAN),
    "contains": (2, 2, TYPE_BOOLEAN), "substring-before": (2, 2, TYPE_STRING),
    "substring-after": (2, 2, TYPE_STRING), "substring": (2, 3, TYPE_STRING), "string-length": (0, 1, TYPE_NUMBER),
    "normalize-space": (0, 1, TYPE_STRING), "translate": (3, 3, TYPE_STRING), "lower-case": (1, 1, TYPE_STRING),
    "upper-case": (1, 1, TYPE_STRING), "not": (1, 1, TYPE_BOOLEAN), "true": (0, 0, TYPE_BOOLEAN),
    "false": (0, 0, TYPE_BOOLEAN), "boolean": (1, 1, TYPE_BOOLEAN), "number": (0, 1, TYPE_NUMBER),
    "sum": (1, 1, TYPE_NUMBER), "floor": (1, 1, TYPE_NUMBER), "ceiling": (1, 1, TYPE_NUMBER),
    "round": (1, 1, TYPE_NUMBER),
}


class Token:
    __slots__ = ("k", "t", "n", "p")

    def __init__(self, k, t, n, p):
        self.k = k
        self.t = t
        self.n = n
        self.p = p


def _letter(ch):
    return is_alpha(ch) if ch < "\x80" else ch.isalpha()


def _xname_start(ch):
    return ch == "_" or _letter(ch)


def _xname_char(ch):
    if ch < "\x80":
        return is_alpha(ch) or is_digit(ch) or ch in "_-."
    return ch.isalpha() or ch.isdecimal()


def _digit(ch):
    return ch != "" and "0" <= ch <= "9"


class _Compiler:
    def __init__(self, expression):
        self.expression = expression
        self.source = expression
        self.tokens = []
        self.index = 0
        self.tokenize()

    def parse(self):
        e = self.parse_expr()
        if self.peek(0).k != T_END:
            self.fail(f"unexpected {self.describe(self.peek(0))}")
        return e

    def fail_at(self, msg, position):
        raise XPathError(f"invalid XPath {quote(self.expression)} at position {position}: {msg}")

    def fail(self, msg):
        self.fail_at(msg, self.peek(0).p)

    def tokenize(self):
        s = self.source
        n = len(s)
        i = 0
        tokens = self.tokens
        while True:
            while i < n and s[i] in SPACE:
                i += 1
            if i >= n:
                tokens.append(Token(T_END, "", 0.0, i))
                return
            start = i
            ch = s[i]
            nxt = s[i + 1] if i + 1 < n else ""
            # XPath 1.0 lexical rule: after a token that can end an operand, '*' and and/or/div/mod are operators
            operator_expected = bool(tokens) and tokens[-1].k in OPERAND_ENDS
            kind = T_END
            txt = ""
            number = 0.0
            if ch == "/":
                if nxt == "/":
                    kind = T_DOUBLE_SLASH
                    i += 2
                else:
                    kind = T_SLASH
                    i += 1
            elif ch in SINGLE:
                kind = SINGLE[ch]
                i += 1
            elif ch == "*":
                kind = T_MULTIPLY if operator_expected else T_STAR
                i += 1
            elif ch == "!":
                if nxt != "=":
                    self.fail_at("'!' must be followed by '='", i)
                kind = T_NEQ
                i += 2
            elif ch == "<" or ch == ">":
                if nxt == "=":
                    kind = T_LE if ch == "<" else T_GE
                    i += 2
                else:
                    kind = T_LT if ch == "<" else T_GT
                    i += 1
            elif ch == ":":
                if nxt != ":":
                    self.fail_at("unexpected ':'", i)
                kind = T_COLON_COLON
                i += 2
            elif ch == '"' or ch == "'":
                stop = s.find(ch, i + 1)
                if stop < 0:
                    self.fail_at("unterminated string literal", i)
                kind = T_LITERAL
                txt = s[i + 1:stop]
                i = stop + 1
            elif ch == "." and nxt == ".":
                kind = T_DOT_DOT
                i += 2
            elif ch == "." and not _digit(nxt):
                kind = T_DOT
                i += 1
            elif _digit(ch) or ch == ".":
                while i < n and _digit(s[i]):
                    i += 1
                if i < n and s[i] == ".":
                    i += 1
                    while i < n and _digit(s[i]):
                        i += 1
                kind = T_NUMBER
                number = float(s[start:i])
            elif _xname_start(ch):
                i += 1
                while i < n:
                    if _xname_char(s[i]):
                        i += 1
                    elif s[i] == ":" and i + 1 < n and s[i + 1] != ":" and (_xname_start(s[i + 1]) or s[i + 1] == "*"):
                        # a prefixed name (dc:creator) or prefix:*
                        i += 2
                        if s[i - 1] == "*":
                            break
                    else:
                        break
                kind = T_NAME
                txt = s[start:i]
                if operator_expected:
                    op = OPERATORS.get(txt)
                    if op is None:
                        self.fail_at(f"expected an operator, found '{txt}'", start)
                    kind = op
            else:
                self.fail_at(f"unexpected character '{ch}'", i)
            tokens.append(Token(kind, txt, number, start))

    # ----- parser helpers -----

    def peek(self, offset):
        i = self.index + offset
        return self.tokens[i if i < len(self.tokens) else len(self.tokens) - 1]

    def advance(self):
        t = self.tokens[self.index]
        self.index += 1
        return t

    def accept(self, kind):
        if self.peek(0).k != kind:
            return False
        self.index += 1
        return True

    def expect(self, kind, what):
        if not self.accept(kind):
            self.fail(f"expected {what}, found {self.describe(self.peek(0))}")

    def describe(self, t):
        if t.k == T_END:
            return "the end of the expression"
        if t.k == T_NAME or t.k == T_LITERAL:
            return f"'{t.t}'"
        if t.k == T_NUMBER:
            return number_to_string(t.n)
        to = min(len(self.source), t.p + 2)
        return "'" + self.source[t.p:to].rstrip() + "'"

    # ----- expressions -----

    @staticmethod
    def attribute_name(e):
        """Whether the expression is a bare @name (one attribute step, no predicates); its name (lower case), or
        None."""
        if e.op != E_PATH or e.start is not None or e.absolute or len(e.steps) != 1:
            return None
        s = e.steps[0]
        if s.axis != AXIS_ATTRIBUTE or s.preds or s.test.k != TK_NAME:
            return None
        return s.test.name.lower() if s.test.name.isascii() else _ascii_lower(s.test.name)

    def make_equality(self, op, left, right):
        """@name = 'literal' is compared in place; everything else by the general rules."""
        name = self.attribute_name(left)
        if name is not None and right.op == E_LITERAL:
            return Expr(E_ATTR_EQUALS, TYPE_BOOLEAN, False, name=name, value=right.value, negate=op == OP_NEQ)
        name = self.attribute_name(right)
        if name is not None and left.op == E_LITERAL:
            return Expr(E_ATTR_EQUALS, TYPE_BOOLEAN, False, name=name, value=left.value, negate=op == OP_NEQ)
        return Expr(E_COMPARE, TYPE_BOOLEAN, left.pos or right.pos, cmp=op, l=left, r=right)

    @staticmethod
    def optimize(steps):
        """//x is descendant-or-self::node()/child::x. Without positional predicates on x it is the same as
        descendant::x: one linear scan of the tag list instead of the children of every node."""
        steps = list(steps)
        i = 0
        while i < len(steps) - 1:
            s = steps[i]
            if s.axis == AXIS_DESCENDANT_OR_SELF and s.test is ANY_NODE_TEST and not s.preds:
                nxt = steps[i + 1]
                if nxt.axis == AXIS_CHILD and not nxt.positional:
                    steps[i] = Step(AXIS_DESCENDANT, nxt.test, nxt.preds)
                    del steps[i + 1]
                elif nxt.axis == AXIS_CHILD or nxt.axis == AXIS_ATTRIBUTE:
                    # only the root and elements have children or attributes: skip the text nodes
                    steps[i] = Step(AXIS_DESCENDANT_OR_SELF, CONTAINERS_TEST, [])
            i += 1
        return steps

    @staticmethod
    def can_start_step(t):
        return t.k in (T_DOT, T_DOT_DOT, T_AT, T_STAR, T_NAME)

    # ----- grammar (XPath 1.0, section 3) -----

    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.accept(T_OR):
            right = self.parse_and()
            left = Expr(E_OR, TYPE_BOOLEAN, left.pos or right.pos, l=left, r=right)
        return left

    def parse_and(self):
        left = self.parse_equality()
        while self.accept(T_AND):
            right = self.parse_equality()
            left = Expr(E_AND, TYPE_BOOLEAN, left.pos or right.pos, l=left, r=right)
        return left

    def parse_equality(self):
        left = self.parse_relational()
        while True:
            if self.accept(T_EQ):
                op = OP_EQ
            elif self.accept(T_NEQ):
                op = OP_NEQ
            else:
                return left
            left = self.make_equality(op, left, self.parse_relational())

    def parse_relational(self):
        left = self.parse_additive()
        while True:
            if self.accept(T_LT):
                op = OP_LT
            elif self.accept(T_LE):
                op = OP_LE
            elif self.accept(T_GT):
                op = OP_GT
            elif self.accept(T_GE):
                op = OP_GE
            else:
                return left
            right = self.parse_additive()
            left = Expr(E_COMPARE, TYPE_BOOLEAN, left.pos or right.pos, cmp=op, l=left, r=right)

    def parse_additive(self):
        left = self.parse_multiplicative()
        while True:
            if self.accept(T_PLUS):
                op = ARITH_ADD
            elif self.accept(T_MINUS):
                op = ARITH_SUB
            else:
                return left
            right = self.parse_multiplicative()
            left = Expr(E_ARITH, TYPE_NUMBER, left.pos or right.pos, arith=op, l=left, r=right)

    def parse_multiplicative(self):
        left = self.parse_unary()
        while True:
            if self.accept(T_MULTIPLY):
                op = ARITH_MUL
            elif self.accept(T_DIV):
                op = ARITH_DIV
            elif self.accept(T_MOD):
                op = ARITH_MOD
            else:
                return left
            right = self.parse_unary()
            left = Expr(E_ARITH, TYPE_NUMBER, left.pos or right.pos, arith=op, l=left, r=right)

    def parse_unary(self):
        if self.accept(T_MINUS):
            operand = self.parse_unary()
            return Expr(E_NEGATE, TYPE_NUMBER, operand.pos, e=operand)
        return self.parse_union()

    def parse_union(self):
        left = self.parse_path()
        while self.peek(0).k == T_PIPE:
            position = self.advance().p
            right = self.parse_path()
            if left.type != TYPE_NODE_SET or right.type != TYPE_NODE_SET:
                self.fail_at("'|' needs node-sets on both sides", position)
            left = Expr(E_UNION, TYPE_NODE_SET, left.pos or right.pos, l=left, r=right)
        return left

    def parse_path(self):
        t = self.peek(0)
        is_filter = t.k in (T_LITERAL, T_NUMBER, T_LPAREN, T_DOLLAR) or \
            (t.k == T_NAME and self.peek(1).k == T_LPAREN and t.t not in NODE_TYPES)
        if not is_filter:
            return self.parse_location_path()
        position = t.p
        primary = self.parse_primary()
        predicates = self.parse_predicates()
        if predicates:
            if primary.type != TYPE_NODE_SET:
                self.fail_at("a predicate needs a node-set", position)
            primary = Expr(E_FILTER, TYPE_NODE_SET, primary.pos, primary=primary, preds=predicates)
        k = self.peek(0).k
        if k != T_SLASH and k != T_DOUBLE_SLASH:
            return primary
        if primary.type != TYPE_NODE_SET:
            self.fail_at("'/' needs a node-set on the left", position)
        steps = self.optimize(self.parse_relative_steps([], True))
        return Expr(E_PATH, TYPE_NODE_SET, primary.pos, start=primary, absolute=False, steps=steps)

    def parse_location_path(self):
        steps = []
        absolute = False
        if self.accept(T_SLASH):
            absolute = True
            if self.can_start_step(self.peek(0)):
                steps = self.parse_relative_steps(steps, False)
        elif self.peek(0).k == T_DOUBLE_SLASH:
            absolute = True
            steps = self.parse_relative_steps(steps, True)
        else:
            if not self.can_start_step(self.peek(0)):
                if self.peek(0).k == T_END:
                    self.fail("unexpected end of the expression")
                self.fail(f"unexpected {self.describe(self.peek(0))}")
            steps = self.parse_relative_steps(steps, False)
        path = Expr(E_PATH, TYPE_NODE_SET, False, start=None, absolute=absolute, steps=self.optimize(steps))
        name = self.attribute_name(path)
        if name is not None:
            path.attr = name  # read in place where a string is needed
        return path

    def parse_relative_steps(self, steps, after_slash):
        """Steps separated by '/' or '//'; after_slash: the next token is a '/' or '//' to read first."""
        if not after_slash:
            steps.append(self.parse_step())
        while True:
            if self.accept(T_SLASH):
                steps.append(self.parse_step())
            elif self.accept(T_DOUBLE_SLASH):
                steps.append(Step(AXIS_DESCENDANT_OR_SELF, ANY_NODE_TEST, []))
                steps.append(self.parse_step())
            else:
                return steps

    def parse_step(self):
        if self.accept(T_DOT):
            return Step(AXIS_SELF, ANY_NODE_TEST, [])
        if self.accept(T_DOT_DOT):
            return Step(AXIS_PARENT, ANY_NODE_TEST, [])
        axis = AXIS_CHILD
        if self.accept(T_AT):
            axis = AXIS_ATTRIBUTE
        elif self.peek(0).k == T_NAME and self.peek(1).k == T_COLON_COLON:
            name = self.advance()
            a = AXES.get(name.t)
            if a is None:
                if name.t == "namespace":
                    self.fail_at("the namespace axis is not supported", name.p)
                self.fail_at(f"unknown axis '{name.t}'", name.p)
            axis = a
            self.advance()
        test = self.parse_node_test()
        return Step(axis, test, self.parse_predicates())

    def parse_node_test(self):
        if self.accept(T_STAR):
            return NodeTest(TK_WILDCARD)
        t = self.peek(0)
        if t.k != T_NAME:
            self.fail(f"expected a node test, found {self.describe(t)}")
        self.advance()
        name = t.t
        if self.peek(0).k == T_LPAREN and name in NODE_TYPES:
            self.advance()
            if name == "processing-instruction":
                self.accept(T_LITERAL)
            self.expect(T_RPAREN, "')'")
            if name == "node":
                return ANY_NODE_TEST
            if name == "text":
                return NodeTest(TK_TEXT)
            if name == "comment":
                return NodeTest(TK_COMMENT)
            return NodeTest(TK_PI)
        if name.endswith(":*"):
            return NodeTest(TK_PREFIX, name[:-1])
        return NodeTest(TK_NAME, name)

    def parse_predicates(self):
        predicates = []
        while self.accept(T_LBRACKET):
            p = self.parse_expr()
            self.expect(T_RBRACKET, "']'")
            # [@name]: an existence check instead of a node-set
            name = self.attribute_name(p)
            if name is not None:
                p = Expr(E_ATTR_EXISTS, TYPE_BOOLEAN, False, name=name)
            predicates.append(p)
        return predicates

    def parse_primary(self):
        t = self.advance()
        if t.k == T_LITERAL:
            return Expr(E_LITERAL, TYPE_STRING, False, value=t.t)
        if t.k == T_NUMBER:
            return Expr(E_NUMBER, TYPE_NUMBER, False, value=t.n)
        if t.k == T_LPAREN:
            e = self.parse_expr()
            self.expect(T_RPAREN, "')'")
            return e
        if t.k == T_DOLLAR:
            self.fail_at("variables are not supported", t.p)

        # function call
        info = FUNCTIONS.get(t.t)
        if info is None:
            self.fail_at(f"unknown function '{t.t}()'", t.p)
        self.expect(T_LPAREN, "'('")
        args = []
        if not self.accept(T_RPAREN):
            while True:
                args.append(self.parse_expr())
                if not self.accept(T_COMMA):
                    break
            self.expect(T_RPAREN, "')'")
        fn = t.t
        if len(args) < info[0] or len(args) > info[1]:
            self.fail_at(f"wrong number of arguments for {fn}()", t.p)
        if fn in ("count", "sum", "name", "local-name") and args and args[0].type != TYPE_NODE_SET:
            self.fail_at(f"{fn}() needs a node-set argument", t.p)
        pos = fn == "position" or fn == "last" or any(a.pos for a in args)
        return self.make_attribute_test(fn, args) or Expr(E_FUNCTION, info[2], pos, fn=fn, args=args)

    def make_attribute_test(self, fn, args):
        """contains(@a, 'x'), starts-with(...), ends-with(...), also with the attribute wrapped in literals
        (contains(concat(' ', @class, ' '), ' x '), the usual class test), are evaluated on the attribute in place,
        without a node-set for every element. None: a plain function call."""
        if fn not in ("contains", "starts-with", "ends-with") or args[1].op != E_LITERAL:
            return None
        arg = args[0]
        name = self.attribute_name(arg)
        if name is not None:
            return Expr(E_ATTR_TEST, TYPE_BOOLEAN, False, fn=fn, l="", name=name, r="", value=args[1].value)
        if arg.op == E_FUNCTION and arg.fn == "concat" and len(arg.args) == 3 and arg.args[0].op == E_LITERAL                 and arg.args[2].op == E_LITERAL:
            name = self.attribute_name(arg.args[1])
            if name is not None:
                return Expr(E_ATTR_TEST, TYPE_BOOLEAN, False, fn=fn, l=arg.args[0].value, name=name,
                            r=arg.args[2].value, value=args[1].value)
        return None


def _ascii_lower(s):
    from ._chars import ascii_lower
    return ascii_lower(s)


def compile_xpath(expression):
    return _Compiler(expression).parse()


def number_to_string(d):
    """The shortest round-trip digits, written without an exponent (XPath has none), as JavaScript writes a
    number."""
    if d != d:
        return "NaN"
    if d in (INF, -INF):
        return "Infinity" if d > 0 else "-Infinity"
    if d == 0:
        return "0"
    if d == math.floor(d) and abs(d) < 1e15:
        return str(int(d))
    r = repr(abs(d))
    if "e" in r:
        mantissa, exponent = r.split("e")
        exponent = int(exponent)
    else:
        mantissa, exponent = r, 0
    if "." in mantissa:
        int_part, frac = mantissa.split(".")
    else:
        int_part, frac = mantissa, ""
    digits = int_part + frac
    point_at = len(int_part) + exponent
    while len(digits) > 1 and digits.startswith("0"):
        digits = digits[1:]
        point_at -= 1
    digits = digits.rstrip("0")
    n = len(digits)
    out = "-" if d < 0 else ""
    if point_at <= 0:
        return out + "0." + "0" * -point_at + digits
    if point_at >= n:
        return out + digits + "0" * (point_at - n)
    return out + digits[:point_at] + "." + digits[point_at:]


# ----- the data model -----

ROOT = 0
ELEMENT = 1
COMMENT_NODE = 2
TEXT = 3
ATTRIBUTE = 4


class XNode:
    """tag: element / comment: the tag; text / attribute: the parent element (-1: the root).
    start: text: first char; attribute: name start. length: text: length; attribute: name length.
    value_start: attribute: value start (-1 for no value).
    major, minor: document order and identity: tags and text nodes by their first char; attributes right after
    their element."""

    __slots__ = ("kind", "tag", "start", "length", "value_start", "value_length", "major", "minor")

    def __init__(self, kind, tag, major, start=0, length=0, value_start=0, value_length=0):
        self.kind = kind
        self.tag = tag
        self.start = start
        self.length = length
        self.value_start = value_start
        self.value_length = value_length
        self.major = major
        self.minor = start if kind == ATTRIBUTE else 0


_ROOT = XNode(ROOT, -1, -1)


def of_tag(d, i):
    return XNode(COMMENT_NODE if d.keys[i] == COMMENT else ELEMENT, i, d.starts[i])


def _range_start(x):
    """The first source position the node covers (for following / preceding)."""
    return -1 if x.kind == ROOT else x.major


def _range_end(d, x):
    """The last source position the node covers."""
    k = x.kind
    if k == ROOT:
        return INF
    if k == ELEMENT or k == COMMENT_NODE:
        return d.ends[x.tag]
    if k == TEXT:
        return x.start + x.length - 1
    return d.starts[x.tag]  # attribute: the children of its element follow it


# ----- evaluation -----

def _matches_text(test):
    return test.k == TK_NODE or test.k == TK_TEXT


def _matches_tags(test):
    return test.k != TK_TEXT and test.k != TK_PI


def _matches_attribute(test, attribute):
    k = test.k
    if k == TK_NAME:
        return equal_fold(attribute, test.name)
    if k == TK_PREFIX:
        n = len(test.name)
        return len(attribute) >= n and equal_fold(attribute[:n], test.name)
    return k == TK_WILDCARD or k == TK_NODE


def _reverse(axis):
    return axis in (AXIS_ANCESTOR, AXIS_ANCESTOR_OR_SELF, AXIS_PRECEDING, AXIS_PRECEDING_SIBLING)


def _fmod(x, y):
    """C fmod: the remainder has the sign of x."""
    if x != x or y != y or x in (INF, -INF) or y == 0:
        return NAN
    if y in (INF, -INF):
        return x
    return math.fmod(x, y)


def _float_floor(v):
    if v != v or v in (INF, -INF):
        return v
    r = float(math.floor(v))
    return -0.0 if r == 0 and v < 0 else r


def _float_ceil(v):
    if v != v or v in (INF, -INF):
        return v
    r = float(math.ceil(v))
    return -0.0 if r == 0 and v < 0 else r


def _round(v):
    if v != v or v in (INF, -INF):
        return v
    return _float_floor(v + 0.5)


def _to_boolean(v):
    if v is True or v is False:
        return v
    if isinstance(v, float):
        return v != 0 and v == v
    return len(v) > 0


def _string_to_number(value):
    """Optional '-', digits with an optional decimal point; anything else is NaN."""
    s = trim_space(value)
    body = s[1:] if s.startswith("-") else s
    if not body or not all(c in "0123456789." for c in body):
        return NAN
    dots = body.count(".")
    if dots > 1 or len(body) == dots:
        return NAN
    return float(s)


class Evaluator:
    """The evaluation of one expression on one document: values are a node-set (a list of XNode), a float, a str
    or True / False."""

    __slots__ = ("d", "all")

    def __init__(self, d):
        self.d = d
        self.all = None

    # ----- node tests -----

    def matches_tag_at(self, test, i):
        """The node test on the tag at index i."""
        d = self.d
        k = test.k
        if k == TK_NAME:
            return test.q.name_matches_at(d, i)
        if k == TK_PREFIX:
            key = d.keys[i]
            name = NAMES[key] if key >= 0 else d.customs[i]
            n = len(test.name)
            return element_at(d, i) and len(name) >= n and equal_fold(name[:n], test.name)
        if k == TK_WILDCARD or k == TK_CONTAINER:
            return element_at(d, i)
        if k == TK_NODE:
            return element_at(d, i) or d.keys[i] == COMMENT
        if k == TK_COMMENT:
            return d.keys[i] == COMMENT
        return False

    def matches(self, test, x, attribute_axis):
        k = x.kind
        if k == ROOT:
            return test.k == TK_NODE or test.k == TK_CONTAINER
        if k == ELEMENT or k == COMMENT_NODE:
            return self.matches_tag_at(test, x.tag)
        if k == TEXT:
            return _matches_text(test)
        if attribute_axis:
            return _matches_attribute(test, self.d.src[x.start:x.start + x.length])
        return test.k == TK_NODE

    # ----- navigation -----

    def add_children(self, x, test, out):
        """Appends the children of the root or an element that match the test, text nodes included, in document
        order."""
        want_text = _matches_text(test)
        want_tags = _matches_tags(test)
        if not want_text and not want_tags:
            return
        d = self.d
        if x.kind == ROOT:
            first = 0
            end = INF
            child_depth = 0
            p = 0
            text_end = len(d.src)
            parent = -1
        elif x.kind == ELEMENT and d.inner_starts[x.tag] > 0 and d.inner_ends[x.tag] > 0:
            t = x.tag
            first = t + 1
            end = d.ends[t]
            child_depth = d.depths[t] + 1
            p = d.inner_starts[t]
            text_end = d.inner_ends[t] + 1
            parent = t
        else:
            return
        ends = d.ends
        depths = d.depths
        starts = d.starts
        keys = d.keys
        count = len(ends)
        i = first
        while i < count:
            if ends[i] > end:
                break
            if depths[i] == child_depth:
                start = starts[i]
                if want_text and start > p:
                    e = start if start < text_end else text_end
                    if e > p:
                        out.append(XNode(TEXT, parent, p, p, e - p))
                if want_tags and self.matches_tag_at(test, i):
                    out.append(XNode(COMMENT_NODE if keys[i] == COMMENT else ELEMENT, i, start))
                p = ends[i] + 1
            i += 1
        if want_text and text_end > p:
            out.append(XNode(TEXT, parent, p, p, text_end - p))

    def add_descendants(self, x, test, out):
        """Appends the descendants of the root or an element that match the test, in document order."""
        if _matches_text(test):
            self.add_descendants_with_text(x, test, out)
            return
        if not _matches_tags(test):
            return
        d = self.d
        if x.kind == ROOT:
            first = 0
            end = INF
        elif x.kind == ELEMENT and d.inner_starts[x.tag] > 0 and d.inner_ends[x.tag] > 0:
            first = x.tag + 1
            end = d.ends[x.tag]
        else:
            return
        # tags only: one linear scan of the flat list
        ends = d.ends
        starts = d.starts
        keys = d.keys
        count = len(ends)
        i = first
        if test.k == TK_NAME:
            q = test.q
            qk = q.key
            named = q.tag_name != ""
            customs = d.customs
            while i < count:
                if ends[i] > end:
                    break
                # a known name: one int compare on the arrays
                k = keys[i]
                if (k == qk or (k < 0 and named and equal_fold(customs[i], q.tag_name))) if qk >= 0 \
                        else q.name_matches_at(d, i):
                    out.append(XNode(ELEMENT, i, starts[i]))
                i += 1
            return
        while i < count:
            if ends[i] > end:
                break
            if self.matches_tag_at(test, i):
                out.append(XNode(COMMENT_NODE if keys[i] == COMMENT else ELEMENT, i, starts[i]))
            i += 1

    def add_descendants_with_text(self, x, test, out):
        children = []
        self.add_children(x, ANY_NODE_TEST, children)
        for child in children:
            if self.matches(test, child, False):
                out.append(child)
            if child.kind == ELEMENT:
                self.add_descendants_with_text(child, test, out)

    def parent_node(self, x):
        k = x.kind
        if k == ROOT:
            return None
        d = self.d
        if k == TEXT or k == ATTRIBUTE:
            return _ROOT if x.tag < 0 else of_tag(d, x.tag)
        t = x.tag
        p = d.parents[t]
        return of_tag(d, p) if d.depths[t] > 0 and p >= 0 else _ROOT

    def add_attributes(self, x, test, out):
        if x.kind != ELEMENT:
            return
        s = at.AttrScanner(self.d, x.tag)
        major = self.d.starts[x.tag]
        while s.next():
            if _matches_attribute(test, s.name()):
                out.append(XNode(ATTRIBUTE, x.tag, major, s.name_start, s.name_length, s.value_start,
                                 s.value_length))

    def attribute_of(self, x, name):
        """The raw value of the first attribute with the name (lower case); None when the element has none."""
        if x.kind != ELEMENT:
            return None
        raw = at.attr_raw(self.d, x.tag, name)
        if raw:
            return raw
        return raw if at.has_attribute(self.d, x.tag, name) else None

    def string_value(self, x):
        """The root and elements give their visible text; text nodes and attributes their decoded value; comments
        their content."""
        d = self.d
        k = x.kind
        if k == ROOT:
            return _text.clear_tags(d.src)
        if k == ELEMENT:
            return d.text_of(x.tag)
        if k == ATTRIBUTE:
            if x.value_start < 0:
                return ""
            return dec.decode(d.src[x.value_start:x.value_start + x.value_length], True)
        if k == TEXT:
            text = d.src[x.start:x.start + x.length]
            if x.tag >= 0 and is_raw_text(d.keys[x.tag]):
                return dec.normalize_newlines(text)
            return dec.decode(text, False)
        # comment
        t = x.tag
        source = d.src[d.starts[t]:d.ends[t] + 1]
        if source.startswith("<!--"):
            source = source[4:]
        if source.endswith("-->"):
            source = source[:-3]
        return source

    def node_name(self, x):
        """name() / local-name(): the lower-case tag or attribute name; "" for other nodes."""
        if x.kind == ELEMENT:
            return self.d.name_of(x.tag)
        if x.kind == ATTRIBUTE:
            return lower(self.d.src[x.start:x.start + x.length])
        return ""

    # ----- values -----

    def to_number(self, v):
        if isinstance(v, float):
            return v
        if v is True:
            return 1.0
        if v is False:
            return 0.0
        if isinstance(v, str):
            return _string_to_number(v)
        return NAN if not v else _string_to_number(self.string_value(v[0]))

    def to_str(self, v):
        if isinstance(v, str):
            return v
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, float):
            return number_to_string(v)
        return "" if not v else self.string_value(v[0])

    @staticmethod
    def sort_unique(nodes):
        """Puts the nodes in document order without duplicates."""
        count = len(nodes)
        if count < 2:
            return nodes
        for i in range(1, count):
            a = nodes[i - 1]
            b = nodes[i]
            if a.major > b.major or (a.major == b.major and a.minor >= b.minor):
                break
        else:
            return nodes
        # stable: ties keep their order
        nodes = sorted(nodes, key=lambda n: (n.major, n.minor))
        out = [nodes[0]]
        last = nodes[0]
        for n in nodes[1:]:
            if n.major != last.major or n.minor != last.minor:
                out.append(n)
                last = n
        return out

    def all_nodes(self):
        if self.all is None:
            a = []
            self.add_descendants(_ROOT, ANY_NODE_TEST, a)
            self.all = a
        return self.all

    def eval_nodes(self, e, node, position, size):
        v = self.eval_expr(e, node, position, size)
        if isinstance(v, list):
            return v
        raise XPathError("the XPath expression does not give a node-set")

    def eval_string(self, e, node, position, size):
        if e.attr is not None:
            # a bare @name: the string-value of the first such attribute, read in place
            raw = at.attr_raw(self.d, node.tag, e.attr) if node.kind == ELEMENT else ""
            return dec.decode(raw, True) if "&" in raw or "\r" in raw else raw
        return self.to_str(self.eval_expr(e, node, position, size))

    def eval_number(self, e, node, position, size):
        return self.to_number(self.eval_expr(e, node, position, size))

    def eval_bool(self, e, node, position, size):
        op = e.op
        if op == E_LITERAL:
            return e.value != ""
        if op == E_AND:
            return self.eval_bool(e.l, node, position, size) and self.eval_bool(e.r, node, position, size)
        if op == E_OR:
            return self.eval_bool(e.l, node, position, size) or self.eval_bool(e.r, node, position, size)
        if op == E_COMPARE:
            return self.compare(e.cmp, self.eval_expr(e.l, node, position, size),
                                self.eval_expr(e.r, node, position, size))
        if op == E_ATTR_EQUALS:
            # '!=' is true when the attribute exists and differs (a missing attribute is an empty node-set)
            raw = self.attribute_of(node, e.name)
            return raw is not None and at.attr_equals(raw, e.value) != e.negate
        if op == E_ATTR_EXISTS:
            return self.attribute_of(node, e.name) is not None
        if op == E_ATTR_TEST:
            # a missing attribute is an empty node-set, whose string-value is ""
            raw = self.attribute_of(node, e.name) or ""
            if dec.needs_decoding(raw):
                raw = dec.decode(raw, True)
            value = e.l + raw + e.r if e.l or e.r else raw
            fn = e.fn
            if fn == "contains":
                return e.value in value
            if fn == "starts-with":
                return value.startswith(e.value)
            return value.endswith(e.value)
        return _to_boolean(self.eval_expr(e, node, position, size))

    def eval_predicate(self, e, node, position, size):
        """A number compares with the position, anything else is converted to boolean."""
        if e.type == TYPE_BOOLEAN:
            return self.eval_bool(e, node, position, size)
        v = self.eval_expr(e, node, position, size)
        if isinstance(v, float):
            return v == position
        return _to_boolean(v)

    def eval_expr(self, e, node, position, size):
        op = e.op
        if op == E_LITERAL or op == E_NUMBER:
            return e.value
        if op in (E_AND, E_OR, E_COMPARE, E_ATTR_EQUALS, E_ATTR_EXISTS, E_ATTR_TEST):
            return self.eval_bool(e, node, position, size)
        if op == E_ARITH:
            x = self.eval_number(e.l, node, position, size)
            y = self.eval_number(e.r, node, position, size)
            a = e.arith
            if a == ARITH_ADD:
                return x + y
            if a == ARITH_SUB:
                return x - y
            if a == ARITH_MUL:
                return x * y
            if a == ARITH_DIV:
                if y == 0:
                    if x != x or x == 0:
                        return NAN
                    return math.copysign(INF, x) * math.copysign(1.0, y)
                return x / y
            return _fmod(x, y)
        if op == E_NEGATE:
            return -self.eval_number(e.e, node, position, size)
        if op == E_UNION:
            a = self.eval_nodes(e.l, node, position, size)
            b = self.eval_nodes(e.r, node, position, size)
            return self.sort_unique(a + b)
        if op == E_PATH:
            if e.start is not None:
                current = self.eval_nodes(e.start, node, position, size)
            elif e.absolute:
                current = [_ROOT]
            else:
                current = [node]
            for s in e.steps:
                if not current:
                    break
                current = self.apply_step(s, current)
            return current
        if op == E_FILTER:
            return self.filter(self.eval_nodes(e.primary, node, position, size), e.preds)
        return self.eval_function(e, node, position, size)

    def compare(self, op, a, b):
        if isinstance(a, list):
            if isinstance(b, list):
                if not a or not b:
                    return False
                values_b = [self.string_value(n) for n in b]
                for x in a:
                    value = self.string_value(x)
                    for other in values_b:
                        if self.compare_atoms(op, value, other):
                            return True
                return False
            return self.compare_with_nodes(op, a, b, True)
        if isinstance(b, list):
            return self.compare_with_nodes(op, b, a, False)
        return self.compare_atoms(op, a, b)

    def compare_with_nodes(self, op, nodes, other, nodes_on_left):
        if other is True or other is False:
            as_bool = len(nodes) > 0
            return self.compare_atoms(op, as_bool, other) if nodes_on_left else self.compare_atoms(op, other, as_bool)
        is_number = isinstance(other, float)
        for n in nodes:
            s = self.string_value(n)
            value = _string_to_number(s) if is_number else s
            if self.compare_atoms(op, value, other) if nodes_on_left else self.compare_atoms(op, other, value):
                return True
        return False

    def compare_atoms(self, op, a, b):
        if op == OP_EQ or op == OP_NEQ:
            if a is True or a is False or b is True or b is False:
                eq = _to_boolean(a) == _to_boolean(b)
            elif isinstance(a, float) or isinstance(b, float):
                eq = self.to_number(a) == self.to_number(b)
            else:
                eq = self.to_str(a) == self.to_str(b)
            return eq == (op == OP_EQ)
        x = self.to_number(a)
        y = self.to_number(b)
        if op == OP_LT:
            return x < y
        if op == OP_LE:
            return x <= y
        if op == OP_GT:
            return x > y
        return x >= y

    # ----- steps -----

    def apply_step(self, step, inp):
        if len(inp) == 1:
            out = []
            self.select_from(step, inp[0], out)
            return out

        # Nested input nodes (//div//a on divs inside divs, //a/ancestor::* on links in one list) give the same
        # nodes again and again: n nested nodes would collect about n^2 nodes before the duplicates are removed.
        # When the predicates do not depend on the position, a node passes them or not whichever input node it
        # comes from, so the input nodes whose results are already collected are skipped: the work stays linear.
        if not step.positional:
            if step.axis == AXIS_DESCENDANT or step.axis == AXIS_DESCENDANT_OR_SELF:
                if not _matches_text(step.test) and self.in_document_order(inp):
                    return self.sort_unique(self.select_descendants_of_nested(step, inp))
            elif step.axis == AXIS_ANCESTOR or step.axis == AXIS_ANCESTOR_OR_SELF:
                return self.sort_unique(self.select_shared_ancestors(step, inp))

        out = []
        for x in inp:
            self.select_from(step, x, out)
        return self.sort_unique(out)

    def select_descendants_of_nested(self, step, inp):
        """The descendant axes (tags only) on input nodes in document order. A tag that lies in the part of the tag
        list already scanned for an earlier input node is skipped."""
        d = self.d
        ends = d.ends
        count = len(ends)
        out = []
        scanned_to = -1  # the tags before this index were scanned for an earlier input node
        for x in inp:
            if x.kind == ELEMENT or x.kind == COMMENT_NODE:
                t = x.tag
                if t < scanned_to:
                    continue
                self.select_from(step, x, out)
                # where add_descendants stops for this tag
                if x.kind == ELEMENT and d.inner_starts[t] > 0 and d.inner_ends[t] > 0:
                    k = t + 1
                    end = ends[t]
                    while k < count and ends[k] <= end:
                        k += 1
                    if k > scanned_to:
                        scanned_to = k
            else:
                self.select_from(step, x, out)
                if x.kind == ROOT:
                    scanned_to = INF
        return out

    def select_shared_ancestors(self, step, inp):
        """The walk up from an input node stops at an ancestor already reached from an earlier one, because that
        walk has gone on to the root. Only the root and elements are marked: they are the only parents."""
        visited = set()
        out = []
        test = step.test
        for x in inp:
            start = len(out)
            current = x
            if step.axis == AXIS_ANCESTOR_OR_SELF:
                container = current.kind == ROOT or current.kind == ELEMENT
                if container and current.major in visited:
                    continue
                if container:
                    visited.add(current.major)
                if self.matches(test, current, False):
                    out.append(current)
            while True:
                p = self.parent_node(current)
                if p is None or p.major in visited:
                    break
                visited.add(p.major)
                current = p
                if self.matches(test, current, False):
                    out.append(current)
            if step.preds and len(out) > start:
                out[start:] = self.filter(out[start:], step.preds)
        return out

    def select_from(self, step, x, out):
        """Appends the nodes of the axis that pass the test and the predicates, in document order."""
        reverse = _reverse(step.axis)
        if not step.preds and not reverse:
            self.collect(step, x, out)
            return
        selected = []
        self.collect(step, x, selected)
        selected = self.filter(selected, step.preds)
        if reverse:
            selected.reverse()
        out.extend(selected)

    def collect(self, step, x, out):
        """Appends the nodes of the axis that pass the node test, in axis order (reverse axes: nearest first)."""
        test = step.test
        axis = step.axis
        if axis == AXIS_CHILD:
            self.add_children(x, test, out)
        elif axis == AXIS_DESCENDANT:
            self.add_descendants(x, test, out)
        elif axis == AXIS_DESCENDANT_OR_SELF:
            if self.matches(test, x, False):
                out.append(x)
            self.add_descendants(x, test, out)
        elif axis == AXIS_SELF:
            if self.matches(test, x, False):
                out.append(x)
        elif axis == AXIS_PARENT:
            p = self.parent_node(x)
            if p is not None and self.matches(test, p, False):
                out.append(p)
        elif axis == AXIS_ANCESTOR or axis == AXIS_ANCESTOR_OR_SELF:
            current = x
            if axis == AXIS_ANCESTOR_OR_SELF and self.matches(test, current, False):
                out.append(current)
            while True:
                current = self.parent_node(current)
                if current is None:
                    break
                if self.matches(test, current, False):
                    out.append(current)
        elif axis == AXIS_FOLLOWING_SIBLING or axis == AXIS_PRECEDING_SIBLING:
            self.collect_siblings(x, test, out, axis == AXIS_FOLLOWING_SIBLING)
        elif axis == AXIS_FOLLOWING:
            self.collect_following(x, test, out)
        elif axis == AXIS_PRECEDING:
            self.collect_preceding(x, test, out)
        elif axis == AXIS_ATTRIBUTE:
            self.add_attributes(x, test, out)

    @staticmethod
    def in_document_order(nodes):
        for i in range(1, len(nodes)):
            a = nodes[i - 1]
            b = nodes[i]
            if a.major > b.major or (a.major == b.major and a.minor >= b.minor):
                return False
        return True

    def collect_siblings(self, x, test, out, following):
        if x.kind == ROOT or x.kind == ATTRIBUTE:
            return
        d = self.d
        if not _matches_text(test) and x.kind != TEXT:
            # tags only: walk the flat list; deeper tags are inside the siblings, a shallower one ends the parent
            t = x.tag
            depths = d.depths
            depth = depths[t]
            count = len(depths)
            step_by = 1 if following else -1
            i = t + step_by
            while 0 <= i < count:
                if depths[i] < depth:
                    break
                if depths[i] == depth and self.matches_tag_at(test, i):
                    out.append(of_tag(d, i))
                i += step_by
            return
        parent = self.parent_node(x)
        if parent is None:
            return
        siblings = []
        self.add_children(parent, ANY_NODE_TEST, siblings)
        index = -1
        for k, s in enumerate(siblings):
            if s.major == x.major and s.minor == x.minor:
                index = k
                break
        if index < 0:
            return
        if following:
            for s in siblings[index + 1:]:
                if self.matches(test, s, False):
                    out.append(s)
        else:
            for k in range(index - 1, -1, -1):
                if self.matches(test, siblings[k], False):
                    out.append(siblings[k])

    def collect_following(self, x, test, out):
        if x.kind == ROOT:
            return
        d = self.d
        after = _range_end(d, x)
        if _matches_text(test):
            for n in self.all_nodes():
                if _range_start(n) > after and self.matches(test, n, False):
                    out.append(n)
            return
        starts = d.starts
        i = x.tag + 1 if x.kind == ELEMENT or x.kind == COMMENT_NODE else 0
        count = len(starts)
        while i < count:
            if starts[i] > after and self.matches_tag_at(test, i):
                out.append(of_tag(d, i))
            i += 1

    def collect_preceding(self, x, test, out):
        if x.kind == ROOT:
            return
        d = self.d
        before = _range_start(x)
        if _matches_text(test):
            a = self.all_nodes()
            for k in range(len(a) - 1, -1, -1):
                if _range_end(d, a[k]) < before and self.matches(test, a[k], False):
                    out.append(a[k])
            return
        ends = d.ends
        i = x.tag - 1 if x.kind == ELEMENT or x.kind == COMMENT_NODE else len(ends) - 1
        while i >= 0:
            if ends[i] < before and self.matches_tag_at(test, i):
                out.append(of_tag(d, i))
            i -= 1

    def filter(self, nodes, predicates):
        """Applies the predicates one after another; positions are 1-based in the order of the list."""
        for p in predicates:
            size = len(nodes)
            nodes = [n for k, n in enumerate(nodes) if self.eval_predicate(p, n, k + 1, size)]
        return nodes

    # ----- functions -----

    def eval_function(self, e, node, position, size):
        args = e.args
        fn = e.fn
        if fn == "last":
            return float(size)
        if fn == "position":
            return float(position)
        if fn == "count":
            return float(len(self.eval_nodes(args[0], node, position, size)))
        if fn == "name" or fn == "local-name":
            name = ""
            if not args:
                name = self.node_name(node)
            else:
                s = self.eval_nodes(args[0], node, position, size)
                if s:
                    name = self.node_name(s[0])
            if fn == "local-name":
                colon = name.rfind(":")
                return name if colon < 0 else name[colon + 1:]
            return name
        if fn == "string":
            return self.string_value(node) if not args else self.eval_string(args[0], node, position, size)
        if fn == "concat":
            return "".join(self.eval_string(a, node, position, size) for a in args)
        if fn == "starts-with":
            return self.eval_string(args[0], node, position, size).startswith(
                self.eval_string(args[1], node, position, size))
        if fn == "ends-with":
            return self.eval_string(args[0], node, position, size).endswith(
                self.eval_string(args[1], node, position, size))
        if fn == "contains":
            return self.eval_string(args[1], node, position, size) in self.eval_string(args[0], node, position, size)
        if fn == "substring-before":
            s = self.eval_string(args[0], node, position, size)
            i = s.find(self.eval_string(args[1], node, position, size))
            return "" if i < 0 else s[:i]
        if fn == "substring-after":
            s = self.eval_string(args[0], node, position, size)
            t = self.eval_string(args[1], node, position, size)
            i = s.find(t)
            return "" if i < 0 else s[i + len(t):]
        if fn == "substring":
            return self.substring(e, node, position, size)
        if fn == "string-length":
            # UTF-16 units, as .NET counts
            return float(utf16_length(self.string_value(node) if not args
                                      else self.eval_string(args[0], node, position, size)))
        if fn == "normalize-space":
            s = self.string_value(node) if not args else self.eval_string(args[0], node, position, size)
            return " ".join(w for w in SPACE_RUNS.split(s) if w)
        if fn == "translate":
            s = self.eval_string(args[0], node, position, size)
            frm = self.eval_string(args[1], node, position, size)
            to = self.eval_string(args[2], node, position, size)
            mapping = {}
            for k, ch in enumerate(frm):
                if ch not in mapping:
                    mapping[ch] = to[k] if k < len(to) else ""
            if not mapping:
                return s
            return "".join(mapping.get(ch, ch) for ch in s)
        if fn == "lower-case":
            return lower(self.eval_string(args[0], node, position, size))
        if fn == "upper-case":
            return upper(self.eval_string(args[0], node, position, size))
        if fn == "not":
            return not self.eval_bool(args[0], node, position, size)
        if fn == "true":
            return True
        if fn == "false":
            return False
        if fn == "boolean":
            return self.eval_bool(args[0], node, position, size)
        if fn == "number":
            if not args:
                return _string_to_number(self.string_value(node))
            return self.eval_number(args[0], node, position, size)
        if fn == "sum":
            total = 0.0
            for n in self.eval_nodes(args[0], node, position, size):
                total += _string_to_number(self.string_value(n))
            return total
        if fn == "floor":
            return _float_floor(self.eval_number(args[0], node, position, size))
        if fn == "ceiling":
            return _float_ceil(self.eval_number(args[0], node, position, size))
        if fn == "round":
            return _round(self.eval_number(args[0], node, position, size))
        raise XPathError(f"unknown XPath function {fn}")

    def substring(self, e, node, position, size):
        """XPath 1.0: the chars at 1-based positions p with round(start) <= p < round(start) + round(length)."""
        args = e.args
        s = self.eval_string(args[0], node, position, size)
        start = _round(self.eval_number(args[1], node, position, size))
        to = start + _round(self.eval_number(args[2], node, position, size)) if len(args) > 2 else INF
        if start != start or to != to:
            return ""
        n = len(s)
        if start <= 1:
            first = 0
        elif start > n:
            first = n
        else:
            first = int(start) - 1
        if to >= n + 1:
            end = n
        elif to <= 1:
            end = 0
        else:
            end = int(to) - 1
        if end <= first:
            return ""
        return s[first:end]


class XPathMatcher:
    """A compiled expression and its evaluation."""

    __slots__ = ("expression", "expr")

    def __init__(self, expression):
        self.expression = expression
        self.expr = compile_xpath(expression)

    @property
    def is_node_set(self):
        return self.expr.type == TYPE_NODE_SET

    def check_node_set(self):
        if self.expr.type != TYPE_NODE_SET:
            raise XPathError(f"the XPath {quote(self.expression)} gives a {TYPE_NAMES[self.expr.type]}, not tags; "
                             "use xpath_value()")

    @staticmethod
    def context(d, i):
        return _ROOT if i is None else of_tag(d, i)

    def tags(self, d, i):
        """The indexes of the selected tags (elements and comments) in document order."""
        self.check_node_set()
        out = []
        for n in Evaluator(d).eval_expr(self.expr, self.context(d, i), 1, 1):
            if n.kind == ELEMENT or n.kind == COMMENT_NODE:
                out.append(n.tag)
        return out

    def values(self, d, i):
        ev = Evaluator(d)
        result = ev.eval_expr(self.expr, self.context(d, i), 1, 1)
        if not isinstance(result, list):
            return [ev.to_str(result)]
        return [ev.string_value(n) for n in result]

    def value(self, d, i):
        ev = Evaluator(d)
        result = ev.eval_expr(self.expr, self.context(d, i), 1, 1)
        if isinstance(result, list):
            return ev.string_value(result[0]) if result else None
        return ev.to_str(result)
