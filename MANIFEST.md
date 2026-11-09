# NIM LIBRARY — MANIFEST

Component inventory, state snapshot and operator runbook. Revision 26.11.8.x.

Profile library for generative character work: one profile per subject (a band
member, or the band), built from raw text and image archives plus a few keys,
carried through repeatable stages to a trained LoRA and comparison renders.
First collection: `lk5`, first profile: `lk5/jody`.

## PIPELINE

```
 raw archives (never copied)      keys "[Instrument:Lead Guitar,...]"
   photos/  memoir text                     │
        │                                   │
        ▼                                   ▼
  ┌──────────┐  profile.json, group.json, sources/{images,text}/ symlinks
  │ new      │──────────────────────────────────────────────────────────┐
  └────┬─────┘                                                          │
       ▼                                                                │
  ┌──────────┐  index.json: every raw file, sha256, duplicates, skips   │
  │ index    │                                                          │
  └────┬─────┘                                                          │
       ▼                                                                │
  ┌──────────┐  stage/images/<id>.png (+ -face.png) + .txt captions     │
  │ init     │  stage/text/passages.jsonl (memoir paragraphs naming      │
  └────┬─────┘  the subject); stage/init.json verdicts ok|review|error  │ every stage
       │                                                                │ appends a
       ├── operator review:  init REF --ok ID | --exclude ID            │ PROV activity
       │                     (edit .txt captions freely; kept)          │ to
       ▼                                                                │ manifest.json
  ┌──────────┐  stage/dataset/img/<repeats>_<trigger> <class>/          │
  │ stage    │  stage/stage.json; link training/datasets/<tag>-<slug>   │
  └────┬─────┘                                                          │
       ▼                                                                │
  ┌──────────┐  train_lora.sh (kohya sd-scripts, SD1.5, mps)            │
  │ train    │  -> models/sd/lora/<name>.safetensors (Forge's folder)    │
  └────┬─────┘  train/<run>.json + .log                                 │
       ▼                                                                │
  ┌──────────┐  same seed, LoRA weight 0 vs 1 per prompt                 │
  │ prompt   │  prompt/<run>/p<N>-w<W>.png, sheet.png, prompt.json  ─────┘
  └──────────┘
```

Each stage reads only the previous stage's files, so any stage can be re-run
alone; hand decisions (overrides, edited captions) survive re-runs.

## LAYOUT

```
nim.library/
├── bin/nim.library        launcher: boots the metashell (module registration), then python3 -m library
├── library.sh             metashell module: nim.library.{new,index,init,stage,train,prompt,list,show,check}
├── .nim/nim.sys.cfg.sh    metashell manifest (NAME library, MODS ( library ))
├── etc/library.json       source-controlled defaults: root, formats, keys, init/stage/train/prompt, term IRIs
├── schema/library.schema.json   JSON Schema 2020-12 ($id urn:nim:library:1), one $def per file written
├── lib/library/
│   ├── cli.py             argparse front end, one cmd_* per command
│   ├── util.py            paths, config, atomic JSON writes (returns sha256), slug/token
│   ├── store.py           Library: profiles, group roster, keys, source links, PROV record()
│   ├── validate.py        stdlib JSON Schema validator (from nim.discovery)
│   ├── index.py  init.py  stage.py  train.py  prompt.py      one module per stage
│   └── workers/           run under sd-scripts' venv (Pillow, OpenCV, torch); JSON over stdin/stdout
│       ├── images.py      decode (HEIC via sips), EXIF rotate, resize, Haar faces, face crops
│       └── generate.py    sd-scripts offline loader + LoRA + DPM++ sampler on mps; contact sheet
└── tests/                 python3 -m unittest discover -s tests (22; workers mocked or skipped)
```

Library data (root: `--root` > `$NIM_LIBRARY_ROOT` > `etc/library.json` `root`,
default `/Volumes/mf-arc/ai/library`):

```
<root>/<tag>/group.json                     schema:MusicGroup roster, synced by `new`
<root>/<tag>/profiles/<slug>/
    profile.json                            schema:Person | MusicGroup; trigger, class, roles, keys
    sources/{images,text}/<name>            symlinks to raw files or dirs
    index.json  manifest.json               inventory; PROV lineage (one activity per run)
    stage/images/  stage/text/  stage/init.json
    stage/dataset/img/<repeats>_<trigger> <class>/   stage/stage.json
    train/<run>.json  train/<run>.log
    prompt/<run>/{p*-w*.png, sheet.png, prompt.json}
```

## INTERFACES

Entry points: `nim.library <command>` (bin), or `nim.library.<command>` after
`nim.mod.register /usr/local/nim/library` in a metashell session.

| Command | Module | Purpose | State |
|---|---|---|---|
| `new profile --type character\|group --tag T --name N [--images P].. [--text P].. [--keys K] [--update]` | `store.py` | create or extend a profile; typed keys fill fields, the rest go to `attributes` | functional |
| `index REF\|TAG` | `index.py` | hash and de-duplicate sources | functional |
| `init REF [--only images\|text]` | `init.py` + `workers/images.py` | resize, face-crop, caption; memoir passages; verdicts | functional (PDF text not extracted) |
| `init REF --ok\|--exclude\|--clear ID..` | `init.py` | hand verdicts (id prefixes accepted), kept across re-runs | functional |
| `stage REF [--repeats N] [--no-link]` | `stage.py` | kohya dataset from accepted images, linked for `train_lora.sh` | functional |
| `train REF [--epochs N] [--name N] [--dry-run] [-- FLAGS]` | `train.py` | run `train_lora.sh`; estimate from last measured s/step; run record | functional (trial run only) |
| `prompt REF [--lora F] [--prompt T].. [--weights W,..] [--seed N] [--steps N] [--size N] [--dry-run]` | `prompt.py` + `workers/generate.py` | comparison renders with and without the LoRA | functional |
| `list [TAG\|REF] [--json]` | `cli.py` | profiles table, or one profile's indexed files | functional |
| `show REF` | `cli.py` | print profile.json | functional |
| `check [REF]` | `cli.py` + `validate.py` | validate profile, group, index, manifest, init, stage files; broken links | functional |

## DATA DEPENDENCIES

| Dependency | Used by | Location / setting | Notes |
|---|---|---|---|
| python3 (stdlib only) | engine | system `python3` | no third-party imports outside `workers/` |
| sd-scripts venv (py3.10, torch 2.6, Pillow, OpenCV, diffusers, transformers) | `init`, `prompt` workers; `train` | `init.python`, `prompt.python`, `prompt.sd_scripts` → `/Volumes/mf-arc/ai/tools/sd-scripts` | `prompt` imports sd-scripts' `library.model_util` and `networks.lora` |
| `train_lora.sh` | `train` | `train.script` → `/Volumes/mf-arc/ai/training/train_lora.sh` | unchanged; its `BASE`/`OUT` must match `prompt.ckpt`/`train.lora_dir` |
| Base checkpoint | `train`, `prompt` | `Realistic_Vision_V5.1_Hyper_pruned.safetensors` | SD1.5 |
| CLIP tokenizer | `prompt` | HF cache `openai/clip-vit-large-patch14` | loaded `local_files_only` (no network) |
| macOS `sips` / `textutil` | `init` | system | HEIC decode; docx/rtf/odt/html text |
| Forge LoRA folder | `train` output | `train.lora_dir` → `/Volumes/mf-arc/ai/models/sd/lora` | trained LoRAs appear in Forge's Lora tab |
| `/Volumes/mf-arc` | everything | external volume | library root, tools, models; must be mounted |

## INTEGRATION

- **nim.metashell** — `library.sh` is a standard module (`nim.source core fn log
  output validate`, `##!` docs, `nim.fn.help nim.library.<cmd>`); registered
  with `nim.mod.register /usr/local/nim/library` (symlink to this repo).
  Registering through a symlink needed the `find -H` fix in `nim.source`
  (metashell branch `claude/source-follow-symlinks`). `bin/nim.library` boots
  the metashell with `set +u` then hands off to python.
- **nim.discovery** — phase-0 grounding for the schema: schema.org MusicGroup,
  OrganizationRole, Person, ImageObject, CreativeWork; PROV-O for lineage. IRIs
  live in `etc/library.json` `terms`; `validate.py` is discovery's validator.
- **nim.agency** — no runtime coupling yet. Character profiles parallel agency
  agents (generated identity vs. archive-derived identity); a shared Person
  shape is a later merge candidate. The metashell taxonomy (`lib/agency/lexicon`)
  and agency's `lex` port are the vocabulary sources for agent names, not for
  library profiles.
- **methodology** — built under its model-first workflow: schema first, every
  write validated, decisions grounded in published vocabularies.

## CONVENTIONS & DEFAULTS

From `etc/library.json` (checked in; overrides via CLI flags):

| Setting | Default | Consumer |
|---|---|---|
| `class` | character→`person`, group→`band` | kohya class word; `--class` overrides |
| trigger | `token(tag)+token(slug)` (`lk5jody`), group `lk5group` | `new --trigger` overrides |
| `init.max_side` / `min_side` | 1024 / 384 px | resize; below `min_side` → review |
| `init.face_crop_scale` / `face_crop_min` | 3.0 / 512 px | face crop when exactly one small face |
| `init.caption` / `caption_face` | `{trigger} {class}` / `…, close-up portrait` | caption templates |
| `stage.steps_per_epoch` / `max_repeats` | 100 / 40 | repeats = ceil(100 / images); `--repeats` overrides |
| `stage.min_images` | 15 | warning threshold |
| `train.epochs` / `sec_per_step` | 10 / 180 | `--epochs`; estimate until a run measures one |
| `prompt.prompts` | portrait; `playing {role} on stage, 1990s concert photo` | `--prompt` (repeatable) |
| `prompt.weights` / `seed` / `steps` / `size` | `[0, 1]` / 1234 / 20 / 512 | flags of the same names |
| `prompt.dtype` | `float16` | UNet + text encoder; VAE always float32 |

IDs: raw items are the first 12 hex digits of their sha256; runs are UTC
`YYYYMMDDTHHMMSSZ`. Every JSON file is validated before it is written, and
written atomically.

## RUNBOOK

One subject, end to end (`lk5/jody` as the example):

```bash
nim.library new profile --type character --tag LK5 --name Jody \
    --images /path/to/photos --text /path/to/memoir.docx \
    --keys "[Instrument:Lead Guitar,Voice:Tenor,Alias:J.D.]"
nim.library index lk5/jody
nim.library init lk5/jody                  # lists review items with reasons
open /Volumes/mf-arc/ai/library/lk5/profiles/jody/stage/images   # look; edit .txt captions
nim.library init lk5/jody --ok ced8 d0d3 --exclude a8b0
nim.library stage lk5/jody                 # warns under 15 images
nim.library train lk5/jody --dry-run       # steps and time estimate
nim.library train lk5/jody                 # quit Forge first; hours to days on this host
nim.library prompt lk5/jody                # sheet.png: base vs LoRA, same seed
nim.library check lk5/jody
```

- Adding material later: `new profile --tag LK5 --name Jody --update --images P`,
  then `index` → `init` → `stage` again (overrides and caption edits are kept).
- Before `train`/`prompt`: quit Forge (`launch.py`) and unload Ollama models
  (`ollama ps`); `train` warns when either holds memory. The host is 8 GB:
  prefer lower memory settings over speed.
- Measured on nim-ss-willow (M3, 8 GB): training ~270 s/step (4-step trial,
  2026-11-08); generation 20–45 s/image at 512 px, float16, 20 steps.
- Lineage: `manifest.json` lists every run with inputs, outputs and sha256s;
  `train/` and `prompt/` keep per-run records and logs.

## KNOWN GAPS

- `prompt` on mps needs PyTorch SDPA attention (`unet.set_use_sdpa(True)`):
  sd-scripts' own attention returns NaN there, which showed up as all-black
  images (the `blank` flag catches it). float32 exceeds 8 GB and thrashes swap,
  so half precision (float16) is the default.
- `class` is a plain `person` by default, so the base model guesses age and
  gender per prompt; set it per subject (`--keys Class:man`) before training.
- Contact sheet labels are truncated to the row width.
- Only a 4-step trial LoRA exists (`lk5-jody-trial`); it proves the pipeline,
  not likeness. Jody has 4 photos against a 15–40 guideline.
- No PDF text extraction; memoir passages are extracted but not yet used
  (captions are templates, not drawn from the text).
- Captions are trigger + class only; per-image descriptions (setting, era,
  instrument) are hand edits until a captioner is added (BLIP weights not
  downloaded).
- Haar face detection misses profiles and stylised artwork; those land in review.
- No `group` profile trained yet; the group roster is kept but has no dataset.
- `train` runs in the foreground; long runs should be started under
  `nohup`/`tmux` (no detach or resume).
- No GitHub remote for this repo yet.
