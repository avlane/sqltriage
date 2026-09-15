# Changelog

Versions follow the usual major.minor.patch.

## 1.0.0 - 2026-09-15

- Baselines: `--baseline FILE` or `--baseline-dir DIR` mark findings new, worse,
  better or unchanged and list what was resolved; `--save DIR` keeps a history.
- New checks: performance counters (deadlocks, lock timeouts, compile rate) and
  persisted version store growth. CPU now tells SQL Server's use from the rest of
  the host's.
- AG: distributed and contained groups are labelled.
- `sqltriage explain WAIT_TYPE`, `--format oneline`, a severity summary in every
  format, a rank and a stable key on every JSON finding (schema version 2).
- `--query-timeout` and `--login-timeout`; permission errors come with advice.
- Tempdb: suggests memory-optimized metadata where it helps. Query Store: says whether
  a failing forced plan was forced by hand or by automatic tuning.
- Fixed: a healthy server reading as an incident (signal waits on an idle
  server, background waits ranked on share alone), an unreadable report file
  left behind by a failed write, slow Agent history queries on a large msdb.
- Documentation for every check in docs/checks.md.

## 0.6.0 - 2025-12-16

- Memory check: queries waiting for a grant, oversized grants, OS and process
  memory pressure, max server memory left at the default, page life expectancy.
- AG: estimated data loss and recovery time, failover readiness.
- Agent: stopped service, jobs that missed their schedule.
- Log backups: age of the last one and the log piled up since.
- Tempdb: non-allocation latch waits resolved to an object on SQL Server 2019+.
- `--samples` for a trend over several intervals, `-o` to write a file,
  `--check` for connection and permissions, `--redact` and `--redact-names`.
- `--library mssql-python` as an alternative to pyodbc.
- Weights per check in the config file.
- Fixed: GAM and SGAM page numbers, sessions blocked for a moment flooding the
  blocking section, an infinite ratio on a zero Query Store baseline, unwritable
  query text with a split surrogate pair, pipes breaking the Markdown table.

## 0.5.0 - 2024-12-10

- Findings that are two views of one problem are linked and the symptom is
  scored down (lock waits against the blocking chain, and so on).
- Query Store: wait categories for regressed queries, health of Query Store
  itself.
- Blocking: the table and index behind KEY lock waits, transaction age.
- Agent: the failing step and its SQL error, ignore patterns.
- `--record` saves query results as fixtures; `--fail-on` for scripts.
- Renderers split into a package, golden report tests.

## 0.4.0 - 2023-12-12

- TOML configuration file (Python 3.11 or newer).
- AG replica health and per-database queues.
- Query Store regressions and forced plan failures.
- Agent job failures and long running jobs.
- Markdown output, `--min-severity`, `--databases`.
- Fixed: a wait delta across a counter reset, blocking cycles missing from the
  report.

## 0.1.0 - 2022-11-29

First usable version: waits, blocking, tempdb, VLF and CPU checks with text and
JSON output, replaying recorded fixtures with `--fixtures`.
