"""
Progression - shop, skills, dojo, reroll (the RPG loops).

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses

from lib.data.economy import train as dojo_train
from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class ProgressionMixin:
    """shop, skills, dojo, reroll (the RPG loops)."""

    def _draw_shop(self, height: int, width: int):
        """Draw tool shop interface - purchase tools with gold."""
        if not self.current_agent:
            self.mode = "roster"
            return

        agent = self.current_agent
        gold = agent["economy"]["gold"]
        rank_id = agent["classification"]["rank_id"]
        owned_tools = {t.get("tool_id") for t in agent.get("tools", [])}

        self._draw_header(width)

        # Header with agent info
        ident = agent["identity"]
        try:
            self.stdscr.addstr(5, 2, f"TOOL SHOP - {ident['name']}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(5, width - 20, f"GOLD: ",
                               curses.color_pair(Colors.STAT_LABEL))
            self.stdscr.addstr(f"{gold:,}",
                               curses.color_pair(Colors.GOLD) | curses.A_BOLD)
        except curses.error:
            pass

        # Tool list (sorted copy - the reference cache must not be mutated)
        tools = sorted(self.tool_refs.values(),
                       key=lambda t: (t.get("cost", 0), t.get("label", "")))

        list_y = 7
        list_height = height - 12
        visible_count = list_height - 2

        # Filter to show available tools (not owned)
        available = [t for t in tools if t["id"] not in owned_tools]

        # Header
        header = f"{'TOOL':<20} {'COST':>6} {'RISK':<10} {'REQ RANK':<10} {'STATUS':<12}"
        try:
            self.stdscr.addstr(list_y, 4, header[:width - 8],
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(list_y + 1, 4, "-" * min(width - 8, len(header)),
                               curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Scroll handling
        start_idx = max(0, self.shop_index - visible_count + 3)

        for i, tool in enumerate(available[start_idx:start_idx + visible_count]):
            row_y = list_y + 2 + i
            if row_y >= list_y + list_height:
                break

            cost = tool.get("cost", 0)
            risk = tool.get("risk", {}).get("level", "?")
            min_rank = tool.get("requirements", {}).get("min_rank_id", 1)

            # Determine status
            can_afford = gold >= cost
            meets_rank = rank_id >= min_rank

            if can_afford and meets_rank:
                status = "AVAILABLE"
                status_color = Colors.STAT_VALUE
            elif not meets_rank:
                status = f"RANK {min_rank}+"
                status_color = Colors.DIM
            else:
                status = "NO GOLD"
                status_color = Colors.HEALTH

            icon = tool.get("icon", "▪")
            label = pad(f"{icon} {tool.get('label', '???')}", 20)
            row = f" {clip(f'{label} {cost:>5}g {risk:<10} {min_rank:<10} {status:<12}', width - 10)} "

            if start_idx + i == self.shop_index:
                self._safe_addstr(row_y, 4, row, curses.color_pair(Colors.HIGHLIGHT))
            else:
                self._safe_addstr(row_y, 4, row, curses.color_pair(status_color))

        # Show tool description at bottom
        if available and 0 <= self.shop_index < len(available):
            selected = available[self.shop_index]
            desc = selected.get("description", "")[:width - 10]
            try:
                self.stdscr.addstr(height - 5, 4, desc,
                                   curses.color_pair(Colors.DIM))
            except curses.error:
                pass

        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Purchase | Q: Back to Agent")

    def _draw_skills(self, height: int, width: int):
        """Draw capability upgrade interface - spend XP to level up."""
        if not self.current_agent:
            self.mode = "roster"
            return

        agent = self.current_agent
        xp = agent["economy"]["xp"]
        caps = agent.get("capabilities", [])

        self._draw_header(width)

        # Header with agent info
        ident = agent["identity"]
        try:
            self.stdscr.addstr(5, 2, f"CAPABILITY TRAINING - {ident['name']}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(5, width - 20, f"XP: ",
                               curses.color_pair(Colors.STAT_LABEL))
            self.stdscr.addstr(f"{xp:,}",
                               curses.color_pair(Colors.XP) | curses.A_BOLD)
        except curses.error:
            pass

        # Capability list
        cap_refs = self.capability_refs

        list_y = 7
        list_height = height - 12

        # Header
        header = f"{'CAPABILITY':<25} {'LEVEL':>6} {'NEXT COST':>10} {'STATUS':<15}"
        try:
            self.stdscr.addstr(list_y, 4, header[:width - 8],
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(list_y + 1, 4, "-" * min(width - 8, len(header)),
                               curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        for i, cap in enumerate(caps[:list_height - 4]):
            row_y = list_y + 2 + i
            cap_ref = cap_refs.get(cap.get("capability_id"), {})
            prof = cap.get("proficiency", 1)
            xp_costs = cap_ref.get("xp_costs", [100, 300, 600, 1000, 1500])

            icon = cap_ref.get("icon", "●")
            label = pad(f"{icon} {cap_ref.get('label', 'Unknown')}", 25)

            if prof >= 5:
                next_cost = "MAX"
                status = "MASTERED"
                status_color = Colors.GOLD
            else:
                next_cost = xp_costs[prof] if prof < len(xp_costs) else 9999
                if xp >= next_cost:
                    status = "CAN UPGRADE"
                    status_color = Colors.STAT_VALUE
                else:
                    status = f"NEED {next_cost - xp} XP"
                    status_color = Colors.DIM
                next_cost = f"{next_cost:,}"

            bar = self._progress_bar(5, prof / 5)
            row = f" {clip(f'{label} [{bar}] {next_cost:>10} {status:<15}', width - 10)} "

            if i == self.skill_index:
                self._safe_addstr(row_y, 4, row, curses.color_pair(Colors.HIGHLIGHT))
            else:
                self._safe_addstr(row_y, 4, row, curses.color_pair(status_color))

        # Show capability description at bottom
        if caps and 0 <= self.skill_index < len(caps):
            cap = caps[self.skill_index]
            cap_ref = cap_refs.get(cap.get("capability_id"), {})
            desc = cap_ref.get("description", "")[:width - 10]
            prof = cap.get("proficiency", 1)
            prof_desc = cap_ref.get("proficiency_levels", {}).get(str(prof), "")
            try:
                self.stdscr.addstr(height - 6, 4, desc,
                                   curses.color_pair(Colors.DIM))
                if prof_desc:
                    self.stdscr.addstr(height - 5, 4, f"Level {prof}: {prof_desc[:width - 20]}",
                                       curses.color_pair(Colors.STAT_VALUE))
            except curses.error:
                pass

        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Upgrade | Q: Back to Agent")

    def _draw_reroll(self, height: int, width: int):
        """Draw reroll/shuffle menu for agent attributes."""
        if not self.current_agent:
            self.mode = "roster"
            return

        agent = self.current_agent
        ident = agent["identity"]

        self._draw_header(width)

        # Header
        try:
            self.stdscr.addstr(5, 2, f"DICE ROLL - {ident['name']}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(5, width - 30, "Roll the dice to shuffle attributes",
                               curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Reroll options
        reroll_options = [
            ("🎲 Reroll Skills", "Randomize skill point distribution"),
            ("🎲 Reroll Capabilities", "Shuffle capability assignments and proficiency"),
            ("🎲 Reroll Tools", "Re-assign starter tools"),
            ("🎲 Reroll Gold", "Randomize gold amount based on XP"),
            ("🎲 Reroll Profile", "New bio, motto, personality, interests"),
            ("⚠️  Full Regenerate", "Complete rebuild (keeps ID and tag only)"),
        ]

        list_y = 8
        box_height = len(reroll_options) + 4
        box_width = 60
        box_x = (width - box_width) // 2

        self._draw_box(list_y, box_x, box_height, box_width, "SELECT ATTRIBUTE TO REROLL")

        for i, (label, desc) in enumerate(reroll_options):
            row_y = list_y + 2 + i
            row = f" {pad(label, 30)} "

            if i == self.reroll_index:
                self._safe_addstr(row_y, box_x + 2, row,
                                  curses.color_pair(Colors.HIGHLIGHT))
            else:
                # Highlight the warning option differently
                color = Colors.HEALTH if "Full" in label else Colors.STAT_VALUE
                self._safe_addstr(row_y, box_x + 2, row, curses.color_pair(color))

        # Show description for selected option
        if 0 <= self.reroll_index < len(reroll_options):
            _, desc = reroll_options[self.reroll_index]
            try:
                self.stdscr.addstr(list_y + box_height + 1, box_x + 2, desc,
                                   curses.color_pair(Colors.DIM))
            except curses.error:
                pass

        # Show current values preview
        preview_y = list_y + box_height + 4
        try:
            self.stdscr.addstr(preview_y, box_x, "Current Values:",
                               curses.color_pair(Colors.TITLE))
            preview_y += 1

            # Skills preview
            skills = agent.get("skills", [])
            skill_str = ", ".join([f"{self.skill_domains.get(s['domain_id'], {}).get('abbrev', '?')}:{s['points']}"
                                   for s in skills[:4]])
            self.stdscr.addstr(preview_y, box_x, f"Skills: {skill_str}...",
                               curses.color_pair(Colors.DIM))
            preview_y += 1

            # Capabilities preview
            caps = agent.get("capabilities", [])
            cap_str = ", ".join([f"{self.capability_refs.get(c['capability_id'], {}).get('icon', '?')}"
                                 for c in caps[:5]])
            self.stdscr.addstr(preview_y, box_x, f"Capabilities: {cap_str}",
                               curses.color_pair(Colors.DIM))
            preview_y += 1

            # Gold preview
            self.stdscr.addstr(preview_y, box_x, f"Gold: {agent['economy']['gold']:,}g  XP: {agent['economy']['xp']:,}",
                               curses.color_pair(Colors.DIM))

        except curses.error:
            pass

        # Status message
        self._draw_message(height - 4, width)

        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Roll | Q: Back to Agent")

    def _handle_reroll_input(self, key: int):
        """Handle reroll menu input."""
        if not self.current_agent:
            self.mode = "roster"
            return

        option_count = 6

        if key == curses.KEY_UP:
            self.reroll_index = (self.reroll_index - 1) % option_count
        elif key == curses.KEY_DOWN:
            self.reroll_index = (self.reroll_index + 1) % option_count
        elif key in (curses.KEY_ENTER, 10, 13):
            self._execute_reroll()
        elif key == ord('q') or key == ord('Q'):
            self.mode = "view"

    def _execute_reroll(self):
        """Execute the selected reroll option with animation."""
        if not self.current_agent:
            return

        agent = self.current_agent
        reroll_labels = ["Skills", "Capabilities", "Tools", "Gold", "Profile", "FULL REGEN"]
        selected_label = reroll_labels[self.reroll_index]

        # Run shuffle animation
        self._animate_reroll(selected_label)

        if self.reroll_index == 0:
            # Reroll Skills
            self._reroll_skills(agent)
        elif self.reroll_index == 1:
            # Reroll Capabilities
            self._reroll_capabilities(agent)
        elif self.reroll_index == 2:
            # Reroll Tools
            self._reroll_tools(agent)
        elif self.reroll_index == 3:
            # Reroll Gold
            self._reroll_gold(agent)
        elif self.reroll_index == 4:
            # Reroll Profile
            self._reroll_profile(agent)
        elif self.reroll_index == 5:
            # Full Regenerate
            self._full_regenerate(agent)

        # Save changes and refresh
        self.ds.update("agent", agent["id"], agent)
        self._load_roster()

        # Re-find current agent
        for a in self.roster:
            if a["id"] == agent["id"]:
                self.current_agent = a
                break

        # Return to agent view (not roster)
        self.mode = "view"

    def _animate_reroll(self, label: str):
        """Animate the dice roll/shuffle effect."""
        import time
        import random

        height, width = self.stdscr.getmaxyx()
        box_width = 40
        box_height = 5
        box_x = (width - box_width) // 2
        box_y = (height - box_height) // 2

        # Dice faces and shuffle chars
        dice_frames = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
        shuffle_chars = ["◐", "◓", "◑", "◒"]

        # Animation: rolling dice
        for frame in range(12):
            self._draw_box(box_y, box_x, box_height, box_width, "ROLLING")

            # Draw dice animation
            dice_str = " ".join([random.choice(dice_frames) for _ in range(5)])
            spinner = shuffle_chars[frame % len(shuffle_chars)]

            try:
                self.stdscr.addstr(box_y + 2, box_x + 4,
                                   f"{spinner} {pad(label, 15)} {dice_str} {spinner}",
                                   curses.color_pair(Colors.GOLD) | curses.A_BOLD)
            except curses.error:
                pass

            self.stdscr.refresh()
            time.sleep(0.08)

        # Final frame: landed
        self._draw_box(box_y, box_x, box_height, box_width, "ROLLED!")
        try:
            final_dice = " ".join([random.choice(dice_frames) for _ in range(5)])
            self.stdscr.addstr(box_y + 2, box_x + 4,
                               f"  {label:<15} {final_dice}  ",
                               curses.color_pair(Colors.STAT_VALUE) | curses.A_BOLD)
        except curses.error:
            pass
        self.stdscr.refresh()
        time.sleep(0.3)

    def _reroll_skills(self, agent: dict):
        """Reroll skill point distribution."""
        skill_points = agent["economy"]["skill_points"]["assigned"]
        agent["skills"] = self.gen.random_skills(skill_points)
        self.message = "Skills rerolled!"
        self.message_color = Colors.STAT_VALUE

    def _reroll_capabilities(self, agent: dict):
        """Reroll capability assignments."""
        focus_id = agent["classification"]["focus_id"]
        rank_id = agent["classification"]["rank_id"]
        agent["capabilities"] = self.gen.random_capabilities(focus_id, rank_id)
        self.message = "Capabilities rerolled!"
        self.message_color = Colors.STAT_VALUE

    def _reroll_tools(self, agent: dict):
        """Reroll tool assignments."""
        rank_id = agent["classification"]["rank_id"]
        # Give some gold back from existing tools
        tool_refs = {t["id"]: t for t in self.ds.load_reference("tool")}
        refund = sum(tool_refs.get(t.get("tool_id"), {}).get("cost", 0) for t in agent.get("tools", []))

        # Re-assign tools with budget
        budget = min(100, agent["economy"]["gold"] + refund)
        new_tools, spent = self.gen.random_starter_tools(rank_id, budget)
        agent["tools"] = new_tools
        # Adjust gold (refund minus new purchases)
        agent["economy"]["gold"] += refund - spent
        self.message = f"Tools rerolled! ({refund}g refunded, {spent}g spent)"
        self.message_color = Colors.STAT_VALUE

    def _reroll_gold(self, agent: dict):
        """Reroll gold amount based on XP."""
        xp = agent["economy"]["xp"]
        config = self.ds.get_config()
        base_gold = config.get("defaults", {}).get("starting_gold", 50)
        new_gold = self.gen._scale_gold(xp, base_gold)
        old_gold = agent["economy"]["gold"]
        agent["economy"]["gold"] = new_gold
        diff = new_gold - old_gold
        diff_str = f"+{diff}" if diff >= 0 else str(diff)
        self.message = f"Gold rerolled: {old_gold} → {new_gold} ({diff_str})"
        self.message_color = Colors.GOLD

    def _reroll_profile(self, agent: dict):
        """Reroll profile (bio, motto, personality, interests)."""
        name = agent["identity"]["name"]

        mottos = self.gen._get_lex('mottos')
        interests = self.gen._get_lex('interests')
        zodiac = self.gen._get_lex('zodiac')

        import random
        agent["profile"] = {
            "bio": self.gen.random_bio(name),
            "avatar": agent.get("profile", {}).get("avatar"),  # Keep avatar
            "motto": random.choice(mottos) if mottos else "Knowledge through observation.",
            "personality": self.gen.random_personality(),
            "interests": random.sample(interests, min(random.randint(2, 4), len(interests))) if interests else [],
            "zodiac": random.choice(zodiac) if zodiac else "Orion",
            "quotes": agent.get("profile", {}).get("quotes", [])  # Keep quotes
        }
        self.message = "Profile rerolled!"
        self.message_color = Colors.STAT_VALUE

    def _full_regenerate(self, agent: dict):
        """Full regenerate - keeps ID and tag only."""
        # Preserve key identifiers
        old_id = agent["id"]
        old_tag = agent["identity"]["tag"]
        old_created = agent["audit"]["created_at"]

        # Generate new agent
        new_agent = self.gen.generate()

        # Restore identifiers
        new_agent["id"] = old_id
        new_agent["identity"]["tag"] = old_tag
        new_agent["audit"]["created_at"] = old_created

        # Copy to current agent (update in place)
        agent.clear()
        agent.update(new_agent)

        self.message = "Full regenerate complete!"
        self.message_color = Colors.HEALTH

    def _handle_shop_input(self, key: int):
        """Handle tool shop input."""
        if not self.current_agent:
            self.mode = "roster"
            return

        tools = self.ds.load_reference("tool")
        owned_tools = {t.get("tool_id") for t in self.current_agent.get("tools", [])}
        available = [t for t in tools if t["id"] not in owned_tools]
        available.sort(key=lambda t: (t.get("cost", 0), t.get("label", "")))

        if key == curses.KEY_UP:
            self.shop_index = max(0, self.shop_index - 1)
        elif key == curses.KEY_DOWN:
            self.shop_index = min(len(available) - 1, self.shop_index + 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            self._purchase_tool(available)
        elif key == ord('q') or key == ord('Q'):
            self.mode = "view"

    def _purchase_tool(self, available: list):
        """Purchase selected tool for current agent."""
        if not available or self.shop_index >= len(available):
            return

        tool = available[self.shop_index]
        agent = self.current_agent
        gold = agent["economy"]["gold"]
        rank_id = agent["classification"]["rank_id"]

        cost = tool.get("cost", 0)
        min_rank = tool.get("requirements", {}).get("min_rank_id", 1)

        if rank_id < min_rank:
            self.message = f"Requires rank {min_rank}+"
            self.message_color = Colors.HEALTH
            return

        if gold < cost:
            self.message = f"Not enough gold (need {cost})"
            self.message_color = Colors.HEALTH
            return

        # Purchase the tool
        from datetime import datetime, timezone
        agent["economy"]["gold"] -= cost
        agent["tools"].append({
            "tool_id": tool["id"],
            "assigned_at": datetime.now(timezone.utc).isoformat(),
            "assigned_by": "operator",
            "proficiency": 2
        })

        # Save agent
        self.ds.update("agent", agent["id"], agent)
        self._load_roster()
        # Re-find current agent
        for a in self.roster:
            if a["id"] == agent["id"]:
                self.current_agent = a
                break

        self.message = f"Purchased {tool.get('label', 'tool')} for {cost}g"
        self.message_color = Colors.STAT_VALUE
        self.shop_index = max(0, self.shop_index - 1)

    def _handle_skills_input(self, key: int):
        """Handle capability upgrade input."""
        if not self.current_agent:
            self.mode = "roster"
            return

        caps = self.current_agent.get("capabilities", [])

        if key == curses.KEY_UP:
            self.skill_index = max(0, self.skill_index - 1)
        elif key == curses.KEY_DOWN:
            self.skill_index = min(len(caps) - 1, self.skill_index + 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            self._upgrade_capability()
        elif key == ord('q') or key == ord('Q'):
            self.mode = "view"

    def _upgrade_capability(self):
        """Upgrade selected capability for current agent."""
        agent = self.current_agent
        caps = agent.get("capabilities", [])

        if not caps or self.skill_index >= len(caps):
            return

        cap = caps[self.skill_index]
        xp = agent["economy"]["xp"]
        prof = cap.get("proficiency", 1)

        if prof >= 5:
            self.message = "Already at max level"
            self.message_color = Colors.DIM
            return

        cap_refs = {c["id"]: c for c in self.ds.load_reference("capability")}
        cap_ref = cap_refs.get(cap.get("capability_id"), {})
        xp_costs = cap_ref.get("xp_costs", [100, 300, 600, 1000, 1500])

        cost = xp_costs[prof] if prof < len(xp_costs) else 9999

        if xp < cost:
            self.message = f"Need {cost - xp} more XP"
            self.message_color = Colors.HEALTH
            return

        # Upgrade
        agent["economy"]["xp"] -= cost
        cap["proficiency"] = prof + 1

        # Save agent
        self.ds.update("agent", agent["id"], agent)
        self._load_roster()
        # Re-find current agent
        for a in self.roster:
            if a["id"] == agent["id"]:
                self.current_agent = a
                break

        self.message = f"Upgraded to level {prof + 1}! (-{cost} XP)"
        self.message_color = Colors.STAT_VALUE

    def _draw_dojo(self, height: int, width: int):
        """Draw the dojo - spend unassigned skill points on skill domains."""
        if not self.current_agent:
            self.mode = "roster"
            return

        agent = self.current_agent
        ident = agent["identity"]
        sp = agent.get("economy", {}).get("skill_points", {})
        unassigned = sp.get("total", 0) - sp.get("assigned", 0)

        self._draw_header(width)
        self._safe_addstr(5, 2, f"DOJO - {ident['name']}",
                          curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        self._safe_addstr(5, max(0, width - 26), "UNASSIGNED SP: ",
                          curses.color_pair(Colors.STAT_LABEL))
        self._safe_addstr(5, max(0, width - 11), str(unassigned),
                          curses.color_pair(Colors.STAT_VALUE) | curses.A_BOLD)

        skills = {s["domain_id"]: s for s in agent.get("skills", [])}
        domains = sorted(self.skill_domains.values(), key=lambda d: d["id"])

        list_y = 7
        header = f"{'DOMAIN':<24} {'POINTS':<20} {'STATUS':<12}"
        self._safe_addstr(list_y, 4, header,
                          curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        self._safe_addstr(list_y + 1, 4, "-" * min(width - 8, 60),
                          curses.color_pair(Colors.DIM))

        for i, domain in enumerate(domains):
            row_y = list_y + 2 + i
            points = skills.get(domain["id"], {}).get("points", 0)
            cap = domain.get("max_points", 20)
            bar = self._progress_bar(10, points / max(1, cap))
            if points >= cap:
                status = "MASTERED"
            elif unassigned > 0:
                status = "TRAINABLE"
            else:
                status = "NO SP"
            lead = pad(f"{domain.get('icon', '')} {domain.get('label', '?')}", 24)
            row = f" {lead} [{bar}] {points:>2}/{cap}  {status:<10} "

            color = Colors.HIGHLIGHT if i == self.dojo_index else Colors.STAT_VALUE
            self._safe_addstr(row_y, 4, clip(row, width - 8),
                              curses.color_pair(color))

        self._draw_message(height - 4, width)
        self._draw_footer(height, width,
                          "UP/DOWN: Navigate | ENTER: Train +1 | Q: Back to Agent")

    def _handle_dojo_input(self, key: int):
        """Handle dojo input."""
        if not self.current_agent:
            self.mode = "roster"
            return

        domains = sorted(self.skill_domains.values(), key=lambda d: d["id"])

        if key == curses.KEY_UP:
            self.dojo_index = (self.dojo_index - 1) % len(domains)
        elif key == curses.KEY_DOWN:
            self.dojo_index = (self.dojo_index + 1) % len(domains)
        elif key in (curses.KEY_ENTER, 10, 13):
            domain = domains[self.dojo_index]
            try:
                payout = dojo_train(self.ds, self.current_agent,
                                    domain["slug"], 1)
            except ValueError as e:
                self.message = str(e)
                self.message_color = Colors.HEALTH
                return
            self.current_agent = payout.agent
            self._load_roster()
            self.message = "; ".join(payout.notes) or "Trained"
            self.message_color = Colors.STAT_VALUE
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "view"

