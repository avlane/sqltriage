# The checks

What each check reads, what it reports, how it scores, and what it needs. Every
query lives at the top of its module in `sqltriage/checks/` as a named SQL
constant, so this page is a map and the code is the reference.

Scores run from 0 to 100: critical is 85 and up, high 65, medium 40, low 15,
info below that. Related findings are linked and the symptom scored down by
`sqltriage/correlate.py`, and `[weights]` in the config file scales a whole check.

## `waits`

- **Reads:** `sys.dm_os_wait_stats`, twice, one interval apart (`-i`, default 10 s;
  `--samples N` repeats it for a trend).
- **Reports:** one finding per significant wait, with its share of the non-idle
  wait time, how many tasks were waiting on average, the signal wait share, what it
  means and what to look at. `sqltriage explain WAIT_TYPE` prints the same text.
- **Leaves out:** idle and background waits (`waitinfo.BENIGN` and the prefixes).
- **Scoring:** the kind of wait (its weight in `waitinfo.py`), its share, and the
  average number of waiting tasks. A wait nobody was stuck on is capped at info.
- **If the counters went backwards** (restart, or `DBCC SQLPERF ... CLEAR`) the
  sample is discarded and the finding says so.
- **Needs:** VIEW SERVER STATE.

## `counters`

- **Reads:** `sys.dm_os_performance_counters`, in the same sample window as the waits.
- **Reports:** deadlocks and lock timeouts during the sample, the share of batches
  that compiled a plan, and a one line workload summary.
- **Needs:** VIEW SERVER STATE.

## `blocking`

- **Reads:** `sys.dm_exec_sessions`, `sys.dm_exec_requests`, `sys.dm_exec_sql_text`,
  `sys.dm_exec_input_buffer`, `sys.dm_tran_session_transactions`,
  `sys.dm_tran_active_transactions`, and `sys.partitions` / `sys.indexes` in the
  blocked session's database.
- **Reports:** a tree per head blocker with wait types, wait resources and times; the
  tables and indexes behind KEY lock waits; how long the head blocker's oldest
  transaction has been open; the head blocker's last batch.
- **Details:** sessions blocked for under two seconds are ignored (`--blocking-min-wait`).
  Rings of sessions that block each other are shown, starting at the longest waiter.
  A negative blocking id (-2, -3, -4) is a system condition, not a session.
- **Scoring:** victims, the longest wait, an open transaction on a sleeping head
  blocker, and the age of that transaction.
- **Needs:** VIEW SERVER STATE.

## `querystore`

- **Reads:** `sys.databases` for databases with Query Store on, then in each:
  `sys.query_store_runtime_stats`, `..._interval`, `sys.query_store_plan`, `..._query`,
  `..._query_text`, `sys.query_store_wait_stats`, `sys.database_query_store_options`,
  and `sys.dm_db_tuning_recommendations`.
- **Reports:** queries whose average duration in the recent window (1 hour) is at
  least twice, and 5 ms more than, their baseline (the 24 hours before), weighted by
  executions; what they waited on; the plan the server recommends forcing; forced
  plans that fail, with the reason; Query Store that is read-only, in error or nearly full.
- **If most of the waiting was for locks,** the finding says the query is blocked, not
  badly planned, and scores lower.
- **Options:** `[querystore]` in the config file.
- **Needs:** VIEW DATABASE STATE in each database. Wait stats and tuning recommendations
  need SQL Server 2017 or later and are skipped without them.

## `tempdb`

- **Reads:** `sys.dm_os_waiting_tasks` (PAGELATCH waits on database 2),
  `tempdb.sys.database_files`, `sys.dm_db_file_space_usage`,
  `sys.dm_tran_version_store_space_usage`, and on SQL Server 2019 and later
  `sys.dm_db_page_info`.
- **Reports:** waits on PFS, GAM and SGAM pages; the owner of other hot pages; too few
  or unevenly sized data files; low free space and what is using it.
- **Needs:** VIEW SERVER STATE.

## `vlf`

- **Reads:** `sys.dm_db_log_info`, `sys.databases`, `sys.master_files`,
  `sys.dm_db_log_stats`.
- **Reports:** databases with thousands of VLFs, with the growth setting that
  probably caused it; the log reuse wait and what it means; FULL or BULK_LOGGED
  databases whose log has not been backed up lately.
- **Needs:** VIEW ANY DEFINITION and VIEW DATABASE STATE; SQL Server 2016 SP2 or later.

## `pvs`

- **Reads:** `sys.dm_tran_persistent_version_store_stats`.
- **Reports:** a persisted version store (Accelerated Database Recovery) that is large
  or a big share of the data files, the oldest active transaction holding it, and aborted
  transactions the cleaner is stuck on.
- **Needs:** SQL Server 2019 or later; skipped before that.

## `agent`

- **Reads:** `msdb.dbo.sysjobs`, `sysjobhistory`, `sysjobactivity`, `syssessions`,
  `sysjobschedules`, `sysschedules`, and `sys.dm_server_services`.
- **Reports:** a stopped Agent service; enabled jobs whose next run is long past;
  failed jobs in the last day with the failing step's error; jobs running far past their
  usual time (three times the average of successful runs).
- **Options:** `--ignore-jobs` patterns, `[agent] hours`.
- **Needs:** read access to msdb (the SQLAgentReaderRole role).

## `ag`

- **Reads:** `sys.availability_groups`, `sys.availability_replicas`,
  `sys.dm_hadr_availability_replica_states`, `sys.dm_hadr_database_replica_states`,
  `sys.dm_hadr_database_replica_cluster_states`.
- **Reports:** replicas that are disconnected, offline or not healthy; databases with a
  growing send or redo queue, suspended movement or lag, with the estimated data loss
  and recovery time of a failover; synchronous replicas that could not take over.
- **Run it on the primary** for complete figures. Distributed and contained groups are
  labelled.
- **Needs:** VIEW SERVER STATE.

## `memory`

- **Reads:** `sys.dm_exec_query_memory_grants`, `sys.dm_exec_query_resource_semaphores`,
  `sys.dm_os_sys_memory`, `sys.dm_os_process_memory`, `sys.configurations`,
  `sys.dm_os_performance_counters`.
- **Reports:** queries waiting for a memory grant, grants of 1 GB or more that are barely
  used, OS or process low memory signals, max server memory left at its default, and a
  low page life expectancy.
- **Needs:** VIEW SERVER STATE.

## `cpu`

- **Reads:** `sys.dm_os_schedulers`, the wait sample for signal waits, and
  `sys.dm_exec_requests` for the heaviest running requests.
- **Reports:** the runnable queue per scheduler and signal waits (which only count when
  tasks are really queueing), one scheduler far busier than the rest, and the requests
  using the most CPU.
- **Needs:** VIEW SERVER STATE.
