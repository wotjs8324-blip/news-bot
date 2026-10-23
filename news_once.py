"""Check Korean economy/politics/society/breaking RSS feeds once per GitHub Actions run.

- Keep the original state format (sent_news.json).
- Initialize each newly added feed without flooding Telegram with old articles.
- Send a brief test/status message when run manually from GitHub Actions.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = Path(__file__).with_name("sent_news.json")


def google_news_rss(domain, topics=None):
    """Google News search RSS scoped to a publisher and optional topics.

    This is a discovery fallback, not an official publisher feed. Google News
    indexing can lag, so a successful RSS read does not guarantee every story.
    """
    query = f"site:{domain}"
    if topics:
        query += f" ({topics})"
    params = urlencode({
        "q": query,
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
    "연합뉴스TV": "yonhapnewstv.co.kr",
    "중앙일보": "joongang.co.kr",
    "중앙일보 경제": "joongang.co.kr",
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
    # Included in the user's ORIGINAL RSS list. A current RSS path was also verified.
    "연합뉴스TV": (
        "https://www.yonhapnewstv.co.kr/browse/feed/",
        google_news_rss("yonhapnewstv.co.kr"),
    ),
    # Official news agency RSS: extra complementary coverage, not a TV channel.
    "연합뉴스": ("https://www.yna.co.kr/rss/news.xml",),
    # The three major newspapers (조중동). Central Daily was missing before.
    "조선일보": ("https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml",),
    "중앙일보": (
        google_news_rss("joongang.co.kr"),
        # Historical Joins RSS URL: kept only as a fallback, not verified live.
        "https://rss.joins.com/joins_news_list.xml",
    ),
    "중앙일보 경제": (
        google_news_rss(
            "joongang.co.kr", "경제 OR 금융 OR 금리 OR 부동산 OR 증시 OR 반도체"
        ),
        "https://rss.joins.com/joins_money_list.xml",
    ),
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


# Extra official feeds for economic learning and housing/markets coverage.
NEWS_FEEDS.update({
    "한국경제 경제": ("https://www.hankyung.com/feed/economy",),
    "한국경제 증권": ("https://www.hankyung.com/feed/finance",),
    "한국경제 부동산": ("https://www.hankyung.com/feed/realestate",),
    "매일경제 증권": ("https://www.mk.co.kr/rss/50200011/",),
    "매일경제 부동산": ("https://www.mk.co.kr/rss/50300009/",),
    "매일경제 기업": ("https://www.mk.co.kr/rss/50100032/",),
})

# Guard against accidentally dropping the outlets the user explicitly requires.
REQUIRED_OUTLETS = (
    "KBS", "MBC", "SBS", "채널A", "YTN", "조선일보", "중앙일보", "동아일보",
    "연합뉴스TV",  # Part of the user's original 9-source RSS list.
)


def validate_required_outlets():
    missing = [name for name in REQUIRED_OUTLETS if name not in NEWS_FEEDS]
    if missing:
        raise RuntimeError("필수 언론사 누락: " + ", ".join(missing))
    print(
        f"[언론사 설정] 필수 {len(REQUIRED_OUTLETS)}곳 모두 포함 / "
        f"뉴스 RSS {len(NEWS_FEEDS)}개 경로: " + ", ".join(REQUIRED_OUTLETS),
        flush=True,
    )


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

    # Society: major accidents and disasters (violent crime alerts were removed on request).
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



# A balanced expansion of the strict emergency-only classifier above.
# The news bot focuses on significant economic news, not every RSS headline.
ECON_PRIMARY = (
    "경제", "금리", "환율", "물가", "인플레이션", "경제성장률", "gdp", "한국은행", "한은", "금통위", "연준", "fomc", "국채", "수출", "수입", "관세", "무역", "재정", "세제", "증시", "코스피", "코스닥", "고용", "실업", "가계부채", "산업", "금융", "거래소",
)
ECON_EVENT = (
    "발표", "확정", "결정", "시행", "개편", "인상", "인하", "동결", "상승", "하락", "급등", "급락", "돌파", "역대", "최고", "최저", "경신", "악화", "개선", "제재", "규제", "완화", "강화", "협상", "타결", "증가", "감소",
)
HOUSING_PRIMARY = (
    "부동산", "아파트", "재건축", "재개발", "주택", "전세", "월세", "집값", "주담대", "청약", "임대차", "공급대책", "분양가", "보유세", "종부세", "양도세", "dsr", "ltv", "전세사기",
)
HOUSING_EVENT = (
    "대책", "규제", "완화", "강화", "정책", "시행", "발표", "확정", "결정", "개편", "공급", "승인", "취소", "상승", "하락", "급등", "급락", "폭등", "폭락", "증가", "감소", "도입", "폐지", "인상", "인하", "금지",
)
POLITICS_PRIMARY = (
    "대통령", "대통령실", "국회", "여야", "여당", "야당", "국민의힘", "민주당", "국무총리", "총리", "장관", "국무회의", "개각", "청문회", "총선", "대선", "지방선거", "재보선", "공천", "법안", "특검", "국정감사", "국감", "헌법재판소", "헌재", "개헌", "정상회담", "한미", "한중", "한일",
)
POLITICS_EVENT = (
    "통과", "가결", "부결", "발의", "거부권", "재의요구", "임명", "지명", "사퇴", "경질", "사임", "출마", "선출", "당선", "합의", "결렬", "발표", "확정", "결정", "시행", "체포", "구속", "기소", "영장", "회담", "담화", "탄핵", "해임",
)
COMPANY_PRIMARY = (
    "삼성전자", "sk하이닉스", "현대차", "기아", "lg에너지솔루션", "lg전자", "네이버", "카카오", "엔비디아", "테슬라", "애플", "마이크로소프트", "tsmc", "삼성바이오",
)
COMPANY_EVENT = (
    "실적", "매출", "영업이익", "순이익", "적자", "흑자", "상장", "ipo", "인수", "합병", "구조조정", "공급계약", "투자계획", "대규모투자", "파산", "법정관리", "감원",
)


def news_priority(source, title):
    """Return (priority, label) or None; source name alone cannot trigger a news alert."""
    t = normalized_title(title)
    if not t or contains_any(t, PROMO_TERMS):
        return None
    emergency = classify_article(source, title)
    if emergency:
        p = 100 if emergency == "금융시장 긴급" else 94
        return p, emergency
    if contains_any(t, HOUSING_PRIMARY) and contains_any(t, HOUSING_EVENT):
        return 82, "부동산"
    # Politics: a political actor plus a concrete decision/event, not commentary.
    if contains_any(t, POLITICS_PRIMARY) and contains_any(t, POLITICS_EVENT):
        if not contains_any(t, SPECULATION):
            return 80, "정치"
    if contains_any(t, COMPANY_PRIMARY) and contains_any(t, COMPANY_EVENT):
        return 78, "기업·산업"
    # Routine tiny index moves are not major news.
    if contains_any(t, MARKET_NAMES) and contains_any(t, ("상승", "하락", "마감")):
        context = ("폭락", "폭등", "급락", "급등", "금리", "환율", "정책", "결정", "제재", "관세", "실적", "대책")
        if not contains_any(t, context):
            percentages = [float(m.group(1)) for m in re.finditer(r"(\d+(?:\.\d+)?)%", t)]
            if not any(value >= 2.0 for value in percentages):
                return None
            return 76, "시장 동향"
    if contains_any(t, ECON_PRIMARY) and contains_any(t, ECON_EVENT):
        # Economic data/policy matters even if publisher didn't mark it breaking.
        return 74, "주요 경제"
    if contains_any(t, MARKET_NAMES):
        # Also catch a large numerical move phrased as "KOSPI 2.1% down".
        for match in re.finditer(r"(\d+(?:\.\d+)?)%", t):
            if float(match.group(1)) >= 2.0 and contains_any(t, ("상승", "하락")):
                return 76, "시장 동향"
    return None


def article_title_key(title):
    # Basic duplicate detection across outlets for IDENTICAL normalized headlines.
    t = re.sub(r"\[[^]]+\]", "", str(title or ""))
    t = re.sub(r"[^\w가-힣]+", "", t.casefold())
    return t[:110] if len(t) >= 10 else ""


def entry_timestamp(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return None


# Read one relevant opinion/column per KST calendar day.
# Official Hankyung opinion feed + optional publisher-checked Google News feeds.
def opinion_search_rss(query):
    params = urlencode({"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    return "https://news.google.com/rss/search?" + params


OPINION_FEEDS = {
    "한국경제 오피니언": ("https://www.hankyung.com/feed/opinion",),
    "매일경제 사설·칼럼": (opinion_search_rss('site:mk.co.kr (사설 OR 칼럼) (경제 OR 부동산 OR 금리 OR 반도체)'),),
    "중앙일보 사설·칼럼": (opinion_search_rss('site:joongang.co.kr (사설 OR 칼럼 OR 시론) (경제 OR 부동산 OR 금리 OR 반도체)'),),
    "한겨레 사설·칼럼": (opinion_search_rss('site:hani.co.kr (사설 OR 칼럼) (경제 OR 부동산 OR 금리 OR 일자리)'),),
}
OPINION_GOOGLE_DOMAINS = {
    "매일경제 사설·칼럼": "mk.co.kr",
    "중앙일보 사설·칼럼": "joongang.co.kr",
    "한겨레 사설·칼럼": "hani.co.kr",
}
ECON_OPINION = (
    "경제", "금리", "물가", "환율", "부동산", "아파트", "주택", "전세", "집값", "코스피", "코스닥", "증시", "주식", "채권", "금융", "은행", "재정", "세금", "관세", "수출", "무역", "기업", "산업", "반도체", "투자", "고용", "실업", "일자리", "성장", "생산성", "재건축", "자산", "ai", "인공지능", "고령화", "국민연금",
)
OPINION_ANALYSIS = (
    "왜", "어떻게", "해법", "대책", "구조", "배경", "선택", "정책", "개혁", "본질", "효과", "영향", "문제", "위기", "기회", "쟁점", "진단", "장기", "미래", "시장", "전략", "전망", "논쟁", "생각", "딜레마", "불평등", "성장",
)
OPINION_MARKER = (
    "사설", "칼럼", "시론", "기고", "오피니언", "매경포럼", "기자24시", "필동정담",
)
OPINION_HOUR_KST = 20
MAX_NEWS_PER_RUN = 12


def opinion_score(source, entry):
    title = str(entry.get("title", "")).strip()
    t = normalized_title(title)
    if not t or contains_any(t, PROMO_TERMS):
        return 0
    is_official = source == "한국경제 오피니언"
    if not is_official and not contains_any(t, OPINION_MARKER):
        return 0
    topics = sum(bool(word in t) for word in ECON_OPINION)
    if not topics:
        return 0
    analytic = sum(bool(word in t) for word in OPINION_ANALYSIS)
    return 4 * min(3, topics) + 2 * min(3, analytic)


def opinion_reading_point(title):
    t = normalized_title(title)
    if contains_any(t, HOUSING_PRIMARY):
        return "부동산 정책과 시장의 인과관계를 살펴봐."
    if contains_any(t, ("금리", "환율", "물가", "재정", "경제")):
        return "글에서 제시한 경제 인과관계와 근거를 살펴봐."
    return "필자의 주장과 근거를 나눠서 읽어봐."


def choose_opinion(session, state, now_kst):
    """Find at most one recent and economics-related opinion article."""
    today = now_kst.date().isoformat()
    if state.get("opinion_date") == today:
        return "already_sent"
    manual = os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if now_kst.hour < OPINION_HOUR_KST and not manual:
        return "waiting_until_evening"

    candidates = []
    seen_opinions = set(state.get("seen_opinion_links", []))
    threshold = now_kst.astimezone(timezone.utc) - timedelta(hours=72)
    for source, urls in OPINION_FEEDS.items():
        entries = get_entries(session, source, urls)
        if entries is None:
            continue
        for entry in entries:
            title = str(entry.get("title", "")).strip()
            link = str(entry.get("link", "")).strip()
            published = entry_timestamp(entry)
            if not title or not link or not published or published < threshold:
                continue
            if link in seen_opinions:
                continue
            expected_domain = OPINION_GOOGLE_DOMAINS.get(source)
            if expected_domain and not matches_publisher(entry, expected_domain):
                continue
            score = opinion_score(source, entry)
            if score:
                candidates.append((score, published, source, title, link))

    if not candidates:
        return "no_relevant_recent_opinion"

    # Weight topic fit/analytical headline above recency, no partisan scoring.
    score, published, source, title, link = max(candidates, key=lambda row: (row[0], row[1]))
    message = (
        "📖 오늘의 경제 읽을거리 (하루 1편)\n\n"
        f"[{source}] {title}\n\n"
        f"🔎 읽을 포인트: {opinion_reading_point(title)}\n\n"
        "※ 사설·칼럼은 필자의 의견이야. 사실과 해석을 구분해서 읽어봐.\n\n"
        + link
    )
    try:
        send_msg(message)
    except RuntimeError as error:
        print(f"[opinion send failed] {error}", flush=True)
        return "telegram_send_failed"

    state["opinion_date"] = today
    state.setdefault("seen_opinion_links", []).append(link)
    state["seen_opinion_links"] = state["seen_opinion_links"][-500:]
    print(f"[opinion sent] {source}: {title} (score={score})", flush=True)
    return "sent"


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
            response = session.get(url, timeout=(4, 8))
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
    validate_required_outlets()
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing (GitHub Secrets).")

    state, first_run = load_state()
    seen = set(state["seen_links"])
    title_keys = set(state.get("seen_title_keys", []))
    initialized = set(state["initialized_feeds"])
    session = make_session()

    successful_sources = 0
    failed_sources = []
    freshly_initialized = 0
    filtered = 0
    eligible = []
    telegram_failures = 0
    manual = os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"

    def remember(link):
        if link not in seen:
            seen.add(link)
            state["seen_links"].append(link)

    for source, urls in NEWS_FEEDS.items():
        entries = get_entries(session, source, urls)
        if entries is None:
            failed_sources.append(source)
            continue
        successful_sources += 1
        if source not in initialized:
            for entry in entries:
                link = str(entry.get("link", "")).strip()
                if link:
                    remember(link)
            initialized.add(source)
            state["initialized_feeds"].append(source)
            freshly_initialized += 1
            print(f"[init] {source}: {len(entries)} old RSS entries marked seen", flush=True)
            continue

        unprocessed = 0
        selected = 0
        omitted = 0
        for entry in entries:
            link = str(entry.get("link", "")).strip()
            title = str(entry.get("title", "")).strip()
            if not link or not title or link in seen:
                continue
            unprocessed += 1
            decision = news_priority(source, title)
            if decision is None:
                omitted += 1
                filtered += 1
                remember(link)
                continue
            priority, category = decision
            selected += 1
            timestamp = entry_timestamp(entry)
            recency = timestamp.timestamp() if timestamp else 0
            eligible.append((priority, recency, source, category, title, link))
        print(f"[RSS] {source}: {len(entries)} fetched / new {unprocessed} / "
              f"eligible {selected} / filtered {omitted}", flush=True)

    # Do not flood Telegram with old missed runs; prefer economy-impacting items.
    eligible.sort(key=lambda item: (item[0], item[1]), reverse=True)
    sent = 0
    duplicate_titles = 0
    for priority, recency, source, category, title, link in eligible:
        key = article_title_key(title)
        if key and key in title_keys:
            duplicate_titles += 1
            remember(link)
            continue
        if sent >= MAX_NEWS_PER_RUN:
            continue   # Do not mark as seen; retry in a subsequent run.
        try:
            send_msg(f"📰 [{category}] {source}\n\n{title}\n\n{link}")
        except RuntimeError as error:
            telegram_failures += 1
            print(f"[Telegram failed] {source}: {error}", flush=True)
            continue
        remember(link)
        if key:
            title_keys.add(key)
            state.setdefault("seen_title_keys", []).append(key)
        sent += 1
        time.sleep(1.0)

    # Daily reading is independent from regular news; no random opinion spam.
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    opinion_status = choose_opinion(session, state, now_kst)
    state["seen_title_keys"] = state.get("seen_title_keys", [])[-4000:]

    # Even if some sources fail, keep state and let the failed ones retry next run.
    save_state(state)
    summary = (
        f"✅ 경제·정치 뉴스 봇 실행 완료\n"
        f"정상 RSS: {successful_sources}/{len(NEWS_FEEDS)}\n"
        f"중요뉴스: {sent}건 전송\n"
        f"중요도 필터로 제외한 기사: {filtered}건\n"
        f"동일 제목 중복: {duplicate_titles}건\n"
        f"새로 초기화한 RSS: {freshly_initialized}개\n"
        f"경제 사설·칼럼: {opinion_status}\n"
        f"텔레그램 전송 실패: {telegram_failures}건"
    )
    if failed_sources:
        summary += "\n조회 실패: " + ", ".join(failed_sources)
    print("[summary] " + summary.replace("\n", " | "), flush=True)

    if manual or first_run:
        send_msg(summary)
    if successful_sources == 0:
        raise RuntimeError("No news sources responded. Check the RSS errors.")


if __name__ == "__main__":
    main()
