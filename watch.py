import argparse
import hashlib
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

from bs4 import BeautifulSoup

MENU_SCRIPT_URL = "https://business.untappd.com/locations/139/themes/340/js"
MAX_DISCORD_BLOCK_LENGTH = 1850
PARSE_FAILURE_EXIT_CODE = 78
EMBED_HTML_PATTERN = re.compile(
    r'container\.innerHTML\s*=\s*("(?:\\.|[^"\\])*")\s*;',
    re.DOTALL,
)

# Discord output settings. The beer name is always included.
SHOW_STYLE = True
SHOW_ABV = True
SHOW_IBU = True
SHOW_BREWERY = True
SHOW_LOCATION = True
SHOW_SIZES_AND_PRICES = False


@dataclass(frozen=True)
class Settings:
    menu_url: str
    state_file: Path
    discord_webhook_url: str

    @classmethod
    def from_environment(cls):
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        if not webhook_url:
            raise RuntimeError("Missing required DISCORD_WEBHOOK_URL variable.")

        return cls(
            menu_url=MENU_SCRIPT_URL,
            state_file=Path(os.getenv("STATE_FILE", "data/state.json")),
            discord_webhook_url=webhook_url,
        )


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

        if SHOW_STYLE and self.style:
            parts.append(self.style)
        if SHOW_ABV and self.abv:
            parts.append(self.abv)
        if SHOW_IBU and self.ibu:
            parts.append(self.ibu)
        if SHOW_BREWERY and self.brewery:
            parts.append(self.brewery)
        if SHOW_LOCATION and self.location:
            parts.append(self.location)
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

    @property
    def lines(self):
        return [beer.diff_line() for beer in self.beers]

    @property
    def display_digest(self):
        discord_data = {
            "updatedAt": self.updated_at,
            "lines": self.lines,
        }
        encoded = json.dumps(discord_data, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def data_digest(self):
        """Track every saved field, including details hidden from Discord."""
        full_menu_data = {
            "title": self.title,
            "updatedAt": self.updated_at,
            "sectionName": self.section_name,
            "items": [asdict(beer) for beer in self.beers],
        }
        encoded = json.dumps(full_menu_data, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self):
        return {
            "sourceUrl": self.source_url,
            "fetchedAt": self.fetched_at,
            "title": self.title,
            "updatedAt": self.updated_at,
            "sectionName": self.section_name,
            "items": [asdict(beer) for beer in self.beers],
            "lines": self.lines,
            "hash": self.display_digest,
            "dataHash": self.data_digest,
        }


def main():
    parser = argparse.ArgumentParser(description="Watch Sabatini's draft menu.")
    parser.add_argument(
        "--post-saved-snapshot",
        action="store_true",
        help="Post the full saved state without checking Untappd or saving changes.",
    )
    arguments = parser.parse_args()
    settings = Settings.from_environment()

    if arguments.post_saved_snapshot:
        post_saved_snapshot(settings)
        return

    try:
        current = read_current_menu(settings.menu_url)
    except MenuParseError as error:
        notify_parse_failure(settings.discord_webhook_url, error)
        raise

    previous = load_state(settings.state_file)

    if previous is None:
        messages = build_initial_messages(current)
        post_to_discord(settings.discord_webhook_url, messages)
        save_state(settings.state_file, current)
        print(f"Posted and saved initial snapshot with {len(current.beers)} drafts.")
        return

    beer_lines_changed = (
        Counter(previous.get("lines", [])) != Counter(current.lines)
    )
    timestamp_changed = previous.get("updatedAt", "") != current.updated_at

    if not beer_lines_changed and not timestamp_changed:
        full_data_changed = previous.get("dataHash") != current.data_digest
        display_hash_changed = previous.get("hash") != current.display_digest

        if full_data_changed or display_hash_changed:
            save_state(settings.state_file, current)
            print("Updated the full state snapshot without notifying Discord.")
            return

        timestamp = current.updated_at or "unknown"
        print(f"No draft list change. Menu timestamp: {timestamp}.")
        return

    messages = build_change_messages(previous, current)
    post_to_discord(settings.discord_webhook_url, messages)
    save_state(settings.state_file, current)
    print(f"Posted update with {len(current.beers)} drafts.")


def post_saved_snapshot(settings):
    snapshot = load_state(settings.state_file)
    if snapshot is None:
        raise RuntimeError(f"No saved snapshot found at {settings.state_file}.")

    messages = build_snapshot_messages(
        snapshot.get("updatedAt", ""),
        snapshot.get("lines", []),
    )
    post_to_discord(settings.discord_webhook_url, messages)
    print(f"Posted saved snapshot with {len(snapshot['lines'])} drafts.")


def read_current_menu(source_url):
    total_started = time.perf_counter()
    html = fetch_menu_html(source_url)

    parse_started = time.perf_counter()
    snapshot = parse_menu_html(html, source_url)
    parse_ms = round((time.perf_counter() - parse_started) * 1000)
    total_ms = round((time.perf_counter() - total_started) * 1000)
    print(f"{parse_ms} ms to parse menu")
    print(f"{total_ms} ms total to read menu")
    return snapshot


def fetch_menu_html(source_url):
    """Fetch Untappd's public embed script and extract its menu HTML."""
    request = Request(
        source_url,
        headers={
            "Accept": "application/javascript",
            "User-Agent": "SabatiniDraftWatcher/1.0",
        },
    )

    fetch_started = time.perf_counter()
    with urlopen(request, timeout=30) as response:
        script_bytes = response.read()
    fetch_ms = round((time.perf_counter() - fetch_started) * 1000)
    print(f"{fetch_ms} ms to fetch menu")

    decode_started = time.perf_counter()
    script = script_bytes.decode("utf-8")
    html = extract_menu_html(script)
    decode_ms = round((time.perf_counter() - decode_started) * 1000)
    print(f"{decode_ms} ms to decode menu HTML")
    return html


def extract_menu_html(script):
    match = EMBED_HTML_PATTERN.search(script)
    if match is None:
        raise MenuParseError("Untappd's embed response contained no menu HTML.")

    escaped_html = match.group(1)[1:-1]
    escape_values = {
        "'": "'",
        '"': '"',
        "$": "$",
        "/": "/",
        "n": "\n",
        "\\": "\\",
    }

    def replace_escape(escape_match):
        character = escape_match.group(1)
        if character not in escape_values:
            raise MenuParseError(
                f"Untappd's embed response used an unsupported escape: \\{character}"
            )
        return escape_values[character]

    return re.sub(r"\\(.)", replace_escape, escaped_html, flags=re.DOTALL)


def parse_menu_html(html, source_url):
    soup = BeautifulSoup(html, "html.parser")
    beers = [parse_beer(node) for node in soup.select(".menu-item")]
    beers = [beer for beer in beers if beer.name]

    if not beers:
        raise MenuParseError("Parsed 0 draft items from Untappd's embed response.")

    return MenuSnapshot(
        source_url=source_url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        title=text_of(soup, ".menu-title") or "Beers on Draft",
        updated_at=text_of(soup, ".date-time time"),
        section_name=text_of(soup, ".section-name"),
        beers=beers,
    )


def parse_beer(node):
    serving_options = []
    for row in node.select(".container-row"):
        serving_type = text_of(row, ".type")
        price = "".join(text_of(row, ".price").split())
        description = " ".join(part for part in [serving_type, price] if part)
        if description:
            serving_options.append(description)

    return DraftBeer(
        name=text_of(node, ".item-name a span") or text_of(node, ".item-name a"),
        style=text_of(node, ".item-style .item-category"),
        abv=text_of(node, ".item-abv"),
        ibu=text_of(node, ".item-ibu"),
        brewery=text_of(node, ".brewery a") or text_of(node, ".brewery"),
        location=text_of(node, ".item-brewery-location"),
        serving_options=serving_options,
    )


def text_of(root, selector):
    element = root.select_one(selector)
    if element is None:
        return ""
    return " ".join(element.get_text(" ", strip=True).split())


def load_state(path):
    if not path.exists():
        return None

    contents = path.read_text(encoding="utf-8").strip()
    if not contents:
        return None

    state = json.loads(contents)
    return state if state.get("lines") else None


def save_state(path, snapshot):
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False)
    path.write_text(serialized + "\n", encoding="utf-8")


def build_change_messages(previous, current):
    beer_diff = build_beer_diff(previous.get("lines", []), current.lines)
    content = [
        f"Sabatini's Draft List Changed [{build_diff_stat(beer_diff)}]",
        f"Updated: {current.updated_at or 'unknown'}",
        "",
        *beer_diff,
    ]
    return make_diff_blocks("\n".join(content))


def build_beer_diff(previous_lines, current_lines):
    """Return true additions and removals while ignoring draft-order changes."""
    removed = Counter(previous_lines) - Counter(current_lines)
    added = Counter(current_lines) - Counter(previous_lines)
    diff = []

    for line in previous_lines:
        if removed[line]:
            diff.append(f"- {line}")
            removed[line] -= 1

    for line in current_lines:
        if added[line]:
            diff.append(f"+ {line}")
            added[line] -= 1

    return diff


def build_diff_stat(beer_diff):
    additions = sum(line.startswith("+ ") for line in beer_diff)
    deletions = sum(line.startswith("- ") for line in beer_diff)

    # Unicode signs avoid Discord's inline diff highlighting on mobile.
    return f"\U0001f7e9\uff0b{additions} \U0001f7e5\u2212{deletions}"


def build_initial_messages(current):
    return build_snapshot_messages(current.updated_at, current.lines)


def build_snapshot_messages(updated_at, lines):
    content = [
        "Sabatini's Draft List Snapshot",
        f"Updated: {updated_at or 'unknown'}",
        "",
        *(f"+ {line}" for line in lines),
    ]
    return make_diff_blocks("\n".join(content))


def notify_parse_failure(webhook_url, error):
    message = (
        "**Sabatini's Draft Watcher Error**\n"
        f"{error}\n"
        "The saved snapshot was not changed.\n"
        "Hourly checks are being disabled to prevent repeated alerts."
    )
    post_to_discord(webhook_url, [message])


def make_diff_blocks(text):
    """Split output into Discord-safe fenced code blocks without breaking lines."""
    chunks = []
    current_chunk = ""

    for line in text.splitlines():
        line = protect_internal_hyphens(line)
        candidate = f"{current_chunk}\n{line}" if current_chunk else line
        if len(candidate) > MAX_DISCORD_BLOCK_LENGTH:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk = candidate

    if current_chunk:
        chunks.append(current_chunk)

    return [f"```diff\n{chunk}\n```" for chunk in chunks]


def protect_internal_hyphens(line):
    """Avoid Discord mobile treating a beer-name hyphen as a removed line."""
    if line.startswith(("- ", "+ ")):
        diff_prefix = line[:2]
        content = line[2:]
        return diff_prefix + content.replace("-", "\u2013")

    return line.replace("-", "\u2013")


def post_to_discord(webhook_url, messages):
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.getenv("GITHUB_REPOSITORY", "kenw2255/draft-watcher").strip("/")
    project_url = f"{server_url}/{repository}"
    user_agent = f"DiscordBot ({project_url}, 1.0)"

    for message in messages:
        request = Request(
            webhook_url,
            data=json.dumps({"content": message}).encode(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": user_agent,
            },
            method="POST",
        )

        try:
            with urlopen(request) as response:
                response.read()
        except HTTPError as error:
            details = error.read().decode(errors="replace")
            raise RuntimeError(
                f"Discord webhook failed with HTTP {error.code}: {details}"
            ) from error


def run():
    try:
        main()
    except MenuParseError:
        traceback.print_exc()
        return PARSE_FAILURE_EXIT_CODE
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
