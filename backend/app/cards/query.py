"""Scryfall search syntax, answered from the local card index.

scryfall_search used to go to the web for every call. The model's queries
use a small part of the syntax (measured over its history: o: 55 times, t:
33, legal: 26, id: 21, set: 12, then names, numbers and blocks), and the
index holds everything those need. This parses that part into SQL; anything
else raises Unsupported and the caller goes to Scryfall as before.

Semantics follow Scryfall's: c: means "includes these colours", id: means
"within this colour identity", = is exact, and juxtaposed terms AND together
with OR, parentheses and a leading - for NOT.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

from app.cards.store import _COLUMN_NAMES, _columns, _has_printings, _row_to_card
from app.db.session import get_engine


class Unsupported(ValueError):
    """The query uses syntax the local index cannot answer."""


_COLOUR_WORDS = {
    "white": "W", "blue": "U", "black": "B", "red": "R", "green": "G", "colorless": "", "c": "",
    "azorius": "WU", "dimir": "UB", "rakdos": "BR", "gruul": "RG", "selesnya": "GW",
    "orzhov": "WB", "izzet": "UR", "golgari": "BG", "boros": "RW", "simic": "GU",
    "bant": "GWU", "esper": "WUB", "grixis": "UBR", "jund": "BRG", "naya": "RGW",
    "abzan": "WBG", "jeskai": "URW", "sultai": "BGU", "mardu": "RWB", "temur": "GUR",
}
_RARITY = {"c": 0, "common": 0, "u": 1, "uncommon": 1, "r": 2, "rare": 2, "m": 3, "mythic": 3,
           "s": 4, "special": 4, "b": 5, "bonus": 5}
_RARITY_SQL = ("CASE c.rarity WHEN 'common' THEN 0 WHEN 'uncommon' THEN 1 WHEN 'rare' THEN 2 "
               "WHEN 'mythic' THEN 3 WHEN 'special' THEN 4 WHEN 'bonus' THEN 5 END")
# Display and printing options: they change what Scryfall shows, not which
# cards match, so the local answer ignores them.
_IGNORED = {"game", "unique", "st", "include", "order", "dir", "prefer", "display", "lang", "in"}
_COMMANDER_ELIGIBLE = (
    "(c.type_line LIKE 'Legendary%Creature%' OR c.oracle_text LIKE '%can be your commander%')"
)

_KEY = re.compile(r"([A-Za-z]+)(!=|<=|>=|:|=|<|>)")
_IGNORE = object()


def _tokens(query: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    i, n = 0, len(query)

    def value_at(j: int) -> tuple[str, int]:
        if j < n and query[j] == "/":
            # A /regex/ value, kept with its slashes so the term knows.
            end = query.find("/", j + 1)
            while end != -1 and query[end - 1] == "\\":
                end = query.find("/", end + 1)
            if end == -1:
                raise Unsupported("unclosed regex")
            return query[j:end + 1], end + 1
        if j < n and query[j] in "\"'":
            end = query.find(query[j], j + 1)
            if end == -1:
                raise Unsupported("unclosed quote")
            return query[j + 1:end], end + 1
        k = j
        while k < n and not query[k].isspace() and query[k] not in "()":
            k += 1
        return query[j:k], k

    while i < n:
        ch = query[i]
        if ch.isspace():
            i += 1
        elif ch in "()":
            out.append((ch, None))
            i += 1
        elif ch == "-" and i + 1 < n and not query[i + 1].isspace():
            out.append(("not", None))
            i += 1
        elif ch == "!":
            value, i = value_at(i + 1)
            out.append(("exact", value))
        else:
            m = _KEY.match(query, i)
            if m:
                value, i = value_at(m.end())
                out.append(("term", (m.group(1).lower(), m.group(2), value)))
            else:
                value, i = value_at(i)
                if value.lower() == "or":
                    out.append(("or", None))
                elif value.lower() == "and":
                    continue
                else:
                    out.append(("term", ("name", ":", value)))
    return out


class _Compiler:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}
        self.uses_printings = False

    def param(self, value: Any) -> str:
        name = f"p{len(self.params)}"
        self.params[name] = value
        return f":{name}"

    def parse(self, tokens: list[tuple[str, Any]]) -> str:
        self.tokens, self.pos = tokens, 0
        sql = self._or()
        if self.pos != len(self.tokens):
            raise Unsupported("unbalanced parentheses")
        return "1=1" if sql is _IGNORE else sql

    def _peek(self) -> str | None:
        return self.tokens[self.pos][0] if self.pos < len(self.tokens) else None

    def _or(self):
        parts = [self._and()]
        while self._peek() == "or":
            self.pos += 1
            parts.append(self._and())
        if len(parts) == 1:
            return parts[0]
        if any(p is _IGNORE for p in parts):
            return _IGNORE
        return "(" + " OR ".join(parts) + ")"

    def _and(self):
        parts = []
        while self._peek() not in (None, ")", "or"):
            parts.append(self._unary())
        kept = [p for p in parts if p is not _IGNORE]
        if not parts:
            raise Unsupported("empty group")
        if not kept:
            return _IGNORE
        return kept[0] if len(kept) == 1 else "(" + " AND ".join(kept) + ")"

    def _unary(self):
        kind = self._peek()
        if kind == "not":
            self.pos += 1
            inner = self._unary()
            return _IGNORE if inner is _IGNORE else f"NOT ({inner})"
        if kind == "(":
            self.pos += 1
            inner = self._or()
            if self._peek() != ")":
                raise Unsupported("unbalanced parentheses")
            self.pos += 1
            return inner
        kind, value = self.tokens[self.pos]
        self.pos += 1
        if kind == "exact":
            p = self.param(value.lower())
            return f"(lower(c.name) = {p} OR lower(c.name) LIKE {p} || ' // %')"
        if kind == "term":
            return self._term(*value)
        raise Unsupported(f"unexpected {kind}")

    def _like(self, column: str, value: str) -> str:
        return f"lower(COALESCE({column}, '')) LIKE {self.param('%' + value.lower() + '%')}"

    def _term(self, key: str, op: str, value: str):
        if key in _IGNORED:
            return _IGNORE
        if _is_regex(value) and key in ("o", "oracle", "fo", "fulloracle", "t", "type", "name", "n"):
            self._only_colon(key, op)
            column = {"t": "c.type_line", "type": "c.type_line", "name": "c.name", "n": "c.name"}.get(
                key, _TEXT)
            try:
                re.compile(value[1:-1])
            except re.error as exc:
                raise Unsupported(f"regex {value}") from exc
            return f"regexp({self.param(value[1:-1])}, {column})"
        if key in ("o", "oracle", "fo", "fulloracle"):
            self._only_colon(key, op)
            # "~" stands for the card's own name, as on Scryfall.
            p = self.param(value.lower())
            # Reminder text excluded, as on Scryfall; the full text until the
            # index is re-imported with oracle_plain.
            return (f"lower({_TEXT}) LIKE "
                    f"'%' || replace({p}, '~', lower(c.name)) || '%'")
        if key in ("t", "type"):
            self._only_colon(key, op)
            return self._like("c.type_line", value)
        if key in ("name", "n"):
            self._only_colon(key, op)
            return self._like("c.name", value)
        if key in ("kw", "keyword"):
            self._only_colon(key, op)
            return self._like("c.keywords", '"' + value + '"')
        if key in ("c", "color", "colour"):
            return self._colours("c.colors", op, value, default=">=")
        if key in ("id", "identity", "ci", "commander", "cmd"):
            return self._colours("c.color_identity", op, value, default="<=")
        if key in ("legal", "f", "format"):
            if value.lower() in ("commander", "edh") and op in (":", "="):
                return "c.legal_commander = 1"
            raise Unsupported(f"{key}:{value}")
        if key in ("cmc", "mv", "manavalue"):
            return self._number("c.cmc", op, value)
        if key in ("pow", "power"):
            return self._number("c.power", op, value, text_column=True)
        if key in ("tou", "toughness"):
            return self._number("c.toughness", op, value, text_column=True)
        if key in ("loy", "loyalty"):
            return self._number("c.loyalty", op, value, text_column=True)
        if key in ("r", "rarity"):
            if value.lower() not in _RARITY:
                raise Unsupported(f"rarity {value}")
            sql_op = {":": "=", "=": "=", "!=": "!="}.get(op, op)
            return f"{_RARITY_SQL} {sql_op} {_RARITY[value.lower()]}"
        if key in ("set", "s", "e", "edition", "b", "block"):
            self._only_colon(key, op)
            self.uses_printings = True
            code, name = self.param(value.lower()), self.param(value.lower())
            where = (f"s.code = {code} OR s.block_code = {code}" if key in ("set", "s", "e", "edition")
                     else f"s.block_code = {code} OR lower(s.block) = {name}")
            return (f"c.oracle_id IN (SELECT p.oracle_id FROM card_printings p "
                    f"JOIN card_sets s ON s.code = p.set_code WHERE {where})")
        if key in ("otag", "oracletag", "function"):
            self._only_colon(key, op)
            return (f"EXISTS (SELECT 1 FROM card_tags g WHERE g.oracle_id = c.oracle_id "
                    f"AND g.slug = {self.param(value.lower())})")
        if key == "produces":
            letters = self._letters(value)
            return " AND ".join(f"c.produced_mana LIKE '%\"{x}\"%'" for x in letters) or "1=1"
        if key == "is":
            if value.lower() == "commander":
                return f"({_COMMANDER_ELIGIBLE} AND c.legal_commander = 1)"
            raise Unsupported(f"is:{value}")
        raise Unsupported(f"{key}{op}")

    @staticmethod
    def _only_colon(key: str, op: str) -> None:
        if op not in (":", "="):
            raise Unsupported(f"{key}{op}")

    @staticmethod
    def _letters(value: str) -> str:
        word = value.lower()
        if word in _COLOUR_WORDS:
            return _COLOUR_WORDS[word]
        if not re.fullmatch(r"[wubrgc]+", word):
            raise Unsupported(f"colour {value}")
        return word.upper().replace("C", "")

    def _colours(self, column: str, op: str, value: str, *, default: str) -> str:
        letters = set(self._letters(value))
        if op == ":":
            op = default
        within = " AND ".join(f"instr({column}, '{x}') = 0" for x in "WUBRG" if x not in letters) or "1=1"
        includes = " AND ".join(f"instr({column}, '{x}') > 0" for x in letters) or "1=1"
        exact = f"({within} AND {includes})"
        if op == "<=":
            return f"({within})"
        if op == ">=":
            return f"({includes})"
        if op == "=":
            return exact
        if op == "!=":
            return f"NOT {exact}"
        if op == "<":
            return f"(({within}) AND NOT {exact})"
        if op == ">":
            return f"(({includes}) AND NOT {exact})"
        raise Unsupported(f"colour {op}")

    def _number(self, column: str, op: str, value: str, *, text_column: bool = False) -> str:
        try:
            number = float(value)
        except ValueError as exc:
            raise Unsupported(f"{column} {value}") from exc
        sql_op = {":": "=", "!=": "!="}.get(op, op)
        target = f"CAST({column} AS REAL)" if text_column else column
        guard = f"{column} GLOB '[0-9]*' AND " if text_column else ""
        return f"({guard}{target} {sql_op} {self.param(number)})"


# Rules text without reminder text once the index is re-imported with it
# (INDEX_VERSION 4); plain rules text before that. Resolved per query, since
# the column appears on the first refresh after upgrading.
_TEXT = "COALESCE(c.oracle_plain, c.oracle_text, '')"
_TEXT_OLD = "COALESCE(c.oracle_text, '')"


def _is_regex(value: str) -> bool:
    return len(value) >= 2 and value.startswith("/") and value.endswith("/")


def _regexp(pattern: str, value: str | None) -> bool:
    # Case-insensitive, like Scryfall's /regex/ search.
    return value is not None and re.search(pattern, value, re.IGNORECASE) is not None


_PLAIN_TEXT: bool | None = None


def _has_plain_text() -> bool:
    global _PLAIN_TEXT
    if not _PLAIN_TEXT:
        with get_engine().begin() as conn:
            _PLAIN_TEXT = any(r[1] == "oracle_plain" for r in conn.execute(text("PRAGMA table_info(cards)")))
    return _PLAIN_TEXT


def compile_query(query: str) -> tuple[str, dict[str, Any]]:
    """``(where_sql, params)`` for ``query``, or Unsupported."""
    compiler = _Compiler()
    where = compiler.parse(_tokens(query))
    if compiler.uses_printings and not _has_printings():
        raise Unsupported("set membership is not imported yet")
    return where, compiler.params


def search(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Cards matching a Scryfall-syntax query, most played first, one per name."""
    where, params = compile_query(query)
    if not _has_plain_text():
        where = where.replace(_TEXT, _TEXT_OLD)
    sql = f"""
        SELECT {_columns('c')} FROM cards c
        WHERE c.playable = 1 AND ({where})
        ORDER BY CASE WHEN c.edhrec_rank IS NULL THEN 1 ELSE 0 END, c.edhrec_rank
        LIMIT :limit
    """
    params["limit"] = limit * 3
    with get_engine().begin() as conn:
        # SQLite has no REGEXP of its own; registered per connection.
        conn.connection.driver_connection.create_function("regexp", 2, _regexp, deterministic=True)
        rows = conn.execute(text(sql), params).fetchall()
    out, seen = [], set()
    for row in rows:
        card = _row_to_card(row[: len(_COLUMN_NAMES)])
        if card["name"] in seen:
            continue
        seen.add(card["name"])
        out.append(card)
        if len(out) >= limit:
            break
    return out
