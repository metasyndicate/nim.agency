# Seed data

Tracked fixtures that give a fresh checkout a working roster. Instance data
(`data/agency/data/agents/`, the depot vault) stays operator-local and
untracked; these files are the shipped starting point copied into it.

    bin/nim.agency data seed --fixtures

Copies every `agents/*.json` into the roster (skipping ids already present),
then enrolls the newly seeded agents - issuing fresh keypairs into
`data/agency/hive/depot/.vault/`.

## Rules

- **No key material, ever.** Records carry no `crypto` block; keys are issued
  at enrollment on the operator's host. `tests/test_seed.py` enforces this.
- Records conform to `agent.schema.json` (persona v2; tested when
  `jsonschema` is installed). Regenerate or migrate them when the schema moves.
- Records are sources, not live state: dispatch accrues XP/gold on the roster
  copy, never here.

## Sample

| codename | class | rank | domain / focus | why it is here |
|---|---|---|---|---|
| experimental.blaster | I | grunt | application / interface | only record with a specialty |
| evolutionary.engine | III | operator | application / interface | class III coverage |
| scholarly.modeller | II | berserker | system / datacenter | `deployed` status, high rank, newest generator |
