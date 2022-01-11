# sqltriage

A small command line tool for the first ten minutes of a SQL Server incident.
You point it at an instance, it samples a few DMVs, and it prints a short,
ranked list of things worth looking at.

Status: early. Nothing is wired up yet except the command line skeleton.

## Plan

- wait stats delta over a sample interval
- blocking chains with the head blocker's input buffer
- Query Store regressions and forced plan failures
- tempdb allocation contention
- VLF counts and log reuse wait
- failed and long running Agent jobs
- availability group replica health
- CPU scheduler pressure

## License

MIT
