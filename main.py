import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import os

# 렌더 포트 검사 통과용 가짜 웹 서버 (백그라운드 실행)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# 서버를 백그라운드로 실행해서 렌더 포트 요구사항 충족
threading.Thread(target=run_web_server, daemon=True).start()"""NBA post alerts from selected X accounts via FixupX RSS feeds."""

import json
import os
import time
from pathlib import Path

import feedparser
import requests


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ECO_BOT_TOKEN = os.environ.get("TELEGRAM_ECO_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN in Replit Secrets before running.")
if not ECO_BOT_TOKEN:
    raise RuntimeError("Set TELEGRAM_ECO_BOT_TOKEN in Replit Secrets before running.")
if not CHAT_ID:
    raise RuntimeError("Set TELEGRAM_CHAT_ID in the shared environment before running.")

NBA_ACCOUNTS = (
    "UnderdogNBA",
    "ShamsCharania",
    "ChrisBHaynes",
    "MarcStein",
    "JakeLFischer",
    "anthonyVslater",
    "mcten",
    "jovanbuha",
    "JaredWeissNBA",
    "PompeyOnSixers",
    "LawMurrayTheNU",
    "eric_nehm",
    "DuaneRankin",
    "FredKatz",
)
NBA_FEEDS = {
    f"@{handle}": f"https://fixupx.com/{handle}/feed.xml?count=20"
    for handle in NBA_ACCOUNTS
}

POLL_SECONDS = 20
MAX_SEEN_IDS_PER_SOURCE = 100
SEEN_STATE_PATH = Path(__file__).with_name("nba_seen.json")
seen_ids_by_source: dict[str, list[str]] = {}


def send_msg(text: str) -> bool:
    """Send a Telegram message without exposing credentials in logs."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        if response.json().get("ok"):
            return True
        else:
            print("Telegram did not accept the NBA alert.")
    except Exception as exc:
        print(f"NBA/Twitter Telegram message send failed ({type(exc).__name__}).")
    return False


def telegram_bot_id(token: str) -> str:
    """Verify a Telegram token and return its bot ID without logging the token."""
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=12,
        )
        response.raise_for_status()
        data = response.json()
        bot_id = data.get("result", {}).get("id")
        if not data.get("ok") or not bot_id:
            raise RuntimeError("Telegram did not return a bot identity.")
        return str(bot_id)
    except Exception:
        raise RuntimeError(
            "Could not verify Telegram bot identity; NBA alerts are paused."
        ) from None


def verify_distinct_telegram_bots() -> None:
    nba_bot_id = telegram_bot_id(BOT_TOKEN)
    eco_bot_id = telegram_bot_id(ECO_BOT_TOKEN)
    if nba_bot_id == eco_bot_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_ECO_BOT_TOKEN resolve to the same "
            "Telegram bot; NBA alerts are paused to keep the alert streams separate."
        )
    print("NBA Telegram bot identity is separate from the economy bot.")


def fetch_feed(url: str):
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NBA-News-Alert/1.0)"},
        timeout=15,
    )
    response.raise_for_status()
    return feedparser.parse(response.content)


def load_seen_state() -> dict[str, list[str]]:
    """Load saved tweet IDs so restarts do not resend old alerts."""
    try:
        with SEEN_STATE_PATH.open(encoding="utf-8") as state_file:
            state = json.load(state_file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"NBA deduplication state could not be read ({type(exc).__name__}).")
        return {}

    if not isinstance(state, dict):
        print("NBA deduplication state has an invalid format; starting fresh.")
        return {}

    return {
        source: [item for item in ids if isinstance(item, str)][
            :MAX_SEEN_IDS_PER_SOURCE
        ]
        for source, ids in state.items()
        if isinstance(source, str) and isinstance(ids, list)
    }


def save_seen_state() -> None:
    """Persist deduplication state atomically."""
    temporary_path = SEEN_STATE_PATH.with_name(f"{SEEN_STATE_PATH.name}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as state_file:
            json.dump(seen_ids_by_source, state_file, ensure_ascii=False)
        temporary_path.replace(SEEN_STATE_PATH)
    except OSError as exc:
        print(f"NBA deduplication state could not be saved ({type(exc).__name__}).")


def entry_id(entry) -> str:
    """Return a stable identifier for an RSS item."""
    return str(getattr(entry, "link", "") or getattr(entry, "id", "")).strip()


def remember_entry(source_name: str, tweet_id: str) -> None:
    ids = seen_ids_by_source.setdefault(source_name, [])
    if tweet_id not in ids:
        ids.append(tweet_id)
        if len(ids) > MAX_SEEN_IDS_PER_SOURCE:
            del ids[:-MAX_SEEN_IDS_PER_SOURCE]
        save_seen_state()


def start_nba_monitor() -> None:
    global seen_ids_by_source
    verify_distinct_telegram_bots()
    seen_ids_by_source = load_seen_state()

    print("NBA X 계정 알림 감시를 시작합니다.")
    if send_msg("✅ NBA 계정 알림이 시작되었습니다."):
        print("NBA Telegram startup message sent.")

    while True:
        for source_name, feed_url in NBA_FEEDS.items():
            try:
                feed = fetch_feed(feed_url)
                entries = feed.entries[:20]

                if source_name not in seen_ids_by_source:
                    seen_ids_by_source[source_name] = [
                        tweet_id
                        for tweet_id in (entry_id(entry) for entry in entries)
                        if tweet_id
                    ][:MAX_SEEN_IDS_PER_SOURCE]
                    save_seen_state()
                    print(
                        f"[기준점 설정] {source_name}: "
                        f"{len(seen_ids_by_source[source_name])}개 기존 게시물"
                    )
                    continue

                known_ids = set(seen_ids_by_source[source_name])
                new_entries = [
                    (entry_id(entry), entry)
                    for entry in entries
                    if entry_id(entry) and entry_id(entry) not in known_ids
                ]

                for tweet_id, entry in reversed(new_entries):
                    title = " ".join(
                        str(getattr(entry, "title", "")).split()
                    )
                    if not title:
                        continue

                    message = f"🏀 NBA | {source_name}\n\n{title}\n\n{tweet_id}"
                    if send_msg(message):
                        remember_entry(source_name, tweet_id)
                        print(f"[NBA 알림 전송 완료] {source_name}: {title}")
            except Exception as exc:
                print(f"[피드 확인 실패] {source_name}: {type(exc).__name__}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    start_nba_monitor()
