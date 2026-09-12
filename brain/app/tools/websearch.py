"""Lightweight web search tool (DuckDuckGo HTML, no API key needed)."""
import requests
from bs4 import BeautifulSoup


def web_search(query: str) -> str:
    """Search the web and return top result titles + snippets + URLs.

    Args:
        query: search query text.
    """
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (JarvisAssistant)"},
            timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for result in soup.select(".result")[:5]:
            title_el = result.select_one(".result__title")
            snippet_el = result.select_one(".result__snippet")
            link_el = result.select_one(".result__url")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            link = link_el.get_text(strip=True) if link_el else ""
            results.append(f"- {title} ({link}): {snippet}")
        return "\n".join(results) if results else "Keine Ergebnisse gefunden."
    except Exception as e:
        return f"Websuche fehlgeschlagen: {e}"
