"""Telegram alerts for important economy, finance, and major societal news across major Korean media outlets."""

import os
import time

import feedparser
import requests


BOT_TOKEN = os.environ.get("TELEGRAM_ECO_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("Set TELEGRAM_ECO_BOT_TOKEN in Replit Secrets before running.")
if not CHAT_ID:
    raise RuntimeError("Set TELEGRAM_CHAT_ID in the shared environment before running.")

# 주요 언론사 9개 종합 RSS 피드 라인업
NEWS_FEEDS = {
    "조선일보": "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml",
    "중앙일보": "https://rss.joins.com/joins_news_list.xml",
    "동아일보": "https://rss.donga.com/total.xml",
    "KBS": "https://news.kbs.co.kr/rss/news.xml",
    "MBC": "https://imnews.imbc.com/rss/news/news_00.xml",
    "SBS": "https://news.sbs.co.kr/news/RealaimeRssFeed.do?pmd=S",
    "YTN": "https://www.ytn.co.kr/_comm/rss_news_headline.php",
    "연합뉴스TV": "https://www.yonhapnewstv.co.kr/category/news/feed",
    "채널A": "https://www.ichannela.com/news/rss/news_rss.xml",
}

POLL_SECONDS = 60  # 1분마다 피드 체크
seen_news: set[str] = set()

# 마음에 들어한 핵심 키워드 리스트 (경제 + 사회/정치/국제 대형 이슈)
KEYWORDS = [
    # 경제 / 금융
    "금리", "환율", "주가", "증시", "코스피", "코스닥", "나스닥",
    "한은", "연준", "Fed", "물가", "인플레이션", "수출", "부동산", "유가",
    # 사회 / 정치 / 국제 대형 이슈
    "속보", "긴급", "대통령", "정부", "국회", "법원", "검찰", "경찰",
    "사고", "화재", "지진", "태풍", "전쟁", "미국", "중국", "북한", "외교",
    "AI", "반도체", "삼성", "현대"
]


def send_msg(text: str) -> bool:
    """Send a message without exposing credentials in error output."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        if response.json().get("ok"):
            print("뉴스 알림 전송 완료.")
            return True
        else:
            print("Telegram에서 뉴스 알림을 받지 않았습니다.")
    except Exception as exc:
        print(f"뉴스 메시지 전송 오류 ({type(exc).__name__}).")
    return False


def start_eco_monitor() -> None:
    print("실시간 주요 언론사 종합 뉴스 감시를 시작합니다.")
    send_msg("✅ *실시간 주요 언론사 종합 속보 알리미가 시작되었습니다.*")

    while True:
        for media_name, feed_url in NEWS_FEEDS.items():
            try:
                response = requests.get(
                    feed_url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    timeout=15,
                )
                response.raise_for_status()
                feed = feedparser.parse(response.content)

                for entry in feed.entries[:5]:
                    news_id = getattr(entry, "link", "") or getattr(entry, "id", "")
                    title = getattr(entry, "title", "").strip()
                    summary = getattr(entry, "summary", "").strip()

                    if not news_id or news_id in seen_news:
                        continue

                    article_text = f"{title} {summary}"
                    if not any(keyword.casefold() in article_text.casefold() for keyword in KEYWORDS):
                        seen_news.add(news_id)
                        continue

                    message = (
                        f"📰 *[속보/뉴스] {media_name}*\n\n"
                        f"📌 *제목:* {title}\n\n"
                        f"🔗 [기사 전문 읽기]({news_id})"
                    )

                    if send_msg(message):
                        seen_news.add(news_id)
                        print(f"[{media_name} 알림 전송 완료] {title}")
            except Exception as exc:
                print(f"[{media_name}] 피드 확인 오류 ({type(exc).__name__}).")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    start_eco_monitor()
