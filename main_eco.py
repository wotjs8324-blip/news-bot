import os
import time
import feedparser
import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_msg(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except:
        pass

def main():
    print("Eco news bot started.")
    sent_links = set()
    
    # 언론사 RSS 주소들만 깔끔하게 정리
    news_feeds = {
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

    # 초기 중복 방지용 링크 수집
    for name, url in news_feeds.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                if hasattr(entry, 'link'):
                    sent_links.add(entry.link)
        except:
            pass

    while True:
        try:
            for name, url in news_feeds.items():
                feed = feedparser.parse(url)
                for entry in feed.entries[:3]:
                    if not hasattr(entry, 'link') or not hasattr(entry, 'title'):
                        continue
                    link = entry.link
                    if link not in sent_links:
                        sent_links.add(link)
                        title = entry.title
                        text = f"📰 *[{name}] 뉴스 속보*\n\n{title}\n\n{link}"
                        send_msg(text)
                        time.sleep(1)
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(120)

if __name__ == "__main__":
    main()
