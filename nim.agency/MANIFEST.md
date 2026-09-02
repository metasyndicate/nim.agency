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
| `dispatch [-y] [-v\|-vv]` | `lib/cli/dispatch.py` | gated mission execution (briefing → confirm → dispatch → payroll) | functional |
| `mission plan\|show\|list\|report` | `lib/cli/mission.py` | briefing preview, saved-mission inspection, reports | functional |
| `project create\|list\|show\|edit\|assign\|release` | `lib/cli/project.py` | stateful scoped work units: crew engagements, host binding, objectives, directives | functional |
| `project cycle\|pause\|resume\|complete` | `lib/cli/project.py` | briefing-gated cycle dispatch from project state; journal; per-project serialization; lifecycle verbs | functional (local host; remote awaits phase D) |
| `easme agent` (pipe) | `tools/easme` | pipestream dispatch: stdin payload + `--hint`/`--preset`; clean stdout for pipelines; gate prompts on /dev/tty | functional |
| `agent list\|show\|generate` | `lib/cli/agent.py` | roster operations | functional |
| `agent credit\|train\|equip` | `lib/cli/agent.py` | economy: paymaster adjustments, dojo, depot | functional |
| `agent enroll [--backfill]` | `lib/cli/agent.py` | badge + primary keypair issuance into the depot vault | functional |
| `data verify\|seed\|schemas\|describe` | `lib/cli/data.py` | datastore init + schema inspection | functional |
| `tui` | `lib/cli/__init__.py` → `tui/builder.py` | curses HUD: roster/dossier (op-profile, service record, edit form), create, dispatch (briefing gate + live events, direct from dossier/crue), missions browser, ledger, dojo, crues + member picker, stats, type foundry (min 80x24) | functional, monolithic (~5900 lines) |
| Programmatic (Python) | `lib.data`, `lib.dispatch` | see README "Programmatic" | functional |

Shared mission flags (`dispatch`, `mission plan`):
`--mission {codebase,security,task}`, `--task PROMPT`, `--preset SLUG`
(one-action dispatch from the `task_preset` table; explicit flags win),
`--target DIR`, `--crew a,b`, `--skill t1,t2`, `--count N`,
`--timeout S`, `--output F`.
Any command that spawns agent work renders the centralized mission briefing
(`lib/dispatch/briefing.py`) first and requires confirmation (`-y` bypasses;
non-TTY without `-y` refuses, exit 2).

## COMPONENTS

| Component | Path | Purpose | Maturity |
|---|---|---|---|
| SchemaManager | `lib/data/schema.py` | schema loading/validation (`data/agency/schema/`); https `$id`s resolve from a local store, validation degrades gracefully without `jsonschema` | stable |
| DataStore / CRUD | `lib/data/crud.py` | JSON datastore, soft-delete, queries; reference-table writes (`add_reference_entry`/`update_reference_entry`, id+slug immutable) | stable |
| AgentGenerator | `lib/data/generator.py` | procedural agents from `lex/` taxonomies, weighted class/tier | stable |
| AgentComposer | `lib/data/composer.py` | operational profile / system prompt assembly; tracks instruction source provenance (`OperationalProfile.sources`) | stable |
| OWL / CrüeRoster | `lib/data/owl.py` | crew assembly | stable |
| Roster matching | `lib/data/roster.py` | competency-term crew selection (skills/tools/specialties/capabilities, scored + disclosed); draft-hire fallback; `available_only` consults project bookings with BOOKED disclosure | functional |
| Projects | `lib/data/project.py` | aggregate-root work units: engagement rows (reuse `assignment_status`), host binding, objectives, journal; derived availability + booking cap; mission outcomes drive engagement transitions (swarming/review/smoked); cycle engine composes missions from project state (`lib/dispatch/builder.py:project_cycle_mission`) | functional |
| Economy | `lib/data/economy.py` | mission payroll, rank/qualifier progression, gold ledger, dojo (`train`) + depot (`equip`) | functional |
| Identity | `lib/data/identity.py` | badge + immutable ed25519 keypair issuance; depot vault custody (`hive/depot/.vault/<dogtag>/`, 0700/0600) | functional |
| Attribution | `lib/data/attribution.py` | codified crediting convention (in perpetuity): git author + trailers, personified banner, code byline; `spawn_context()` captures the operator/host/session identity the crew piggybacks on, stamped onto missions (`dispatched_by`) and disclosed in briefings + report footers | functional |
| Mission / Constraints | `lib/dispatch/mission.py` | work orders, tasks, dependency graph, JSON persistence; optional `project_id`/`cycle` tags | stable |
| MissionBuilder + templates | `lib/dispatch/builder.py` | fluent builder; `codebase`/`security` templates | stable |
| Dispatcher | `lib/dispatch/dispatcher.py` | async execution, dependency ordering, report assembly (on success and failure); records provider policy refusals to the agent conduct ledger; drives project engagement transitions for project-tagged missions | stable |
| Briefing | `lib/dispatch/briefing.py` | centralized mission disclosure renderer (verbosity 0/-v/-vv); single scope authority (`mission_scope`) shared with dispatcher | stable |
| CLI | `lib/cli/` | argparse command modules behind `bin/nim.agency` | stable |
| Provider (claude-cli) | `lib/dispatch/provider.py` | executes tasks via `claude --print`; per-task timeout; `binary_path` disclosure; classifies failures (`policy_violation` vs generic) | stable |
| DispatchLogger | `lib/dispatch/ops_log.py` | CSV + JSON lifecycle logs → `~/.nim/agency/` | stable |
| Remote ops | `lib/remote/` (ssh, vault, protocol, keys, safety, ops_log) | asyncssh transport, Fernet vault, command classification, audit trail | implemented; needs live validation |
| TUI | `tui/builder.py` + `tui/textkit.py` | RPG-themed HUD; cell-accurate rendering (`textkit` handles wide-glyph clipping/padding); threaded live dispatch; substation Connect (suspends HUD, hands terminal to ssh(1), verbose failures); dossier Access view (A); foundry covers role_title/task_preset/skill_domain/ops_domain/qualifier | functional; refactor candidate |

## CONVENTIONS & DEFAULTS

Codified in `etc/agency.json` (checked into source; treated as immutable at
runtime — overrides via CLI flags):

| Setting | Default | Consumer |
|---|---|---|
| `dispatch.provider` | `claude-cli` | dispatcher |
| `dispatch.max_concurrent` | `2` | `lib/cli/dispatch.py` |
| `dispatch.timeout_seconds` | `600` | mission templates; `--timeout` overrides |
| `dispatch.modality` | `readonly` | global posture: `readonly` (missions must be read-only + local), `supervised` (write-capable local allowed where explicitly configured), `unrestricted` (remote too - inert until phase D). Checked at the briefing AND by the dispatcher backstop; unknown values clamp to `readonly` |
| `dispatch.report_dir` | `log` (project-relative) | report + mission JSON output |
| `logging.dir` | `~/.nim/agency` | dispatch/audit logs |
| `economy.*` | `25`/`10` per task, `15`/`5` bonus, `10` sp/rank | payroll + promotion rates (`lib/data/economy.py`) |

Output artifacts per mission run:

- `log/mission-<id>-report.md` — assembled report incl. payroll (written on success **and** failure)
- `log/mission-<id>-report.json` — full mission record: prompts, constraints, per-task results/errors
- `data/agency/data/transactions/ledger.json` — immutable gold ledger
- `data/agency/hive/depot/.vault/<dogtag>/` — key material + manifest (gitignored)
- `~/.nim/agency/dispatch.log` / `dispatch.json` — lifecycle event logs
- `~/.nim/agency/remote_operations.json` — remote ops audit trail (mode 0600)

## KNOWN GAPS

- No test suite.
- No dependency declaration (core is stdlib; `asyncssh`, `cryptography`,
  `jsonschema` optional — see README).
- `tui/builder.py` is a single-file monolith (~4900 lines; split candidate).
- Key distribution/revocation flows not yet built (vault issuance only);
  mission outcomes feed the economy but not achievements.
- TUI dispatch backgrounds its worker thread if the operator leaves the
  run screen; no reattach view yet beyond re-entering Dispatch.
- Project subsystem phases D–E pending (access grants + remote dispatch,
  TUI projects screen) — tracked in
  `.nim/instructions/nim.agency.projects.bootstrap.md`.
- Substation Deploy (TUI `D`) reports its missing prerequisites honestly
  but does not yet execute; ships with phase D access grants. Capability
  foundry editing skipped (structurally complex rows).
- Remote layer (`lib/remote/`) untested against live infrastructure this revision.
- `mission show -v/-vv` recomposes prompts from the current datastore, not
  dispatch-time state (caveat printed in output).
- `lib/dispatch/ops_log.py` and `lib/remote/ops_log.py` share a filename but
  are distinct (dispatch lifecycle vs HQ remote-ops messaging) — rename
  candidate, not a consolidation candidate.
