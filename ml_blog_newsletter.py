#!/usr/bin/env python3
"""Track major tech-company ML blogs and generate a newsletter on a custom cadence."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import email.message
import hashlib
import smtplib
import sqlite3
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_SOURCES: Sequence[Tuple[str, str]] = (
    ("OpenAI Blog", "https://openai.com/blog/rss.xml"),
    ("Google Research Blog", "https://research.google/blog/rss/"),
    ("Google AI Blog", "https://ai.googleblog.com/feeds/posts/default"),
    ("Meta AI Blog", "https://ai.meta.com/blog/rss/"),
    ("Netflix Tech Blog", "https://netflixtechblog.com/feed"),
    ("Uber Engineering", "https://www.uber.com/blog/engineering/rss/"),
    ("AWS ML Blog", "https://aws.amazon.com/blogs/machine-learning/feed/"),
    ("Microsoft Research Blog", "https://www.microsoft.com/en-us/research/feed/"),
    ("NVIDIA Blog", "https://blogs.nvidia.com/feed/"),
    ("Anthropic News", "https://www.anthropic.com/news/rss.xml"),
)

CATEGORY_RULES: Dict[str, Sequence[str]] = {
    "LLM": (
        "llm",
        "large language model",
        "gpt",
        "transformer",
        "prompt",
        "rag",
        "agent",
        "fine-tuning",
    ),
    "Algorithms": (
        "algorithm",
        "optimization",
        "inference",
        "training",
        "benchmark",
        "evaluation",
        "diffusion",
        "reinforcement learning",
    ),
    "Infrastructure": (
        "infra",
        "infrastructure",
        "kubernetes",
        "serving",
        "latency",
        "throughput",
        "distributed",
        "gpu",
        "compiler",
        "storage",
    ),
    "Data": (
        "dataset",
        "data pipeline",
        "feature store",
        "etl",
        "quality",
    ),
    "Product": (
        "launch",
        "product",
        "release",
        "platform",
        "api",
    ),
}


@dataclasses.dataclass
class FeedItem:
    source: str
    title: str
    link: str
    published: dt.datetime
    summary: str
    category: str

    @property
    def item_id(self) -> str:
        seed = f"{self.source}|{self.link}|{self.title}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class NewsletterStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                published_ts TEXT NOT NULL,
                category TEXT NOT NULL,
                seen_ts TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_ts TEXT PRIMARY KEY,
                cadence TEXT NOT NULL,
                item_count INTEGER NOT NULL,
                output_path TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def unseen_items(self, items: Iterable[FeedItem]) -> List[FeedItem]:
        unseen: List[FeedItem] = []
        for item in items:
            row = self.conn.execute("SELECT 1 FROM items WHERE id = ?", (item.item_id,)).fetchone()
            if row is None:
                unseen.append(item)
        return unseen

    def record_items(self, items: Iterable[FeedItem]) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self.conn.executemany(
            "INSERT OR IGNORE INTO items (id, source, title, link, published_ts, category, seen_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item.item_id,
                    item.source,
                    item.title,
                    item.link,
                    item.published.isoformat(),
                    item.category,
                    now,
                )
                for item in items
            ],
        )
        self.conn.commit()

    def record_run(self, cadence: str, item_count: int, output_path: Path) -> None:
        self.conn.execute(
            "INSERT INTO runs (run_ts, cadence, item_count, output_path) VALUES (?, ?, ?, ?)",
            (dt.datetime.now(dt.timezone.utc).isoformat(), cadence, item_count, str(output_path)),
        )
        self.conn.commit()


def parse_date(value: Optional[str]) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.timezone.utc)

    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            parsed = dt.datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed
        except ValueError:
            continue

    return dt.datetime.now(dt.timezone.utc)


def categorize(text: str) -> str:
    lowered = text.lower()
    for category, keywords in CATEGORY_RULES.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return "General"


def _find_text(parent: ET.Element, paths: Sequence[str]) -> Optional[str]:
    for path in paths:
        node = parent.find(path)
        if node is not None and node.text:
            return node.text.strip()
    return None


def _extract_items(source_name: str, root: ET.Element) -> List[FeedItem]:
    entries = root.findall(".//item")
    is_atom = False
    if not entries:
        entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        is_atom = True

    out: List[FeedItem] = []
    for entry in entries:
        if is_atom:
            title = _find_text(entry, ["{http://www.w3.org/2005/Atom}title"]) or "Untitled"
            link_node = entry.find("{http://www.w3.org/2005/Atom}link")
            link = link_node.attrib.get("href", "") if link_node is not None else ""
            published = _find_text(
                entry,
                ["{http://www.w3.org/2005/Atom}updated", "{http://www.w3.org/2005/Atom}published"],
            )
            summary = _find_text(
                entry,
                ["{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content"],
            ) or ""
        else:
            title = _find_text(entry, ["title"]) or "Untitled"
            link = _find_text(entry, ["link"]) or ""
            published = _find_text(entry, ["pubDate", "published", "updated"])
            summary = _find_text(entry, ["description", "summary"]) or ""

        category = categorize(f"{title} {summary}")
        out.append(
            FeedItem(
                source=source_name,
                title=title,
                link=link,
                published=parse_date(published),
                summary=summary,
                category=category,
            )
        )

    return out


def fetch_feed(source_name: str, feed_url: str, timeout_s: int = 20) -> List[FeedItem]:
    req = urllib.request.Request(feed_url, headers={"User-Agent": "ml-newsletter-bot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[WARN] Could not fetch {source_name} ({feed_url}): {exc}")
        return []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        print(f"[WARN] Could not parse feed {source_name}: {exc}")
        return []

    items = _extract_items(source_name, root)
    print(f"[INFO] {source_name}: {len(items)} posts fetched")
    return items


def compile_newsletter(items: Sequence[FeedItem], cadence: str, generated_at: dt.datetime) -> str:
    by_category: Dict[str, List[FeedItem]] = {}
    for item in sorted(items, key=lambda i: i.published, reverse=True):
        by_category.setdefault(item.category, []).append(item)

    lines = [
        f"# ML Engineering Watch ({cadence})",
        "",
        f"Generated at: {generated_at.isoformat()}",
        f"Total new posts: {len(items)}",
        "",
    ]

    for category in sorted(by_category.keys()):
        lines.append(f"## {category}")
        for item in by_category[category]:
            when = item.published.astimezone(dt.timezone.utc).strftime("%Y-%m-%d")
            lines.append(f"- **{item.source}** ({when}) — [{item.title}]({item.link})")
        lines.append("")

    if not items:
        lines.append("No new posts found in this cycle.")

    return "\n".join(lines).strip() + "\n"


def send_email(
    smtp_host: str,
    smtp_port: int,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    use_tls: bool = True,
) -> None:
    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as client:
        if use_tls:
            client.starttls()
        if username and password:
            client.login(username, password)
        client.send_message(msg)


def next_run_delay_seconds(cadence: str, at_hour: int, at_minute: int) -> int:
    now = dt.datetime.now()
    target = now.replace(hour=at_hour, minute=at_minute, second=0, microsecond=0)
    if cadence == "hourly":
        target = now + dt.timedelta(hours=1)
        target = target.replace(minute=at_minute, second=0, microsecond=0)
    elif cadence == "daily":
        if target <= now:
            target += dt.timedelta(days=1)
    elif cadence == "weekly":
        while target <= now:
            target += dt.timedelta(days=7)
    else:
        raise ValueError(f"Unsupported cadence: {cadence}")

    return int((target - now).total_seconds())


def run_once(
    store: NewsletterStore,
    cadence: str,
    output_dir: Path,
    limit_per_feed: int,
    send_email_args: Optional[dict],
) -> Path:
    all_items: List[FeedItem] = []
    for name, url in DEFAULT_SOURCES:
        all_items.extend(fetch_feed(name, url)[:limit_per_feed])

    unseen = store.unseen_items(all_items)
    newsletter = compile_newsletter(unseen, cadence, dt.datetime.now(dt.timezone.utc))

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"newsletter-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    output_path = output_dir / filename
    output_path.write_text(newsletter, encoding="utf-8")

    store.record_items(unseen)
    store.record_run(cadence, len(unseen), output_path)

    if send_email_args:
        send_email(subject=f"ML Engineering Watch ({cadence})", body=newsletter, **send_email_args)
        print(f"[INFO] Sent newsletter email to {send_email_args['recipient']}")

    print(f"[INFO] Generated newsletter: {output_path} ({len(unseen)} new posts)")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="newsletter_state.db", help="Path to sqlite database")
    parser.add_argument("--output-dir", default="newsletters", help="Where markdown newsletters are saved")
    parser.add_argument("--cadence", choices=["hourly", "daily", "weekly"], default="daily")
    parser.add_argument("--at", default="08:30", help="Local time HH:MM for daily/weekly runs")
    parser.add_argument("--limit-per-feed", type=int, default=15)
    parser.add_argument("--generate-now", action="store_true", help="Generate immediately and exit")

    parser.add_argument("--smtp-host")
    parser.add_argument("--smtp-port", type=int, default=587)
    parser.add_argument("--smtp-sender")
    parser.add_argument("--smtp-recipient")
    parser.add_argument("--smtp-username")
    parser.add_argument("--smtp-password")
    parser.add_argument("--smtp-no-tls", action="store_true")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if ":" not in args.at:
        raise SystemExit("--at must be HH:MM")
    hour_str, minute_str = args.at.split(":", maxsplit=1)
    at_hour, at_minute = int(hour_str), int(minute_str)

    store = NewsletterStore(Path(args.db_path))
    send_email_args = None
    if args.smtp_host and args.smtp_sender and args.smtp_recipient:
        send_email_args = {
            "smtp_host": args.smtp_host,
            "smtp_port": args.smtp_port,
            "sender": args.smtp_sender,
            "recipient": args.smtp_recipient,
            "username": args.smtp_username,
            "password": args.smtp_password,
            "use_tls": not args.smtp_no_tls,
        }

    if args.generate_now:
        run_once(
            store=store,
            cadence=args.cadence,
            output_dir=Path(args.output_dir),
            limit_per_feed=args.limit_per_feed,
            send_email_args=send_email_args,
        )
        return

    print(f"[INFO] Running scheduler with cadence={args.cadence} at {args.at}")
    while True:
        delay = next_run_delay_seconds(args.cadence, at_hour, at_minute)
        print(f"[INFO] Sleeping for {delay} seconds")
        time.sleep(max(delay, 0))
        run_once(
            store=store,
            cadence=args.cadence,
            output_dir=Path(args.output_dir),
            limit_per_feed=args.limit_per_feed,
            send_email_args=send_email_args,
        )


if __name__ == "__main__":
    main()
