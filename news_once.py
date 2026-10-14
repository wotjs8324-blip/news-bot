"""Check Korean economy/society/breaking RSS feeds once per GitHub Actions run.

- Keep the original state format (sent_news.json).
- Initialize each newly added feed without flooding Telegram with old articles.
- Send a brief test/status message when run manually from GitHub Actions.
"""

import json
import os
import re
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

# Importance-based filtering (free, deterministic, headline-only).
# A publisher's "breaking" label or an RSS section never grants priority.
# Tune these keyword groups later based on actual alerts.

PROMO_TERMS = (
    "절임배추", "사전예약", "사전판매", "예약판매",
    "할인행사", "특가판매", "쿠폰", "팝업스토어", "신제품출시",
    "드라마", "예능", "영화개봉", "콘서트", "맛집추천",
)
MARKET_EMERGENCIES = (
    "사이드카", "서킷브레이커", "서킷브레이크", "사이드브레이크",
    "증시거래중단", "주식시장거래중단", "매매거래중단",
    "금융위기", "외환위기", "국가부도", "뱅크런",
)
MARKET_NAMES = ("코스피", "코스닥", "주식시장", "증시")
SHARP_MOVES = ("폭락", "폭등", "급락", "급등", "사상최대낙폭", "사상최대폭")
SPECULATION = ("전망", "예상", "가능성", "우려", "기대", "관측", "검토", "가정")
MAJOR_CRIMES = (
    "살인", "피살", "살해", "흉기난동", "연쇄살인",
    "묻지마폭행", "납치", "유괴", "시신발견",
)
MAJOR_DISASTERS = (
    "대형화재", "대형산불", "건물붕괴", "교량붕괴",
    "열차탈선", "여객기추락", "항공기추락",
    "가스폭발", "압사사고", "원전사고",
    "대규모정전", "유독가스누출",
)
DISASTER_EVENTS = ("화재", "사고", "폭발", "붕괴", "침몰", "추돌", "추락", "산불")
EXTREME_WEATHER = ("태풍", "폭우", "홍수", "산불")
WEATHER_IMPACTS = ("긴급대피", "대규모대피", "인명피해", "사망", "특별재난지역", "비상대응")
SECURITY_EVENTS = ("탄도미사일", "미사일발사", "포격", "핵실험", "무인기침범", "군사분계선침범", "군사도발")


def normalized_title(title):
    """Drop spaces/punctuation used to split Korean keywords."""
    return re.sub(r"[\s·ㆍ\-_]+", "", str(title or "").casefold())


def contains_any(text, keywords):
    return any(word in text for word in keywords)


def _multiple_casualties(title):
    """Match simple '3 people dead' forms; skip a routine single-person accident."""
    patterns = (
        r"(\d+)명(?:이|가|은|는|도|이상|가량)?(?:사망|숨져|숨진|중상|실종)",
        r"(?:사망|중상|실종|사상자)(\d+)명",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, title):
            if int(match.group(1)) >= 2:
                return True
    return False


def classify_article(source, title):
    """Return a category only when the headline describes a major event."""
    t = normalized_title(title)
    if not t or contains_any(t, PROMO_TERMS):
        return None

    # Economy: emergency market mechanisms, exceptional movement or policy decisions.
    if contains_any(t, MARKET_EMERGENCIES) and not contains_any(t, SPECULATION):
        return "금융시장 긴급"
    if contains_any(t, MARKET_NAMES) and contains_any(t, SHARP_MOVES):
        if not contains_any(t, SPECULATION):
            return "금융시장 긴급"
    if contains_any(t, ("환율", "원달러", "원·달러")) and contains_any(
        t, ("폭등", "폭락", "급등", "급락", "1500원돌파", "1600원돌파")
    ):
        if not contains_any(t, SPECULATION):
            return "금융시장 긴급"
    central_banks = ("한국은행", "한은", "금통위", "연준", "fomc")
    policy_rate_news = "기준금리" in t or ("금리" in t and contains_any(t, central_banks))
    if policy_rate_news and contains_any(t, ("인하", "인상", "동결")):
        if not contains_any(t, SPECULATION):
            return "주요 경제정책"

    # Society: serious violent crimes and major accidents.
    if contains_any(t, MAJOR_CRIMES):
        return "강력범죄"
    if "흉기" in t and contains_any(t, ("난동", "찌르", "휘둘러", "위협", "중상", "검거")):
        return "강력범죄"
    if contains_any(t, MAJOR_DISASTERS):
        return "중대재난"
    if contains_any(t, DISASTER_EVENTS):
        if _multiple_casualties(t) or contains_any(t, ("대규모대피", "다수사상", "인명피해속출")):
            return "중대재난"
    if "지진" in t and contains_any(
        t, ("규모5", "규모6", "규모7", "대피", "사망", "인명피해")
    ):
        return "중대재난"
    if contains_any(t, EXTREME_WEATHER) and contains_any(t, WEATHER_IMPACTS):
        return "중대재난"

    # Politics/security: decisions or active threats, not mere commentary.
    if "계엄" in t and contains_any(t, ("선포", "발령", "해제")):
        return "국가 중대사건"
    if "탄핵" in t and contains_any(t, ("가결", "인용", "기각", "선고", "파면")):
        return "국가 중대사건"
    if "대통령" in t and contains_any(t, ("체포", "구속", "파면", "하야")):
        return "국가 중대사건"
    if "북한" in t and contains_any(t, SECURITY_EVENTS):
        return "국가안보"
    if contains_any(t, ("전쟁발발", "전면전돌입", "국가비상사태", "대규모개인정보유출")):
        return "국가 중대사건"
    return None


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
              f"새 알림 {source_sent}건, 중요도 제외 {filtered_out}건, 누적 {sent_count}건", flush=True)

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
