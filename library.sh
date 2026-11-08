#!/usr/bin/env bash

# another *righteous entropy synthesis* by ⎰𝙉𝙄𝙈⎰ ...
# ----------------------------------------------------------------------------
#   NIM Substation One | Planet Earth | https://github.com/metasyndicate
# ----------------------------------------------------------------------------
#  [NIM.METASHELL.SUBSYSTEM]: ***library***
#    profile library for generative character work: subject profiles
#    (characters, groups) built from raw text and image archives, staged
#    for LoRA training. Engine: lib/library (python3, stdlib only).
# ----------------------------------------------------------------------------

nim.source core fn log output validate

# === GLOBALS ===

[[ -z ${NIM_LIBRARY_HOME} ]] && declare -gx NIM_LIBRARY_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -z ${NIM_LIBRARY_PY} ]]   && declare -gx NIM_LIBRARY_PY="${PYTHON:-python3}"
# NIM_LIBRARY_ROOT (optional) overrides etc/library.json "root"

# === PRIVATE ===

##! @function .nim.library.py.fn
##! @description run the python engine with the subsystem's lib on PYTHONPATH
##! @param [args...]       passed through to `python3 -m library`
##! @return the engine's exit status
function .nim.library.py.fn() {
    command -v "${NIM_LIBRARY_PY}" >/dev/null 2>&1 || {
        nim.log.error "library: ${NIM_LIBRARY_PY} not found in PATH"
        return 1; }
    PYTHONPATH="${NIM_LIBRARY_HOME}/lib${PYTHONPATH:+:${PYTHONPATH}}" \
        "${NIM_LIBRARY_PY}" -m library "${@}"; }

# === PUBLIC ===

##! @function nim.library.new
##! @description create a profile: directories, source links, profile.json, group roster
##! @param <what>                       profile
##! @param --type <character|group>     subject kind
##! @param --tag <TAG>                  collection tag, e.g. LK5
##! @param --name <name>                display name
##! @param [--text <path>]              raw text file or dir (repeatable)
##! @param [--images <path>]            raw image file or dir (repeatable)
##! @param [--keys "[K:V,...]"]         attributes; Instrument/Role/Voice/Class/Alias are typed
##! @param [--update]                   add sources/keys to an existing profile
##! @return 0 on success, 1 on failure
##! @example nim.library.new profile --type character --tag LK5 --name Jody --images ~/raw/jody --keys "[Instrument:Lead Guitar,Voice:Tenor]"
function nim.library.new() {
    nim.fn.validate.args "${@}" || return 1
    nim.validate.enum "${1}" profile || return 1
    .nim.library.py.fn new "${@}" || { nim.out.fail "library: new failed"; return 1; }; }

##! @function nim.library.index
##! @description hash and de-duplicate a profile's sources into index.json
##! @param <ref>           <tag>/<slug>, or <tag> for every profile in it
##! @return 0 on success, 1 on failure
##! @example nim.library.index lk5/jody
function nim.library.index() {
    nim.fn.validate.args "${@}" || return 1
    .nim.library.py.fn index "${@}" || { nim.out.fail "library: index failed"; return 1; }; }

##! @function nim.library.list
##! @description list profiles (all or one tag), or one profile's indexed files
##! @param [ref]           <tag> or <tag>/<slug>
##! @param [--json]        machine-readable output
##! @output table or JSON on stdout
function nim.library.list() { .nim.library.py.fn list "${@}"; }

##! @function nim.library.show
##! @description print a profile.json
##! @param <ref>           <tag>/<slug>
function nim.library.show() {
    nim.fn.validate.args "${@}" || return 1
    .nim.library.py.fn show "${@}"; }

##! @function nim.library.check
##! @description validate profile, group, index and manifest files against the schema
##! @param [ref]           <tag> or <tag>/<slug>
##! @return 0 if valid, 1 otherwise
function nim.library.check() { .nim.library.py.fn check "${@}"; }

##! @function nim.library.init
##! @description resize, face-crop and caption images; extract memoir passages; or set hand verdicts
##! @param <ref>                         <tag>/<slug>
##! @param [--only images|text]          redo one half only
##! @param [--ok|--exclude|--clear ID..] hand verdicts on images, kept across re-runs
##! @return 0 on success, 1 on failure
##! @example nim.library.init lk5/jody
function nim.library.init() {
    nim.fn.validate.args "${@}" || return 1
    .nim.library.py.fn init "${@}" || { nim.out.fail "library: init failed"; return 1; }; }

##! @function nim.library.stage
##! @description build the kohya dataset from accepted images and link it into training/datasets
##! @param <ref>           <tag>/<slug>
##! @param [--repeats N]   kohya repeats per image
##! @param [--no-link]     do not link into the training datasets dir
##! @return 0 on success, 1 on failure
##! @example nim.library.stage lk5/jody --repeats 10
function nim.library.stage() {
    nim.fn.validate.args "${@}" || return 1
    .nim.library.py.fn stage "${@}" || { nim.out.fail "library: stage failed"; return 1; }; }
