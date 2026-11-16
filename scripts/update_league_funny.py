"""Refresh the League-Funny board and prepare summaries for new articles."""

import argparse
import json
import re
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from league_funny_summary import BODY_SELECTOR, article_payload, codex_summary, read_article, render_article

BOARD_URL = "https://www.league-funny.com/gossiping/new"
ARTICLE_URL = re.compile(r"https://www\.league-funny\.com/gossiping/article-([0-9]+)")
TAIPEI = timezone(timedelta(hours=8))
HOME = Path(__file__).resolve().parents[1] / "index.html"
START = "      <!-- league-funny:begin -->\n"
END = "      <!-- league-funny:end -->"
ARCHIVE_START = "        <!-- league-funny-archive:begin -->\n"
ARCHIVE_END = "        <!-- league-funny-archive:end -->"
ARTICLE_LINK = 'a[href*="/gossiping/article-"]'
LIST_SELECTOR = f'.feedItem {ARTICLE_LINK}'


class BrowserReadError(RuntimeError):
    pass


@dataclass(frozen=True)
class Post:
    number: str
    url: str
    title: str
    published: datetime

    @property
    def summary_path(self):
        return f"pages/event-league-funny-{self.number}.html"

    def snapshot_entry(self):
        return {"url": self.url, "title": self.title, "date": self.published.isoformat()}


class SavedBoard(HTMLParser):
    """Read visible article metadata without executing the saved page's scripts."""

    def __init__(self):
        super().__init__()
        self.posts = []
        self.card = None
        self.div_depth = 0
        self.title = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "div":
            if self.card is not None:
                self.div_depth += 1
            elif "feedItem" in (attributes.get("class") or "").split():
                self.card = {}
                self.div_depth = 1
        if self.card is None:
            return

        if tag == "a" and attributes.get("href"):
            href = urljoin(BOARD_URL, attributes["href"])
            if ARTICLE_URL.fullmatch(href):
                self.card.setdefault("url", href)
        elif tag == "time" and attributes.get("datetime"):
            self.card.setdefault("date", attributes["datetime"])
        elif tag == "h2" and "title" not in self.card:
            self.title = []

    def handle_data(self, data):
        if self.title is not None:
            self.title.append(data)

    def handle_endtag(self, tag):
        if self.card is None:
            return
        if tag == "h2" and self.title is not None:
            self.card["title"] = "".join(self.title)
            self.title = None
        elif tag == "div":
            self.div_depth -= 1
            if self.div_depth == 0:
                self.posts.append(self.card)
                self.card = None
                self.title = None


def read_saved_page(path):
    page = SavedBoard()
    page.feed(path.read_text(encoding="utf-8-sig"))
    page.close()
    return {
        "captured_at": datetime.fromtimestamp(path.stat().st_mtime, TAIPEI).isoformat(timespec="seconds"),
        "posts": page.posts,
    }


@contextmanager
def browser_page(channel, *, headless):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("請先執行 python -m pip install playwright") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, headless=headless)
        try:
            yield browser.new_page(locale="zh-TW")
        finally:
            browser.close()


def open_page(page, url, selector):
    from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

    feed_denied = False

    def observe(response):
        nonlocal feed_denied
        parts = urlsplit(response.url)
        if parts.hostname == "www.league-funny.com" and parts.path == "/service/api/feed":
            feed_denied |= response.status == 403

    page.on("response", observe)
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if response is None or not response.ok:
            status = response.status if response else "無回應"
            raise BrowserReadError(f"遊戲大亂鬥回應 {status}")
        page.locator(selector).first.wait_for(state="attached", timeout=15_000)
    except PlaywrightTimeout as exc:
        message = "八卦版的貼文請求回應 403" if feed_denied else "公開頁面未載入文章資料"
        raise BrowserReadError(message) from exc
    except PlaywrightError as exc:
        raise BrowserReadError("瀏覽器無法讀取公開頁面") from exc
    finally:
        page.remove_listener("response", observe)


class BrowserReader:
    def __init__(self, channel, stack):
        self.channel = channel
        self.session = stack.enter_context(ExitStack())
        self.page = None
        self.headless = True

    def load(self, url, selector):
        if self.page is None:
            self.page = self.session.enter_context(browser_page(self.channel, headless=self.headless))
        try:
            open_page(self.page, url, selector)
        except BrowserReadError:
            if not self.headless:
                raise
            print("背景讀取未取得文章，改用一般瀏覽器。", file=sys.stderr, flush=True)
            self.session.close()
            self.page = None
            self.headless = False
            return self.load(url, selector)
        return self.page


def fetch_snapshot(reader):
    page = reader.load(BOARD_URL, LIST_SELECTOR)
    posts = page.locator(".feedItem").evaluate_all(
        """(cards, selector) => cards.map(card => ({
            url: card.querySelector(selector)?.href,
            title: card.querySelector('h2')?.textContent.trim(),
            date: card.querySelector('time')?.getAttribute('datetime')
        }))""",
        ARTICLE_LINK,
    )
    return {"captured_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "posts": posts}


def parse_date(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("時間必須含時區")
    return result.astimezone(TAIPEI)


def ordered_posts(entries):
    posts = {}
    for item in entries:
        url = item.get("url") or ""
        title = " ".join((item.get("title") or "").split())
        match = ARTICLE_URL.fullmatch(url)
        if not title or not match or not item.get("date"):
            continue
        posts.setdefault(match[1], Post(match[1], url, title, parse_date(item["date"])))
    return sorted(posts.values(), key=lambda post: post.published, reverse=True)


def render_snapshot(snapshot):
    captured = parse_date(snapshot["captured_at"])
    posts = ordered_posts(snapshot["posts"])
    if not posts:
        raise ValueError("未取得可用的八卦版文章")

    lines = [
        '      <p class="feed-updated">擷取時間 '
        f'<time datetime="{captured.isoformat()}">{captured:%Y-%m-%d %H:%M}</time>（台灣時間）</p>',
        '      <ul class="card feed-list">',
    ]
    for post in posts:
        href = post.summary_path if (HOME.parent / post.summary_path).is_file() else post.url
        lines.append(
            f'        <li><a href="{href}"><span>{escape(post.title)}</span>'
            f'<time datetime="{post.published.isoformat()}">{post.published:%m/%d %H:%M}</time></a></li>'
        )
    lines.append("      </ul>")
    return "\n".join(lines) + "\n", len(posts)


def render_archive():
    cards = []
    for path in (HOME.parent / "pages").glob("event-league-funny-*.html"):
        if not re.fullmatch(r"event-league-funny-[0-9]+\.html", path.name):
            continue
        page = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        title = escape(page.select_one("#article-title").get_text(" ", strip=True))
        tag = escape(page.select_one(".article-header .card-tag").get_text(" ", strip=True))
        excerpt = escape(page.select_one(".article-intro, #summary li, #summary .overview-item p").get_text(" ", strip=True))
        published = parse_date(page.select_one(".article-header time")["datetime"])
        card = f'''        <article class="card event-card">
          <div class="event-card-body">
            <div class="meta"><span class="card-tag">{tag}</span><span>遊戲大亂鬥八卦版</span></div>
            <h3>{title}</h3>
            <p>{excerpt}</p>
            <a class="action action-link" href="pages/{path.name}" aria-label="閱讀懶人包：{title}">閱讀懶人包 <span aria-hidden="true">↗</span></a>
          </div>
        </article>
'''
        cards.append((published, card))
    return "".join(card for _, card in sorted(cards, reverse=True))


def replace_section(source, start_marker, end_marker, content):
    if source.count(start_marker) != 1 or source.count(end_marker) != 1:
        raise ValueError("首頁缺少唯一的更新區塊標記")
    start = source.index(start_marker) + len(start_marker)
    end = source.index(end_marker, start)
    return source[:start] + content + source[end:]


def write_file(path, content, *, new_only=False):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        if new_only:
            path.hardlink_to(temporary)
        else:
            temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def update_home(snapshot):
    source = HOME.read_text(encoding="utf-8")
    rendered, count = render_snapshot(snapshot)
    source = replace_section(source, START, END, rendered)
    source = replace_section(source, ARCHIVE_START, ARCHIVE_END, render_archive())
    write_file(HOME, source)
    return count


def create_summary(post, article, images):
    output = render_article(post, codex_summary(article), images)
    try:
        write_file(HOME.parent / post.summary_path, output, new_only=True)
    except FileExistsError:
        return False
    return True


def refresh(args):
    with ExitStack() as stack:
        reader = BrowserReader(args.channel, stack)
        capture = None
        if args.browser_import:
            if args.browser_import == "-":
                import_text = sys.stdin.buffer.read().decode("utf-8-sig")
            else:
                import_text = Path(args.browser_import).read_text(encoding="utf-8-sig")
            capture = json.loads(import_text)
            if not isinstance(capture.get("articles"), dict):
                raise ValueError("瀏覽器匯入資料缺少 articles 欄位")
            snapshot = capture
        elif args.saved_page:
            snapshot = read_saved_page(args.saved_page)
        elif args.snapshot:
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
        else:
            snapshot = fetch_snapshot(reader)

        parse_date(snapshot["captured_at"])
        if not ordered_posts(snapshot["posts"]):
            raise ValueError("未取得可用的八卦版文章")
        pending_path = HOME.parent / ".league-funny-pending.json"
        previous = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.is_file() else []
        queue = ordered_posts(snapshot["posts"] + previous)
        created = 0
        pending = []
        for post in queue:
            if (HOME.parent / post.summary_path).is_file():
                continue
            try:
                if capture is not None:
                    record = capture["articles"].get(post.number)
                    if record is None:
                        raise ValueError("這次瀏覽器擷取未包含正文")
                    article, images = article_payload(post, record["text"], record["images"], record["has_video"])
                elif args.articles_dir:
                    saved = args.articles_dir / f"article-{post.number}.html"
                    article, images = read_article(saved.read_text(encoding="utf-8-sig"), post)
                else:
                    page = reader.load(post.url, BODY_SELECTOR)
                    article, images = read_article(page.content(), post)
                created += create_summary(post, article, images)
            except Exception as exc:
                # A failed post stays in the queue without discarding the rest of the batch.
                pending.append(post.snapshot_entry())
                print(f"文章 {post.number} 待整理：{exc}", file=sys.stderr)

        if pending:
            write_file(pending_path, json.dumps(pending, ensure_ascii=False, indent=2) + "\n")
        else:
            pending_path.unlink(missing_ok=True)
        return update_home(snapshot), created, len(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="msedge", help="已安裝的瀏覽器 channel，例如 msedge 或 chrome")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--snapshot", type=Path, help="從既有 JSON 快照匯入")
    source.add_argument("--saved-page", type=Path, help="從瀏覽器儲存的八卦版 HTML 匯入")
    source.add_argument("--browser-import", metavar="JSON", help="接收瀏覽器擷取資料，使用 - 從 stdin 讀取")
    parser.add_argument("--articles-dir", type=Path, help="讀取已儲存的 article-編號.html 正文")
    args = parser.parse_args()
    try:
        count, created, pending = refresh(args)
    except BrowserReadError as exc:
        print(f"首頁更新失敗：{exc}。可在本專案的 Codex 聊天輸入「更新 gossip-wiki 八卦版」。", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"首頁更新失敗：{exc}", file=sys.stderr)
        return 1
    print(f"八卦版清單 {count} 篇，新增懶人包 {created} 篇，待整理 {pending} 篇")
    return 2 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
