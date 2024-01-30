"""What the common wait types mean, and which ones to ignore."""
from collections import namedtuple

# Waits that mostly mean "a background task is idle".  Filtering these out is
# the usual first step when reading sys.dm_os_wait_stats.
BENIGN = frozenset(
    """
    BROKER_EVENTHANDLER BROKER_RECEIVE_WAITFOR BROKER_TASK_STOP BROKER_TO_FLUSH
    BROKER_TRANSMITTER CHECKPOINT_QUEUE CHKPT CLR_AUTO_EVENT CLR_MANUAL_EVENT
    CLR_SEMAPHORE CXCONSUMER DBMIRROR_DBM_EVENT DBMIRROR_EVENTS_QUEUE DBMIRROR_WORKER_QUEUE
    DBMIRRORING_CMD DIRTY_PAGE_POLL DISPATCHER_QUEUE_SEMAPHORE
    FT_IFTS_SCHEDULER_IDLE_WAIT FT_IFTSHC_MUTEX HADR_CLUSAPI_CALL
    HADR_FILESTREAM_IOMGR_IOCOMPLETION HADR_LOGCAPTURE_WAIT
    HADR_NOTIFICATION_DEQUEUE HADR_TIMER_TASK HADR_WORK_QUEUE
    KSOURCE_WAKEUP LAZYWRITER_SLEEP LOGMGR_QUEUE MEMORY_ALLOCATION_EXT
    ONDEMAND_TASK_QUEUE PARALLEL_REDO_DRAIN_WORKER PARALLEL_REDO_LOG_CACHE
    PARALLEL_REDO_TRAN_LIST PARALLEL_REDO_WORKER_SYNC
    PARALLEL_REDO_WORKER_WAIT_WORK PREEMPTIVE_XE_GETTARGETSTATE
    PWAIT_ALL_COMPONENTS_INITIALIZED PWAIT_DIRECTLOGCONSUMER_GETNEXT
    QDS_ASYNC_QUEUE QDS_CLEANUP_STALE_QUERIES_TASK_MAIN_LOOP_SLEEP
    QDS_PERSIST_TASK_MAIN_LOOP_SLEEP QDS_SHUTDOWN_QUEUE
    REQUEST_FOR_DEADLOCK_SEARCH RESOURCE_QUEUE SERVER_IDLE_CHECK
    SLEEP_BPOOL_FLUSH SLEEP_DBSTARTUP SLEEP_DCOMSTARTUP SLEEP_MASTERDBREADY
    SLEEP_MASTERMDREADY SLEEP_MASTERUPGRADED SLEEP_MSDBSTARTUP
    SLEEP_SYSTEMTASK SLEEP_TASK SLEEP_TEMPDBSTARTUP SNI_HTTP_ACCEPT
    SOS_WORK_DISPATCHER SP_SERVER_DIAGNOSTICS_SLEEP SQLTRACE_BUFFER_FLUSH
    SQLTRACE_INCREMENTAL_FLUSH_SLEEP SQLTRACE_WAIT_ENTRIES WAIT_FOR_RESULTS
    WAIT_XTP_CKPT_CLOSE WAIT_XTP_HOST_WAIT WAIT_XTP_OFFLINE_CKPT_NEW_LOG
    WAIT_XTP_RECOVERY WAITFOR WAITFOR_TASKSHUTDOWN XE_DISPATCHER_JOIN
    XE_DISPATCHER_WAIT XE_LIVE_TARGET_TVF XE_TIMER_EVENT XTP_PREEMPTIVE_TASK
    """.split()
)


# Families of background waits.  Each release adds a few more names to these
# (QDS_*, PARALLEL_REDO_*, ...), so match the family rather than only the
# names we knew about.
BENIGN_PREFIXES = (
    "BROKER_",
    "CLR_",
    "DBMIRROR",
    "FT_",
    "PARALLEL_REDO_",
    "PWAIT_",
    "QDS_",
    "SLEEP_",
    "SQLTRACE_",
    "WAIT_XTP_",
    "XE_",
)


def is_benign(wait_type):
    return wait_type in BENIGN or wait_type.startswith(BENIGN_PREFIXES)


# weight: how much a wait of this kind adds to a finding's score (0-50)
Info = namedtuple("Info", "category weight text advice")

UNKNOWN = Info(
    "other",
    10,
    "Not one of the waits this tool knows how to explain.",
    ["Look the wait type up in the sys.dm_os_wait_stats documentation."],
)

WAIT_INFO = {
    "WRITELOG": Info(
        "log",
        25,
        "Sessions are waiting for commit log blocks to be hardened to disk.",
        [
            "Check write latency on the log file (sys.dm_io_virtual_file_stats).",
            "Look for many tiny autocommit transactions that could be batched.",
        ],
    ),
    "SOS_SCHEDULER_YIELD": Info(
        "cpu",
        20,
        "Tasks used their full quantum and queued to get back on a scheduler: CPU pressure.",
        [
            "Compare signal wait time to total wait time; a high ratio confirms CPU pressure.",
            "Find the top CPU consumers in sys.dm_exec_query_stats or Query Store.",
        ],
    ),
    "CXPACKET": Info(
        "parallelism",
        15,
        "Parallel query threads waiting on each other.",
        [
            "Review MAXDOP and cost threshold for parallelism.",
            "A few skewed threads usually point at stale statistics or a bad estimate.",
        ],
    ),
    "ASYNC_NETWORK_IO": Info(
        "client",
        15,
        "SQL Server has results ready and is waiting for the client to consume them.",
        [
            "Usually the application is slow to read rows, not the network.",
            "Check for row-by-row processing or very large result sets.",
        ],
    ),
    "HADR_SYNC_COMMIT": Info(
        "ag",
        30,
        "Commits on the primary wait for a synchronous secondary to harden the log.",
        [
            "Check log send queue, network latency and secondary log write latency.",
            "See the availability group section of this report.",
        ],
    ),
    "RESOURCE_SEMAPHORE": Info(
        "memory",
        40,
        "Queries are waiting for a memory grant before they can start.",
        [
            "Look at sys.dm_exec_query_memory_grants for large or runaway grants.",
            "Check for missing indexes causing big sorts and hashes.",
        ],
    ),
    "THREADPOOL": Info(
        "workers",
        50,
        "No worker thread was available to run a request. New connections may stall.",
        [
            "Find what is holding workers: blocking chains and long parallel queries.",
            "Connect through the DAC if the instance will not accept logins.",
        ],
    ),
    "IO_COMPLETION": Info(
        "io",
        15,
        "Waiting on non-data-page I/O such as sorts spilling to tempdb.",
        ["Check tempdb file latency."],
    ),
    "ASYNC_IO_COMPLETION": Info(
        "io",
        10,
        "Waiting on asynchronous I/O, commonly backups or restores.",
        ["Check whether a backup or restore is running."],
    ),
    "BACKUPIO": Info("io", 10, "A backup is waiting on its target device.", ["Check backup target throughput."]),
    "LOGBUFFER": Info(
        "log",
        20,
        "Waiting for space in the log buffer, usually because log writes cannot keep up.",
        ["Treat like WRITELOG: look at log file latency."],
    ),
    "RESOURCE_SEMAPHORE_QUERY_COMPILE": Info(
        "memory",
        35,
        "Compilations are queued because too many are already running.",
        [
            "Look for a burst of ad hoc queries or a plan cache that was just flushed.",
            "Check the compilations/sec counter and optimize for ad hoc workloads.",
        ],
    ),
    "LOG_RATE_GOVERNOR": Info(
        "log",
        35,
        "Log writes are being throttled by a log rate governor (typically Azure SQL Managed Instance).",
        ["Reduce log generation or move to a service tier with a higher log rate."],
    ),
    "WRITE_COMPLETION": Info(
        "io",
        20,
        "Waiting for a data file write to complete, as during checkpoints or a spill.",
        ["Check write latency on the data files."],
    ),
    "PREEMPTIVE_OS_WRITEFILEGATHER": Info(
        "io",
        20,
        "A file is being grown and zero-initialized.",
        [
            "Enable instant file initialization for data files (not possible for the log).",
            "Grow files in larger planned steps rather than many small autogrowths.",
        ],
    ),
    "PREEMPTIVE_OS_FLUSHFILEBUFFERS": Info(
        "io",
        20,
        "Waiting for the operating system to flush file buffers to disk.",
        ["Usually storage latency during checkpoints or backups."],
    ),
    "TRANSACTION_MUTEX": Info(
        "locking",
        20,
        "Several sessions are sharing one transaction (as with MARS or distributed work) and waiting on each other.",
        ["Check for applications using MARS or enlisting in a distributed transaction."],
    ),
    "OLEDB": Info(
        "client",
        15,
        "Waiting on an OLE DB provider call, typically a linked server query.",
        ["Find the linked server queries (sys.dm_exec_requests with wait_type OLEDB)."],
    ),
    "HADR_THROTTLE_LOG_RATE_SEND_RECV_QUEUE_SIZE": Info(
        "ag",
        35,
        "Log generation on the primary is being throttled because a secondary is too far behind.",
        ["The send or redo queue on a secondary is large; see the availability group section."],
    ),
    "HADR_TRANSPORT_FLOW_CONTROL": Info(
        "ag",
        30,
        "Log transport to a secondary is held up by flow control.",
        ["Check network throughput and the secondary's ability to receive."],
    ),
    "CMEMTHREAD": Info(
        "memory",
        20,
        "Contention on a thread-safe memory object, often the plan cache.",
        ["Look for ad hoc plan cache bloat or very high compile rates."],
    ),
}

# Matched with str.startswith, in order.
PREFIX_INFO = [
    (
        "LCK_M_",
        Info(
            "locking",
            35,
            "Sessions are blocked waiting for a lock held by someone else.",
            [
                "Look at the blocking section for the head blocker.",
                "Check for open transactions left by the application.",
            ],
        ),
    ),
    (
        "PAGEIOLATCH_",
        Info(
            "io",
            25,
            "Waiting for a data page to be read from disk into memory.",
            [
                "Check read latency per file in sys.dm_io_virtual_file_stats.",
                "Look for scans that a better index would avoid, or memory pressure.",
            ],
        ),
    ),
    (
        "PAGELATCH_",
        Info(
            "latch",
            30,
            "Contention on a data page that is already in memory.",
            [
                "In tempdb this is allocation contention (PFS, GAM, SGAM); see the tempdb section.",
                "In a user database look for last-page insert hot spots.",
            ],
        ),
    ),
    (
        "LATCH_",
        Info(
            "latch",
            20,
            "Contention on an internal non-buffer latch.",
            ["Check sys.dm_os_latch_stats for the latch class."],
        ),
    ),
    (
        "HADR_",
        Info(
            "ag",
            15,
            "An availability group related wait.",
            ["See the availability group section of this report."],
        ),
    ),
]


def explain(wait_type):
    info = WAIT_INFO.get(wait_type)
    if info is not None:
        return info
    for prefix, info in PREFIX_INFO:
        if wait_type.startswith(prefix):
            return info
    return UNKNOWN
