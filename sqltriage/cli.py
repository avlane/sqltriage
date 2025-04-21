"""Command line entry point."""
import argparse
import os
import sys

from . import __version__
from . import config
from .checks import CHECKS
from .connect import build_connection_string
from .context import Context, ManualClock
from .render import FORMATS
from .models import LEVELS, severity_rank
from .report import build_report, filter_report
from .source import FixtureSource, PyodbcSource, RecordingSource


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sqltriage",
        description="Ranked triage report for a SQL Server incident.",
    )
    parser.add_argument("--version", action="version", version="sqltriage " + __version__)
    parser.add_argument("--config", metavar="FILE", help="TOML settings (default: ./sqltriage.toml, then ~/.config/sqltriage.toml)")
    parser.add_argument("-S", "--server", default="localhost", help="instance to connect to")
    parser.add_argument("-d", "--database", default="master", help="initial database")
    parser.add_argument("-U", "--user", help="SQL login (default is a trusted connection)")
    parser.add_argument("--driver", help="ODBC driver name (default: newest installed of 18 and 17)")
    parser.add_argument("--no-encrypt", action="store_true", help="connect without encryption")
    parser.add_argument("--trust-server-cert", action="store_true", help="accept a self-signed server certificate")
    parser.add_argument("--record", metavar="DIR", help="also save every query result to DIR as replayable fixtures")
    parser.add_argument("--fixtures", metavar="DIR", help="replay recorded query results instead of connecting")
    parser.add_argument("-i", "--interval", type=float, default=10.0, help="seconds between wait stat samples")
    parser.add_argument("--samples", type=int, default=1, help="how many consecutive intervals to sample (shows a trend)")
    parser.add_argument("-n", "--top", type=int, default=5, help="how many waits to report")
    parser.add_argument("-f", "--format", choices=sorted(FORMATS), help="text (default), json or markdown")
    parser.add_argument("-o", "--output", metavar="FILE", help="write the report to FILE instead of standard output")
    parser.add_argument("--min-severity", choices=[label for _, label in reversed(LEVELS)], default="info",
                        help="hide findings ranked below this severity")
    parser.add_argument("--databases", help="comma separated databases to look at (default: all)")
    parser.add_argument("--exclude-databases", help="comma separated databases to leave out")
    parser.add_argument("--ignore-jobs", help="comma separated Agent job name patterns to skip, such as 'Purge*'")
    parser.add_argument("--fail-on", choices=[label for _, label in reversed(LEVELS)],
                        help="exit with status 1 if any finding is at least this severe")
    parser.add_argument("--blocking-min-wait", type=float, metavar="SECONDS",
                        help="ignore sessions blocked for less than this long (default 2)")
    parser.add_argument("--only", help="comma separated checks to run: " + ",".join(CHECKS))
    return parser


def connection_string(args):
    return build_connection_string(
        args.server,
        args.database,
        user=args.user,
        driver=args.driver,
        encrypt=not args.no_encrypt,
        trust_server_certificate=args.trust_server_cert,
    )


EXTENSION_FORMATS = {".md": "markdown", ".markdown": "markdown", ".json": "json", ".txt": "text"}


def choose_format(args):
    """--format if given, else guessed from the output file's extension, else text."""
    if args.format:
        return args.format
    if args.output:
        return EXTENSION_FORMATS.get(os.path.splitext(args.output)[1].lower(), "text")
    return "text"


def write_output(path, text):
    """Write the report next to its final name first, so a reader never sees half of it."""
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temporary, path)


def main(argv=None, out=None):
    out = out or sys.stdout
    parser = build_parser()
    options = {}
    path = config.find(parser.parse_known_args(argv)[0].config)
    if path:
        try:
            settings = config.load(path)
        except config.ConfigError as exc:
            sys.stderr.write("{}\n".format(exc))
            return 2
        parser.set_defaults(**config.argument_defaults(settings))
        options = config.context_options(settings)
    args = parser.parse_args(argv)
    names = None
    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip()]
        unknown = [n for n in names if n not in CHECKS]
        if unknown:
            sys.stderr.write("unknown check: {}\n".format(", ".join(unknown)))
            return 2
    if args.fixtures and args.record:
        sys.stderr.write("--record needs a live connection, not --fixtures\n")
        return 2
    if args.fixtures:
        source = FixtureSource(args.fixtures)
    else:
        source = PyodbcSource(connection_string(args))
        if args.record:
            source = RecordingSource(source, args.record)
    options = dict(options)
    for name in ("databases", "exclude_databases", "ignore_jobs"):
        value = getattr(args, name)
        if value:
            options[name] = [d.strip() for d in value.split(",") if d.strip()]
    if args.blocking_min_wait is not None:
        options["blocking_min_wait_ms"] = int(args.blocking_min_wait * 1000)
    ctx = Context(source, interval=args.interval, top=args.top, samples=max(1, args.samples), options=options)
    if args.fixtures:
        clock = ManualClock()  # recorded samples are already apart in time
        ctx.sleep, ctx.clock = clock.sleep, clock.now
    try:
        report = build_report(ctx, args.server, names)
    finally:
        source.close()
    failing = args.fail_on is not None and any(f.severity_rank >= severity_rank(args.fail_on) for f in report.findings)
    if args.min_severity != "info":
        report = filter_report(report, args.min_severity)
    text = FORMATS[choose_format(args)](report)
    if args.output:
        write_output(args.output, text)
        sys.stderr.write("wrote {}\n".format(args.output))
    else:
        out.write(text)
    return 1 if failing else 0
