"""Dependency-free Sabatini draft watcher using a marker-based HTML scanner."""

import gzip
import hashlib
import html as html_tools
import json
import os
import re
import time
import traceback
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MENU_SCRIPT_URL = "https://business.untappd.com/locations/139/themes/340/js"
MAX_DISCORD_BLOCK_LENGTH = 1850
PARSE_FAILURE_EXIT_CODE = 78
EMBED_HTML_PATTERN = re.compile(
    r'container\.innerHTML\s*=\s*("(?:\\.|[^"\\])*")\s*;', re.DOTALL
)
ITEM_PATTERN = re.compile(
    r'<div\b[^>]*\bclass\s*=\s*(["\'])[^"\']*\bmenu-item\b[^"\']*\1[^>]*>',
    re.IGNORECASE,
)
LINK_PATTERN = re.compile(r"<a\b[^>]*>(.*?)</a\s*>", re.IGNORECASE | re.DOTALL)
TIME_PATTERN = re.compile(r"<time\b[^>]*>(.*?)</time\s*>", re.IGNORECASE | re.DOTALL)
PRICE_PATTERN = re.compile(
    r"<span\b[^>]*\bclass\s*=\s*([\"'])"
    r"(?:[^\"']*\s)?price(?:\s[^\"']*)?\1[^>]*>\s*"
    r"<span\b[^>]*\bclass\s*=\s*([\"'])"
    r"(?:[^\"']*\s)?currency-hideable(?:\s[^\"']*)?\2[^>]*>"
    r"(.*?)</span\s*>\s*([^<]*?)</span\s*>",
    re.IGNORECASE | re.DOTALL,
)
TAG_PATTERN = re.compile(r"<[^>]*>")
COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)

# Discord output settings. The beer name is always included.
SHOW_STYLE = True
SHOW_ABV = True
SHOW_IBU = True
SHOW_BREWERY = True
SHOW_LOCATION = True
SHOW_SIZES_AND_PRICES = False

# Experimental: set to True to skip scanning when the decoded HTML is unchanged.
SKIP_PARSE_WHEN_RAW_HTML_UNCHANGED = False


def class_element_pattern(class_name, tag=r"[a-z][\w:-]*"):
    return re.compile(
        rf"<({tag})\b[^>]*\bclass\s*=\s*([\"'])"
        rf"(?:[^\"']*\s)?{re.escape(class_name)}(?:\s[^\"']*)?\2"
        rf"[^>]*>(.*?)</\1\s*>",
        re.IGNORECASE | re.DOTALL,
    )


CLASS_PATTERNS = {
    "menu_title": class_element_pattern("menu-title"),
    "date_time": class_element_pattern("date-time", "div"),
    "section_name": class_element_pattern("section-name"),
    "item_name": class_element_pattern("item-name", "h4"),
    "style": class_element_pattern("item-category"),
    "abv": class_element_pattern("item-abv"),
    "ibu": class_element_pattern("item-ibu"),
    "brewery": class_element_pattern("brewery"),
    "location": class_element_pattern("item-brewery-location"),
    "serving_type": class_element_pattern("type"),
}


class MenuParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class DraftBeer:
    name: str
    style: str
    abv: str
    ibu: str
    brewery: str
    location: str
    serving_options: list[str]

    def diff_line(self):
        parts = [self.name]
        optional = (
            (SHOW_STYLE, self.style),
            (SHOW_ABV, self.abv),
            (SHOW_IBU, self.ibu),
            (SHOW_BREWERY, self.brewery),
            (SHOW_LOCATION, self.location),
        )
        parts.extend(value for enabled, value in optional if enabled and value)
        if SHOW_SIZES_AND_PRICES and self.serving_options:
            parts.append(", ".join(self.serving_options))
        return " | ".join(parts)


@dataclass(frozen=True)
class MenuSnapshot:
    source_url: str
    fetched_at: str
    title: str
    updated_at: str
    section_name: str
    beers: list[DraftBeer]
    raw_html_hash: str

    @property
    def lines(self):
        return [beer.diff_line() for beer in self.beers]

    @property
    def display_digest(self):
        value = {"updatedAt": self.updated_at, "lines": self.lines}
        return digest(value)

    @property
    def data_digest(self):
        value = {
            "title": self.title,
            "updatedAt": self.updated_at,
            "sectionName": self.section_name,
            "rawHtmlHash": self.raw_html_hash,
            "items": [asdict(beer) for beer in self.beers],
        }
        return digest(value)

    def to_dict(self):
        return {
            "sourceUrl": self.source_url,
            "fetchedAt": self.fetched_at,
            "title": self.title,
            "updatedAt": self.updated_at,
            "sectionName": self.section_name,
            "rawHtmlHash": self.raw_html_hash,
            "items": [asdict(beer) for beer in self.beers],
            "lines": self.lines,
            "hash": self.display_digest,
            "dataHash": self.data_digest,
        }


def digest(value):
    encoded = json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def main():
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("Missing required DISCORD_WEBHOOK_URL variable.")
    state_file = Path(os.getenv("STATE_FILE", "data/state.json"))

    previous = load_state(state_file)

    try:
        current = read_current_menu(MENU_SCRIPT_URL, previous)
    except MenuParseError as error:
        notify_parse_failure(webhook_url, error)
        raise

    if current is None:
        return

    if previous is None:
        post_to_discord(webhook_url, build_snapshot_messages(current))
        save_state(state_file, current)
        print(f"Posted and saved initial snapshot with {len(current.beers)} drafts.")
        return

    beer_lines_changed = Counter(previous.get("lines", [])) != Counter(current.lines)
    timestamp_changed = previous.get("updatedAt", "") != current.updated_at
    if not beer_lines_changed and not timestamp_changed:
        raw_hash_changed = previous.get("rawHtmlHash") != current.raw_html_hash
        if previous.get("dataHash") != current.data_digest or previous.get("hash") != current.display_digest or raw_hash_changed:
            save_state(state_file, current)
            print("Updated the full state snapshot without notifying Discord.")
        else:
            print(f"No draft list change. Menu timestamp: {current.updated_at or 'unknown'}.")
        return

    post_to_discord(webhook_url, build_change_messages(previous, current))
    save_state(state_file, current)
    print(f"Posted update with {len(current.beers)} drafts.")


def read_current_menu(source_url, previous=None):
    total_started = time.perf_counter()
    html = fetch_menu_html(source_url)
    raw_html_hash = hashlib.sha256(html.encode()).hexdigest()

    if (
        globals().get("SKIP_PARSE_WHEN_RAW_HTML_UNCHANGED", False)
        and previous
        and previous.get("rawHtmlHash") == raw_html_hash
    ):
        total_ms = round((time.perf_counter() - total_started) * 1000)
        print("Raw menu HTML is unchanged; skipped scanning.")
        print(f"{total_ms} ms total to read menu")
        return None

    parse_started = time.perf_counter()
    snapshot = parse_menu_markers(html, source_url, raw_html_hash)
    parse_ms = round((time.perf_counter() - parse_started) * 1000)
    total_ms = round((time.perf_counter() - total_started) * 1000)
    print(f"{parse_ms} ms to scan menu")
    print(f"{total_ms} ms total to read menu")
    return snapshot


def fetch_menu_html(source_url):
    request = Request(
        source_url,
        headers={
            "Accept": "application/javascript",
            "Accept-Encoding": "gzip",
            "User-Agent": "SabatiniDraftWatcher/1.0",
        },
    )
    started = time.perf_counter()
    with urlopen(request, timeout=30) as response:
        script_bytes = response.read()
        content_encoding = response.headers.get("Content-Encoding", "").lower()
    fetch_ms = round((time.perf_counter() - started) * 1000)

    decode_started = time.perf_counter()
    transferred_kb = round(len(script_bytes) / 1024)
    if content_encoding == "gzip":
        script_bytes = gzip.decompress(script_bytes)
        size_text = f"{transferred_kb} KB transferred, {round(len(script_bytes) / 1024)} KB decompressed"
    else:
        size_text = f"{transferred_kb} KB, uncompressed response"
    print(f"{fetch_ms} ms to download menu ({size_text})")
    html = extract_menu_html(script_bytes.decode("utf-8"))
    print(f"{round((time.perf_counter() - decode_started) * 1000)} ms to decode menu HTML")
    return html


def extract_menu_html(script):
    match = EMBED_HTML_PATTERN.search(script)
    if match is None:
        raise MenuParseError("Untappd's embed response contained no menu HTML.")
    escapes = {"'": "'", '"': '"', "$": "$", "/": "/", "n": "\n", "\\": "\\"}

    def replace_escape(match):
        character = match.group(1)
        if character not in escapes:
            raise MenuParseError(f"Untappd's embed response used an unsupported escape: \\{character}")
        return escapes[character]

    return re.sub(r"\\(.)", replace_escape, match.group(1)[1:-1], flags=re.DOTALL)


def parse_menu_markers(html, source_url, raw_html_hash=""):
    item_starts = list(ITEM_PATTERN.finditer(html))
    pagination_start = html.find('<div class="pagination-container">')
    beers = []

    for index, item_match in enumerate(item_starts):
        start = item_match.start()
        fallback_end = pagination_start if pagination_start > start else len(html)
        end = item_starts[index + 1].start() if index + 1 < len(item_starts) else fallback_end
        block = html[start:end]
        name_container = extract_inner(block, CLASS_PATTERNS["item_name"])
        name_match = LINK_PATTERN.search(name_container)
        serving_types = extract_all_text(block, CLASS_PATTERNS["serving_type"])
        prices = extract_prices(block)
        serving_options = [
            " ".join(part for part in pair if part)
            for pair in zip(serving_types, prices)
            if any(pair)
        ]
        beer = DraftBeer(
            name=html_text(name_match.group(1) if name_match else ""),
            style=extract_text(block, CLASS_PATTERNS["style"]),
            abv=extract_text(block, CLASS_PATTERNS["abv"]),
            ibu=extract_text(block, CLASS_PATTERNS["ibu"]),
            brewery=extract_text(block, CLASS_PATTERNS["brewery"]),
            location=extract_text(block, CLASS_PATTERNS["location"]),
            serving_options=serving_options,
        )
        if beer.name:
            beers.append(beer)

    if not beers:
        raise MenuParseError("Parsed 0 draft items from Untappd's embed response.")

    date_container = extract_inner(html, CLASS_PATTERNS["date_time"])
    time_match = TIME_PATTERN.search(date_container)
    return MenuSnapshot(
        source_url=source_url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        title=extract_text(html, CLASS_PATTERNS["menu_title"]) or "Beers on Draft",
        updated_at=html_text(time_match.group(1) if time_match else ""),
        section_name=extract_text(html, CLASS_PATTERNS["section_name"]),
        beers=beers,
        raw_html_hash=raw_html_hash,
    )


def extract_inner(source, pattern):
    match = pattern.search(source)
    return match.group(3) if match else ""


def extract_text(source, pattern):
    return html_text(extract_inner(source, pattern))


def extract_all_text(source, pattern):
    return [html_text(match.group(3)) for match in pattern.finditer(source)]


def extract_prices(source):
    return [
        "".join((html_text(match.group(3)) + html_text(match.group(4))).split())
        for match in PRICE_PATTERN.finditer(source)
    ]


def html_text(value):
    value = COMMENT_PATTERN.sub("", value)
    value = TAG_PATTERN.sub("", value)
    return " ".join(html_tools.unescape(value).split())


def load_state(path):
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    return state if state.get("lines") else None


def save_state(path, snapshot):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_beer_diff(previous_lines, current_lines):
    removed = Counter(previous_lines) - Counter(current_lines)
    added = Counter(current_lines) - Counter(previous_lines)
    result = []
    for line in previous_lines:
        if removed[line]:
            result.append(f"- {line}")
            removed[line] -= 1
    for line in current_lines:
        if added[line]:
            result.append(f"+ {line}")
            added[line] -= 1
    return result


def build_change_messages(previous, current):
    beer_diff = build_beer_diff(previous.get("lines", []), current.lines)
    additions = sum(line.startswith("+ ") for line in beer_diff)
    deletions = sum(line.startswith("- ") for line in beer_diff)
    text = "\n".join([
        f"Sabatini's Draft List Changed [\U0001f7e9\uff0b{additions} \U0001f7e5\u2212{deletions}]",
        f"Updated: {current.updated_at or 'unknown'}", "", *beer_diff,
    ])
    return make_diff_blocks(text)


def build_snapshot_messages(snapshot):
    text = "\n".join([
        "Sabatini's Draft List Snapshot",
        f"Updated: {snapshot.updated_at or 'unknown'}", "",
        *(f"+ {line}" for line in snapshot.lines),
    ])
    return make_diff_blocks(text)


def make_diff_blocks(text):
    chunks, current = [], ""
    for original_line in text.splitlines():
        line = protect_internal_hyphens(original_line)
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MAX_DISCORD_BLOCK_LENGTH:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [f"```diff\n{chunk}\n```" for chunk in chunks]


def protect_internal_hyphens(line):
    if line.startswith(("- ", "+ ")):
        return line[:2] + line[2:].replace("-", "\u2013")
    return line.replace("-", "\u2013")


def notify_parse_failure(webhook_url, error):
    post_to_discord(webhook_url, [
        "**Sabatini's Draft Scanner Error**\n"
        f"{error}\nThe saved snapshot was not changed."
    ])


def post_to_discord(webhook_url, messages):
    repository = os.getenv("GITHUB_REPOSITORY", "kenw2255/draft-watcher").strip("/")
    user_agent = f"DiscordBot (https://github.com/{repository}, 1.0)"
    for message in messages:
        request = Request(
            webhook_url,
            data=json.dumps({"content": message}).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": user_agent},
            method="POST",
        )
        try:
            with urlopen(request) as response:
                response.read()
        except HTTPError as error:
            details = error.read().decode(errors="replace")
            raise RuntimeError(f"Discord webhook failed with HTTP {error.code}: {details}") from error


def run():
    try:
        main()
    except MenuParseError:
        traceback.print_exc()
        return PARSE_FAILURE_EXIT_CODE
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
