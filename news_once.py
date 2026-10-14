"""Check Korean economy/society/breaking RSS feeds once per GitHub Actions run.

- Keep the original state format (sent_news.json).
- Initialize each newly added feed without flooding Telegram with old articles.
- Send a brief test/status message when run manually from GitHub Actions.
"""

import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = Path(__file__).with_name("sent_news.json")


def google_news_rss(domain):
    """Public Google News search RSS limited to a publisher's own website."""
    params = urlencode({
        "q": f"site:{domain}",
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    })
    return f"https://news.google.com/rss/search?{params}"


# These providers don't currently have a reliably confirmed direct Korean RSS.
# For these we use Google News search feeds, NOT official broadcaster RSS.
# If Google News doesn't list an article it cannot be sent by this bot.
GOOGLE_SOURCE_DOMAINS = {
    "KBS": "news.kbs.co.kr",
    "MBC": "imnews.imbc.com",
    "YTN": "ytn.co.kr",
    "채널A": "ichannela.com",
}


def matches_publisher(entry, expected_domain):
    """Avoid attributing a Google News article to the wrong publisher."""
    source = entry.get("source") or {}
    publisher_url = str(source.get("href", ""))
    publisher_host = (urlsplit(publisher_url).hostname or "").lower()
    return bool(publisher_host) and (
        publisher_host == expected_domain or publisher_host.endswith("." + expected_domain)
    )


# Publisher-owned RSS pages, checked against their published RSS directories.
# A tuple means: if the first address fails, try the next one.
NEWS_FEEDS = {
    # Broadcaster news: four Google News search RSS feeds, and SBS's official RSS.
    # All new feeds are initialized silently on their first successful run.
    "KBS": (google_news_rss("news.kbs.co.kr"),),
    "MBC": (google_news_rss("imnews.imbc.com"),),
    "YTN": (google_news_rss("ytn.co.kr"),),
    "채널A": (google_news_rss("ichannela.com"),),
    # Keep these two, as both worked in the user's latest GitHub Actions log.
    "조선일보": ("https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml",),
    "동아일보": ("https://rss.donga.com/total.xml",),
    # SBS RSS directory: https://news.sbs.co.kr/news/rss.do
    "SBS": (
        "https://news.sbs.co.kr/news/newsflashRssFeed.do?plink=RSSREADER",
        "https://news.sbs.co.kr/news/headlineRssFeed.do?plink=RSSREADER",
    ),
    "SBS 경제": (
        "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=02&plink=RSSREADER",
    ),
    "SBS 사회": (
        "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=03&plink=RSSREADER",
    ),
    # Maeil Business Newspaper RSS directory: https://www.mk.co.kr/rss
    "매일경제 경제": ("https://www.mk.co.kr/rss/30100041/",),
    "매일경제 사회": ("https://www.mk.co.kr/rss/50400012/",),
    # Newsis RSS directory: https://www.newsis.com/RSS/
    "뉴시스 속보": (
        "https://www.newsis.com/RSS/sokbo.xml",
        "https://nwww.newsis.com/RSS/sokbo.xml",
    ),
    "뉴시스 경제": (
        "https://www.newsis.com/RSS/economy.xml",
        "https://nwww.newsis.com/RSS/economy.xml",
    ),
    "뉴시스 사회": (
        "https://www.newsis.com/RSS/society.xml",
        "https://nwww.newsis.com/RSS/society.xml",
    ),
    # MBN RSS directory: https://www.mbn.co.kr/pages/customer/rss.php
    "MBN 경제": ("https://www.mbn.co.kr/rss/economy/",),
    "MBN 사회": ("https://www.mbn.co.kr/rss/society/",),
}

# General feeds use headline keywords; dedicated category feeds use their published sections.
ECONOMY = (
    "경제", "금리", "환율", "물가", "인플레이션", "증시", "코스피", "코스닥",
    "주식", "주가", "금융", "은행", "부동산", "아파트", "전세", "대출",
    "수출", "무역", "관세", "반도체", "기업", "고용", "실업", "한국은행",
    "유가", "세금", "국채", "가계부채", "gdp", "달러", "상장", "기준금리",
    "투자", "채권", "상승", "하락", "시가총액", "매출", "영업이익",
)
SOCIETY = (
    "사회", "사고", "화재", "사망", "체포", "경찰", "검찰", "법원",
    "재판", "구속", "기소", "실종", "지진", "폭우", "태풍", "산불",
    "정부", "국회", "대통령", "재난", "참사", "시위", "파업", "범죄",
    "구조", "피해", "수사", "침수", "폭발", "안전", "의료",
)
BREAKING = ("속보", "긴급", "단독")


def classify(title):
    title = title.casefold()
    if any(word in title for word in BREAKING):
        return "속보"
    if any(word in title for word in ECONOMY):
        return "경제"
    if any(word in title for word in SOCIETY):
        return "사회"
    return None


# Dedicated section RSS feeds already indicate a subject even when a headline
# does not contain one of our generic keywords.
SECTION_CATEGORY = {
    "SBS 경제": "경제",
    "SBS 사회": "사회",
    "매일경제 경제": "경제",
    "매일경제 사회": "사회",
    "뉴시스 속보": "속보",
    "뉴시스 경제": "경제",
    "뉴시스 사회": "사회",
    "MBN 경제": "경제",
    "MBN 사회": "사회",
}

def classify_article(source, title):
    # Breaking labels in titles take precedence over RSS section labels.
    if any(word in title.casefold() for word in BREAKING):
        return "속보"
    return SECTION_CATEGORY.get(source) or classify(title)


def send_msg(text):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("텔레그램 토큰 또는 채팅 ID가 없어. GitHub Secrets 2개를 확인해 줘.")

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=(5, 15),
        )
        result = response.json()
    except (requests.RequestException, ValueError) as error:
        raise RuntimeError(f"텔레그램 연결/응답 오류 ({type(error).__name__})") from error

    if not response.ok or not result.get("ok"):
        reason = result.get("description", "알 수 없는 오류")
        raise RuntimeError(f"텔레그램 API 오류: HTTP {response.status_code} - {reason}")


def load_state():
    if not STATE_FILE.exists():
        return {"seen_links": [], "initialized_feeds": []}, True
    with STATE_FILE.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if not isinstance(state, dict) or not isinstance(state.get("seen_links"), list) or not isinstance(state.get("initialized_feeds"), list):
        raise RuntimeError("sent_news.json 형식이 이상해. 파일을 확인해 줘 (삭제하면 중복 발송될 수 있어).")
    return state, False


def save_state(state):
    state["seen_links"] = state["seen_links"][-5000:]
    temp_file = STATE_FILE.with_suffix(".json.tmp")
    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp_file.replace(STATE_FILE)


def make_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; PersonalRSSReader/2.0)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
    })
    retry = Retry(
        total=1,
        backoff_factor=0.8,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


def get_entries(session, name, urls):
    last_error = "알 수 없는 오류"
    for index, url in enumerate(urls, start=1):
        try:
            response = session.get(url, timeout=(5, 12))
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            entries = parsed.entries[:40]
            # Search results could contain articles from other publishers; reject them.
            if (urlsplit(url).hostname or "").lower() == "news.google.com":
                expected_domain = GOOGLE_SOURCE_DOMAINS.get(name)
                if expected_domain:
                    before = len(entries)
                    entries = [entry for entry in entries
                               if matches_publisher(entry, expected_domain)]
                    print(f"[간접RSS] {name}: 구글 뉴스 {before}건 중 언론사 검증 {len(entries)}건", flush=True)
            if not entries:
                last_error = "응답은 받았지만 해당 언론사 RSS 기사가 없음"
                continue
            if index > 1:
                print(f"[대체주소 성공] {name}: {index}번째 주소 사용", flush=True)
            return entries
        except requests.RequestException as error:
            if isinstance(error, requests.HTTPError) and error.response is not None:
                last_error = f"HTTP {error.response.status_code}"
            else:
                last_error = type(error).__name__
    print(f"[실패] {name}: {last_error}", flush=True)
    return None


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 없어. GitHub Secrets를 확인해 줘.")

    state, is_first_run = load_state()
    seen = set(state["seen_links"])
    initialized = set(state["initialized_feeds"])
    session = make_session()

    successful_sources = 0
    sent_count = 0
    telegram_failures = 0
    failed_sources = []
    newly_initialized = 0

    def remember(link):
        if link not in seen:
            seen.add(link)
            state["seen_links"].append(link)

    for name, urls in NEWS_FEEDS.items():
        entries = get_entries(session, name, urls)
        if entries is None:
            failed_sources.append(name)
            continue

        successful_sources += 1
        articles = [
            (str(entry.get("link", "")).strip(), str(entry.get("title", "")).strip())
            for entry in reversed(entries)
        ]

        # Each source is initialized separately, so adding sources won't spam old news.
        if name not in initialized:
            for link, _ in articles:
                if link:
                    remember(link)
            initialized.add(name)
            state["initialized_feeds"].append(name)
            newly_initialized += 1
            print(f"[초기화] {name}: 기존 기사 {len(articles)}개 제외", flush=True)
            continue

        new_articles = 0
        source_sent = 0
        filtered_out = 0
        for link, title in articles:
            if not link or not title or link in seen:
                continue
            new_articles += 1
            category = classify_article(name, title)
            if category is None:
                filtered_out += 1
                remember(link)
                continue

            try:
                send_msg(f"📰 [{category}] {name}\n\n{title}\n\n{link}")
            except RuntimeError as error:
                telegram_failures += 1
                print(f"[텔레그램 실패] {name}: {error}", flush=True)
                # Leave unsent links out of the seen list so a later run can retry.
                continue

            remember(link)
            sent_count += 1
            source_sent += 1
            time.sleep(0.4)

        print(f"[정상] {name}: {len(articles)}건 조회, 미확인 {new_articles}건, "
              f"새 알림 {source_sent}건, 키워드 제외 {filtered_out}건, 누적 {sent_count}건", flush=True)

    if successful_sources == 0:
        raise RuntimeError("모든 RSS 조회 실패. 소스별 HTTP 에러를 위에서 확인해 줘.")

    save_state(state)
    summary = (
        f"✅ 뉴스 봇 확인 완료\n"
        f"정상 RSS: {successful_sources}/{len(NEWS_FEEDS)}개\n"
        f"새 알림: {sent_count}건\n"
        f"새로 초기화한 RSS: {newly_initialized}개\n"
        f"텔레그램 발송 실패: {telegram_failures}건"
    )
    if failed_sources:
        summary += "\n조회 실패: " + ", ".join(failed_sources)

    print("완료: " + summary.replace("\n", " / "), flush=True)

    # Manual 'Run workflow' sends a status report even if no new articles match.
    # Scheduled executions only send matching articles (plus a one-time welcome).
    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch" or is_first_run:
        send_msg(summary)

    if telegram_failures:
        print(f"[주의] 텔레그램 발송 실패 {telegram_failures}건. 실패한 뉴스는 다음 실행에서 재시도해.", flush=True)


if __name__ == "__main__":
    main()
