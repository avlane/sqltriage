"""Builds the incident report the golden files are made from."""
import datetime
import os

from sqltriage.context import Context, ManualClock
from sqltriage.report import build_report
from sqltriage.source import FixtureSource

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "incident1")
GOLDEN = os.path.join(HERE, "golden")
WHEN = datetime.datetime(2023, 3, 14, 10, 0, 0)


def incident_report():
    clock = ManualClock()
    ctx = Context(FixtureSource(FIXTURES), interval=10, top=5, sleep=clock.sleep, clock=clock.now)
    return build_report(ctx, "SQLPROD01", now=WHEN)
