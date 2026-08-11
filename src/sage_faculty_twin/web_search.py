from __future__ import annotations

from datetime import date
from dataclasses import dataclass
from html import unescape
from re import IGNORECASE, compile as re_compile
from time import monotonic
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx


_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_WEATHER_QUERY_MARKERS = (
    "天气", "气温", "温度", "体感", "湿度", "风力", "风速", "降雨", "下雨", "weather", "temperature"
)
_NEWS_QUERY_MARKERS = (
    "新闻", "资讯", "动态", "最新", "最近", "近况", "发布", "announcement", "news", "update",
)
_SEARCH_FILLER_RE = re_compile(
    r"请问|帮我|帮忙|查一下|查下|查询一下|查询|告诉我|想知道|看下|看一下|了解一下|搜一下|搜索一下|"
    r"实时|最新|当前|现在|此刻|最近|刚刚|今天|今日|目前|"
    r"有什么|有没有|有哪些|什么样|怎样的|如何|咋样|怎么样|怎么",
    IGNORECASE,
)
_ANSWER_STYLE_RE = re_compile(
    r"请用.{0,8}回答|用.{0,8}回答|一句话回答|简短回答|简要回答|简单回答|直接回答|只回答|长话短说|简洁一点|简洁些",
    IGNORECASE,
)
_DEFINITION_STYLE_RE = re_compile(
    r"是什么|是啥|什么意思|含义是什么|介绍一下|介绍下|说一下|讲一下|讲讲|解释一下|解释下",
    IGNORECASE,
)
_NEWS_ENTITY_FILLER_RE = re_compile(
    r"请|帮我|帮忙|一下|下|告诉我|想知道|看下|看一下|给我|麻烦|麻烦你|"
    r"有什么|有没有|有哪些|什么样|怎样|如何|咋样|怎么样|怎么|什么|哪些",
    IGNORECASE,
)
_NEWS_POSITIVE_HOST_TOKENS = (
    "ithome.com", "36kr.com", "mydrivers.com", "news.qq.com", "finance.sina.com.cn",
    "reuters.com", "theverge.com", "techcrunch.com", "bloomberg.com", "wsj.com",
)
_NEWS_HOST_WEIGHTS = {
    "reuters.com": 18.0,
    "bloomberg.com": 16.0,
    "wsj.com": 15.0,
    "theverge.com": 12.0,
    "techcrunch.com": 12.0,
    "openai.com": 12.0,
    "nvidia.com": 11.0,
    "nvidia.cn": 11.0,
    "ithome.com": 10.0,
    "news.qq.com": 9.0,
    "finance.sina.com.cn": 8.0,
    "thepaper.cn": 8.0,
    "36kr.com": 8.0,
    "mydrivers.com": 4.0,
}
_NEWS_NEGATIVE_HOST_TOKENS = (
    "baike.baidu.com", "apifox.com", "developers.openai.ac.cn", "wikipedia.org",
)
_NEWS_POSITIVE_TEXT_TOKENS = (
    "新闻", "资讯", "动态", "发布", "宣布", "开源", "融资", "ipo", "更新", "上线",
)
_NEWS_NEGATIVE_TEXT_TOKENS = (
    "教程", "百科", "官网入口", "入口地址", "是什么", "开发者", "文档", "apidoc", "api 文档",
)
_NEWS_AGGREGATE_PATH_MARKERS = (
    "/tag/", "/tags/", "/topic/", "/topics/", "/search", "/list", "?tag=", "?tags=",
)
_NEWS_ARTICLE_PATH_MARKERS = (
    "/rain/a/", "/roll/", "/article", "/articles/", "/post/", "/posts/", "/p/", "doc-",
    "newsdetail", "forward_",
)
_NEWS_OFFICIAL_MARKERS = (
    "newsroom", "新闻中心", "press release", "press-release", "press releases",
)
_DATE_PATTERNS = (
    re_compile(r"(?<!\d)(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)"),
    re_compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"),
    re_compile(r"(?<!\d)(20\d{2})年(\d{1,2})月(\d{1,2})日"),
)


@dataclass
class WebSearchResult:
    title: str
    url: str
    snippet: str
    score: float


class WebSearchClient:
    """Sync web search client.  Prefers Tavily (purpose-built for AI),
    falls back to Bing RSS / Bing HTML when Tavily is unavailable."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_results: int,
        tavily_api_key: str = "",
    ) -> None:
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._max_results = max(1, min(int(max_results), 8))
        self._tavily_api_key = (tavily_api_key or "").strip()
        self._search_deadline: float | None = None

    @property
    def enabled(self) -> bool:
        return True

    def search(self, query: str, *, max_results: int | None = None) -> list[WebSearchResult]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []

        limit = self._max_results if max_results is None else max(1, min(int(max_results), 8))

        # Treat the configured timeout as a budget for the *whole* provider
        # chain, not once per fallback. Otherwise an RSS timeout followed by
        # an HTML timeout can silently double the latency of /chat.
        deadline = monotonic() + self._timeout_seconds
        previous_deadline = self._search_deadline
        self._search_deadline = deadline
        try:
            # --- Tavily (primary) ---
            if self._tavily_api_key and monotonic() < deadline:
                try:
                    tavily_results = self._search_tavily(normalized_query, limit)
                    if tavily_results:
                        return tavily_results
                except Exception:
                    pass  # fall through to Bing

            # --- Bing fallback ---
            search_query = self._rewrite_query_for_bing(normalized_query)
            for searcher in (self._search_bing_rss, self._search_bing_html):
                if monotonic() >= deadline:
                    break
                try:
                    results = searcher(search_query, limit)
                except Exception:
                    continue
                if results:
                    reranked = self._rerank_results(normalized_query, results, limit)
                    if reranked:
                        return reranked
            return []
        finally:
            self._search_deadline = previous_deadline

    # ---- Tavily backend ----

    def _search_tavily(self, query: str, max_results: int) -> list[WebSearchResult]:
        from tavily import TavilyClient

        client = TavilyClient(api_key=self._tavily_api_key)
        response = client.search(
            query,
            max_results=max_results,
            include_raw_content=False,
        )
        hits: list[WebSearchResult] = []
        for index, item in enumerate(response.get("results", [])):
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue
            hits.append(
                WebSearchResult(
                    title=title[:300],
                    url=url[:1000],
                    snippet=(item.get("content") or "")[:500],
                    score=float(item.get("score", max_results - index)),
                )
            )
        return hits

    def _client(self) -> httpx.Client:
        timeout_seconds = self._timeout_seconds
        if self._search_deadline is not None:
            timeout_seconds = max(0.05, self._search_deadline - monotonic())
        return httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": _DEFAULT_USER_AGENT,
            },
        )

    def _search_bing_rss(self, query: str, max_results: int) -> list[WebSearchResult]:
        url = f"https://www.bing.com/search?format=rss&q={quote(query)}"
        with self._client() as client:
            response = client.get(
                url,
                headers={
                    "Accept": "application/rss+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.7",
                },
            )
            response.raise_for_status()
            xml = response.text

        blocks = xml.split("<item>")
        results: list[WebSearchResult] = []
        for block in blocks[1:]:
            if len(results) >= max_results:
                break
            title = self._extract_between(block, "<title>", "</title>")
            link = self._extract_between(block, "<link>", "</link>")
            description = self._extract_between(block, "<description>", "</description>")
            if not title or not link:
                continue
            results.append(
                WebSearchResult(
                    title=unescape(title)[:300],
                    url=self._decode_bing_result_url(unescape(link))[:1000],
                    snippet=unescape(description)[:500],
                    score=float(max_results - len(results)),
                )
            )
        return results

    @staticmethod
    def _rewrite_query_for_bing(query: str) -> str:
        lowered = query.lower()
        if any(marker in query or marker in lowered for marker in _WEATHER_QUERY_MARKERS):
            location = WebSearchClient._extract_weather_location(query)
            if location:
                return f"{location} 天气 预报"
        if any(marker in query or marker in lowered for marker in _NEWS_QUERY_MARKERS):
            entity = WebSearchClient._extract_news_entity(query)
            if entity:
                return f"{entity} 新闻"
        return WebSearchClient._normalize_query(query)

    def _search_bing_html(self, query: str, max_results: int) -> list[WebSearchResult]:
        with self._client() as client:
            response = client.get(
                "https://www.bing.com/search",
                params={"q": query, "setlang": "zh-Hans"},
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            response.raise_for_status()
            html = response.text

        results: list[WebSearchResult] = []
        for block in html.split('<li class="b_algo"')[1:]:
            if len(results) >= max_results:
                break
            href_marker = '<h2><a href="'
            if href_marker not in block:
                continue
            href = block.split(href_marker, 1)[1].split('"', 1)[0]
            title_block = block.split(">", 2)
            title = self._strip_tags(title_block[2].split("</a>", 1)[0]) if len(title_block) > 2 else ""
            snippet = ""
            if '<div class="b_caption"><p>' in block:
                snippet = self._strip_tags(
                    block.split('<div class="b_caption"><p>', 1)[1].split("</p>", 1)[0]
                )
            if not title or not href:
                continue
            results.append(
                WebSearchResult(
                    title=title[:300],
                    url=self._decode_bing_result_url(href)[:1000],
                    snippet=snippet[:500],
                    score=float(max_results - len(results)),
                )
            )
        return results

    @classmethod
    def _rerank_results(
        cls,
        original_query: str,
        results: list[WebSearchResult],
        max_results: int,
    ) -> list[WebSearchResult]:
        news_intent = cls._is_news_query(original_query)
        query_tokens = cls._query_tokens(original_query)
        seen_urls: set[str] = set()
        rescored: list[WebSearchResult] = []

        for index, result in enumerate(results):
            canonical_url = cls._canonical_url(result.url)
            if canonical_url in seen_urls:
                continue
            seen_urls.add(canonical_url)
            score = cls._score_result(result, query_tokens=query_tokens, index=index, news_intent=news_intent)
            if news_intent and score < -5:
                continue
            rescored.append(
                WebSearchResult(
                    title=result.title,
                    url=result.url,
                    snippet=result.snippet,
                    score=score,
                )
            )

        rescored.sort(key=lambda item: item.score, reverse=True)
        return rescored[:max_results]

    @classmethod
    def _score_result(
        cls,
        result: WebSearchResult,
        *,
        query_tokens: list[str],
        index: int,
        news_intent: bool,
    ) -> float:
        host = urlparse(result.url).netloc.lower()
        title_lower = result.title.lower()
        snippet_lower = result.snippet.lower()
        combined = f"{title_lower} {snippet_lower} {host}"
        score = 50.0 - index * 6.0
        path = urlparse(result.url).path.lower()
        result_date = cls._extract_result_date(result)

        for token in query_tokens:
            token_lower = token.lower()
            if token_lower and token_lower in combined:
                score += 6.0

        if news_intent:
            if any(token in host for token in _NEWS_POSITIVE_HOST_TOKENS):
                score += 18.0
            if any(token in host for token in _NEWS_NEGATIVE_HOST_TOKENS):
                score -= 28.0
            score += cls._news_source_weight(host)
            if any(token in combined for token in _NEWS_POSITIVE_TEXT_TOKENS):
                score += 10.0
            if any(token in combined for token in _NEWS_NEGATIVE_TEXT_TOKENS):
                score -= 18.0
            if "/news" in result.url.lower() or "news." in host or "/article" in result.url.lower():
                score += 8.0
            if any(marker in result.url.lower() for marker in _NEWS_AGGREGATE_PATH_MARKERS):
                score -= 14.0
            if any(marker in path for marker in _NEWS_ARTICLE_PATH_MARKERS):
                score += 10.0
            if any(marker in combined for marker in _NEWS_OFFICIAL_MARKERS) and any(
                token.lower() in combined for token in query_tokens
            ):
                score += 8.0
            if title_lower.startswith("首页") or title_lower.startswith("home "):
                score -= 8.0
            score += cls._recency_bonus(result_date)

        return score

    @staticmethod
    def _news_source_weight(host: str) -> float:
        for token, weight in _NEWS_HOST_WEIGHTS.items():
            if token in host:
                return weight
        return 0.0

    @staticmethod
    def _recency_bonus(result_date: date | None) -> float:
        if result_date is None:
            return 0.0
        age_days = (date.today() - result_date).days
        if age_days < 0:
            return 0.0
        if age_days <= 3:
            return 18.0
        if age_days <= 7:
            return 14.0
        if age_days <= 30:
            return 8.0
        if age_days <= 90:
            return 3.0
        if age_days >= 365:
            return -8.0
        return 0.0

    @classmethod
    def _extract_result_date(cls, result: WebSearchResult) -> date | None:
        text = f"{result.title} {result.snippet} {result.url}"
        for pattern in _DATE_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            try:
                year = int(match.group(1))
                month = int(match.group(2))
                day = int(match.group(3))
                return date(year, month, day)
            except ValueError:
                continue
        return None

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}"

    @staticmethod
    def _normalize_query(query: str) -> str:
        trimmed = query.strip()[:160]
        if not trimmed:
            return ""
        normalized = trimmed.translate(str.maketrans({
            "，": " ", "。": " ", "！": " ", "？": " ", "?": " ", "、": " ", ",": " ", ".": " ",
            ":": " ", "：": " ", "；": " ", ";": " ", "（": " ", "）": " ", "(": " ", ")": " ",
            "【": " ", "】": " ", "[": " ", "]": " ", '"': " ", "'": " ", "“": " ", "”": " ", "‘": " ", "’": " ",
        }))
        normalized = _SEARCH_FILLER_RE.sub(" ", normalized)
        normalized = " ".join(normalized.split())
        return normalized if len(normalized) >= 4 else trimmed

    @staticmethod
    def _is_news_query(query: str) -> bool:
        lowered = query.lower()
        return any(marker in query or marker in lowered for marker in _NEWS_QUERY_MARKERS)

    @classmethod
    def _extract_news_entity(cls, query: str) -> str:
        normalized = cls._normalize_query(query)
        for token in _NEWS_QUERY_MARKERS:
            normalized = normalized.replace(token, " ")
            normalized = normalized.replace(token.upper(), " ")
        normalized = _ANSWER_STYLE_RE.sub(" ", normalized)
        normalized = _DEFINITION_STYLE_RE.sub(" ", normalized)
        normalized = _NEWS_ENTITY_FILLER_RE.sub(" ", normalized)
        normalized = " ".join(normalized.split()).strip()
        if normalized:
            return normalized
        return query.strip()

    @classmethod
    def _query_tokens(cls, query: str) -> list[str]:
        normalized = cls._normalize_query(query)
        tokens = [token for token in normalized.split() if len(token) >= 2]
        return tokens[:8]

    @staticmethod
    def _extract_between(text: str, start: str, end: str) -> str:
        if start not in text or end not in text:
            return ""
        return text.split(start, 1)[1].split(end, 1)[0].strip()

    @staticmethod
    def _strip_tags(text: str) -> str:
        cleaned: list[str] = []
        in_tag = False
        for char in text:
            if char == "<":
                in_tag = True
                continue
            if char == ">":
                in_tag = False
                cleaned.append(" ")
                continue
            if not in_tag:
                cleaned.append(char)
        return unescape("".join(cleaned)).replace("\xa0", " ").strip()

    @staticmethod
    def _decode_bing_result_url(url: str) -> str:
        try:
            parsed = urlparse(url)
            encoded = parse_qs(parsed.query).get("u", [""])[0]
            if not encoded:
                return url
            normalized = encoded[2:] if encoded.startswith("a1") else encoded
            return unquote(normalized)
        except Exception:
            return url

    @staticmethod
    def _extract_weather_location(query: str) -> str:
        normalized = query
        for token in (
            "请问", "帮我", "帮忙", "查一下", "查下", "查询一下", "查询", "告诉我", "想知道", "看下", "看一下",
            "实时", "最新", "当前", "现在", "此刻", "今天", "今日", "目前", "明天", "后天", "这两天", "最近",
            "今天天气怎么样", "今天天气如何", "天气怎么样", "天气如何", "天气", "气温", "温度", "体感", "湿度",
            "风力", "风速", "降雨概率", "降雨", "下雨吗", "会下雨吗", "会不会下雨", "多少度", "多少", "几度",
        ):
            normalized = normalized.replace(token, " ")
        normalized = normalized.translate(str.maketrans({
            "，": " ", "。": " ", "！": " ", "？": " ", "?": " ", "、": " ", ",": " ", ".": " ",
            ":": " ", "：": " ", "；": " ", ";": " ", "（": " ", "）": " ", "(": " ", ")": " ",
            "【": " ", "】": " ", "[": " ", "]": " ", '"': " ", "'": " ", "“": " ", "”": " ", "‘": " ", "’": " ",
        }))
        normalized = _ANSWER_STYLE_RE.sub(" ", normalized)
        normalized = " ".join(normalized.split()).strip()
        if normalized:
            return normalized
        for candidate in query.split():
            candidate = candidate.strip()
            if candidate:
                return candidate
        return ""
