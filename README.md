# Content Calendar Generator

Generates a full month of content calendar events and pushes them to Google Calendar automatically.
Run once at the start of each month.

---

## Run Command

```bash
python3 generate_calendar.py --month [MM] --year [YYYY]
```

**Example — June 2026:**

```bash
python3 generate_calendar.py --month 06 --year 2026
```

**Optional: target a specific calendar by ID**

```bash
python3 generate_calendar.py --month 06 --year 2026 --calendar your_calendar_id@group.calendar.google.com
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
| `config.py` | All settings: allocations, weekly schedule, auth paths, color IDs |
| `prompts.py` | Every daily prompt organised by content type |
| `requirements.txt` | Python package dependencies |

---

## Customising

**Change allocation percentages or weekly day assignments:**
Edit `config.py` → `WEEKLY_SCHEDULE` and `SUNDAY_ROTATION`.

**Add or edit prompts:**
Edit `prompts.py` → the `PROMPTS` dict. Each key must match a content type string
used in `config.py`.

---

## Weekly Posting Schedule

| Day | Content Type |
|---|---|
| Monday | Entrepreneurship Journey |
| Tuesday | Building Systems |
| Wednesday | Entrepreneurship Journey |
| Thursday | Personal Transformation |
| Friday | Building Systems |
| Saturday | Entrepreneurship Journey |
| Sunday | Alternates — Educational (odd weeks) / Personal Transformation (even weeks) |

---

## Content Allocation Targets

| Type | Target |
|---|---|
| Building Systems | 35% (~10–11 posts) |
| Entrepreneurship Journey | 30% (~9 posts) |
| Personal Transformation | 20% (~6 posts) |
| Educational | 15% (~4–5 posts) |

> Actual counts are driven by the weekly schedule above.
> The targets are reference benchmarks only.
