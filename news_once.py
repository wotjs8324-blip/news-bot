"""Economy, society and breaking-news alerts. Run once per GitHub Actions job."""

import json
import os
import time
from pathlib import Path

import feedparser
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = Path(__file__).with_name("sent_news.json")

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

# A simple title-based filter. Refine the keywords after testing delivery.
ECONOMY = (
    "경제", "금리", "환율", "물가", "인플레이션", "증시", "코스피", "코스닥",
    "주식", "주가", "금융", "은행", "부동산", "아파트", "전세", "대출",
    "수출", "무역", "관세", "반도체", "기업", "고용", "실업", "한국은행",
    "유가", "세금", "국채", "가계부채", "gdp",
)
SOCIETY = (
    "사회", "사고", "화재", "사망", "체포", "경찰", "검찰", "법원",
    "재판", "구속", "기소", "실종", "지진", "폭우", "태풍", "산불",
    "정부", "국회", "대통령", "재난", "참사", "시위", "파업",
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


def send_msg(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=(5, 15),
        )
    except requests.RequestException:
        raise RuntimeError("텔레그램에 연결하지 못했어. 네트워크 상태를 확인해 줘.") from None

    if not response.ok:
        try:
            reason = response.json().get("description", "알 수 없는 오류")
        except ValueError:
            reason = "알 수 없는 오류"
        raise RuntimeError(f"텔레그램 발송 실패: HTTP {response.status_code} - {reason}")


def load_state():
    if not STATE_FILE.exists():
        return {"seen_links": [], "initialized_feeds": []}, True
    with STATE_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data, False


def save_state(state):
    state["seen_links"] = state["seen_links"][-5000:]
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 없어. GitHub Secrets를 확인해 줘."
        )

    state, is_first_run = load_state()
    seen = set(state.get("seen_links", []))
    state.setdefault("seen_links", [])
    initialized = set(state.get("initialized_feeds", []))
    state.setdefault("initialized_feeds", [])

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; RSSNewsBot/1.0)"})
    working_feeds = 0
    sent_count = 0
    failures = 0

    def remember(link):
        if link not in seen:
            seen.add(link)
            state["seen_links"].append(link)

    for name, url in NEWS_FEEDS.items():
        try:
            response = session.get(url, timeout=(5, 15))
            response.raise_for_status()
            entries = feedparser.parse(response.content).entries[:20]
            if not entries:
                print(f"[주의] {name}: RSS 기사가 없어", flush=True)
                continue
        except (requests.RequestException, ValueError) as error:
            print(f"[오류] {name}: RSS 조회 실패 ({type(error).__name__})", flush=True)
            continue

        working_feeds += 1
        articles = [
            (str(entry.get("link", "")).strip(), str(entry.get("title", "")).strip())
            for entry in reversed(entries)
        ]

        # On the first successful fetch of each source, don't spam old articles.
        if name not in initialized:
            for link, _ in articles:
                if link:
                    remember(link)
            initialized.add(name)
            state["initialized_feeds"].append(name)
            print(f"[초기화] {name}: 이전 기사 제외", flush=True)
            continue

        for link, title in articles:
            if not link or not title or link in seen:
                continue
            category = classify(title)
            if category is None:
                remember(link)
                continue

            try:
                send_msg(f"📰 [{category}] {name}\n\n{title}\n\n{link}")
            except RuntimeError as error:
                failures += 1
                print(f"[오류] {name}: {error}", flush=True)
                # Do not mark as sent, so the next run can retry.
                continue

            remember(link)
            sent_count += 1
            time.sleep(0.5)

        print(f"[확인] {name}: 조회 완료", flush=True)

    if working_feeds == 0:
        raise RuntimeError("모든 RSS 조회에 실패했어. RSS 주소와 네트워크를 확인해 줘.")

    if is_first_run:
        # This also confirms that the bot token and chat ID are working.
        send_msg("✅ 뉴스 알림 봇이 준비됐어! 앞으로 새 경제·사회·속보 뉴스를 보내줄게.")

    save_state(state)
    print(
        f"완료: 정상 RSS {working_feeds}개 / 새 알림 {sent_count}개 / "
        f"발송 실패 {failures}개 / 초기화된 언론사 {len(initialized)}개",
        flush=True,
    )


if __name__ == "__main__":
    main()
