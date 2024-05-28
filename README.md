# sqltriage

A small command line tool for the first ten minutes of a SQL Server incident.
You point it at an instance, it samples a few DMVs, and it prints a short,
ranked list of things worth looking at, each with the evidence and a next step.

It only reads. Everything it runs is a `SELECT` against DMVs and system views.

## What it checks

| check      | looks at                                                              |
|------------|-----------------------------------------------------------------------|
| `waits`    | `sys.dm_os_wait_stats` sampled twice; idle waits dropped, rest explained |
| `blocking` | blocked sessions as a tree, with the head blocker's input buffer       |
| `tempdb`   | PAGELATCH waits on PFS / GAM / SGAM pages in tempdb                    |
| `vlf`      | VLF counts (`sys.dm_db_log_info`) and `log_reuse_wait_desc`            |
| `querystore` | regressed queries (recent vs baseline) and forced plan failures      |
| `agent`    | failed jobs in the last day and jobs running far past their usual time |
| `ag`       | replica connection and sync health, send/redo queues, lag             |
| `cpu`      | runnable queue per scheduler and signal waits                          |

## Usage

    pip install pyodbc
    python -m sqltriage -S SQLPROD01
    python -m sqltriage -S SQLPROD01 -i 30 -f json
    python -m sqltriage -S SQLPROD01 --only blocking,vlf

Windows authentication is the default. For a SQL login pass `-U name` and put
the password in the `SQLTRIAGE_PASSWORD` environment variable.

ODBC Driver 18 encrypts by default. If the instance has a self-signed
certificate use `--trust-server-cert`; `--no-encrypt` turns encryption off.

The login needs `VIEW SERVER STATE` and `VIEW ANY DEFINITION`. The VLF check
uses `sys.dm_db_log_info`, so SQL Server 2016 SP2 or later; the other checks run
on 2016 and later.

## Recording an incident

`--record DIR` saves every query result to `DIR` while the report runs. The
files replay with `--fixtures DIR`, so you can look at the same data again later
or attach it to a ticket. They contain real query text, logins and host names:
read them before sharing.

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
Python 3.11 and 3.12.

## License

MIT
