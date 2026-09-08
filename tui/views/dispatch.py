"""
Dispatch - mission form, preset picker, briefing gate, threaded live run.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses
import threading
from pathlib import Path

from lib.config import get_config as agency_config, report_dir
from lib.data.roster import match_agents, draft_hire
from lib.dispatch import (
    Dispatcher, SyndicateError, adhoc_task_mission, capability_violation,
    get_provider,
)
from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class DispatchMixin:
    """mission form, preset picker, briefing gate, threaded live run."""

    DISPATCH_FIELDS = [
        ("task", "Task", "What the agent(s) should do (required)"),
        ("skill", "Skill terms", "Comma-separated competency terms for crew matching"),
        ("target", "Target dir", "Working directory for the mission"),
        ("count", "Crew size", "Agents to assign when matching by skill"),
    ]

    def _enter_dispatch_mode(self):
        """Open the dispatch mission form."""
        if self.dispatch_running:
            self.mode = "dispatch_run"
            return
        self.dispatch_field_index = 0
        self.dispatch_brief = None
        self.dispatch_preset = None
        self.mode = "dispatch"

    def _cycle_dispatch_preset(self, direction: int = 1):
        """Stamp the form from the next task_preset (one-action dispatch:
        T to pick, P to brief, Y to launch). Fields stay visible and
        editable - the preset is a starting point, not a cage."""
        presets = sorted(self.ds.load_reference("task_preset"),
                         key=lambda p: p.get("sort_order", p["id"]))
        if not presets:
            self.message = "No presets defined - add some in the Type Foundry"
            self.message_color = Colors.HEALTH
            return
        if self.dispatch_preset is None:
            index = 0 if direction > 0 else len(presets) - 1
        else:
            current = next((i for i, p in enumerate(presets)
                            if p["slug"] == self.dispatch_preset["slug"]), -1)
            index = (current + direction) % len(presets)
        preset = presets[index]
        self.dispatch_preset = preset
        self.dispatch_form["task"] = preset.get("prompt", "")
        if preset.get("skill"):
            self.dispatch_form["skill"] = preset["skill"]
        if preset.get("count"):
            self.dispatch_form["count"] = str(preset["count"])
        self.message = (f"PRESET {index + 1}/{len(presets)}: "
                        f"{preset['label']}")
        self.message_color = Colors.STAT_VALUE

    def _draw_dispatch_form(self, height: int, width: int):
        """Draw the ad-hoc mission form."""
        self._draw_header(width)

        box_width = min(72, width - 4)
        box_height = 14
        box_y = 6
        box_x = (width - box_width) // 2
        self._draw_box(box_y, box_x, box_height, box_width, "DISPATCH - MISSION FORM")

        y = box_y + 2
        for i, (field_key, label, hint) in enumerate(self.DISPATCH_FIELDS):
            is_selected = (i == self.dispatch_field_index)
            value = self.dispatch_form.get(field_key, "")
            if is_selected and self.editing:
                display = f"[{self.edit_buffer}_]"
                color = Colors.HIGHLIGHT
            else:
                display = value or "(unset)" if field_key == "task" else value
                color = Colors.HIGHLIGHT if is_selected else Colors.STAT_VALUE
            self._safe_addstr(y, box_x + 2, f"{label:<12}",
                              curses.color_pair(Colors.STAT_LABEL))
            self._safe_addstr(y, box_x + 15, clip(display, box_width - 18),
                              curses.color_pair(color))
            y += 1
            if is_selected:
                self._safe_addstr(y, box_x + 15, clip(hint, box_width - 18),
                                  curses.color_pair(Colors.DIM))
            y += 1

        # Preset attribution
        y += 1
        if self.dispatch_preset:
            icon = self.dispatch_preset.get("icon") or ""
            preset_note = (f"Preset: {icon} {self.dispatch_preset['label']} "
                           f"[{self.dispatch_preset['slug']}]").strip()
            self._safe_addstr(y, box_x + 2, clip(preset_note, box_width - 4),
                              curses.color_pair(Colors.STAT_VALUE))
        else:
            self._safe_addstr(y, box_x + 2,
                              clip("Preset: (none - T cycles the foundry's "
                                   "task presets)", box_width - 4),
                              curses.color_pair(Colors.DIM))
        y += 1

        # Crew source disclosure
        if self.dispatch_crew_override:
            crew_note = f"Crew: {self.dispatch_crew_label}"
        elif self.selected_agents:
            crew_note = f"Crew: operator shortlist ({len(self.selected_agents)} agent(s))"
        elif self.dispatch_form.get("skill"):
            crew_note = "Crew: competency-matched from roster (draft-hire fallback)"
        else:
            crew_note = "Crew: first available from roster"
        self._safe_addstr(y, box_x + 2, clip(crew_note, box_width - 4),
                          curses.color_pair(Colors.GOLD))

        self._draw_message(box_y + box_height + 1, width)
        if self.editing:
            self._draw_footer(height, width, "Type to edit | ENTER: Apply | ESC: Cancel")
        else:
            self._draw_footer(height, width,
                              "UP/DOWN: Field | ENTER: Edit | T: Preset | "
                              "P: Plan Briefing | Q: Back")

    def _handle_dispatch_form_input(self, key: int):
        """Handle dispatch form input."""
        if self.editing:
            if key == 27:
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_ENTER, 10, 13):
                field_key = self.DISPATCH_FIELDS[self.dispatch_field_index][0]
                self.dispatch_form[field_key] = self.edit_buffer.strip()
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.edit_buffer = self.edit_buffer[:-1]
            elif 32 <= key <= 126:
                self.edit_buffer += chr(key)
            return

        field_count = len(self.DISPATCH_FIELDS)
        if key == curses.KEY_UP:
            self.dispatch_field_index = (self.dispatch_field_index - 1) % field_count
        elif key == curses.KEY_DOWN:
            self.dispatch_field_index = (self.dispatch_field_index + 1) % field_count
        elif key in (curses.KEY_ENTER, 10, 13):
            field_key = self.DISPATCH_FIELDS[self.dispatch_field_index][0]
            self.edit_buffer = self.dispatch_form.get(field_key, "")
            self.editing = True
        elif key == ord('t'):
            self._cycle_dispatch_preset(1)
        elif key == ord('T'):
            self._cycle_dispatch_preset(-1)
        elif key == ord('p') or key == ord('P'):
            self._prepare_dispatch_brief()
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.dispatch_crew_override = []
            self.dispatch_crew_label = ""
            self.mode = "menu"

    def _prepare_dispatch_brief(self):
        """Resolve crew + build the mission; open the briefing gate."""
        task = self.dispatch_form.get("task", "").strip()
        if not task:
            self.message = "Task prompt is required"
            self.message_color = Colors.HEALTH
            return

        target = Path(self.dispatch_form.get("target", ".") or ".").expanduser().resolve()
        if not target.is_dir():
            self.message = f"Target not found: {target}"
            self.message_color = Colors.HEALTH
            return

        try:
            count = max(1, int(self.dispatch_form.get("count") or "1"))
        except ValueError:
            count = 1

        try:
            provider = get_provider()
        except SyndicateError as exc:
            self.message = str(exc)
            self.message_color = Colors.HEALTH
            return
        if not provider.is_available():
            self.message = provider.unavailable_reason
            self.message_color = Colors.HEALTH
            return

        # Crew resolution, fully disclosed
        crew, reasons = [], []
        if self.dispatch_crew_override:
            crew = self.dispatch_crew_override
            reasons = [(a["identity"]["codename"], self.dispatch_crew_label)
                       for a in crew]
        elif self.selected_agents:
            crew = [a for a in self.roster if a["id"] in self.selected_agents]
            reasons = [(a["identity"]["codename"], "operator shortlist")
                       for a in crew]
        elif self.dispatch_form.get("skill", "").strip():
            terms = [t.strip() for t in self.dispatch_form["skill"].split(",")
                     if t.strip()]
            matches = match_agents(terms, count=count, ds=self.ds)
            if matches:
                crew = [m.agent for m in matches]
                reasons = [(m.codename,
                            f"score:{m.score} " + "; ".join(m.reasons[:3]))
                           for m in matches]
            else:
                hire = draft_hire(self.ds)
                self._load_roster()
                crew = [hire]
                reasons = [(hire["identity"]["codename"],
                            "drafted - no qualified agent on roster")]
        else:
            crew = self.roster[:count]
            reasons = [(a["identity"]["codename"], "first available")
                       for a in crew]

        if not crew:
            self.message = "No agents available - generate some first"
            self.message_color = Colors.HEALTH
            return

        title = None
        if (self.dispatch_preset
                and self.dispatch_preset.get("prompt", "").strip() == task):
            title = f"Preset: {self.dispatch_preset['label']}"
        mission = adhoc_task_mission(crew, str(target), task, title=title)

        # Fail the briefing, not the mission: a channel without a harness
        # cannot serve a filesystem task, and would invent one instead.
        syndicate = getattr(provider, "syndicate", None)
        channel = getattr(provider, "channel", None)
        if syndicate and channel:
            refusal = capability_violation(
                mission.constraints, syndicate, channel, self.ds)
            if refusal:
                self.message = refusal
                self.message_color = Colors.HEALTH
                return

        self.dispatch_brief = {
            "mission": mission,
            "crew": crew,
            "reasons": reasons,
            "provider": provider,
        }
        self.mode = "dispatch_brief"

    def _draw_dispatch_brief(self, height: int, width: int):
        """Draw the briefing gate - full disclosure before any agent work."""
        self._draw_header(width)
        brief = self.dispatch_brief
        if not brief:
            self.mode = "dispatch"
            return

        mission = brief["mission"]
        box_width = min(76, width - 4)
        box_height = min(12 + len(brief["crew"]) + len(mission.tasks), height - 9)
        box_y = 5
        box_x = (width - box_width) // 2
        self._draw_box(box_y, box_x, box_height, box_width,
                       f"MISSION BRIEFING [{mission.id}]")

        y = box_y + 1
        inner = box_width - 4

        def line(label, value, color=Colors.STAT_VALUE):
            nonlocal y
            self._safe_addstr(y, box_x + 2, f"{label:<10}",
                              curses.color_pair(Colors.STAT_LABEL))
            self._safe_addstr(y, box_x + 12, clip(str(value), inner - 10),
                              curses.color_pair(color))
            y += 1

        line("OBJECTIVE", mission.objective)
        line("TARGET", mission.constraints.working_directory)
        scope = "READ-ONLY" if mission.constraints.read_only else "READ/WRITE"
        scope += " | LOCAL" if mission.constraints.local_only else " | REMOTE OK"
        line("SCOPE", scope, Colors.GOLD)
        line("SYNDICATE", brief["provider"].config.name, Colors.GOLD)
        line("ENDPOINT", brief["provider"].descriptor, Colors.DIM)
        line("TIMEOUT", f"{mission.constraints.timeout_seconds}s per task")
        y += 1

        self._safe_addstr(y, box_x + 2, f"CREW ({len(brief['crew'])}):",
                          curses.color_pair(Colors.TITLE))
        y += 1
        for codename, reason in brief["reasons"]:
            if y >= box_y + box_height - 2:
                break
            self._safe_addstr(y, box_x + 4,
                              clip(f"~ {pad(codename, 24)} {reason}", inner - 2),
                              curses.color_pair(Colors.STAT_VALUE))
            y += 1

        self._draw_message(box_y + box_height + 1, width)
        self._draw_footer(height, width,
                          "Y/ENTER: DISPATCH | Q: Back to Form")

    def _handle_dispatch_brief_input(self, key: int):
        """Handle briefing gate input."""
        if key in (curses.KEY_ENTER, 10, 13, ord('y'), ord('Y')):
            self._launch_dispatch()
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "dispatch"

    def _launch_dispatch(self):
        """Start the dispatcher on a worker thread; the draw loop streams
        events as they land."""
        brief = self.dispatch_brief
        if not brief or self.dispatch_running:
            return

        self.dispatch_events = []
        self.dispatch_result = None
        self.dispatch_running = True
        self.dispatch_crew_override = []
        self.dispatch_crew_label = ""
        self.dispatch_thread = threading.Thread(
            target=self._dispatch_worker, args=(brief["mission"],), daemon=True)
        self.dispatch_thread.start()
        self.mode = "dispatch_run"

    def _dispatch_worker(self, mission):
        """Worker thread: run the mission, save reports, settle payroll."""
        import asyncio
        from datetime import datetime as _dt

        def on_event(event):
            stamp = _dt.now().strftime("%H:%M:%S")
            self.dispatch_events.append(
                f"{stamp} [{event.event_type}] {event.message}")

        try:
            dispatcher = Dispatcher(datastore=self.ds)
            result = asyncio.run(dispatcher.execute(
                mission, on_event=on_event,
                max_concurrent=agency_config()["dispatch"]["max_concurrent"]))

            rdir = report_dir()
            rdir.mkdir(parents=True, exist_ok=True)
            report_path = rdir / f"mission-{mission.id}-report.md"
            if result.final_report:
                report_path.write_text(result.final_report)
            result.save(report_path.with_suffix(".json"))
            self.dispatch_events.append(f"         report: {report_path}")
            self.dispatch_result = result
            self._load_roster()
        except Exception as e:
            self.dispatch_events.append(f"         [error] {e}")
        finally:
            self.dispatch_running = False

    def _draw_dispatch_run(self, height: int, width: int):
        """Draw the live dispatch event stream."""
        self._draw_header(width)

        result = self.dispatch_result
        if self.dispatch_running:
            status, color = "DISPATCHED - agents in the field...", Colors.XP
        elif result is not None:
            status_value = result.status.value
            if status_value == "complete":
                status, color = "MISSION COMPLETE", Colors.STAT_VALUE
            else:
                status, color = f"MISSION {status_value.upper()}", Colors.HEALTH
        else:
            status, color = "MISSION ERROR - see events", Colors.HEALTH
        self._safe_addstr(5, 2, status, curses.color_pair(color) | curses.A_BOLD)

        box_y = 7
        box_height = height - 10
        box_width = width - 4
        self._draw_box(box_y, 2, box_height, box_width, "EVENT LOG")

        visible = box_height - 2
        events = self.dispatch_events[-visible:]
        for i, event in enumerate(events):
            if "payroll" in event:
                ecolor = Colors.GOLD
            elif "complete" in event:
                ecolor = Colors.STAT_VALUE
            elif "fail" in event or "error" in event:
                ecolor = Colors.HEALTH
            else:
                ecolor = Colors.NORMAL
            self._safe_addstr(box_y + 1 + i, 4, clip(event, box_width - 4),
                              curses.color_pair(ecolor))

        if self.dispatch_running:
            self._draw_footer(height, width,
                              "Mission running - events stream live | Q: Background it")
        else:
            self._draw_footer(height, width, "ENTER/Q: Back to Menu")

    def _handle_dispatch_run_input(self, key: int):
        """Handle live-run screen input."""
        if key in (curses.KEY_ENTER, 10, 13) and not self.dispatch_running:
            self.mode = "menu"
        elif key == ord('q') or key == ord('Q') or key == 27:
            if self.dispatch_running:
                self.message = "Mission continues in background - check Dispatch later"
                self.message_color = Colors.XP
            self.mode = "menu"

