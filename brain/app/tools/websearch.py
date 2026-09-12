"""Web access: searching (DuckDuckGo HTML, no API key) and actually reading pages.

Search alone only ever returns titles and blurbs, which is enough to *find* something but not
to answer with it -- asking for "today's headlines" yielded "tagesschau.de - die erste Adresse
für Nachrichten" rather than any headline. fetch_url() closes that gap, and handles RSS/Atom
as well as HTML since news sites expose far cleaner content that way.
"""
import logging

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("jarvis.web")

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
MAX_CHARS = 6000


def web_search(query: str) -> str:
    """Search the web and return the top result titles, snippets and URLs.

    Returns only what a results page shows -- to answer from a page's actual content, follow up
    with fetch_url() on the most promising URL.

    Args:
        query: search query text.
    """
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": UA},
            timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for result in soup.select(".result")[:6]:
            title_el = result.select_one(".result__title")
            snippet_el = result.select_one(".result__snippet")
            link_el = result.select_one(".result__url")
            if not title_el:
                continue
            # separator=" " matters: DuckDuckGo wraps matched terms in <b>, and without it the
            # words either side get glued together ("Nachrichten,Schlagzeilenund").
            title = title_el.get_text(" ", strip=True)
            snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
            link = link_el.get_text(" ", strip=True) if link_el else ""
            results.append(f"- {title} ({link}): {snippet}")
        return "\n".join(results) if results else "Keine Ergebnisse gefunden."
    except Exception as e:
        log.exception("web_search fehlgeschlagen")
        return f"Websuche fehlgeschlagen: {e}"


def fetch_url(url: str) -> str:
    """Fetch a web page (or RSS/Atom feed) and return its readable text content.

    Use this whenever the answer depends on what a page actually says rather than on its
    existence: news headlines and articles, documentation, prices, schedules, any page found
    via web_search. For news specifically, a site's RSS feed (often /rss, /feed, or
    .../rss.xml) gives much cleaner and more current results than its homepage.

    Args:
        url: full URL including https://
    """
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "").lower()
        body = resp.text

        if "xml" in content_type or body.lstrip()[:200].lower().find("<rss") != -1 or "<feed" in body[:500].lower():
            soup = BeautifulSoup(body, "xml")
            items = soup.find_all(["item", "entry"])[:20]
            if items:
                lines = []
                for item in items:
                    title = item.find("title")
                    desc = item.find(["description", "summary"])
                    date = item.find(["pubDate", "published", "updated"])
                    line = f"- {title.get_text(' ', strip=True)}" if title else "-"
                    if date:
                        line += f" [{date.get_text(' ', strip=True)}]"
                    if desc:
                        line += f": {desc.get_text(' ', strip=True)[:200]}"
                    lines.append(line)
                return "\n".join(lines)[:MAX_CHARS]

        soup = BeautifulSoup(body, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "form"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        # Collapse the blank-line noise that stripping tags leaves behind.
        lines = [ln for ln in (l.strip() for l in text.splitlines()) if ln]
        return "\n".join(lines)[:MAX_CHARS] or "Seite enthielt keinen lesbaren Text."
    except Exception as e:
        log.exception("fetch_url fehlgeschlagen fuer %s", url)
        return f"Konnte {url} nicht laden: {e}"
