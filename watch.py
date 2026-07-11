import difflib
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


DEFAULT_MENU_URL = (
    "https://www-sabatinis-com.filesusr.com/html/"
    "78ef16_e5a731e6668aa7c1284a2b632b9ae06e.html"
)
MAX_DISCORD_BLOCK_LENGTH = 1850

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
            menu_url=os.getenv("UNTAPPD_EMBED_URL", DEFAULT_MENU_URL),
            state_file=Path(os.getenv("STATE_FILE", "data/state.json")),
            discord_webhook_url=webhook_url,
        )


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
    settings = Settings.from_environment()
    current = read_current_menu(settings.menu_url)
    previous = load_state(settings.state_file)

    if previous is None:
        messages = build_initial_messages(current)
        post_to_discord(settings.discord_webhook_url, messages)
        save_state(settings.state_file, current)
        print(f"Posted and saved initial snapshot with {len(current.beers)} drafts.")
        return

    beer_lines_changed = previous.get("lines", []) != current.lines
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


def read_current_menu(source_url):
    html = render_menu_page(source_url)
    return parse_menu_html(html, source_url)


def render_menu_page(source_url):
    """Render the JavaScript-powered Untappd embed and return its final HTML."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-dev-shm-usage"])

        try:
            page = browser.new_page(
                extra_http_headers={
                    "referer": "https://www.sabatinis.com/bottleshop"
                }
            )
            page.goto(source_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector(".menu-item", timeout=30_000)
            return page.content()
        finally:
            browser.close()


def parse_menu_html(html, source_url):
    soup = BeautifulSoup(html, "html.parser")
    beers = [parse_beer(node) for node in soup.select(".menu-item")]
    beers = [beer for beer in beers if beer.name]

    if not beers:
        raise RuntimeError(f"Parsed 0 draft items from {source_url}.")

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
    previous_display = [
        f"Updated: {previous.get('updatedAt') or 'unknown'}",
        *previous.get("lines", []),
    ]
    current_display = [
        f"Updated: {current.updated_at or 'unknown'}",
        *current.lines,
    ]

    # ndiff identifies sequence changes; filtering removes all unchanged context.
    diff = [
        line
        for line in difflib.ndiff(previous_display, current_display)
        if line.startswith(("- ", "+ "))
    ]
    content = [
        "Sabatini draft list changed",
        f"Updated: {current.updated_at or 'unknown'}",
        "",
        *diff,
    ]
    return make_diff_blocks("\n".join(content))


def build_initial_messages(current):
    content = [
        "Sabatini draft list snapshot",
        f"Updated: {current.updated_at or 'unknown'}",
        "",
        *(f"+ {line}" for line in current.lines),
    ]
    return make_diff_blocks("\n".join(content))


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
        return diff_prefix + content.replace("-", "–")

    return line.replace("-", "–")


def post_to_discord(webhook_url, messages):
    project_url = os.getenv("CI_PROJECT_URL", "https://gitlab.com")
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


if __name__ == "__main__":
    main()
