"""URL canonicalisation and title clustering. BUILD_SPEC §10.2.

Two outlets' copies of one story must collapse into a single item whose primary
link is the copy from the higher-priority source, with the others preserved in
`also_urls` so the reader can see the story was corroborated.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from .config import load_settings

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Words that carry no distinguishing signal in a financial headline.
_STOPWORDS = {
    "a", "an", "the", "of", "for", "to", "in", "on", "at", "by", "with", "and",
    "or", "as", "is", "are", "was", "were", "its", "it", "from", "after", "over",
    "amid", "says", "said", "new", "up", "down", "that", "this", "has", "have",
}

_AMP_HOST_RE = re.compile(r"(?:^|\.)cdn\.ampproject\.org$")


def canonical_url(url: str) -> str:
    """Strip tracking parameters, unwrap AMP and aggregator redirects, normalise."""
    if not url:
        return ""
    url = url.strip()

    # Google News and Bing wrap the publisher link in a redirect; if the real
    # URL is present as a query parameter, prefer it.
    try:
        parsed = urlparse(url)
    except ValueError:
        return url

    qs = dict(parse_qsl(parsed.query, keep_blank_values=False))
    for key in ("url", "u", "target", "redirect", "r"):
        inner = qs.get(key)
        if inner and inner.startswith(("http://", "https://")):
            return canonical_url(unquote(inner))

    if _AMP_HOST_RE.search(parsed.netloc):
        # https://xxx.cdn.ampproject.org/c/s/example.com/path -> https://example.com/path
        m = re.match(r"^/[cv]/s?/(.+)$", parsed.path)
        if m:
            return canonical_url("https://" + m.group(1))

    tracking = set(load_settings().get("tracking_params", []))
    kept = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if k.lower() not in tracking]
    kept.sort()

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.endswith(":80"):
        netloc = netloc[:-3]
    if netloc.endswith(":443"):
        netloc = netloc[:-4]

    path = parsed.path or "/"
    if path.endswith("/amp"):
        path = path[:-4]
    if path.endswith("/amp/"):
        path = path[:-5]
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    scheme = "https" if parsed.scheme in ("http", "https", "") else parsed.scheme
    return urlunparse((scheme, netloc, path, "", urlencode(kept), ""))


def url_hash(url: str) -> str:
    """Stable 16-hex-char id for a canonical URL. Used as the item id and seen-key."""
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def normalise_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title or "").lower()
    t = t.replace("’", "'").replace("‘", "'")
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def title_signature(title: str) -> frozenset[str]:
    """Content words of a headline, for cheap set-overlap clustering."""
    words = [w for w in normalise_title(title).split() if w not in _STOPWORDS and len(w) > 2]
    return frozenset(words)


def title_similarity(a: str, b: str) -> float:
    """Jaccard overlap of content words. 1.0 means identical word sets."""
    sa, sb = title_signature(a), title_signature(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


TITLE_MATCH_THRESHOLD = 0.62


def dedupe(items: list[dict], threshold: float = TITLE_MATCH_THRESHOLD) -> list[dict]:
    """Collapse duplicates. Input order is irrelevant; output is newest-first.

    An item wins a cluster if its source priority is higher; ties are broken by
    the earlier publication time (the outlet that had it first).
    """
    by_url: dict[str, dict] = {}
    for it in items:
        key = url_hash(it.get("url", ""))
        it = dict(it)
        it["id"] = key
        it["url"] = canonical_url(it.get("url", ""))
        it.setdefault("also_urls", [])
        it.setdefault("also_sources", [])
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = it
        else:
            by_url[key] = _merge(existing, it)

    clusters: list[dict] = []
    signatures: list[frozenset[str]] = []
    for it in sorted(by_url.values(), key=lambda x: (-int(x.get("priority", 0)),
                                                     x.get("published_at", ""))):
        sig = title_signature(it.get("title", ""))
        placed = False
        for idx, existing_sig in enumerate(signatures):
            if not sig or not existing_sig:
                continue
            overlap = len(sig & existing_sig) / len(sig | existing_sig)
            if overlap >= threshold:
                clusters[idx] = _merge(clusters[idx], it)
                placed = True
                break
        if not placed:
            clusters.append(it)
            signatures.append(sig)

    clusters.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return clusters


def _merge(primary: dict, other: dict) -> dict:
    """Keep the higher-priority copy as primary; remember the other link."""
    if int(other.get("priority", 0)) > int(primary.get("priority", 0)):
        primary, other = other, primary

    merged = dict(primary)
    also = list(merged.get("also_urls") or [])
    for candidate in [other.get("url")] + list(other.get("also_urls") or []):
        if candidate and candidate != merged.get("url") and candidate not in also:
            also.append(candidate)
    merged["also_urls"] = also[:4]

    also_sources = list(merged.get("also_sources") or [])
    for name in [other.get("source")] + list(other.get("also_sources") or []):
        if name and name != merged.get("source") and name not in also_sources:
            also_sources.append(name)
    merged["also_sources"] = also_sources[:4]

    # Prefer the earliest known publication time and the longest snippet.
    for key in ("published_at",):
        a, b = merged.get(key), other.get(key)
        if a and b:
            merged[key] = min(a, b)
        else:
            merged[key] = a or b
    if len(str(other.get("summary") or "")) > len(str(merged.get("summary") or "")):
        merged["summary"] = other.get("summary")
    return merged
