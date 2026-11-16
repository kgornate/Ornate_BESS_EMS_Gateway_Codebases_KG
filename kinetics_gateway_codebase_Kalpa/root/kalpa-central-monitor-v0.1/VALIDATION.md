# Validation

Four local automated tests passed against the available Kalpa backend source:

- Configuration isolates the test outbox/cursors and leaves the source configuration unchanged.
- Missing frozen S2 policy selection is rejected.
- Actual S1/S2/S3/S4/S8/S10 producer classes run against simulated local API inputs. S2 generates all 9,494 frozen signals. Messages have zero transport attempts; the dashboard's SQLite inspection preserves row counts. No command execution is permitted in this test.
- The harness imports no uploader, backend HTTP transport or command processor.

Bash syntax checks passed. Testing used Linux x86/Python 3.12; installation, systemd startup and live API authentication on DEBIX/Python 3.13 remain to be verified by the user. No software was installed remotely during package preparation.
