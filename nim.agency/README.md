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
python3 lib/data/seed.py --verify
python3 lib/data/seed.py --seed-agents 10

# see who's on the roster
python3 bin/dispatch.py --list-agents

# preview a mission without executing
python3 bin/dispatch.py --mission codebase --target /path/to/repo --dry-run

# dispatch a crew
python3 bin/dispatch.py --mission codebase --target /path/to/repo --count 3

# launch the TUI agent builder (min terminal 80x24)
python3 bin/nim tui
```

## INTERFACES

### Command: `bin/dispatch.py`

```
python3 bin/dispatch.py [options]

  --mission {codebase,security,custom}   mission template (default: codebase)
  --target DIR                           target directory (default: .)
  --crew name.one,name.two               explicit crew by codename
  --count N                              crew size when auto-selecting (default: 3)
  --timeout S                            per-task timeout override
  --output FILE                          report path override
  --dry-run                              print the mission plan, don't execute
  --list-agents                          show available agents
```

### TUI: `bin/nim tui`

Interactive curses agent builder — create and configure agents, allocate
skill points, manage keys and credentials.

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

Every dispatch announces its output locations up front and writes:

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
bin/          CLI entry points (nim, dispatch.py)
etc/          source-controlled defaults (agency.json)
lib/data/     datastore, schema, agent generation & composition
lib/dispatch/ missions, dispatcher, provider, logging
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
