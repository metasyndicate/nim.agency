# NIM AGENCY — MANIFEST

Component inventory and state snapshot. Revision 26.7.14.x.

## LAYOUT

```
nim.agency/
├── bin/            CLI entry points
│   ├── nim         interface controller (tui, help)
│   └── dispatch.py mission dispatch CLI
├── etc/
│   └── agency.json source-controlled runtime defaults (read-only at runtime)
├── lib/
│   ├── config.py   defaults loader (etc/agency.json)
│   ├── data/       datastore, schema, agent generation & composition
│   ├── dispatch/   mission orchestration, provider, dispatch logging
│   └── remote/     SSH transport, credential vault, safety, audit logging
├── data/agency/
│   ├── schema/     JSON Schema definitions (core, reference, meta)
│   └── data/       instance data (agents, groups, reference, instruction)
├── lex/            lexicon/taxonomy CSVs for procedural generation
├── tui/            curses TUI (agent builder) + ASCII mugshots
├── log/            mission reports & mission JSON (gitignored)
└── arc/            archives — drafts, retired artifacts (gitignored)
```

## INTERFACES

| Interface | Entry | Invocation | State |
|---|---|---|---|
| Mission dispatch (CLI) | `bin/dispatch.py` | `python3 bin/dispatch.py [--mission codebase\|security] [--target DIR] [--crew a,b] [--count N] [--timeout S] [--output F] [--dry-run] [--list-agents]` | functional |
| TUI agent builder | `bin/nim` → `tui/builder.py` | `python3 bin/nim tui` (min 80x24 terminal) | functional, monolithic (~3600 lines) |
| Datastore seed/verify | `lib/data/seed.py` | `python3 lib/data/seed.py [--verify] [--seed-agents N] [--list-schemas] [--describe SCHEMA]` | functional |
| Programmatic (Python) | `lib.data`, `lib.dispatch` | see README "Programmatic" | functional |

## COMPONENTS

| Component | Path | Purpose | Maturity |
|---|---|---|---|
| SchemaManager | `lib/data/schema.py` | schema loading/validation (`data/agency/schema/`) | stable |
| DataStore / CRUD | `lib/data/crud.py` | JSON datastore, soft-delete, queries | stable |
| AgentGenerator | `lib/data/generator.py` | procedural agents from `lex/` taxonomies, weighted class/tier | stable |
| AgentComposer | `lib/data/composer.py` | operational profile / system prompt assembly | stable |
| OWL / CrüeRoster | `lib/data/owl.py` | crew assembly | stable |
| Mission / Constraints | `lib/dispatch/mission.py` | work orders, tasks, dependency graph, JSON persistence | stable |
| MissionBuilder + templates | `lib/dispatch/builder.py` | fluent builder; `codebase`/`security` templates | stable |
| Dispatcher | `lib/dispatch/dispatcher.py` | async execution, dependency ordering, report assembly (on success and failure) | stable |
| Provider (claude-cli) | `lib/dispatch/provider.py` | executes tasks via `claude --print`; per-task timeout | stable |
| DispatchLogger | `lib/dispatch/ops_log.py` | CSV + JSON lifecycle logs → `~/.nim/agency/` | stable |
| Remote ops | `lib/remote/` (ssh, vault, protocol, keys, safety, ops_log) | asyncssh transport, Fernet vault, command classification, audit trail | implemented; needs live validation |
| TUI | `tui/builder.py` | RPG-themed agent builder HUD | functional; refactor candidate |

## CONVENTIONS & DEFAULTS

Codified in `etc/agency.json` (checked into source; treated as immutable at
runtime — overrides via CLI flags):

| Setting | Default | Consumer |
|---|---|---|
| `dispatch.provider` | `claude-cli` | dispatcher |
| `dispatch.max_concurrent` | `2` | `bin/dispatch.py` |
| `dispatch.timeout_seconds` | `600` | mission templates; `--timeout` overrides |
| `dispatch.report_dir` | `log` (project-relative) | report + mission JSON output |
| `logging.dir` | `~/.nim/agency` | dispatch/audit logs |

Output artifacts per mission run:

- `log/mission-<id>-report.md` — assembled report (written on success **and** failure)
- `log/mission-<id>-report.json` — full mission record: prompts, constraints, per-task results/errors
- `~/.nim/agency/dispatch.log` / `dispatch.json` — lifecycle event logs
- `~/.nim/agency/remote_operations.json` — remote ops audit trail (mode 0600)

## KNOWN GAPS

- No test suite.
- No dependency declaration (core is stdlib; `asyncssh`, `cryptography`,
  `jsonschema` optional — see README).
- `--mission custom` not yet supported via CLI (programmatic only).
- `tui/builder.py` is a single-file monolith.
- Remote layer (`lib/remote/`) untested against live infrastructure this revision.
