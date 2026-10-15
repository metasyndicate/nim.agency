"""
`nim.agency lex` - the NIM flavor vocabulary (lex/*.csv).

    lex groups [--kind word|phrase|sentence|template|record]
    lex show GROUP [--column NAME]
    lex random GROUP [GROUP ...]          one value from each, in order
    lex salad [WORDS]                     one value from each of WORDS distinct word tables
    lex name [--title | --no-title]       given name + surname, honorific or post-nominal
    lex check                             files against lex/csv-metadata.json (CSVW)

random, salad, and name take --count N and --seed S (repeatable output).
Cross-platform port of lex/taxonomy.sh (lib/data/lex.py).
"""

import argparse
import random
import sys

from ..data import lex


def _rng(args) -> random.Random:
    return random.Random(args.seed)


def _repeat(args, make) -> int:
    rng = _rng(args)
    try:
        for _ in range(args.count):
            print(make(rng))
    except lex.LexError as exc:
        print(f"lex: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_groups(args) -> int:
    lexicon = lex.default()
    for name in lexicon.groups(args.kind):
        t = lexicon.tables[name]
        print(f"  {name:<14} {t.kind:<9} {len(t.rows()):>4}  {t.title}")
    return 0


def cmd_show(args) -> int:
    try:
        table = lex.default().table(args.group)
        values = table.values(args.column)
    except lex.LexError as exc:
        print(f"lex: {exc}", file=sys.stderr)
        return 2
    print(f"{table.title} ({table.path.name}, {table.kind})")
    if table.description:
        print(f"  {table.description}")
    print(f"  columns: {', '.join(c.name for c in table.columns)}\n")
    for value in values:
        print(f"  {'; '.join(value) if isinstance(value, list) else value}")
    return 0


def cmd_random(args) -> int:
    return _repeat(args, lambda rng: lex.default().phrase(args.groups, rng))


def cmd_salad(args) -> int:
    return _repeat(args, lambda rng: lex.default().salad(args.words, rng))


def cmd_name(args) -> int:
    return _repeat(args, lambda rng: lex.default().name(rng, args.title))


def cmd_check(args) -> int:
    lexicon = lex.default()
    problems = lexicon.check()
    for p in problems:
        print(f"  {p}")
    print(f"{len(lexicon.tables)} table(s) described, {len(problems)} problem(s)")
    return 1 if problems else 0


def add_parser(subparsers):
    p = subparsers.add_parser(
        "lex", help="Flavor vocabulary: groups, show, random, salad, name, check")
    actions = p.add_subparsers(dest="action", required=True)

    groups = actions.add_parser("groups", help="List the vocabulary tables")
    groups.add_argument("--kind", choices=lex.KINDS, help="Only tables of this row unit")
    groups.set_defaults(func=cmd_groups)

    show = actions.add_parser("show", help="Print one table's values")
    show.add_argument("group")
    show.add_argument("--column", help="Column name (default: the first)")
    show.set_defaults(func=cmd_show)

    draw = _draw_flags()
    rnd = actions.add_parser("random", parents=[draw],
                             help="One value from each named table, in order")
    rnd.add_argument("groups", nargs="+", metavar="GROUP")
    rnd.set_defaults(func=cmd_random)

    salad = actions.add_parser("salad", parents=[draw],
                               help="One value from each of N distinct word tables")
    salad.add_argument("words", nargs="?", type=int, default=3)
    salad.set_defaults(func=cmd_salad)

    name = actions.add_parser("name", parents=[draw],
                              help="A name with an honorific or post-nominal letters")
    title = name.add_mutually_exclusive_group()
    title.add_argument("--title", dest="title", action="store_true", default=None,
                       help="Always an honorific")
    title.add_argument("--no-title", dest="title", action="store_false",
                       help="Never an honorific (post-nominal letters possible)")
    name.set_defaults(func=cmd_name)

    check = actions.add_parser("check", help="Check lex/ against lex/csv-metadata.json")
    check.set_defaults(func=cmd_check)


def _draw_flags() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--count", type=int, default=1, help="How many (default: 1)")
    parent.add_argument("--seed", type=int, help="Seed for repeatable output")
    return parent
