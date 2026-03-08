# ML Engineering Blog Newsletter Tool

`ml_blog_newsletter.py` tracks major tech-company ML engineering blogs (algorithms, infrastructure, LLMs, etc.) and generates a markdown newsletter on a configurable cadence.

## What it tracks
Default feeds include:
- OpenAI
- Google Research / Google AI
- Meta AI
- Netflix Tech Blog
- Uber Engineering
- AWS ML Blog
- Microsoft Research
- NVIDIA
- Anthropic

## Features
- Pulls RSS/Atom feeds from major tech companies.
- Categorizes posts into `LLM`, `Algorithms`, `Infrastructure`, `Data`, `Product`, and `General`.
- Deduplicates items using SQLite so each post only appears once.
- Writes each run to a timestamped markdown newsletter.
- Optional SMTP delivery.
- Supports `hourly`, `daily`, and `weekly` cadence.

## Quick start
```bash
python3 ml_blog_newsletter.py --generate-now
```

This writes:
- `newsletter_state.db` (seen post state)
- `newsletters/newsletter-YYYYMMDD-HHMMSS.md`

## Schedule examples
Daily at 8:30 local time:
```bash
python3 ml_blog_newsletter.py --cadence daily --at 08:30
```

Weekly at Monday's equivalent local wall time (every 7 days from next run):
```bash
python3 ml_blog_newsletter.py --cadence weekly --at 09:00
```

Hourly at minute 15:
```bash
python3 ml_blog_newsletter.py --cadence hourly --at 00:15
```

## Email delivery
```bash
python3 ml_blog_newsletter.py \
  --generate-now \
  --smtp-host smtp.gmail.com \
  --smtp-port 587 \
  --smtp-sender you@example.com \
  --smtp-recipient you@example.com \
  --smtp-username you@example.com \
  --smtp-password "app-password"
```

To disable TLS:
```bash
python3 ml_blog_newsletter.py --generate-now --smtp-no-tls ...
```

## Notes
- Customize feeds by editing `DEFAULT_SOURCES` in `ml_blog_newsletter.py`.
- Customize categories by editing `CATEGORY_RULES`.
- For production scheduling, you can also run `--generate-now` via cron/systemd.
