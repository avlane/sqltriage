"""Command line entry point."""
import argparse
import os
import sys

from . import __version__
from .checks import CHECKS
from .context import Context
from .render import FORMATS
from .report import build_report
from .source import FixtureSource, PyodbcSource


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sqltriage",
        description="Ranked triage report for a SQL Server incident.",
    )
    parser.add_argument("--version", action="version", version="sqltriage " + __version__)
    parser.add_argument("-S", "--server", default="localhost", help="instance to connect to")
    parser.add_argument("-d", "--database", default="master", help="initial database")
    parser.add_argument("-U", "--user", help="SQL login (default is a trusted connection)")
    parser.add_argument("--fixtures", metavar="DIR", help="replay recorded query results instead of connecting")
    parser.add_argument("-i", "--interval", type=float, default=10.0, help="seconds between wait stat samples")
    parser.add_argument("-n", "--top", type=int, default=5, help="how many waits to report")
    parser.add_argument("-f", "--format", choices=sorted(FORMATS), default="text")
    parser.add_argument("--only", help="comma separated checks to run: " + ",".join(CHECKS))
    return parser


def connection_string(args):
    parts = [
        "DRIVER={ODBC Driver 17 for SQL Server}",
        "SERVER=" + args.server,
        "DATABASE=" + args.database,
        "APP=sqltriage",
    ]
    if args.user:
        parts.append("UID=" + args.user)
        parts.append("PWD=" + os.environ.get("SQLTRIAGE_PASSWORD", ""))
    else:
        parts.append("Trusted_Connection=yes")
    return ";".join(parts)


def main(argv=None, out=None):
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    names = None
    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip()]
        unknown = [n for n in names if n not in CHECKS]
        if unknown:
            sys.stderr.write("unknown check: {}\n".format(", ".join(unknown)))
            return 2
    if args.fixtures:
        source = FixtureSource(args.fixtures)
    else:
        source = PyodbcSource(connection_string(args))
    ctx = Context(source, interval=args.interval, top=args.top)
    try:
        report = build_report(ctx, args.server, names)
    finally:
        source.close()
    out.write(FORMATS[args.format](report))
    return 0
