"""
prompts.py — Daily content prompts organised by content type.

Structure:
  PROMPTS: dict[str, list[str]]
    Keys match the content type strings used in config.WEEKLY_SCHEDULE
    and config.SUNDAY_ROTATION.

Usage:
  from prompts import PROMPTS
  prompts_for_type = PROMPTS["Building Systems"]

Rotation note: the generator rotates through each list sequentially,
wrapping around only after all prompts in the list have been used once.
"""

PROMPTS: dict[str, list[str]] = {

    # --- Building Systems ---------------------------------------------------
    "Building Systems": [
        "What system, tool, or automation did I build or improve?",
        "Show the prospecting agent running — new ZIP code, new output",
        "Show a client website build — before and after",
        "Show the research pipeline generating a brand brief",
        "What workflow did I optimize this week?",
        "Show the CRM or Supabase dashboard with real data",
        "What AI tool did I use and what did it produce?",
        "Show the JSON brief output and how I use it to pitch",
        "Build timelapse — show a site coming together",
        "What business problem did I solve with code?",
        "Free tool update — what changed in the lead generator?",
    ],

    # --- Entrepreneurship Journey -------------------------------------------
    "Entrepreneurship Journey": [
        "What happened this week while building the business?",
        "Document a sales call — win or loss, both are content",
        "What rejection taught me something valuable?",
        "What difficult decision did I make this week?",
        "What is the hardest part about building right now?",
        "A lesson from talking to a local business owner",
        "Why I was terrified to make sales calls — and did it anyway",
        "What I learned after getting rejected",
        "What happened this week that other builders can relate to?",
        "Honest update — where MoreClientsCo stands right now",
        "What surprised me about running a business?",
        "The gap between where I am and where I want to be",
    ],

    # --- Personal Transformation --------------------------------------------
    "Personal Transformation": [
        "What personal lesson shaped me recently?",
        "Fitness or boxing update — what I worked on this week",
        "How discipline in one area carries into everything else",
        "A mindset shift that changed how I approach the business",
        "What sobriety or recovery taught me about building",
        "Injury recovery update — what setbacks teach you",
        "The connection between physical discipline and mental clarity",
    ],

    # --- Educational --------------------------------------------------------
    # Workshop-funnel prompts added June 2026 to support monthly workshop lead gen
    "Educational": [
        "How to get on Google Maps without a storefront — step by step",
        "Why every service business needs lead capture not just a website",
        "What a CRM actually does in plain language",
        "How automated follow-ups work and why most businesses skip them",
        "Local SEO explained — why your competitor shows up before you",
        "The referral system — how to turn happy clients into new ones",
        "What happens when someone fills out your contact form — the right way",
        "Workshop promo — register for this month's free session: [link]",
        "5 digital systems every service business needs to grow — preview",
        "Google Business Profile optimization in 10 minutes",
    ],
}
