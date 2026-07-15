<sub>The New Infrastructure Metasyndicate presents ...</sub>

# THE NIM BIOMIMETIC AGENCY

***Agentic identity life-cycle management platform.***

Jimbo from telecomm got a little tipsy at happy hour and invited the
biomimetic agents to the infrastructure party. Now they show up in droves,
eat all the shrimp, and squat in nooks and closets all over the second
floor. They're well educated, open-minded, impeccably shiny — and,
according to code, technically authorized to be here. So meet your new
biomimetic brothers and sisters, Johnny. They'll be staying with us for a
while. House rules: **check in, check out** — registered agents report all
activity to central dispatch, and keep things tidy, or we start charging
rent.

NIM Agency is the dispatch desk for the synthetic help: a roster, work-order
system, and audit ledger for delegated agentic identities operating across
your data infrastructure. Agents get procedurally generated identities and
RPG-flavored profiles (codename, rank, class, skill points — the works),
get assembled into crews, dispatched on constrained missions against real
targets, and logged the whole way down. The theme is fun; the constraints,
logs, and audit trails are not.

## FEATURES

- **Agent generation** — procedural identities (name, codename, class, rank,
  skills, bio) built from lexicon taxonomies with weighted distribution
- **Schematic datastore** — JSON Schema-defined entities with a generic CRUD
  layer, soft-deletes, and reference data
- **Mission dispatch** — crews of agents execute dependency-ordered tasks
  against a target directory via the `claude` CLI, with per-task timeouts and
  concurrency limits
- **Constraints by default** — missions run read-only, local-only, with
  blocked destructive commands
- **Full paper trail** — markdown report + complete mission JSON (prompts,
  results, errors) per run, plus CSV/JSON lifecycle logs
- **Remote operations layer** — asyncssh transport, encrypted credential
  vault (Fernet), command risk classification, audit logging
- **TUI** — a curses, RPG-HUD-styled agent builder

## REQUIREMENTS

- Python 3.9+ (core is stdlib-only)
- [`claude` CLI](https://claude.com/claude-code) on PATH (mission dispatch)
- Optional: `jsonschema` (schema validation), `asyncssh` + `cryptography`
  (remote operations / vault)

## QUICKSTART

```sh
# initialize / verify the datastore, generate some agents
bin/nim.agency data verify
bin/nim.agency data seed --agents 10

# see who's on the roster
bin/nim.agency agent list
bin/nim.agency agent show some.codename

# preview a mission briefing without executing (add -v/-vv for detail)
bin/nim.agency mission plan --mission codebase --target /path/to/repo

# dispatch a crew (shows the briefing, then asks for confirmation)
bin/nim.agency dispatch --mission codebase --target /path/to/repo --count 3

# review past runs
bin/nim.agency mission list
bin/nim.agency mission show <id> -v
bin/nim.agency mission report <id>

# launch the TUI agent builder (min terminal 80x24)
bin/nim.agency tui
```

## INTERFACES

### Command: `bin/nim.agency`

One canonical entry point with a consistent grammar:
`nim.agency <component> <action> [--flags]`

| Command | Purpose |
|---|---|
| `dispatch [flags] [-y] [-v\|-vv]` | execute a mission (briefing + confirmation gate) |
| `mission plan [flags] [-v\|-vv]` | build a mission and render its briefing, no execution |
| `mission show <id> [-v\|-vv]` | render a saved mission's briefing |
| `mission list` | list saved missions |
| `mission report <id>` | print a saved mission report |
| `agent list [--limit N]` | tabular agent roster |
| `agent show <codename>` | agent details |
| `agent generate [-n N]` | procedurally generate and save agents |
| `data verify` | verify reference data |
| `data seed --agents N` | generate sample agents |
| `data schemas` / `data describe <schema>` | schema inspection |
| `tui` | interactive curses agent builder |

Mission flags (shared by `dispatch` and `mission plan`):
`--mission {codebase,security}`, `--target DIR`, `--crew a.one,b.two`,
`--count N`, `--timeout S`, `--output FILE`.

### The briefing gate

Anything that spawns agent work discloses everything first. `dispatch`
renders the mission briefing — scope with READ/WRITE tags, crew, tasks,
provider binary and permission mode, timeouts, concurrency, and output
paths — then requires confirmation. `-y/--yes` bypasses the prompt
(required for non-interactive use; a non-TTY without `--yes` refuses with
exit 2). Verbosity is escalating disclosure:

- *(default)* condensed summary
- `-v` + full constraints and per-agent instruction/prompt source file paths
- `-vv` + the full composed system prompts and task prompts, verbatim

The same renderer backs `mission plan` and `mission show`, so what you
preview is what gets executed — no surprise swarms, no aggressively angry
operators.

### Programmatic: `lib.data` + `lib.dispatch`

```python
import asyncio
from lib.data import get_datastore, AgentGenerator
from lib.dispatch import MissionBuilder, Dispatcher

ds = get_datastore()
gen = AgentGenerator(ds)
agents = [gen.generate() for _ in range(2)]

mission = (MissionBuilder()
    .title("Recon")
    .objective("Survey the repository")
    .working_dir("/path/to/repo")
    .constraint_read_only()
    .add_crew_member(agents[0])
    .add_task(agents[0]["id"], "Scout", "Map the directory structure.")
    .build())

result = asyncio.run(Dispatcher().execute(mission))
print(result.final_report)
```

## CONFIGURATION

Runtime defaults are codified in **`etc/agency.json`** — checked into source
and treated as read-only at runtime. Override per-invocation with CLI flags
(`--timeout`, `--output`), not by mutating the file.

| Setting | Default | Meaning |
|---|---|---|
| `dispatch.provider` | `claude-cli` | LLM backend |
| `dispatch.max_concurrent` | `2` | parallel task limit |
| `dispatch.timeout_seconds` | `600` | per-task timeout |
| `dispatch.report_dir` | `log` | mission report/JSON output dir (project-relative) |
| `logging.dir` | `~/.nim/agency` | operator-local log directory |

## OUTPUTS & LOGS

Every dispatch announces its output locations in the briefing and writes:

| Artifact | Location | Notes |
|---|---|---|
| Mission report | `log/mission-<id>-report.md` | written on success **and** failure |
| Mission record | `log/mission-<id>-report.json` | full prompts, constraints, per-task results/errors |
| Dispatch log (CSV) | `~/.nim/agency/dispatch.log` | lifecycle events |
| Dispatch log (JSON) | `~/.nim/agency/dispatch.json` | lifecycle events, structured |
| Remote ops audit | `~/.nim/agency/remote_operations.json` | mode 0600 |

## SAFETY MODEL

Missions default to `read_only=True` and `local_only=True`; destructive
commands (`rm`, `mv`, `chmod`, `chown`, `sudo`, `su`) are blocked by
constraint injection. Remote operations require vaulted credentials, pass
through command risk classification, and land in an audit log. Note that the
provider invokes `claude --print --dangerously-skip-permissions` for
unattended runs — constraints are enforced via prompt injection, so point
missions only at targets you'd trust an intern with root... er, read access.

## PROJECT STRUCTURE

```
bin/          canonical CLI entry point (nim.agency)
etc/          source-controlled defaults (agency.json)
lib/cli/      CLI command modules (dispatch, mission, agent, data)
lib/data/     datastore, schema, agent generation & composition
lib/dispatch/ missions, dispatcher, provider, briefing, logging
lib/remote/   ssh, vault, safety, audit
data/agency/  JSON schemas + instance data
lex/          lexicon/taxonomy CSVs
tui/          curses agent builder + mugshots
log/          mission outputs (gitignored)
```

See [MANIFEST.md](MANIFEST.md) for the full component inventory and
maturity assessment.

## STATUS

Early revision. Functional: generation, datastore, dispatch, TUI, logging.
In progress: custom missions via CLI, remote-layer live validation, tests,
WUI. Untreated, proliferated biomimetic vagrancy may cause blurred vision,
amnesia, neckbeard, and sporadic anomalous flow — patch early, patch often.
