"""Kleiner Auswerter für die Excel-Formeln des Artikel-Exports (nur für Tests).

openpyxl speichert Formeln, rechnet sie aber nicht. Damit die Tests prüfen können, dass die Formeln
dasselbe ergeben wie die App – und nach einer Änderung in der Datei neu rechnen –, wertet dieses Modul
den verwendeten Ausschnitt aus: + - * / ^, Vergleiche, Text, Zellbezüge und Bereiche (auch auf andere
Blätter) sowie IF, AND, N, ISNUMBER, IFERROR, VLOOKUP, ROUND, PI, COUNT, SUM.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Any

from openpyxl.utils import column_index_from_string

_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<str>\"[^\"]*\")|(?P<ref>(?:[A-Za-z_][\w]*!)?\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)"
    r"|(?P<bool>TRUE|FALSE)\b(?!\()|(?P<name>[A-Z][A-Z0-9.]*)(?=\()|(?P<op><>|>=|<=|[-+*/^=<>(),]))"
)


class XlError(Exception):
    """#WERT!, #NV …"""


def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"Formel nicht verstanden ab: {text[pos:]}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


def _number(v: Any) -> float:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, timedelta):
        return v.total_seconds() / 86400
    if isinstance(v, datetime):
        return (v - datetime(1899, 12, 30)).total_seconds() / 86400
    raise XlError(f"keine Zahl: {v!r}")


class Evaluator:
    def __init__(self, workbook: Any):
        self.wb = workbook

    def cell(self, sheet: str, ref: str) -> Any:
        value = self.wb[sheet][ref.replace("$", "")].value
        if isinstance(value, str) and value.startswith("="):
            return self.formula(sheet, value[1:])
        return value

    def formula(self, sheet: str, text: str) -> Any:
        self.sheet, self.toks, self.i = sheet, _tokens(text), 0
        value = self._compare()
        if self.i != len(self.toks):
            raise ValueError(f"Rest der Formel: {self.toks[self.i:]}")
        return value

    # --- Parser --------------------------------------------------------------------------------

    def _peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _take(self) -> tuple[str, str]:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def _compare(self) -> Any:
        left = self._add()
        tok = self._peek()
        if tok and tok[1] in ("=", "<>", ">", "<", ">=", "<="):
            op = self._take()[1]
            right = self._add()
            if isinstance(left, str) or isinstance(right, str):
                a, b = str(left).lower(), str(right).lower()
            else:
                a, b = _number(left), _number(right)
            return {"=": a == b, "<>": a != b, ">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
        return left

    def _add(self) -> Any:
        value = self._mul()
        while (tok := self._peek()) and tok[1] in "+-" and tok[0] == "op":
            op = self._take()[1]
            right = self._mul()
            value = _number(value) + _number(right) if op == "+" else _number(value) - _number(right)
        return value

    def _mul(self) -> Any:
        value = self._pow()
        while (tok := self._peek()) and tok[1] in "*/" and tok[0] == "op":
            op = self._take()[1]
            right = self._pow()
            if op == "/" and _number(right) == 0:
                raise XlError("#DIV/0!")
            value = _number(value) * _number(right) if op == "*" else _number(value) / _number(right)
        return value

    def _pow(self) -> Any:
        value = self._unary()
        while (tok := self._peek()) and tok[1] == "^":
            self._take()
            value = _number(value) ** _number(self._unary())
        return value

    def _unary(self) -> Any:
        tok = self._peek()
        if tok and tok[1] == "-":
            self._take()
            return -_number(self._unary())
        return self._primary()

    def _primary(self) -> Any:
        kind, text = self._take()
        if kind == "num":
            return float(text)
        if kind == "str":
            return text[1:-1]
        if kind == "bool":
            return text == "TRUE"
        if kind == "op" and text == "(":
            value = self._compare()
            self._take()  # )
            return value
        if kind == "ref":
            return self._ref(text)
        if kind == "name":
            self._take()  # (
            args: list[Any] = []
            if self._peek()[1] != ")":
                while True:
                    start = self.i
                    try:
                        args.append(self._compare())
                    except XlError as exc:
                        args.append(exc)  # IFERROR fängt Fehler der Argumente
                        self.i = self._skip_arg(start)
                    if self._take()[1] == ")":
                        break
            else:
                self._take()
            return self._call(text, args)
        raise ValueError(f"unerwartet: {text}")

    def _skip_arg(self, start: int) -> int:
        depth, i = 0, start
        while i < len(self.toks):
            t = self.toks[i][1]
            if t == "(":
                depth += 1
            elif t == ")":
                if depth == 0:
                    return i
                depth -= 1
            elif t == "," and depth == 0:
                return i
            i += 1
        return i

    def _ref(self, text: str) -> Any:
        sheet = self.sheet
        if "!" in text:
            sheet, text = text.split("!")
        if ":" not in text:
            saved = (self.sheet, self.toks, self.i)
            try:
                return self.cell(sheet, text)
            finally:
                self.sheet, self.toks, self.i = saved
        a, b = text.replace("$", "").split(":")
        ca, ra = re.match(r"([A-Z]+)(\d+)", a).groups()
        cb, rb = re.match(r"([A-Z]+)(\d+)", b).groups()
        ws = self.wb[sheet]
        return [
            [ws.cell(row=r, column=c).value for c in range(column_index_from_string(ca), column_index_from_string(cb) + 1)]
            for r in range(int(ra), int(rb) + 1)
        ]

    def _call(self, name: str, args: list[Any]) -> Any:
        if name == "IFERROR":
            return args[1] if isinstance(args[0], XlError) else args[0]
        if name == "IF":  # wie Excel: nur der gewählte Zweig zählt
            if isinstance(args[0], XlError):
                raise args[0]
            chosen = args[1] if args[0] else (args[2] if len(args) > 2 else False)
            if isinstance(chosen, XlError):
                raise chosen
            return chosen
        for a in args:
            if isinstance(a, XlError):
                raise a
        if name == "AND":
            return all(args)
        if name == "N":
            return args[0] if isinstance(args[0], (int, float)) and not isinstance(args[0], bool) else 0.0
        if name == "ISNUMBER":
            return isinstance(args[0], (int, float, timedelta)) and not isinstance(args[0], bool)
        if name == "PI":
            return math.pi
        if name == "ROUND":
            return round(_number(args[0]), int(_number(args[1])))
        if name == "COUNT":
            return float(sum(isinstance(a, (int, float, timedelta)) and not isinstance(a, bool) for a in args))
        if name == "SUM":
            if len(args) == 1 and isinstance(args[0], list):
                values = [v for row in args[0] for v in row]
            else:
                values = args
            return sum(_number(v) for v in values if isinstance(v, (int, float, timedelta)) and not isinstance(v, bool))
        if name == "VLOOKUP":
            needle, table, index = args[0], args[1], int(_number(args[2]))
            for row in table:
                if row[0] is not None and str(row[0]).lower() == str(needle).lower():
                    return row[index - 1]
            raise XlError("#NV")
        raise ValueError(f"Funktion {name} nicht unterstützt")
