# NIM AGENCY — MANIFEST

Component inventory and state snapshot. Revision 26.7.14.x.

## LAYOUT

```
nim.agency/
├── bin/
│   └── nim.agency  canonical CLI launcher (all logic in lib/cli/)
├── etc/
│   └── agency.json source-controlled runtime defaults (read-only at runtime)
├── lib/
│   ├── config.py   defaults loader (etc/agency.json)
│   ├── cli/        CLI command modules (dispatch, mission, agent, data, tui)
│   ├── data/       datastore, schema, agent generation & composition
│   ├── dispatch/   mission orchestration, provider, briefing, dispatch logging
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

Single canonical entry point: `bin/nim.agency <component> <action> [--flags]`

| Command | Backing module | Purpose | State |
|---|---|---|---|
| `dispatch [-y] [-v\|-vv]` | `lib/cli/dispatch.py` | gated mission execution (briefing → confirm → dispatch) | functional |
| `mission plan\|show\|list\|report` | `lib/cli/mission.py` | briefing preview, saved-mission inspection, reports | functional |
| `agent list\|show\|generate` | `lib/cli/agent.py` | roster operations | functional |
| `data verify\|seed\|schemas\|describe` | `lib/cli/data.py` | datastore init + schema inspection | functional |
| `tui` | `lib/cli/__init__.py` → `tui/builder.py` | curses agent builder (min 80x24) | functional, monolithic (~3600 lines) |
| Programmatic (Python) | `lib.data`, `lib.dispatch` | see README "Programmatic" | functional |

Shared mission flags (`dispatch`, `mission plan`): `--mission {codebase,security}`,
`--target DIR`, `--crew a,b`, `--count N`, `--timeout S`, `--output F`.
Any command that spawns agent work renders the centralized mission briefing
(`lib/dispatch/briefing.py`) first and requires confirmation (`-y` bypasses;
non-TTY without `-y` refuses, exit 2).

## COMPONENTS

| Component | Path | Purpose | Maturity |
|---|---|---|---|
| SchemaManager | `lib/data/schema.py` | schema loading/validation (`data/agency/schema/`) | stable |
| DataStore / CRUD | `lib/data/crud.py` | JSON datastore, soft-delete, queries | stable |
| AgentGenerator | `lib/data/generator.py` | procedural agents from `lex/` taxonomies, weighted class/tier | stable |
| AgentComposer | `lib/data/composer.py` | operational profile / system prompt assembly; tracks instruction source provenance (`OperationalProfile.sources`) | stable |
| OWL / CrüeRoster | `lib/data/owl.py` | crew assembly | stable |
| Mission / Constraints | `lib/dispatch/mission.py` | work orders, tasks, dependency graph, JSON persistence | stable |
| MissionBuilder + templates | `lib/dispatch/builder.py` | fluent builder; `codebase`/`security` templates | stable |
| Dispatcher | `lib/dispatch/dispatcher.py` | async execution, dependency ordering, report assembly (on success and failure); records provider policy refusals to the agent conduct ledger | stable |
| Briefing | `lib/dispatch/briefing.py` | centralized mission disclosure renderer (verbosity 0/-v/-vv); single scope authority (`mission_scope`) shared with dispatcher | stable |
| CLI | `lib/cli/` | argparse command modules behind `bin/nim.agency` | stable |
| Provider (claude-cli) | `lib/dispatch/provider.py` | executes tasks via `claude --print`; per-task timeout; `binary_path` disclosure; classifies failures (`policy_violation` vs generic) | stable |
| DispatchLogger | `lib/dispatch/ops_log.py` | CSV + JSON lifecycle logs → `~/.nim/agency/` | stable |
| Remote ops | `lib/remote/` (ssh, vault, protocol, keys, safety, ops_log) | asyncssh transport, Fernet vault, command classification, audit trail | implemented; needs live validation |
| TUI | `tui/builder.py` | RPG-themed agent builder HUD | functional; refactor candidate |

## CONVENTIONS & DEFAULTS

Codified in `etc/agency.json` (checked into source; treated as immutable at
runtime — overrides via CLI flags):

| Setting | Default | Consumer |
|---|---|---|
| `dispatch.provider` | `claude-cli` | dispatcher |
| `dispatch.max_concurrent` | `2` | `lib/cli/dispatch.py` |
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
- Custom missions not yet supported via CLI (programmatic only);
  `--dry-run` replaced by `nim.agency mission plan`.
- `tui/builder.py` is a single-file monolith.
- Remote layer (`lib/remote/`) untested against live infrastructure this revision.
- `mission show -v/-vv` recomposes prompts from the current datastore, not
  dispatch-time state (caveat printed in output).
