import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import feedparser
import requests

# 렌더 포트 검사 통과용 웹 서버
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
        
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# 백그라운드로 웹 서버 켜기
threading.Thread(target=run_web_server, daemon=True).start()

# 텔레그램 설정
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 설정되지 않았습니다.")

def send_msg(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception:
        return False

# 농구 뉴스 수집 메인 루프
def main():
    print("Basketball bot started successfully.")
    sent_links = set()
    
    # 농구/트위터 RSS 주소만 넣는 곳
    rss_urls = [
        "https://rss.app/feeds/v1.5/user_Underdog__NBA.json",
    ]

    while True:
        try:
            for url in rss_urls:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    if not hasattr(entry, 'link') or not hasattr(entry, 'title'):
                        continue
                    link = entry.link
                    if link not in sent_links:
                        sent_links.add(link)
                        title = entry.title
                        text = f"🔥 *NBA 소식 속보*\n\n{title}\n{link}"
                        send_msg(text)
                        time.sleep(1)
        except Exception as e:
            print(f"Error occurred: {e}")
            
        time.sleep(60)

if __name__ == "__main__":
    main()
