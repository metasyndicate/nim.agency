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
│   ├── cli/        CLI command modules (dispatch, mission, syndicate, agent, data, tui)
│   ├── data/       datastore, schema, agent generation & composition
│   ├── dispatch/   mission orchestration, syndicate inventory, providers,
│   │               briefing, dispatch logging, BIRD relay, agency harness
│   ├── install/    instance home + venv resolution, doctor, receipts, launcher
│   └── remote/     SSH transport, credential vault, safety, audit logging
├── data/agency/
│   ├── schema/     JSON Schema definitions (core, reference, meta)
│   └── data/       instance data (agents, groups, reference, instruction)
├── lex/            flavor vocabulary CSVs, described by csv-metadata.json (W3C CSVW)
├── tui/            curses HUD: builder.py core shell + chrome.py (draw
│                   primitives) + mugshots.py + views/*.py screen mixins
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
| `syndicate list\|show\|probe` | `lib/cli/syndicate.py` | model provider inventory: transports, channels, capability grants, egress/cost disclosure, recorded conformance; probe records reachability + advertised models and flags inventory drift (incl. tool_use granted on a model without the `tools` capability) | functional |
| `syndicate conform` | `lib/cli/syndicate.py` → `lib/dispatch/conformance.py` | fixed known-answer suite (`kat-1`) against a fixture tree; harness cases when the gate admits read-only tool missions, text cases otherwise; scorecard to `log/`, pass rate onto `status.conformance` | functional |
| `easme agent` (pipe) | `tools/easme` | pipestream dispatch: stdin payload + `--hint`/`--preset`; routed through the syndicate inventory (`--provider`, `--text-only`, `--tools`, `--expect[-json]`); capability gate at the briefing; clean stdout for pipelines; gate prompts on /dev/tty | functional |
| `agent list\|show\|generate` | `lib/cli/agent.py` | roster operations | functional |
| `agent credit\|train\|equip` | `lib/cli/agent.py` | economy: paymaster adjustments, dojo, depot | functional |
| `agent enroll [--backfill]` | `lib/cli/agent.py` | badge + primary keypair issuance into the depot vault | functional |
| `data verify\|seed\|schemas\|describe` | `lib/cli/data.py` | datastore init + schema inspection | functional |
| `data check [ENTITY...] [-v]` | `lib/cli/data.py` → `lib/data/refs.py` | conformance of stored data: each record against its schema (needs jsonschema) and every declared reference (`x-nim-ref`, incl. `within`/`where` bounds); exit 1 on invalid or dangling | functional |
| `data form\|options\|list\|show\|new\|edit\|delete ENTITY` | `lib/cli/compose.py` → `lib/data/forms.py`, `lib/data/drafts.py` | generic composition of ANY registered entity from its schema: field tree, choice options (reference tables, enums, dependent bounds), JSON Pointer edits (`--set/--add/--remove/--unset`), `--dry-run` diffs; read-only fields refused; deletes refused while referenced | functional |
| `syndicate form\|options\|new\|edit\|delete` | `lib/cli/syndicate.py` (mounts `compose`) | compose syndicates; channel capabilities bounded by the transport ceiling; `show` prints the hosting substation | functional |
| `substation list\|show\|form\|options\|new\|edit\|delete` | `lib/cli/substation.py` (mounts `compose`) | hosts under inventory (operator-local `~/.nim/agency/substations`); ssh/https/http/local connection kinds | functional |
| `install env\|doctor\|create\|list\|show\|remove` | `lib/cli/install.py` → `lib/install/` | instance setup: home resolution (`--home` > `NIM_HOME` > config > legacy `~/.nim/agency` > platform data dir; write-probed, sources reported), PEP 405 venv with PEP 735 dependency groups, PEP 668 aware, schema'd receipts; `doctor` checks (`--json`, exit 1 on fail). `bin/nim.agency` and `tools/easme` re-exec into the `default` instance's venv (`NIM_NO_VENV=1` opts out) | functional (I1-I5; update/repair, locks, Windows/Linux runs open) |
| `lex groups\|show\|random\|salad\|name\|check` | `lib/cli/lex.py` → `lib/data/lex.py` | flavor vocabulary: tables read by their CSVW description; seeded phrase/salad/name composition (port of the retired `lex/taxonomy.sh`); `check` conforms files to `lex/csv-metadata.json` | functional |
| `tui` | `lib/cli/__init__.py` → `tui/builder.py` | curses HUD: roster/dossier (op-profile, service record, edit form), create, dispatch (briefing gate + live events, direct from dossier/crue), missions browser, ledger, dojo, crues + member picker, stats, type foundry (min 80x24) | functional |
| Programmatic (Python) | `lib.data`, `lib.dispatch` | see README "Programmatic" | functional |

Shared mission flags (`dispatch`, `mission plan`):
`--mission {codebase,security,task}`, `--task PROMPT`, `--preset SLUG`
(one-action dispatch from the `task_preset` table; explicit flags win),
`--target DIR`, `--crew a,b`, `--skill t1,t2`, `--count N`,
`--timeout S`, `--output F`, `--provider SYNDICATE[:CHANNEL]`,
`--text-only` (declares the task needs no tools, so text-only syndicates
may serve it), `--tools A,B` (narrow allowed_tools to named harness
functions), `--expect TEXT` / `--expect-json JSON` (known-answer check per
task, settled before payroll).
Any command that spawns agent work renders the centralized mission briefing
(`lib/dispatch/briefing.py`) first and requires confirmation (`-y` bypasses;
non-TTY without `-y` refuses, exit 2).

Dispatch is gated twice over: `modality_violation` (what a mission may DO)
and `capability_violation` (what the target channel CAN do). A mission
requiring filesystem or shell access is refused by a text-only channel at
the briefing rather than answered from imagination.

## COMPONENTS

| Component | Path | Purpose | Maturity |
|---|---|---|---|
| SchemaManager | `lib/data/schema.py` | schema loading/validation (`data/agency/schema/`); https `$id`s resolve from a local store, validation degrades gracefully without `jsonschema` | stable |
| DataStore / CRUD | `lib/data/crud.py` | JSON datastore, soft-delete, queries; reference-table writes (`add_reference_entry`/`update_reference_entry`, id+slug immutable) | stable |
| AgentGenerator | `lib/data/generator.py` | procedural agents from `lex/` taxonomies, weighted class/tier | stable |
| Persona layers | `lib/data/persona.py` + `reference/{work_role,work_task,work_style,tone_axis,trait}.json` | archetypes grounded in O*NET occupations / NICE work roles (`work_role_slugs`): ROLE GROUNDING (description, Core tasks by importance) and WORKING STYLE (the occupation's highest-impact O*NET work styles + the agent's emphases) prompt sections; tone axes rendered as Voice under the communication protocol; lex flavor kept out of prompts; derived temperament for display. Tables rebuilt from publisher files by `tools/persona/` (O*NET CC BY 4.0 attribution in each table) | functional (evaluation open) |
| Lexicon | `lib/data/lex.py` + `lex/csv-metadata.json` | `lex/` tables described after W3C CSVW (columns, titles, required, separators, row unit as `dc:type`); RFC 4180 parsing by column name; conformance check; the generator's `LexLoader` reads through it | functional |
| Installer | `lib/install/` (paths, doctor, instance, launch) + `core/install.schema.json` | instance home and venv resolution with sources, environment checks, venv creation with dependency groups, receipts (operator-local `install` entity), launcher re-exec | functional |
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
| Syndicate inventory | `lib/dispatch/syndicate.py` + `data/agency/data/syndicates/` | multivalued model-provider inventory: address resolution (`syndicate[:channel]`), capability derivation from mission constraints, dependency closure, channel validation (dependency gaps + transport ceiling), and the `capability_violation` dispatch gate | functional |
| Providers | `lib/dispatch/provider.py` | one class per transport: `ClaudeCliProvider` (subprocess harness), `OllamaProvider` (`/api/generate`), `OllamaChatProvider` (`/api/chat`, tool calls), `OpenAICompatProvider` (`/v1/chat/completions`, tool calls); registry resolves the handler named by the `transport` table; stdlib-only HTTP (urllib on a worker thread); tool-call transports take the agency-harness path when a task is offered functions, single-shot otherwise; tasks without tools get an explicit no-tools notice so they cannot narrate inspections they never performed | functional |
| Agency harness | `lib/dispatch/harness/` + `reference/harness_function.json` | client-side tool loop for rogue syndicates: offer join (functions ∩ allowed_tools ∩ channel capabilities ∩ depot grants), policy executor (working-directory confinement incl. symlinks, offer-only, read-only, per-call deadline, no network), bounded loop (turns, calls/turn, wall clock, token budget, context-window budgeting with wrap-up), every call audit-logged as `tool_call`; serves Read/LS/Glob/Grep | functional (read-only; shell/write deferred) |
| Declared references | `lib/data/refs.py` + metaschema `$defs/x-nim-ref` | machine-readable foreign keys (`x-nim-ref {entity, key, within?, where?}`), integrity checks, inbound-reference lookup (deletes RESTRICT) | functional |
| Composition kernel | `lib/data/forms.py`, `lib/data/drafts.py` | schema → form model (field kinds scored from constraints, DASH-style); Drafts check readOnly/required/types/bounds/enums/references/`if`-`then` without jsonschema, then save through the validating datastore | functional |
| Validation modes | `lib/data/crud.py` `check()` + `etc/agency.json` `validation` | every write (create, update, replace, reference rows, ledger) validates when jsonschema is installed; per-entity `enforce` (refuse) or `warn` (log via `nim.data`) | functional |
| BIRD relay | `lib/dispatch/bird.py` + `reference/anomaly.json` | Biomimetic Inference Relay Dewarbler: every HTTP exchange with a syndicate passes `transmit`; link/frame/context/content anomalies recorded per task as RFC 9457 problems (ops log `anomaly`, `[BIRD]` events, report section, conformance scorecard); link faults retransmitted with full-jitter backoff / Retry-After within row limits, a per-task budget, and the deadline | functional (groundwork: no reask/fix yet, no circuit breaker) |
| Harness profiles + command policy | `reference/harness_profile.json`, `reference/command_policy.json` | channels name a profile (functions, commands, loop limits; missing profile fails closed); command_policy is the reviewed-before-use shell allowlist (allow/ask/deny, option allowlists, GTFOBins hazards) - inert until a Shell function exists | data + CRUD |
| Verification | `lib/dispatch/verification.py` | known-answer checks (`contains`, `json_subset`) settled by the dispatcher before payroll; wrong answers fail the task without a conduct charge; verdicts in reports, mission JSON, and ops log (`task_verify`) | functional |
| DispatchLogger | `lib/dispatch/ops_log.py` | CSV + JSON lifecycle logs → `~/.nim/agency/` | stable |
| Remote ops | `lib/remote/` (ssh, vault, protocol, keys, safety, hq_log) | asyncssh transport, Fernet vault, command classification, audit trail | implemented; needs live validation |
| TUI | `tui/builder.py` (core shell) + `tui/chrome.py` + `tui/mugshots.py` + `tui/views/*.py` (13 screen mixins) + `tui/textkit.py` | RPG-themed HUD, mixins over shared state (move-only split of the former 6k-line monolith); cell-accurate rendering; threaded live dispatch; substation Connect (suspends HUD, hands terminal to ssh(1), verbose failures); dossier Access view (A); foundry covers role_title/task_preset/transport/channel_capability/skill_domain/ops_domain/qualifier | functional |

## CONVENTIONS & DEFAULTS

Codified in `etc/agency.json` (checked into source; treated as immutable at
runtime — overrides via CLI flags):

| Setting | Default | Consumer |
|---|---|---|
| `dispatch.provider` | `claude_cli` | default syndicate address (`syndicate` or `syndicate:channel`) resolved through the inventory; `--provider` overrides per mission |
| `dispatch.egress_policy` | `public` | how far prompt content may travel: `local` (this host only), `private` (also operator-controlled networks/tailnet), `public` (also third-party APIs). Independent of `modality`: modality governs what a mission may DO, egress governs where its prompts GO. Disclosed in every briefing |
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

- Test coverage is seeded, not exhaustive: `tests/` covers the datastore,
  project aggregate, availability/matching, dispatch pipeline (stub
  provider), attribution, modality, and the syndicate inventory
  (resolution, capability truth table, channel validation, HTTP providers
  against a stubbed transport) — TUI and remote layers are still
  untested. Run: `python3 -m unittest discover -s tests`.
- TUI split landed move-only; deeper quality passes (mugshot art into
  data files, choice-helper consolidation, unused-import sweep) queue
  through `code_assimilation` cycles per module.
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
- Validation needs `jsonschema` (pyproject extra `validation`); without it
  writes rely on the composition kernel's stdlib checks only, and
  `data check` reports references but skips schema conformance. Agent
  and group records do not yet conform (entities in `warn` mode; plan
  O4/O5); archetype has 17 unresolved references (plan O6).
- Command policy is data only: no Shell harness function yet, and
  `lib/remote/safety.py` still uses its own hardcoded command sets (plan O7).
- BIRD remedies implemented: retransmit, record, fail. `reask` and `fix`
  are vocabulary only; no per-syndicate circuit breaker yet.
- Plan of record: `.nim/work/plan.101426.compose.md`.
- `mission show -v/-vv` recomposes prompts from the current datastore, not
  dispatch-time state (caveat printed in output).
- Resolved: the former `lib/remote/ops_log.py` (HQ remote-ops messaging) is
  now `lib/remote/hq_log.py`, ending the twin-filename collision with
  `lib/dispatch/ops_log.py` (dispatch lifecycle).
