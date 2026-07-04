#!/usr/bin/env python3
"""Daily Inspiration Digest — cron 배치 스크립트."""
import json
import os
import smtplib
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import feedparser
import requests

# ── 크리덴셜 로드 ──────────────────────────────────────────────
def _load_env():
    env_path = Path.home() / ".claude" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

KST = timezone(timedelta(hours=9))
NOW  = datetime.now(KST)
TODAY = NOW.date()
DOW   = NOW.weekday()  # 0=월 2=수 4=금

LOG_DIR = Path.home() / ".claude" / "overseer-log"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path.home() / "projects" / "daily-digest" / "seen.db"

# ── 로그 ───────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now(KST).strftime("%H:%M:%S")
    line = f"[digest {TODAY}] {ts} {msg}"
    print(line, flush=True)

# ── RSS 피드 소스 ──────────────────────────────────────────────
LLM_FEEDS = [
    "https://www.reddit.com/r/LocalLLaMA/.rss",
]
CHAOS_FEEDS = [
    "https://news.ycombinator.com/rss",
    "https://lobste.rs/rss",
    "https://www.reddit.com/r/slatestarcodex/.rss",
    "https://www.reddit.com/r/selfhosted/.rss",
    "https://news.hada.io/rss/news",
]
CONSTR_FEEDS = [  # 수요일
    "https://www.construction-physics.com/feed",
    "https://worksinprogress.co/feed/",
]
MACRO_FEEDS = [   # 금요일
    "https://www.gzeromedia.com/gzero-world/rss.xml",
    "https://www.ourworldindata.org/atom.xml",
    "https://www.ben-evans.com/benedictevans/rss.xml",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (digest-bot/1.0)"}


def fetch_feed(url: str, slot_hint: str) -> list[dict]:
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
        items = []
        for e in feed.entries[:20]:
            title = getattr(e, "title", "").strip()
            link  = getattr(e, "link",  "").strip()
            if not title or not link:
                continue
            items.append({"title": title, "url": link, "source": url, "slot_hint": slot_hint, "signal": 0})
        log(f"  {url[:60]} → {len(items)}건")
        return items
    except Exception as ex:
        log(f"  WARN feed 실패 {url[:60]}: {ex}")
        return []


def collect() -> list[dict]:
    log("=== collect ===")
    items = []
    for url in LLM_FEEDS:
        items += fetch_feed(url, "llm_tool")
    for url in CHAOS_FEEDS:
        items += fetch_feed(url, "chaos")
    if DOW == 2:
        for url in CONSTR_FEEDS:
            items += fetch_feed(url, "official_constr")
    if DOW == 4:
        for url in MACRO_FEEDS:
            items += fetch_feed(url, "official_macro")

    # GitHub trending 스크레이프 (html)
    items += fetch_github_trending()

    log(f"collect 총 {len(items)}건")
    return items


def fetch_github_trending() -> list[dict]:
    results = []
    for lang in ("python", ""):
        url = f"https://github.com/trending/{lang}?since=daily"
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")
            import re
            repos = re.findall(r'href="/([^/"]+/[^/"]+)"', html)
            seen_repos = set()
            for r in repos:
                if r in seen_repos or r.count("/") != 1:
                    continue
                seen_repos.add(r)
                # 키워드 필터
                if any(kw in r.lower() for kw in ("mcp", "llm", "agent", "connector", "tool", "plugin")):
                    results.append({
                        "title": r,
                        "url": f"https://github.com/{r}",
                        "source": "github_trending",
                        "slot_hint": "llm_tool",
                        "signal": 0,
                    })
                if len(seen_repos) >= 25:
                    break
        except Exception as ex:
            log(f"  WARN github trending 실패: {ex}")
    log(f"  github trending → {len(results)}건")
    return results


# ── dedup ──────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, first_seen_date TEXT)")
    con.commit()
    return con


def dedup(items: list[dict], con) -> list[dict]:
    log("=== dedup ===")
    cutoff = (TODAY - timedelta(days=30)).isoformat()
    seen_urls = {row[0] for row in con.execute("SELECT url FROM seen WHERE first_seen_date >= ?", (cutoff,))}
    fresh = [i for i in items if i["url"] not in seen_urls]
    log(f"dedup: {len(items)} → {len(fresh)}건 (제거 {len(items)-len(fresh)}건)")
    return fresh


def mark_seen(urls: list[str], con):
    con.executemany("INSERT OR IGNORE INTO seen VALUES (?,?)", [(u, TODAY.isoformat()) for u in urls])
    con.commit()


# ── curate ─────────────────────────────────────────────────────
def curate(items: list[dict]) -> list[dict]:
    log("=== curate ===")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log("WARN ANTHROPIC_API_KEY 없음 → curate 스킵")
        return []

    slots = ["①LLM툴/커넥터", "②재밌는헛소리"]
    if DOW == 2:
        slots.append("③-수건설/인프라")
    if DOW == 4:
        slots.append("③-금라지스케일세계정세")

    candidates_json = json.dumps(items, ensure_ascii=False, indent=None)

    prompt = f"""수신자 프로파일: ODA 물/인프라 엔지니어, 직접 서버·툴 만드는 사람, 물리/유체 관심, 로컬LLM 실험 중.
오늘({TODAY}, weekday={DOW}) 채울 슬롯: {', '.join(slots)}

규칙:
- 슬롯①: 거대담론(GPT/Claude 신버전 등) 금지. "내 서버에 붙여쓸 툴/커넥터/스킬" 레벨만.
- 슬롯②: 밈·웃긴거 금지. 발산적·생각 자극하는 날것만.
- 슬롯③-수: 건설/인프라 정중앙. 수신자 토목/ODA와 직결.
- 슬롯③-금: "2030년 X조 달러" 식 예측 수치/면피성 outlook 금지. "판이 왜 이렇게 바뀌나" 설명하는 글 우선.
- 각 슬롯 1개 선택 (최대 2개). 건질 게 없으면 빈 배열.
- 출력: JSON only, 프리앰블/백틱/설명 금지.

후보 목록:
{candidates_json}

출력 형식 (JSON array):
[{{"slot":"①LLM툴/커넥터","title":"...","url":"...","source":"...","why_you":"한줄","summary":"한줄"}}]"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"].strip()
        # JSON 펜스 제거
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        result = json.loads(raw)
        log(f"curate 완료 → {len(result)}개 선택")
        return result
    except Exception as ex:
        log(f"ERROR curate 실패: {ex}")
        return []


# ── send ───────────────────────────────────────────────────────
def send_telegram(curated: list[dict]):
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("WARN TELEGRAM 크리덴셜 없음 → 텔레그램 스킵")
        return

    if not curated:
        text = f"📰 {TODAY} 영감 다이제스트\n오늘은 건질 게 없었어요."
    else:
        lines = [f"📰 {TODAY} 영감 다이제스트\n"]
        for item in curated:
            lines.append(f"{item['slot']}\n{item['title']}\n{item['why_you']}\n{item['url']}\n")
        text = "\n".join(lines)

    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(url, data, timeout=10)
        log("텔레그램 발송 완료")
    except Exception as ex:
        log(f"ERROR 텔레그램 발송 실패: {ex}")


def send_email(curated: list[dict]):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    to_addr   = os.environ.get("EMAIL_TO")
    if not all([smtp_host, smtp_user, smtp_pass, to_addr]):
        log("WARN 이메일 크리덴셜 불완전 → 이메일 스킵")
        return

    subject = f"[영감] {TODAY} 다이제스트"
    if not curated:
        body = f"오늘({TODAY})은 건질 아이템이 없었어요."
    else:
        parts = []
        for item in curated:
            parts.append(
                f"[{item['slot']}] {item['title']}\n"
                f"왜: {item['why_you']}\n"
                f"요약: {item['summary']}\n"
                f"링크: {item['url']}\n"
            )
        body = f"=== {TODAY} 영감 다이제스트 ===\n\n" + "\n".join(parts)

    try:
        msg = MIMEMultipart()
        msg["From"]    = smtp_user
        msg["To"]      = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_addr, msg.as_string())
        log("이메일 발송 완료")
    except Exception as ex:
        log(f"ERROR 이메일 발송 실패: {ex}")


# ── main ───────────────────────────────────────────────────────
def main():
    log(f"=== 시작 (KST {NOW.strftime('%Y-%m-%d %H:%M')}, weekday={DOW}) ===")
    con = init_db()

    items   = collect()
    fresh   = dedup(items, con)
    curated = curate(fresh)

    if curated:
        mark_seen([i["url"] for i in curated], con)

    send_telegram(curated)
    send_email(curated)

    log("=== 완료 ===")


if __name__ == "__main__":
    main()
