# Content Calendar Generator

Generates a full month of short-form video content calendar events and pushes them to Google Calendar automatically.
Run once at the start of each month.

---

## Run Command

```bash
python3 generate_calendar.py --month [MM] --year [YYYY]
```

**Example — July 2026:**

```bash
python3 generate_calendar.py --month 07 --year 2026
```

**Optional: target a specific calendar by ID**

```bash
python3 generate_calendar.py --month 07 --year 2026 --calendar your_calendar_id@group.calendar.google.com
```

**Preview without creating events**

```bash
python3 generate_calendar.py --month 07 --year 2026 --dry-run
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure local environment

Copy `.env.example` to `.env` and update values as needed.

```bash
cp .env.example .env
```

The generator reads simple `KEY=value` pairs from `.env` automatically.

### 3. Add your service account key

The script authenticates via a Google service account JSON key.
Default expected path: `~/growth_agency/credentials/service-account.json`

To use a different path, update `GOOGLE_SERVICE_ACCOUNT_FILE` in `.env`.

### 4. Grant calendar access to the service account

In Google Calendar settings → "Share with specific people", add the service
account email (found in the JSON key as `"client_email"`) with
"Make changes to events" permission.

---

## File Reference

| File | Purpose |
|---|---|
| `generate_calendar.py` | Main script — orchestrates schedule generation and Google Calendar push |
| `config.py` | All settings: pillar labels, allocations, weekly schedule, auth paths, color IDs |
| `prompts.py` | Every daily short-form video prompt organised by pillar |
| `requirements.txt` | Python package dependencies |

---

## Customising

**Change allocation percentages, labels, colors, or weekly day assignments:**
Edit `config.py` → `CONTENT_TYPES`, `WEEKLY_SCHEDULE`, and `FIFTH_SUNDAY_CONTENT_TYPE`.

**Add or edit prompts:**
Edit `prompts.py` → the `PROMPTS` dict. Each key must match a content type string
used in `config.py`.

---

## Weekly Posting Schedule

| Day | Content Type |
|---|---|
| Monday | Customer Acquisition in Action |
| Tuesday | Building Systems & Tools |
| Wednesday | Customer Acquisition in Action |
| Thursday | Agency Execution |
| Friday | Building Systems & Tools |
| Saturday | Customer Acquisition in Action |
| Sunday | Mindset & Discipline |

Fifth Sundays are assigned to Agency Execution to rebalance the monthly allocation.

---

## Content Allocation Targets

| Type | Target |
|---|---|
| Customer Acquisition in Action | 40% |
| Building Systems & Tools | 25% |
| Agency Execution | 20% |
| Mindset & Discipline | 15% |

> Actual counts are driven by the weekly schedule above.
> The targets are reference benchmarks only.
