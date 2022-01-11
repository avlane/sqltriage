"""Command line entry point."""
import argparse

from . import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sqltriage",
        description="Ranked triage report for a SQL Server incident.",
    )
    parser.add_argument("--version", action="version", version="sqltriage " + __version__)
    return parser


def main(argv=None):
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
