"""
`nim.agency project` - stateful scoped work units.

    project create   charter a project (slug, title, worktree, host)
    project list     tabular project index
    project show     full disclosure: crew, host, objectives, journal
    project edit     update fields; add objectives/directives/specs/artifacts
    project assign   engage an agent with a role and responsibilities
    project release  end an agent's engagement (row becomes history)
"""

import sys
from pathlib import Path

from ..config import get_config, modality_violation, report_dir
from ..data import get_datastore
from ..data.project import (
    active_engagements, append_journal, assign_agent, binding_violation,
    complete_project, create_project, cycle_in_flight, engagement_status,
    find_project, next_cycle, next_objective_id, primary_worktree,
    project_status, release_agent,
)
from ..data.worklog import (
    log_queue_access, project_record_path, queue_banner, source_line,
)
from ..dispatch import (
    Dispatcher, build_briefing, project_cycle_mission,
    render_briefing,
)

from .agent import _find_agent
from .common import (
    confirm_dispatch, print_event, resolve_provider, save_report,
)


def _find_project_or_die(ds, ref: str) -> dict:
    project = find_project(ds, ref)
    if not project:
        print(f"Project not found: {ref}", file=sys.stderr)
        print("List projects with: nim.agency project list", file=sys.stderr)
        raise SystemExit(1)
    return project


def _find_substation(ds, codename: str) -> dict:
    match = [s for s in ds.query("substation").data
             if s.get("identity", {}).get("codename") == codename]
    if not match:
        print(f"\033[91mERROR: Substation not found: {codename}\033[0m",
              file=sys.stderr)
        raise SystemExit(1)
    return match[0]


def _codenames(ds) -> dict:
    """agent_id -> codename map for engagement disclosure."""
    return {a["id"]: a["identity"]["codename"]
            for a in ds.query("agent", include_retired=True).data}


def cmd_create(args) -> int:
    ds = get_datastore()

    worktree = None
    if args.target:
        target = Path(args.target).resolve()
        if not target.exists():
            print(f"\033[91mERROR: Target directory not found: {target}\033[0m",
                  file=sys.stderr)
            raise SystemExit(1)
        worktree = str(target)

    substation_id = None
    substation_name = "local"
    if args.substation:
        substation = _find_substation(ds, args.substation)
        substation_id = substation["id"]
        substation_name = args.substation

    try:
        project = create_project(
            ds, args.slug, args.title,
            description=args.description or "",
            priority=args.priority,
            worktree=worktree,
            writable=args.writable,
            substation_id=substation_id,
        )
    except ValueError as e:
        print(f"\033[91mERROR: {e}\033[0m", file=sys.stderr)
        return 1

    status = project_status(ds, project)
    mode = "read/write" if args.writable else "read-only"
    print("=" * 60)
    print("PROJECT CHARTERED")
    print("=" * 60)
    print(f"SLUG:      {project['slug']}")
    print(f"TITLE:     {project['title']}")
    print(f"STATUS:    {status.get('label', '?')}")
    print(f"PRIORITY:  {project['priority']}")
    print(f"HOST:      {substation_name}")
    if worktree:
        print(f"WORKTREE:  {worktree} ({mode}, primary)")
    print(f"ID:        {project['id']}")
    print(f"\nNext: assign crew with "
          f"nim.agency project assign {project['slug']} <codename> "
          f"--role lead --resp \"...\"")
    return 0


def cmd_list(args) -> int:
    ds = get_datastore()
    projects = ds.query("project", sort_key="slug").data
    if not projects:
        print("No projects. Charter one: nim.agency project create "
              "--slug my-project --title \"...\" --target /path")
        return 0

    statuses = {s["id"]: s for s in ds.load_reference("project_status")}
    print(f"{'SLUG':<24} {'STATUS':<12} {'PRI':>3} {'CREW':>4} {'OBJ':>7} "
          f"{'CYC':>3} TITLE")
    print("-" * 90)
    for project in projects:
        status = statuses.get(project.get("status_id"), {}).get("label", "?")
        crew = len(active_engagements(project))
        objectives = project.get("objectives", [])
        done = sum(1 for o in objectives if o.get("done"))
        cycles = len(project.get("journal", []))
        print(f"{project.get('slug', '?'):<24} {status:<12} "
              f"{project.get('priority', '?'):>3} {crew:>4} "
              f"{f'{done}/{len(objectives)}':>7} {cycles:>3} "
              f"{project.get('title', '')}")
    return 0


def cmd_show(args) -> int:
    ds = get_datastore()
    project = _find_project_or_die(ds, args.ref)
    record = project_record_path(ds, project)
    print(queue_banner(project, record))
    log_queue_access("show", project=project, path=record)
    status = project_status(ds, project)
    codenames = _codenames(ds)

    print(f"PROJECT:   {project['slug']}")
    print(f"TITLE:     {project['title']}")
    if project.get("description"):
        print(f"ABOUT:     {project['description']}")
    print(f"STATUS:    {status.get('label', project.get('status_id', '?'))}   "
          f"PRIORITY: {project.get('priority', '?')}")
    print(f"ID:        {project.get('id')}")

    binding = project.get("host_binding", {})
    substation_id = binding.get("substation_id")
    host = "local"
    if substation_id:
        substation = ds.read("substation", substation_id)
        host = (substation or {}).get("identity", {}).get(
            "codename", substation_id)
    permissions = ",".join(binding.get("required_permissions", []) or ["read"])
    print(f"HOST:      {host}   required permissions: {permissions}")
    for worktree in binding.get("worktrees", []):
        mode = "read/write" if worktree.get("writable") else "read-only"
        primary = "  (primary)" if worktree.get("primary") else ""
        print(f"WORKTREE:  {worktree.get('path')} ({mode}){primary}")

    policy = project.get("policy", {})
    print(f"POLICY:    read_only:{policy.get('read_only', True)}  "
          f"max_cycles:{policy.get('max_cycles') or '-'}  "
          f"max_concurrent_engagements:"
          f"{policy.get('max_concurrent_engagements', 1)}")

    engagements = project.get("engagements", [])
    if engagements:
        print("CREW:")
        for row in engagements:
            codename = codenames.get(row.get("agent_id"), row.get("agent_id"))
            state = engagement_status(ds, row)
            if row.get("released_at"):
                tag = f"RELEASED {row['released_at'][:10]}"
            else:
                tag = state.get("label", "?")
            print(f"  {codename:<24} {row.get('role', '?'):<12} {tag}")
            for resp in row.get("responsibilities", []):
                print(f"      - {resp}")

    objectives = project.get("objectives", [])
    if objectives:
        done = sum(1 for o in objectives if o.get("done"))
        print(f"OBJECTIVES ({done}/{len(objectives)} done):")
        for objective in objectives:
            box = "x" if objective.get("done") else " "
            print(f"  [{box}] {objective['id']}. {objective['text']}")
            if objective.get("acceptance"):
                print(f"        accept: {objective['acceptance']}")

    directives = project.get("directives", [])
    if directives:
        print("DIRECTIVES:")
        for directive in directives:
            print(f"  * {directive}")

    specifications = project.get("specifications", [])
    if specifications:
        print("SPECS:")
        for spec in specifications:
            label = spec.get("label") or "-"
            print(f"  {label:<20} {spec.get('path_or_url')}")

    artifacts = project.get("artifacts", [])
    if artifacts:
        print("ARTIFACTS:")
        for artifact in artifacts:
            label = artifact.get("label") or "-"
            kind = artifact.get("kind") or "?"
            print(f"  {label:<20} [{kind}] {artifact.get('path')}")

    journal = project.get("journal", [])
    if journal:
        print(f"JOURNAL ({len(journal)} cycle(s), last 5):")
        for entry in journal[-5:]:
            at = (entry.get("at") or "")[:19].replace("T", " ")
            print(f"  cycle {entry.get('cycle')}  "
                  f"mission:{entry.get('mission_id', '-')}  "
                  f"{entry.get('status', '?'):<10} {at}")
            if entry.get("note"):
                print(f"      {entry['note']}")
    return 0


def cmd_edit(args) -> int:
    ds = get_datastore()
    project = _find_project_or_die(ds, args.ref)
    updates = {}
    changes = []

    if args.title:
        updates["title"] = args.title
        changes.append(f"~ title: {args.title}")
    if args.description is not None:
        updates["description"] = args.description
        changes.append("~ description updated")
    if args.priority is not None:
        updates["priority"] = args.priority
        changes.append(f"~ priority: {args.priority}")

    if args.cap is not None:
        if args.cap < 1:
            print("\033[91mERROR: --cap must be at least 1\033[0m",
                  file=sys.stderr)
            return 1
        policy = dict(project.get("policy") or {})
        policy["max_concurrent_engagements"] = args.cap
        updates["policy"] = policy
        changes.append(f"~ max_concurrent_engagements: {args.cap}")

    if args.status:
        status = ds.get_reference_by_slug("project_status", args.status)
        if not status:
            slugs = ", ".join(
                s["slug"] for s in ds.load_reference("project_status"))
            print(f"\033[91mERROR: Unknown status '{args.status}' "
                  f"(one of: {slugs})\033[0m", file=sys.stderr)
            return 1
        previous = project_status(ds, project)
        updates["status_id"] = status["id"]
        changes.append(f"~ status: {previous.get('label', '?')} -> "
                       f"{status['label']}")

    if args.add_objective:
        if args.acceptance and len(args.add_objective) > 1:
            print("\033[91mERROR: --acceptance pairs with a single "
                  "--add-objective\033[0m", file=sys.stderr)
            return 1
        objectives = list(project.get("objectives", []))
        for text in args.add_objective:
            objective = {"id": next_objective_id({"objectives": objectives}),
                         "text": text, "done": False}
            if args.acceptance:
                objective["acceptance"] = args.acceptance
            objectives.append(objective)
            changes.append(f"+ objective {objective['id']}: {text}")
        updates["objectives"] = objectives

    if args.complete_objective:
        objectives = updates.get("objectives",
                                 list(project.get("objectives", [])))
        for objective_id in args.complete_objective:
            match = [o for o in objectives if o.get("id") == objective_id]
            if not match:
                print(f"\033[91mERROR: No objective {objective_id} on "
                      f"{project['slug']}\033[0m", file=sys.stderr)
                return 1
            match[0]["done"] = True
            changes.append(f"x objective {objective_id} done: "
                           f"{match[0]['text']}")
        updates["objectives"] = objectives

    if args.add_directive:
        directives = list(project.get("directives", []))
        for directive in args.add_directive:
            directives.append(directive)
            changes.append(f"+ directive: {directive}")
        updates["directives"] = directives

    if args.add_spec:
        specifications = list(project.get("specifications", []))
        for spec in args.add_spec:
            label, _, path = spec.partition("=")
            if not path:
                label, path = "", label
            specifications.append(
                {"label": label or path, "path_or_url": path})
            changes.append(f"+ spec: {label or path} -> {path}")
        updates["specifications"] = specifications

    if args.add_artifact:
        artifacts = list(project.get("artifacts", []))
        for artifact_arg in args.add_artifact:
            label, _, rest = artifact_arg.partition("=")
            if not rest:
                label, rest = "", label
            path, _, kind = rest.rpartition(":")
            if not path or kind not in ("report", "code", "doc"):
                path, kind = rest, "report"
            artifacts.append(
                {"label": label or path, "path": path, "kind": kind})
            changes.append(f"+ artifact: [{kind}] {path}")
        updates["artifacts"] = artifacts

    if not updates:
        print("Nothing to edit - see: nim.agency project edit --help",
              file=sys.stderr)
        return 1

    ds.update("project", project["id"], updates)
    print(f"PROJECT EDIT: {project['slug']}")
    for change in changes:
        print(f"  {change}")
    return 0


def cmd_assign(args) -> int:
    ds = get_datastore()
    project = _find_project_or_die(ds, args.ref)
    agent = _find_agent(ds, args.codename)

    try:
        engagement = assign_agent(
            ds, project, agent,
            role=args.role, responsibilities=args.resp or [])
    except ValueError as e:
        print(f"\033[91mERROR: {e}\033[0m", file=sys.stderr)
        return 1

    state = engagement_status(ds, engagement)
    print(f"ENGAGEMENT: {args.codename} -> {project['slug']}")
    print(f"  role:   {engagement['role']}")
    if engagement["responsibilities"]:
        print("  responsibilities:")
        for resp in engagement["responsibilities"]:
            print(f"    - {resp}")
    print(f"  status: {state.get('label', '?')} "
          f"({state.get('bagman_state', '?')})")
    print(f"  crew:   {len(active_engagements(project))} engaged on "
          f"{project['slug']}")
    return 0


def cmd_cycle(args) -> int:
    """Dispatch the project's stored work detail through the briefing-gated
    mission pipeline. Steering happens by editing the project between
    cycles - the cycle itself just runs what the project says."""
    ds = get_datastore()
    project = _find_project_or_die(ds, args.ref)

    status = project_status(ds, project)
    if not status.get("is_active"):
        print(f"\033[91mERROR: cycle refused - {project['slug']} is "
              f"{status.get('label', '?')}; resume it first: "
              f"nim.agency project resume {project['slug']}\033[0m",
              file=sys.stderr)
        return 1

    if (project.get("host_binding") or {}).get("substation_id"):
        print("\033[91mERROR: cycle refused - remote host binding requires "
              "access grants (phase D, not yet built). Rebind to the local "
              "host to cycle now.\033[0m", file=sys.stderr)
        return 1

    rows = active_engagements(project)
    if not rows:
        print(f"\033[91mERROR: no crew engaged on {project['slug']} - "
              f"assign first: nim.agency project assign {project['slug']} "
              f"<codename> --role lead --resp \"...\"\033[0m", file=sys.stderr)
        return 1

    if cycle_in_flight(project) and not args.force:
        print(f"\033[91mERROR: cycle refused - engagement(s) still swarming "
              f"from a previous cycle. Cycles serialize per project; pass "
              f"--force only if that cycle is dead.\033[0m", file=sys.stderr)
        return 1

    worktree = primary_worktree(project)
    if not worktree or not Path(worktree.get("path", "")).exists():
        print(f"\033[91mERROR: no usable worktree on {project['slug']} - "
              f"the primary worktree path must exist\033[0m", file=sys.stderr)
        return 1

    # Fail the briefing, not the mission: global posture vs project policy,
    # then policy vs host binding (permission-mismatch guardrail)
    refusal = modality_violation(
        bool((project.get("policy") or {}).get("read_only", True)), True)
    if not refusal:
        refusal = binding_violation(project)
    if refusal:
        print(f"\033[91mERROR: cycle refused - {refusal}\033[0m",
              file=sys.stderr)
        return 1

    provider = resolve_provider(ds, args)
    if provider is None:
        return 1

    agents = {}
    for row in rows:
        agent = ds.read("agent", row["agent_id"])
        if not agent:
            print(f"\033[91mERROR: engaged agent missing from datastore: "
                  f"{row['agent_id']}\033[0m", file=sys.stderr)
            return 1
        agents[row["agent_id"]] = agent

    policy = project.get("policy") or {}
    ran = 0
    for _ in range(args.n):
        cycle = next_cycle(project)
        if policy.get("max_cycles") and cycle > policy["max_cycles"]:
            print(f"POLICY: max_cycles ({policy['max_cycles']}) reached - "
                  f"no further cycles.")
            break

        # Queue processing ceremony: disclose exactly which queue sources
        # feed this cycle's composition, and audit-log each read
        record = project_record_path(ds, project)
        print(queue_banner(project, record))
        log_queue_access("cycle", project=project, path=record,
                         detail=f"composing cycle {cycle}")
        for spec in project.get("specifications", []):
            label = spec.get("label") or spec.get("path_or_url", "?")
            print(source_line(f"spec:{label}", spec.get("path_or_url", "?")))
            log_queue_access("spec", project=project,
                             path=spec.get("path_or_url"),
                             detail=f"cycle {cycle} composition")

        mission = project_cycle_mission(project, rows, agents, cycle)
        report_path = report_dir() / f"mission-{mission.id}-report.md"

        briefing = build_briefing(
            mission, ds=ds, report_path=report_path,
            provider=provider, verbosity=args.verbose)
        print(render_briefing(briefing, verbosity=args.verbose))

        # Fail the briefing, not the mission
        if briefing.capability_refusal:
            print(f"\033[91mERROR: cycle refused - "
                  f"{briefing.capability_refusal}\033[0m", file=sys.stderr)
            return 1 if ran == 0 else 0

        if not confirm_dispatch(args.yes):
            return 2 if ran == 0 else 0

        print(f"\nDISPATCHING CYCLE {cycle}...\n")
        dispatcher = Dispatcher(datastore=ds, provider=provider)
        result = dispatcher.execute_sync(
            mission, on_event=print_event,
            max_concurrent=get_config()["dispatch"]["max_concurrent"])

        save_report(result, report_path)

        # Re-read: the dispatcher moved engagement rows underneath us
        project = ds.read("project", project["id"])
        completed = sum(1 for t in result.tasks
                        if t.status.value == "complete")
        entry = append_journal(
            ds, project, cycle, mission.id, result.status.value,
            note=f"{completed}/{len(result.tasks)} tasks complete")
        print(f"\nJOURNAL: cycle {entry['cycle']}  mission:{mission.id}  "
              f"{entry['status']}  ({entry['note']})")

        print("ENGAGEMENTS:")
        codenames = _codenames(ds)
        for row in active_engagements(project):
            state = engagement_status(ds, row)
            print(f"  {codenames.get(row['agent_id'], row['agent_id']):<24} "
                  f"{state.get('label', '?')}")
        print(f"\nReview the report, close objectives you accept "
              f"(project edit {project['slug']} --complete-objective N), "
              f"steer, then cycle again.")

        ran += 1
        if result.status.value != "complete":
            return 1
        rows = active_engagements(project)
    return 0


def _set_project_status(ds, project: dict, slug: str) -> dict:
    status = ds.get_reference_by_slug("project_status", slug)
    previous = project_status(ds, project)
    ds.update("project", project["id"], {"status_id": status["id"]})
    print(f"PROJECT {status['label'].upper()}: {project['slug']} "
          f"({previous.get('label', '?')} -> {status['label']})")
    return status


def cmd_pause(args) -> int:
    ds = get_datastore()
    project = _find_project_or_die(ds, args.ref)
    if project_status(ds, project).get("is_terminal"):
        print(f"\033[91mERROR: {project['slug']} is closed - re-charter "
              f"instead\033[0m", file=sys.stderr)
        return 1
    _set_project_status(ds, project, "paused")
    print("  cycles refuse until resumed; engagements persist.")
    return 0


def cmd_resume(args) -> int:
    ds = get_datastore()
    project = _find_project_or_die(ds, args.ref)
    if project_status(ds, project).get("is_terminal"):
        print(f"\033[91mERROR: {project['slug']} is closed - re-charter "
              f"instead\033[0m", file=sys.stderr)
        return 1
    _set_project_status(ds, project, "active")
    return 0


def cmd_complete(args) -> int:
    ds = get_datastore()
    project = _find_project_or_die(ds, args.ref)
    status = project_status(ds, project)
    if status.get("is_terminal"):
        print(f"\033[91mERROR: {project['slug']} is already "
              f"{status.get('label', 'closed')}\033[0m", file=sys.stderr)
        return 1
    if cycle_in_flight(project):
        print(f"\033[91mERROR: a cycle is still swarming on "
              f"{project['slug']} - let it land first\033[0m",
              file=sys.stderr)
        return 1

    objectives = project.get("objectives", [])
    done = sum(1 for o in objectives if o.get("done"))
    released = complete_project(ds, project)
    print(f"PROJECT COMPLETE: {project['slug']}")
    print(f"  objectives: {done}/{len(objectives)} done   "
          f"cycles run: {len(project.get('journal', []))}   "
          f"engagements released: {released}")
    return 0


def cmd_release(args) -> int:
    ds = get_datastore()
    project = _find_project_or_die(ds, args.ref)
    agent = _find_agent(ds, args.codename)

    try:
        engagement = release_agent(ds, project, agent)
    except ValueError as e:
        print(f"\033[91mERROR: {e}\033[0m", file=sys.stderr)
        return 1

    print(f"RELEASED: {args.codename} from {project['slug']} "
          f"(engaged since {engagement['assigned_at'][:10]}, "
          f"role: {engagement.get('role', '?')})")
    remaining = len(active_engagements(project))
    print(f"  crew: {remaining} still engaged" if remaining
          else "  crew: none engaged")
    return 0


def add_parser(subparsers):
    p = subparsers.add_parser(
        "project", help="Stateful scoped work units (crew, host, objectives)")
    actions = p.add_subparsers(dest="action", required=True)

    create = actions.add_parser("create", help="Charter a project")
    create.add_argument("--slug", required=True,
                        help="Addressable reference (unique, lowercase)")
    create.add_argument("--title", required=True, help="Project title")
    create.add_argument("--description", help="What this project is about")
    create.add_argument("--target", help="Primary worktree path")
    create.add_argument("--writable", action="store_true",
                        help="Worktree is writable (default: read-only)")
    create.add_argument("--substation",
                        help="Substation codename (default: local host)")
    create.add_argument("--priority", type=int, default=5,
                        help="Priority 1-10, 1=critical (default: 5)")
    create.set_defaults(func=cmd_create)

    lst = actions.add_parser("list", help="List projects")
    lst.set_defaults(func=cmd_list)

    show = actions.add_parser("show", help="Full project disclosure")
    show.add_argument("ref", help="Project slug or id")
    show.set_defaults(func=cmd_show)

    edit = actions.add_parser("edit", help="Edit project fields")
    edit.add_argument("ref", help="Project slug or id")
    edit.add_argument("--title", help="New title")
    edit.add_argument("--description", help="New description")
    edit.add_argument("--priority", type=int, help="New priority (1-10)")
    edit.add_argument("--cap", type=int,
                      help="Per-agent concurrent engagement cap "
                           "(policy.max_concurrent_engagements)")
    edit.add_argument("--status",
                      help="New status slug (draft, active, paused, blocked, "
                           "review, complete, archived)")
    edit.add_argument("--add-objective", action="append", metavar="TEXT",
                      help="Add an objective (repeatable)")
    edit.add_argument("--acceptance",
                      help="Acceptance criteria for a single --add-objective")
    edit.add_argument("--complete-objective", action="append", type=int,
                      metavar="N", help="Mark objective N done (repeatable)")
    edit.add_argument("--add-directive", action="append", metavar="TEXT",
                      help="Add a standing directive (repeatable)")
    edit.add_argument("--add-spec", action="append", metavar="LABEL=PATH",
                      help="Add a specification reference (repeatable)")
    edit.add_argument("--add-artifact", action="append",
                      metavar="LABEL=PATH[:KIND]",
                      help="Declare an output artifact; KIND is report, "
                           "code, or doc (repeatable)")
    edit.set_defaults(func=cmd_edit)

    assign = actions.add_parser(
        "assign", help="Engage an agent with a role and responsibilities")
    assign.add_argument("ref", help="Project slug or id")
    assign.add_argument("codename", help="Agent codename")
    assign.add_argument("--role", default="specialist",
                        help="Engagement role (default: specialist)")
    assign.add_argument("--resp", action="append", metavar="TEXT",
                        help="Responsibility line (repeatable; becomes the "
                             "agent's task prompt at cycle time)")
    assign.set_defaults(func=cmd_assign)

    release = actions.add_parser(
        "release", help="End an agent's engagement")
    release.add_argument("ref", help="Project slug or id")
    release.add_argument("codename", help="Agent codename")
    release.set_defaults(func=cmd_release)

    cycle = actions.add_parser(
        "cycle",
        help="Dispatch the project's work detail (briefing gate + journal)")
    cycle.add_argument("ref", help="Project slug or id")
    cycle.add_argument("-n", type=int, default=1,
                       help="Cycles to run back-to-back (default: 1)")
    cycle.add_argument("-y", "--yes", action="store_true",
                       help="Bypass the confirmation gate")
    cycle.add_argument("--provider", type=str,
                       help="Syndicate to dispatch to, as 'syndicate' or "
                            "'syndicate:channel' (default: dispatch.provider "
                            "in etc/agency.json)")
    cycle.add_argument("--force", action="store_true",
                       help="Cycle even if a previous cycle looks in-flight")
    cycle.add_argument("-v", "--verbose", action="count", default=0,
                       help="Briefing verbosity (-v: constraints + prompt "
                            "sources, -vv: full prompts)")
    cycle.set_defaults(func=cmd_cycle)

    pause = actions.add_parser("pause", help="Pause a project (cycles refuse)")
    pause.add_argument("ref", help="Project slug or id")
    pause.set_defaults(func=cmd_pause)

    resume = actions.add_parser("resume", help="Reactivate a paused project")
    resume.add_argument("ref", help="Project slug or id")
    resume.set_defaults(func=cmd_resume)

    complete = actions.add_parser(
        "complete", help="Close a project and release its engagements")
    complete.add_argument("ref", help="Project slug or id")
    complete.set_defaults(func=cmd_complete)
