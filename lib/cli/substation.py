"""
`nim.agency substation` - hosts under agency inventory.

A substation is a host-level configuration item that hosts syndicate
endpoints (plan D-HOST). Records are operator-local (~/.nim/agency), so
they hold hostnames and usernames that do not belong in the repo.

    substation list | show REF | form | options POINTER
    substation new  --set /identity/name=bonsai ...
    substation edit REF --set ... | delete REF

All verbs are the generic composition verbs (lib/cli/compose.py) with the
entity fixed; REF is an id, codename, or name.
"""

from . import compose


def add_parser(subparsers):
    p = subparsers.add_parser(
        "substation",
        help="Hosts under inventory (list, show, form, options, new, edit, delete)")
    actions = p.add_subparsers(dest="action", required=True)
    compose.add_verbs(actions, "substation", read_verbs=True)
