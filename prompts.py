"""
prompts.py — Daily short-form video prompts organised by content pillar.

Structure:
  PROMPTS: dict[str, list[str]]
    Keys match the content pillar keys used in config.CONTENT_TYPES.

Usage:
  from prompts import PROMPTS
  prompts_for_type = PROMPTS["engineering"]

Rotation note: the generator rotates through each list sequentially,
wrapping around only after all prompts in the list have been used once.

Provisional pillars (September 2026): config.CONTENT_TYPES was updated to a
new engineering-focused pillar set (engineering, career, building_in_public,
mindset), replacing the old agency-oriented pillars (acquisition, building,
execution). Prompt content is optional and does not affect routing (see
config.PROMPT_GENERATION_ENABLED and generate_calendar.build_schedule), so
this update is intentionally minimal: "engineering", "career", and
"building_in_public" below are short placeholder lists only, meant to keep
prompt generation functional while the new strategy is tested — not a
designed content plan. "mindset" is left as its original agency-era content
because that pillar key is unchanged; its prompts do not yet reflect the new
engineering/career framing. Designing real prompts for all four pillars is a
separate follow-up task, not part of the pillar-swap itself.
"""

PROMPTS: dict[str, list[str]] = {
    "engineering": [
        "Walked through how I architected [system/feature]. Here's the tradeoff I made and why.",
        "Hit a bug in [project] that took [X] hours to track down. Here's the root cause and the fix.",
        "Wired up [API/tool] into [project]. Here's what it unlocked and how it works.",
    ],
    "career": [
        "Applied to [X] roles this week, heard back from [Y]. Here's what I changed in my approach.",
        "Went through an interview for [role type]. Here's what I got asked and how I'd answer it differently now.",
        "Skill I'm focused on leveling up right now: [skill]. Here's why it matters for breaking in.",
    ],
    "building_in_public": [
        "Progress update on [project]: [what changed this week]. Next up: [what's next].",
        "Tried [approach] on [project] and it didn't work. Here's what I'm doing instead.",
        "Shipped [feature/milestone] on [project] today. Here's a quick look.",
    ],
    "mindset": [
        "Ran this morning before cold calls. The discipline transfers. Pain now, results later.",
        "Wanted to quit on [task] after [Y] hours. Remembered [anime character/reference] about perseverance. Solved it at hour [Z].",
        "[X] rejections today. Day 1 I would've quit. Day [Z] I'm just collecting data. This is what relentlessness looks like.",
        "My mom always said '[lesson from childhood].' Applied it to building MoreClientsCo this week.",
        "Gaming taught me [lesson]. Running taught me [lesson]. Building taught me [lesson]. They're all the same.",
        "The security job taught me [skill]. Now I use it every day in the agency. Never waste an experience.",
        "Caribbean roots reminder: [cultural value]. This shows up in how I [apply it to work].",
        "Anime arc reference: [character/arc] is relentlessness. That's what [this week] felt like.",
        "Consistency over intensity. One call a day beats ten calls once a week. Playing the long game.",
        "Fear was here. I was here. I did the thing anyway. That's it. That's the whole framework.",
        "People ask 'how do you stay motivated?' You don't. You stay disciplined. Motivation is a feeling. Discipline is a choice.",
        "Being willing to look stupid, fail publicly, learn in front of people--that's the actual edge.",
    ],
}
