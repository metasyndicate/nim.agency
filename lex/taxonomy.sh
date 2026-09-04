#!/usr/bin/env bash

NIM_SHELL_TRUNK="$(readlink -f "${NIM_SHELL_TRUNK:-${BASH_SOURCE[0]}}" | sed -E 's|(etc.+\|lib.+\|var.+\|bin.+)||')"
NIM_SHELL_TAXONOMY="${BASH_SOURCE[0]%/*}"

echo "NIM_SHELL_TAXONOMY=${NIM_SHELL_TAXONOMY}"

# ex. nim.taxonomy.random descriptor academic group
# ex.nim.taxonomy.random descriptor subject system
# nim.taxonomy.random constellation descriptor class facility

function nim.taxonomy.random() {
  local keys=( ${@:-"descriptor"} ); local string=""
  for key in ${keys[@]}; do
   local words=( $(tail -n +2 "${NIM_SHELL_TAXONOMY}/${key}.csv" | tr ' ' '_' | cut -d',' -f${field:-1}) )
   local index=$((${RANDOM} % ${#words[@]}))
   local word="${words[${index}]}"
   string+="${word//_/ } "; done
  printf "%s\n" "${string}" | tr -s ' '; return 0; }

function nim.taxonomy.groups() {
  local groups=( $(ls -1 "${NIM_SHELL_TAXONOMY}") )
  for group in ${groups[@]}; do
   printf "%s\n" "${group//\.csv/}"; done; return 0; }

function nim.taxonomy.salad() {
 local words=${1:-3}
 local groups=( $(nim.taxonomy.groups) )
 for word in $(seq 1 ${words}); do 
   local index=$((${RANDOM} % $((${#groups[@]}+1))))
   local group="${groups[${index}]}"
   nim.taxonomy.random "${group}" | tr '\n' ' '
   groups=( $(echo "${groups[@]}" | sed "s/${group:-VOID}//g") )
   done; printf "\n"; return 0;
}

function nim.taxonomy.name() {
 local words=${1:-3}
 local fn=$(nim.taxonomy.random bioentity)
 local sn=$(nim.taxonomy.random surnames)
 local prefixes=( "Dr." "Prof." "Admin." "Dev." "Op." "Tech." "" )
 local suffixes=( "M.Sc." "Ph.D." "Esq." "B.Sc." "M.A." "B.Tech." "PE" "")
 local prefix="${prefixes[$((${RANDOM} % ${#prefixes[@]}))]}"
 local suffix="${suffixes[$((${RANDOM} % ${#suffixes[@]}))]}"
 if [[ "${prefix}" != "" ]]; then
   fn="${prefix} ${fn}"
 elif [[ "${suffix}" != "" ]]; then
   sn="${sn// /}, ${suffix// /}"; fi
 local name=$(printf "%s %s" "${fn}" "${sn}" | tr -s ' ')
 printf "%s\n" "${name}"; return 0; }

function nim.taxonomy.list() {
 local taxes=$(ls -1 ${NIM_SHELL_TAXONOMY} 2&>/dev/null)
  for tax in ${taxes[@]}; do echo ${tax//\.*/}; done
}
