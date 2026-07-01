"""
prompts.py — Daily short-form video prompts organised by content pillar.

Structure:
  PROMPTS: dict[str, list[str]]
    Keys match the content pillar keys used in config.CONTENT_TYPES and
    config.WEEKLY_SCHEDULE.

Usage:
  from prompts import PROMPTS
  prompts_for_type = PROMPTS["acquisition"]

Rotation note: the generator rotates through each list sequentially,
wrapping around only after all prompts in the list have been used once.
"""

PROMPTS: dict[str, list[str]] = {
    "acquisition": [
        "Recorded a cold call today. [X] people picked up, [Y] were interested. Here's what worked and what didn't.",
        "Sent [X] cold emails to [category]. [Y]% reply rate. Here's the subject line that got the most opens.",
        "Just landed my first [business type] client. Here's the free offer I used and why they said yes.",
        "DM outreach experiment: Targeted [X] followers of [creator name]. Got [Y] responses. Here's the pitch that worked.",
        "Cold call recording: Got a demo booked. Here's the exact hook I used when they answered.",
        "Follow-up sequence working: Day 1 call, day 3 DM, day 7 second call. Got 1 interested lead. Here's how I tracked it.",
        "Email automation test: Sent [X] follow-ups with 1 testimonial. [Y]% opened the follow-up. Testimonial drove the conversion.",
        "My acquisition funnel right now: [#] warm leads, [#] demos booked, [#] closing. Here's what changed this week.",
        "Rejection today on a cold call. 'I'll handle it myself.' Here's why that happens and how I learn from it.",
        "Testing a new cold call hook based on their Google reviews. Got [X] more interested prospects. Here's the exact script.",
        "Lead scoring system working: Focusing on businesses with [criteria]. [X]% better conversion rate. Here's my qualification framework.",
        "Scaled from [X] prospects to [Y] in one scrape run. Here's what changed in the prospecting process.",
    ],
    "building": [
        "Built [tool name] because [problem]. Here's what it does and how it saves [Y] hours per week.",
        "Rebuilt the lead generator to handle [improvement]. Architecture walkthrough: [brief description].",
        "New feature: Now I can [capability]. This solves [problem we had]. Quick demo: [link].",
        "Created a [tool type] because manual [process] was killing productivity. Here's how it works.",
        "Scraped [X] prospects in [Y] hours using [system]. 2 months ago this took [Z] hours. Here's the optimization.",
        "Integrated [API/tool] with our pipeline. Now [new capability]. Code walkthrough on YouTube.",
        "Building tools that I need to run the agency. Latest: [tool name]. Docs: [link].",
        "Our tech stack for customer acquisition: [tool 1], [tool 2], [tool 3]. Here's why each one matters.",
        "Learned [lesson] while building [system]. Rebuilt it [Y] times before it worked. Here's the final version.",
        "Content calendar generator running. Pushes [X] events to Google Calendar monthly. Full automation setup: [link].",
        "Research pipeline improvement: Now pulls [data type] in [time]. Before: [old way]. Here's the code pattern.",
        "Dashboard showing real-time [metric]: [X] prospects, [Y] demos, [Z] clients. Built this to track what matters.",
    ],
    "execution": [
        "Day [X] of MoreClientsCo: [metric]. This week: [wins]. Here's what I'm learning.",
        "Lost a prospect today. They said '[reason].' Here's what I could've done differently.",
        "First client converted. What it took: [offer], [result], [timeline]. Here's the full story.",
        "Pipeline update: [Y] warm leads, [X] demos booked, [Z] closing. Shifted strategy this week based on [learning].",
        "Weekly wins and losses recap. Wins: [#]. Losses: [#]. Biggest lesson: [lesson].",
        "Changed my pitch this week. Old: [approach]. New: [approach]. Results: [improvement]%.",
        "Booking rate improved from [X]% to [Y]%. Here's what changed in the conversation.",
        "MoreClientsCo metrics: [MRR], [#] clients, [#] in pipeline. Here's the breakdown.",
        "Switched [tool/process] because [reason]. Early results: [outcome]. Will report back in a week.",
        "Client retention rate: [X]%. Why some stay, why some leave. Here's what I'm improving.",
        "Revenue this month: [amount]. Breakdown: [#] from packages, [#] from tools. Here's the unit economics.",
        "Outreach volume: Sent [#] cold emails, made [#] calls. Conversion rate: [X]%. Working on improving [metric].",
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
