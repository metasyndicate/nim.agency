# nim.library

Profile library for generative character work. A profile is one subject, either a
band member (`character`, schema:Person) or the band itself (`group`, schema:MusicGroup).
It is built from rough raw sources (text and image archives) plus a few keys, then
carried through staged, repeatable steps toward LoRA training.

```
nim.library new profile --type character --tag LK5 --name Jody \
    --images /path/to/raw/photos --text /path/to/memoir.md \
    --keys "[Instrument:Lead Guitar,Voice:Tenor,Era:1990s]"
nim.library index lk5/jody        # hash, de-duplicate -> index.json
nim.library list [lk5|lk5/jody]   # profiles, or one profile's files
nim.library init lk5/jody         # resize, face-crop, caption; verdicts
nim.library stage lk5/jody        # kohya dataset from accepted images
nim.library train lk5/jody --epochs 1 --name lk5-jody-trial [--dry-run]
nim.library show lk5/jody
nim.library check
```

Stages: `new` → `index` → `init` → `stage` → `train` → `prompt`. The first five
exist; `prompt` (fixed comparison prompts) follows.

`init` writes `stage/` in each profile:
- `images/<id>.png`: downscaled to a 1024 px long side, plus `<id>-face.png` when there
  is exactly one face and it is small in the frame. Each has a `.txt` caption
  from the template in `etc/library.json`. Edit captions by hand freely; edits are kept.
- `init.json`: a verdict per image, `ok` / `review` (no face, several faces,
  low resolution) / `error`. Settle `review` items with
  `nim.library init REF --ok ID` or `--exclude ID`; `stage` uses only `ok`.
- `text/passages.jsonl`: memoir paragraphs that mention the subject's name or an
  alias (`--keys Alias:...`). Extracts .txt/.md directly and .docx/.rtf/.odt/.html
  with macOS `textutil`; .pdf is not handled yet.

`stage` copies every accepted image (override, else status, is `ok`), full frame
and face crop, with its caption into `stage/dataset/img/<repeats>_<trigger> <class>/`,
writes `stage/stage.json` (items, repeats, step estimate, warnings) and links the
dataset as `training/datasets/<tag>-<slug>`, so `train_lora.sh lk5-jody` trains it as
is. Repeats default to about 100 images per epoch (`etc/library.json` `stage`);
`--repeats N` overrides, `--no-link` skips the link. The dataset is rebuilt each run.

`train` runs `train_lora.sh <tag>-<slug> --max_train_epochs N --output_name NAME`
(flags after `--` are passed through), prints a time estimate first (`--dry-run` stops
there) and warns if Forge or an Ollama model is holding memory. Each run writes
`train/<run>.log` and `train/<run>.json` (steps, seconds, s/step, output + sha256); the
measured s/step becomes the next estimate. The LoRA lands in Forge's LoRA folder.

The image work runs in `lib/library/workers/images.py` under `init.python`
(sd-scripts' venv: Pillow + OpenCV Haar face detection).

## Layout

```
<root>/<tag>/group.json                     MusicGroup roster (kept in sync by `new`)
<root>/<tag>/profiles/<slug>/profile.json   the subject
                             sources/{text,images}/   symlinks to raw data (never copied)
                             index.json               every raw file, sha256, duplicates
                             manifest.json            PROV activities, one per stage run
```

The root is `--root`, then `$NIM_LIBRARY_ROOT`, then `etc/library.json` (`/Volumes/mf-arc/ai/library`).

## Keys

`--keys` takes `K:V` pairs. The keys listed in `etc/library.json`
(Instrument, Role, Voice, Class, Alias/AKA) fill typed fields: `roles[]` as
schema:OrganizationRole, `voiceType`, `class` and `alternateName`. Any other key
goes into `attributes` unchanged. Promote a key once it is used consistently.
Add sources or keys later with `new profile ... --update`.

## Design

- Schema first: `schema/library.schema.json` (JSON Schema 2020-12) validates
  every file before it is written. Term IRIs are in `etc/library.json` `terms`.
- Grounded by nim.discovery: schema.org MusicGroup, OrganizationRole, Person,
  ImageObject and CreativeWork, plus PROV-O for lineage.
- Engine: `lib/library` (python3, stdlib only). Shell: `library.sh` metashell module
  (`nim.mod.register /usr/local/nim/library` → `nim.library.*`, `nim.fn.help`).

Tests: `python3 -m unittest discover -s tests`
