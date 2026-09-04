# NIM AGENCY — CODE CONVENTIONS

The internally established principles, patterns, nomenclature, and
prohibited practices. The `code_assimilation` task preset audits modules
against this document; new work is expected to conform from the start.

## PRINCIPLES

1. **Observable ceremonies over silent side effects.** Every
   state-changing command prints what it did (uppercase labeled sections,
   banners, per-change lines). Every read of a work-queue source is
   audit-logged and announced (`Processing Work Queue: ...`).
2. **The briefing gate fronts anything that spawns agent work.** Full
   disclosure first, confirmation second, dispatch third. Non-TTY without
   `--yes` refuses (exit 2); piped stdin prompts on `/dev/tty`.
3. **Fail the briefing, not the mission.** Policy checks (modality,
   binding, availability) refuse before composition, with an actionable
   message naming the knob that would change the answer.
4. **Core is stdlib-only.** `asyncssh`, `cryptography`, `jsonschema` are
   optional extras; every code path degrades gracefully without them and
   says so honestly (skip notices, prerequisite messages - never a
   silent no-op and never a hard crash).
5. **The datastore is the single source of truth.** Static vocabularies,
   presets, and assets belong in `data/` files loaded through the data
   layer - never hardcoded in code, never heredoc'd. Reference tables
   are the relational anchors: `id` and `slug` immutable once created.
6. **Reference, never copy.** Aggregates store ids/slugs/paths pointing
   at canonical records. Denormalization requires a stated reason.
7. **Attribution in perpetuity.** Agent-credited output routes through
   `lib/data/attribution.py`; missions carry `dispatched_by` spawn
   context. Never invent a new attribution format.
8. **Honest reporting.** Placeholder behavior is labeled as such, caps
   and truncations are disclosed, failures carry stderr - "fizzling out"
   is a defect class.

## STRUCTURE & NAMING

- Modules: lowercase, single concern, target < ~1,000 lines. A file past
  that is a split candidate; screens/views become mixins over shared
  `self` state, extracted move-only (no call-site churn).
- Methods: `snake_case`; `_underscore` for internal; verbs first
  (`_draw_*`, `_handle_*`, `_enter_*`, `_open_*`, `_save_*` in the TUI;
  `cmd_*` in CLI modules; bare nouns for pure helpers).
- CLI grammar: `nim.agency <component> <action> [--flags]`; each
  component is one `lib/cli/*.py` module exposing `add_parser()` +
  `cmd_*` functions returning int exit codes (0 ok, 1 error, 2 gate
  declined).
- Reference tables ship as `{"$schema": ..., "data": [...]}` wrappers;
  schemas carry `$nim.meta` with a unique `nim.id`.
- Docstrings: first line says what and why in plain voice; constraints
  the code can't show go in comments; no narration of the obvious.
- Errors to stderr in red (`\033[91m...`); helpful next-command hints on
  empty states.

## TESTING

- `tests/` is stdlib `unittest`; every class runs against an isolated
  temp datastore seeded from real reference data (`tests/support.py`).
- Both postures pass: with `jsonschema` (real validation) and without
  (graceful degradation). Posture-dependent tests pin their config.
- New guardrails ship with their truth table; new formats with content
  assertions.

## PROHIBITED

- Heredocs or in-code literals for static data that belongs in `data/`
  (ASCII art, vocabularies, templates, seed rows).
- Reimplementing a centralized helper locally (check `lib/config`,
  `lib/data`, `lib/dispatch`, `tui/textkit` before writing one).
- Swallowing exceptions without surfacing a message (the fizzle).
- Dead code and advertised-but-unbound keys/flags.
- `sys.path` bootstraps anywhere but the canonical entry points.
- Mutating `etc/agency.json` at runtime; operator overrides are flags.
- Bypassing `modality()` / the briefing gate from any entry point.
- Interactive prompts in library code (prompting belongs to the CLI/TUI
  layer; libraries return refusals).
