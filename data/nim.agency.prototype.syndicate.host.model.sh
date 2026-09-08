#!/usr/bin/env bash

# [09.06.26]@bradley >> TODO: consider nim.metashell integration / conventions
# [09.06.26]@bradley >> TODO: integrate with nim.agency, schematize and merge data
# syndicate.sh: auxiliary/bespoke biomimetitic service provider command & control

# [09.06.26]@bradley >> prototype ollama on nim substation bonsau (es-au04959)
#   - needs code convention assimilation, general tuning/normalization
#   - `syndicate.sh` metashell integration, module/funtion onboarding
#   - extract data from code, store in metashell store(s)
#   - substations host one or more syndicates/channels, schematize 

# --- parameters ---

# [09.06.26]@bradley >> TODO: load from data (metashell json or codex)
#   implement multiple agent syndicates 

NIM_UNIT_MODEL="qwen3-coder:30b"; TEMPERATURE=0.2                         
NIM_UNIT_URI="http://localhost:11434/api/generate"                
NIM_UNIT_PROMPT="You are an expert developer auxiliary unit. \
Give concise, highly accurate answers without fluff."

# --- functions ---

nim.syndicate.help() {
    echo -e "Usage: $(basename "$0") [subcommand] [options]\n\n"
    echo -e "Subcommands:\n"; printf "%16s%64s\n" "task" "General technical or conceptual queries"
    printf "%16s%64s\n" "code" "Strictly generates code blocks with no conversational filler"
    echo -e "Options:"; printf "%16s%64s\n" "-m, --message \"text\"" "Pass message via argument"
    printf "%16s%64s\n" "-t, --temp VALUE" "Control parameter: Creativity/Randomness (0.0 to 1.0, default 0.2)"
    printf "%16s%64s\n" "-h, --help" "Show this help menu\n\n"
    echo -e "Examples:\n"; echo -e "\techo \"What is a pointer?\" | $(basename "$0") ask"
    echo -e "\t$(basename "$0") code -m \"Python binary search\" --temp 0.0"; exit 0; }


# --- subcommand capture ---

SUBCOMMAND="${1}"; if [[ -z "${SUBCOMMAND}" || "${SUBCOMMAND}" == "-h" || \
"${SUBCOMMAND}" == "--help" ]]; then nim.syndicate.help; fi; shift 
# move past the subcommand to parse remaining options
MESSAGE=""; while [[ $# -gt 0 ]]; do case "$1" in
    -m|--message) MESSAGE="${2}"; shift 2 ;; -t|--temp) TEMPERATURE="${2}"; shift 2 ;;
    *) echo -e "UNKNOWN OPTION: $1\n"; nim.syndicate.help; ;; esac;done

# --- pipe input handling ---

# pipe *fires on empty message argument*, check for stdin data
if [[ -z "${MESSAGE}" ]]; then; if [ ! -t 0 ]; then MESSAGE=$(cat); else
    echo -e "error: empty set\n"; nim.syndicate.help; fi; fi

# --- subcommand modifier handling ---

# [09.06.26]@bradley >> TODO: load from data (metashell json or codex)
case "${SUBCOMMAND}" in ask) ;; code) NIM_UNIT_PROMPT="REQUIRED OUTPUT: valid, \
functional source code, syntax or short technical response, inferred from input \
conversational chat or explanations outside markdown blocks is PROHIBITED" ;;
*) echo "Unknown subcommand: ${SUBCOMMAND}"; nim.syndicate.help ;; esac

# --- syndicate data payload ---

PAYLOAD=$(jq -n --arg mdl "${NIM_UNIT_MODEL}" --arg prpt "${MESSAGE}" \
  --arg sys "${NIM_UNIT_PROMPT}" --argjson temp "${TEMPERATURE}" \
  '{model: $mdl, prompt: $prpt, system: $sys,
    stream: false, options: { temperature: $temp } }')

# --- syndicate dispatch ---

RESPONSE=\ # syndicate channel response data
$(curl -s -X POST "${NIM_UNIT_URI}" -H "Content-Type: application/json" -d "${PAYLOAD}")

(( $? != 0 )) || [[ -z "${RESPONSE}" ]] && { # error code or empty response, handle ...
    echo "error: syndicate channel anomaly (${NIM_UNIT_URI})"; exit 1; }

# --- response data extraction ---

echo "${RESPONSE}" | jq -r '.response' # parse response data, extract and display field(s)
