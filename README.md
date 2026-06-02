# sqltriage

A command line tool for the first ten minutes of a SQL Server incident. You
point it at an instance, it samples a handful of DMVs, and it prints a short,
ranked list of the things worth looking at, each with the evidence and a
suggested next step.

It only reads. Everything it runs is a `SELECT` against DMVs and system views,
and the only waiting it does is the sample interval for the wait stats delta.

## What it checks

| check        | looks at                                                                      |
|--------------|-------------------------------------------------------------------------------|
| `waits`      | `sys.dm_os_wait_stats` sampled twice; idle waits dropped, the rest explained  |
| `blocking`   | blocked sessions as a tree, the objects they wait on, the head blocker's input buffer and transaction age |
| `querystore` | regressed queries (recent against baseline), their waits, forced plan failures, Query Store health |
| `tempdb`     | PFS / GAM / SGAM page latch contention, file layout, space use, version store |
| `vlf`        | VLF counts (`sys.dm_db_log_info`), log growth settings, `log_reuse_wait_desc` |
| `counters`   | deadlocks, lock timeouts and compile rate over the sample, from the performance counters |
| `agent`      | failed jobs with the failing step's error, jobs running far past their usual time |
| `ag`         | replica connection and sync health, send and redo queues, suspended movement, lag |
| `pvs`        | persisted version store (Accelerated Database Recovery) growth, SQL Server 2019+ |
| `memory`     | queries waiting for a memory grant, and grants far larger than they use       |
| `cpu`        | runnable queue per scheduler, signal waits, imbalance, the heaviest requests  |

## Usage

    pip install pyodbc
    python -m sqltriage -S SQLPROD01
    python -m sqltriage -S SQLPROD01 -i 30 -f markdown
    python -m sqltriage -S SQLPROD01 --only blocking,vlf --databases PayrollDB

Windows authentication is the default. For a SQL login pass `-U name` and put
the password in the `SQLTRIAGE_PASSWORD` environment variable.

`--check` tests the connection and the permissions the checks need, and exits.

By default the tool uses pyodbc. Microsoft's `mssql-python` driver, which needs no
ODBC driver installed, works too: `pip install mssql-python` and pass
`--library mssql-python` (or just do not install pyodbc and it is picked up).

ODBC Driver 18 encrypts by default. If the instance has a self-signed
certificate use `--trust-server-cert`; `--no-encrypt` turns encryption off.

The login needs `VIEW SERVER STATE` and `VIEW ANY DEFINITION`. The VLF check
uses `sys.dm_db_log_info`, so SQL Server 2016 SP2 or later; the other checks run
on 2016 and later, up to SQL Server 2025. Python 3.11 or newer is needed.

Output formats (`-f`): `text` (the default), `json` (with a `schema_version`)
and `markdown` for pasting into a ticket. `--min-severity high` hides the lower
ranked findings and says how many were hidden.

## How findings are ranked

Every finding has a score from 0 to 100 and a label: critical (85 and up), high
(65), medium (40), low (15) and info. Each check scores from what it measured:
how many sessions are blocked and for how long, how far past its usual time a
job is running, how deep the runnable queue is, and so on.

Some findings are two views of the same problem. A `LCK_M_X` wait is what a
blocking chain looks like from the wait stats, so when both are present the wait
is marked as the same problem and ranked lower, and the blocking finding says
what it also shows up as (see `sqltriage/correlate.py`).

## Sample

From the recorded incident in `tests/fixtures/incident1` (an application left a
transaction open, tempdb is contended, and an AG secondary is disconnected):

```
sqltriage report for SQLPROD01 at 2023-03-14 10:00:00
SQL Server 2019 15.0.4298.1 (Enterprise Edition (64-bit)), 16 CPUs, up since 2023-03-02T03:12:44

 1. [CRITICAL 95] Blocking: spid 57 is blocking 6 session(s)
    Longest wait 94s.
      spid 57 [sleeping] svc_payroll@APP01 open_tran=1  <- head blocker
      |- spid 61 LCK_M_X 94.0s svc_payroll@APP02 db=PayrollDB open_tran=1
      |    KEY: 5:72057594043432960 (8194443284a0)
      | `- spid 72 LCK_M_U 20.0s svc_payroll@APP03 db=PayrollDB open_tran=1
      |      KEY: 5:72057594043498496 (91ab07c6d5e0)
      |- spid 64 LCK_M_S 61.0s rpt_reader@RPT01 db=PayrollDB
      |    KEY: 5:72057594043432960 (8194443284a0)
      | `- spid 70 LCK_M_X 33.0s svc_payroll@APP02 db=PayrollDB open_tran=1
      |      KEY: 5:72057594043498496 (c2f9d31a4b11)
      |   `- spid 81 LCK_M_S 4.0s rpt_reader@RPT01 db=PayrollDB
      |        KEY: 5:72057594043498496 (c2f9d31a4b11)
      `- spid 75 LCK_M_X 12.0s svc_payroll@APP03 db=PayrollDB open_tran=1
           KEY: 5:72057594043432960 (8194443284a0)
      - locks contended: dbo.PayRun.PK_PayRun (3 waiting), dbo.PayRunLine.IX_PayRunLine_PayRunId (3 waiting)
      - oldest open transaction began 2023-03-14T08:51:20 (1h09m ago)
      - head blocker input buffer - RPC Event: Proc [Database Id = 5 Object Id = 1134627161]
      - also shows up as: LCK_M_S, LCK_M_X
      - also shows up as: Log: PayrollDB has 18,432 VLFs and log reuse wait ACTIVE_TRANSACTION
      > Find out why spid 57 is not finishing; KILL is the last resort.
 2. [CRITICAL 90] Log: PayrollDB has 18,432 VLFs and log reuse wait ACTIVE_TRANSACTION
      - 18,432 VLFs in 61,440 MB of log (17900 active)
      - log autogrowth is 10%, which is how it got this many
      - log_reuse_wait_desc = ACTIVE_TRANSACTION: An open transaction is holding the start of the log.
      - the open transaction holding the log is probably the head blocker in: Blocking: spid 57 is blocking 6 session(s)
      > Shrink the log once and regrow it in a few large steps to rebuild the VLFs.
      > Set a fixed growth increment of 1024 to 8192 MB instead.
      > Find the oldest open transaction (DBCC OPENTRAN) and check the blocking section.
 3. [HIGH 81] tempdb allocation contention: 15 task(s) waiting on PFS/GAM/SGAM pages
    Sessions are queueing on tempdb's allocation bitmaps while creating or dropping temp objects.
      - PFS   2:1:1  6 task(s) waiting, longest 31 ms
      - SGAM  2:1:3  4 task(s) waiting, longest 25 ms
      - PFS   2:3:1  3 task(s) waiting, longest 15 ms
      - GAM   2:2:2  1 task(s) waiting, longest 6 ms
      - PFS   2:1:8088  1 task(s) waiting, longest 5 ms
      - other 2:1:1234  1 task(s) waiting, longest 4 ms
      - also shows up as: PAGELATCH_UP
      > Make sure tempdb has several equally sized data files (one per core, up to 8, is the usual start).
      > Look for code that creates and drops temp tables at a very high rate, or large table variables.
```

## Recording an incident

`--record DIR` saves every query result to `DIR` while the report runs. The
files replay with `--fixtures DIR`, so you can look at the same data again later
or attach it to a ticket. They contain real query text, logins and host names:
read them before sharing.

## Looking up a wait type

    python -m sqltriage explain WRITELOG PAGEIOLATCH_SH

prints what sqltriage knows about each: the category, what it means and what to
check. Idle waits are named as such.

## Comparing with an earlier report

Save a report as JSON on a quiet day, or just before a change:

    python -m sqltriage -S SQLPROD01 -f json -o before.json

Later, `--baseline before.json` lists what has been resolved since and marks each finding NEW, WORSE (10 or more points
up), BETTER or unchanged, and the header counts them. Findings are matched by
what they are about (the session, wait type, database, query or job), not by
their numbers. `--only-changes` hides the unchanged ones.

To keep a history, run with `--save reports --baseline-dir reports`: each run is
saved as `reports/sqltriage-<server>-<timestamp>.json` and compared with the
newest earlier one for the same server. The saved files are not redacted.

## Sharing a report

`--redact` masks string literals, long numbers, hex values and e-mail addresses
in the report, which is where query text leaks real data. `--redact-names` also
replaces logins and host names with stable tokens (`user1@host1`). It is a
safety net, so read the report before it goes into a ticket.

## Exit status

`0` normally, `1` when `--fail-on LEVEL` is given and a finding is at least that
severe (judged before `--min-severity` hides anything), `2` for a usage or
configuration error. That makes it usable from a monitoring job.

## Configuration file

Settings can live in `sqltriage.toml` (current directory) or
`~/.config/sqltriage.toml`, or any file named with `--config`. Flags override the
file. See `sqltriage/config.py` for every key; for example:

    [connection]
    server = "SQLPROD01"
    trust_server_cert = true

    [sample]
    interval = 15

    [agent]
    hours = 12

    [querystore]
    min_execs = 20
    ratio = 2.5

    [weights]       # scale a check's scores; 0 silences it
    agent = 0.5

Python 3.11 or newer is needed (the config reader is `tomllib`).

## Trying it without a server

The tests replay recorded query results from `tests/fixtures/`. You can do the
same from the command line:

    python -m sqltriage --fixtures tests/fixtures/incident1 -S SQLPROD01

Sample (blocking and tempdb only):

```
sqltriage report for SQLPROD01

 1. [HIGH 84] Blocking: spid 57 is blocking 6 session(s)
    Longest wait 94s.
      spid 57 [sleeping] svc_payroll@APP01 open_tran=1  <- head blocker
      |- spid 61 LCK_M_X 94.0s svc_payroll@APP02 db=PayrollDB open_tran=1
      |    KEY: 5:72057594043432960 (8194443284a0)
      | `- spid 72 LCK_M_U 20.0s svc_payroll@APP03 db=PayrollDB open_tran=1
      |      KEY: 5:72057594043432960 (91ab07c6d5e0)
      |- spid 64 LCK_M_S 61.0s rpt_reader@RPT01 db=PayrollDB
      |    KEY: 5:72057594043432960 (8194443284a0)
      | `- spid 70 LCK_M_X 33.0s svc_payroll@APP02 db=PayrollDB open_tran=1
      |      KEY: 5:72057594043432960 (c2f9d31a4b11)
      |   `- spid 81 LCK_M_S 4.0s rpt_reader@RPT01 db=PayrollDB
      |        KEY: 5:72057594043432960 (c2f9d31a4b11)
      `- spid 75 LCK_M_X 12.0s svc_payroll@APP03 db=PayrollDB open_tran=1
           KEY: 5:72057594043432960 (8194443284a0)
      - head blocker input buffer - RPC Event: Proc [Database Id = 5 Object Id = 1134627161]
      > Find out why spid 57 is not finishing; KILL is the last resort.
 2. [HIGH 76] tempdb allocation contention: 15 task(s) waiting on PFS/GAM/SGAM pages
    Sessions are queueing on tempdb's allocation bitmaps while creating or dropping temp objects.
      - PFS   2:1:1  6 task(s) waiting, longest 31 ms
      - SGAM  2:1:3  4 task(s) waiting, longest 25 ms
      - PFS   2:3:1  3 task(s) waiting, longest 15 ms
      - GAM   2:2:2  1 task(s) waiting, longest 6 ms
      - PFS   2:1:8088  1 task(s) waiting, longest 5 ms
      - other 2:1:1234  1 task(s) waiting, longest 4 ms
      > Make sure tempdb has several equally sized data files (one per core, up to 8, is the usual start).
      > Look for code that creates and drops temp tables at a very high rate, or large table variables.
 3. [MEDIUM 48] Blocking: spid 93 is blocking 1 session(s)
    Longest wait 6s.
      spid 93 [sleeping] CORP\jmendez@LAPTOP-JM07 open_tran=1  <- head blocker
      `- spid 95 LCK_M_IX 6.0s svc_payroll@APP01 db=PayrollDB open_tran=1
           PAGE: 5:1:88213
      - head blocker input buffer - Language Event: BEGIN TRAN; UPDATE dbo.Orders SET Status = 'HOLD' WHERE OrderId = 1042; -- TODO: commit after checking totals
      > Find out why spid 93 is not finishing; KILL is the last resort.
```

## Tests

    python -m unittest discover -s tests

No database and no third party packages are needed.

The tests also run on GitHub Actions (`.github/workflows/test.yml`) for
Python 3.11 to 3.14 (warnings are errors), and fail if the golden report files are out of date
(`python -m tests.regen_golden` rewrites them).

See [CHANGELOG.md](CHANGELOG.md) for what changed in each version.

## License

MIT
