import streamlit as st
import yfinance as yf
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import concurrent.futures
import re
import base64
import html
from pathlib import Path

st.set_page_config(page_title="화천기공 NEWS CLIPPING", layout="wide")

st.markdown("""
<style>
.block-container { padding-top: 1rem; padding-bottom: 2rem; max-width: 1400px; }
header { visibility: hidden; }
::-webkit-scrollbar { height: 6px; }
::-webkit-scrollbar-thumb { background-color: #E2E8F0; border-radius: 10px; }
::-webkit-scrollbar-track { background: transparent; }
</style>
""", unsafe_allow_html=True)

KST = timezone(timedelta(hours=9))
NAVER_STOCK_BASE = "https://stock.naver.com"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Referer": "https://stock.naver.com/",
    "Accept": "application/json,text/plain,*/*",
}


# -----------------------------------------------------------------------------
# 공통 유틸
# -----------------------------------------------------------------------------
def to_float(value, default=0.0):
    try:
        if value is None:
            return default
        text = str(value).replace(",", "").replace("%", "").strip()
        if not text or text in {"-", "--", "N/A", "null"}:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def first_value(d, keys, default=None):
    if not isinstance(d, dict):
        return default
    for key in keys:
        value = d.get(key)
        if value not in (None, ""):
            return value
    return default


def extract_list(payload):
    """API 응답이 list 또는 여러 형태의 dict여도 목록을 최대한 안전하게 추출."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("content", "contents", "items", "stocks", "stockList", "list", "data", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = extract_list(value)
            if nested:
                return nested
    return []


# -----------------------------------------------------------------------------
# 날씨
# -----------------------------------------------------------------------------
@st.cache_data(ttl=1800)
def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=35.1547&longitude=126.9156&current_weather=true"
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        data = res.json()

        if "current_weather" not in data:
            return "광주 날씨: 데이터 파싱 실패"

        temp = data["current_weather"]["temperature"]
        code = data["current_weather"]["weathercode"]

        if code == 0:
            wf = "맑음"
        elif code in [1, 2, 3]:
            wf = "구름많음/흐림"
        elif code in [45, 48]:
            wf = "안개"
        elif code in [51, 53, 55, 61, 63, 65, 80, 81, 82]:
            wf = "비"
        elif code in [71, 73, 75, 85, 86]:
            wf = "눈"
        elif code in [95, 96, 99]:
            wf = "천둥번개"
        else:
            wf = "알수없음"

        return f"광주 날씨: {temp}℃ ({wf})"
    except Exception:
        return "광주 날씨 통신 오류"


# -----------------------------------------------------------------------------
# 환율: Yahoo Finance (실시간/최근 종가 기반)
# -----------------------------------------------------------------------------
def fetch_fx_ticker(ticker, is_jpy=False):
    try:
        hist = yf.Ticker(ticker).history(period="10d").dropna()

        if len(hist) < 2 and ticker == "CNYKRW=X":
            krw = yf.Ticker("KRW=X").history(period="10d").dropna()
            cny = yf.Ticker("CNY=X").history(period="10d").dropna()
            if len(krw) >= 2 and len(cny) >= 2:
                curr = krw["Close"].iloc[-1] / cny["Close"].iloc[-1]
                prev = krw["Close"].iloc[-2] / cny["Close"].iloc[-2]
                return curr, ((curr - prev) / prev) * 100

        if len(hist) < 2:
            return 0.0, 0.0

        current = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2])
        if is_jpy:
            current *= 100
            prev *= 100
        pct = ((current - prev) / prev) * 100 if prev else 0.0
        return current, pct
    except Exception:
        return 0.0, 0.0


@st.cache_data(ttl=600)
def get_fx_rows():
    specs = [
        ("KRW=X", "미국 달러(USD)", False),
        ("EURKRW=X", "유럽 유로(EUR)", False),
        ("JPYKRW=X", "일본 엔(100)", True),
        ("CNYKRW=X", "중국 위안(CNY)", False),
    ]

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fetch_fx_ticker, ticker, is_jpy): (ticker, name)
            for ticker, name, is_jpy in specs
        }
        for future in concurrent.futures.as_completed(futures):
            ticker, name = futures[future]
            price, pct = future.result()
            rows.append({"code": ticker, "name": name, "price": price, "pct": pct})

    order = {ticker: idx for idx, (ticker, _, _) in enumerate(specs)}
    rows.sort(key=lambda x: order.get(x["code"], 999))
    return rows


# -----------------------------------------------------------------------------
# 주식 랭킹: Npay 증권의 현재 공개 read-only API 기반
# - 국내 시가총액: marketSum
# - 국내 거래량 급증: upperQuantTop
# - 미국 거래량 상위: quantTop
# -----------------------------------------------------------------------------
def fetch_json(url, params=None, timeout=10):
    res = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=timeout)
    res.raise_for_status()
    return res.json()


def parse_domestic_stock(item):
    code = str(first_value(item, ["itemcode", "itemCode", "code", "stockCode"], ""))
    name = str(first_value(item, ["itemname", "itemName", "name", "stockName"], ""))
    price = to_float(first_value(item, ["nowPrice", "closePrice", "currentPrice", "price"], 0))
    pct = to_float(first_value(item, ["prevChangeRate", "fluctuationsRatio", "changeRate", "changePercent"], 0))
    return {"code": code, "name": name, "price": price, "pct": pct}


def parse_foreign_stock(item):
    code = str(first_value(item, ["reutersCode", "symbolCode", "symbol", "itemcode", "itemCode", "code"], ""))
    name = str(first_value(item, ["stockName", "stockNameEng", "itemname", "itemName", "name", "companyName"], code))
    price = to_float(first_value(item, ["closePrice", "nowPrice", "currentPrice", "price", "lastPrice"], 0))
    pct = to_float(first_value(item, ["fluctuationsRatio", "prevChangeRate", "changeRate", "changePercent"], 0))
    return {"code": code, "name": name, "price": price, "pct": pct}


@st.cache_data(ttl=300)
def fetch_naver_domestic_ranking(order_type, limit=10):
    try:
        payload = fetch_json(
            f"{NAVER_STOCK_BASE}/api/domestic/market/stock/default",
            params={
                "tradeType": "KRX",
                "marketType": "ALL",
                "orderType": order_type,
                "startIdx": 0,
                "pageSize": max(limit, 10),
            },
        )
        rows = []
        for item in extract_list(payload):
            row = parse_domestic_stock(item)
            if row["name"] and row["price"] > 0:
                rows.append(row)
            if len(rows) >= limit:
                break
        return rows
    except Exception:
        return []


@st.cache_data(ttl=300)
def fetch_naver_usa_ranking(order_type="quantTop", limit=10):
    try:
        payload = fetch_json(
            f"{NAVER_STOCK_BASE}/api/foreign/market/stock/global",
            params={
                "nation": "usa",
                "tradeType": "ALL",
                "orderType": order_type,
                "startIdx": 0,
                "pageSize": max(limit, 10),
            },
        )
        rows = []
        for item in extract_list(payload):
            row = parse_foreign_stock(item)
            if row["name"] and row["price"] > 0:
                rows.append(row)
            if len(rows) >= limit:
                break
        return rows
    except Exception:
        return []


@st.cache_data(ttl=300)
def get_market_rows():
    # 고정 후보군에서 고르는 방식이 아니라 실행 시점의 시장 랭킹을 직접 조회한다.
    domestic_market_cap = fetch_naver_domestic_ranking("marketSum", 10)
    domestic_volume_surge = fetch_naver_domestic_ranking("upperQuantTop", 10)
    usa_volume = fetch_naver_usa_ranking("quantTop", 10)
    return domestic_market_cap, domestic_volume_surge, usa_volume


# -----------------------------------------------------------------------------
# 뉴스
# 핵심 변경점
# 1) 언론사 화이트리스트 제거: 전문 산업매체가 통째로 잘리던 문제 해결
# 2) '투자' 등 산업기사에서 흔한 단어를 블랙리스트에서 제거
# 3) 24시간 고정이 아니라 1일 -> 3일 -> 7일 순으로 부족분 보충
# 4) 제목만이 아니라 RSS description까지 relevance 검사
# -----------------------------------------------------------------------------
NEWS_BLACKLIST = [
    "카지노", "바카라", "도박", "슬롯", "토토", "룰렛", "꽁머니", "성인사이트",
    "몰카", "불법촬영", "성범죄",
    "아이돌", "가수", "배우", "예능", "드라마", "영화", "콘서트",
    "야구", "축구", "농구", "프로야구", "프로축구",
    "고등학교", "신입생", "입학설명회", "알바", "아르바이트",
    "가구", "인테리어", "장판", "창호",
]

NEWS_SOURCE_BLACKLIST = [
    "Histoire pour tous", "티스토리", "Tistory", "blogspot", "개인 블로그"
]


def normalize_title(title):
    title = re.sub(r"\s+", " ", title or "").strip()
    return title


def title_key(title):
    text = re.sub(r"\[.*?\]", "", title.lower())
    text = re.sub(r"[^0-9a-z가-힣]", "", text)
    return text


def is_similar_title(a, b):
    a_key, b_key = title_key(a), title_key(b)
    if not a_key or not b_key:
        return False
    if a_key == b_key:
        return True
    # 지나치게 공격적인 중복제거를 피하고, 거의 같은 기사만 제거
    shorter = min(len(a_key), len(b_key))
    if shorter < 12:
        return False
    common = sum(1 for i in range(shorter - 1) if a_key[i:i+2] in b_key)
    return common / max(shorter - 1, 1) >= 0.72


def contains_any(text, keywords):
    lowered = (text or "").lower()
    compact = lowered.replace(" ", "")
    for keyword in keywords:
        kw = keyword.lower().strip()
        if not kw:
            continue
        if re.fullmatch(r"[a-z0-9+#.\-/ ]+", kw):
            if kw in lowered:
                return True
        elif kw.replace(" ", "") in compact:
            return True
    return False


@st.cache_data(ttl=1800)
def fetch_google_rss(query, days=1, limit=40):
    try:
        q = f"({query}) when:{days}d"
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
            "q": q,
            "hl": "ko",
            "gl": "KR",
            "ceid": "KR:ko",
        })
        res = requests.get(url, timeout=12, headers={"User-Agent": REQUEST_HEADERS["User-Agent"]})
        res.raise_for_status()
        soup = BeautifulSoup(res.content, "xml")

        now_utc = datetime.now(timezone.utc)
        results = []

        for item in soup.find_all("item"):
            raw_title = normalize_title(item.title.text if item.title else "")
            source = normalize_title(item.source.text if item.source else "알수없음")
            link = item.link.text.strip() if item.link else "#"
            desc = BeautifulSoup(item.description.text, "html.parser").get_text(" ", strip=True) if item.description else ""

            # Google News 제목의 끝 '- 언론사' 제거
            clean_title = raw_title
            if source and raw_title.endswith(f" - {source}"):
                clean_title = raw_title[: -(len(source) + 3)].strip()
            elif " - " in raw_title:
                clean_title = raw_title.rsplit(" - ", 1)[0].strip()

            combined = f"{clean_title} {desc}"
            if any(word.lower() in combined.lower() for word in NEWS_BLACKLIST):
                continue
            if any(word.lower() in source.lower() for word in NEWS_SOURCE_BLACKLIST):
                continue

            pub_dt = None
            pub_tag = item.find("pubDate")
            if pub_tag:
                try:
                    pub_dt = parsedate_to_datetime(pub_tag.text).astimezone(timezone.utc)
                    # when:Nd를 쓰더라도 RSS가 드물게 오래된 결과를 섞는 경우를 차단
                    if (now_utc - pub_dt).total_seconds() > (days * 86400 + 6 * 3600):
                        continue
                    if pub_dt > now_utc + timedelta(hours=1):
                        continue
                except Exception:
                    pub_dt = None

            results.append({
                "title": clean_title,
                "link": link,
                "source": source,
                "description": desc,
                "pub_dt": pub_dt,
            })
            if len(results) >= limit:
                break

        return results
    except Exception:
        return []


def merge_news(existing, candidates, keywords, target_limit):
    for news in candidates:
        haystack = f"{news['title']} {news.get('description', '')}"
        if keywords and not contains_any(haystack, keywords):
            continue
        if any(is_similar_title(news["title"], x["title"]) for x in existing):
            continue
        existing.append(news)
        if len(existing) >= target_limit:
            break
    return existing


def get_category_news(queries, keywords, target_limit=10):
    """최근 1일 우선. 부족하면 3일, 그래도 부족하면 7일까지 단계적으로 확장."""
    collected = []
    for days in (1, 3, 7):
        # 같은 기간의 검색어는 병렬 조회해서 Streamlit 초기 로딩 시간을 줄인다.
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(queries), 4)) as executor:
            futures = [executor.submit(fetch_google_rss, query, days, 40) for query in queries]
            for future in concurrent.futures.as_completed(futures):
                try:
                    candidates = future.result()
                except Exception:
                    candidates = []
                merge_news(collected, candidates, keywords, target_limit)
                if len(collected) >= target_limit:
                    break
        if len(collected) >= target_limit:
            break

    # 최신 기사부터 정렬. 날짜가 없는 결과는 뒤로 보낸다.
    collected.sort(
        key=lambda x: x.get("pub_dt") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    if not collected:
        return [{
            "title": "최근 7일 이내 관련 뉴스를 불러오지 못했습니다.",
            "link": "#",
            "source": "알림",
            "description": "",
            "pub_dt": None,
        }]
    return collected[:target_limit]


NEWS_CONFIG = {
    "machinery": {
        "queries": [
            '"공작기계" OR "머시닝센터" OR "CNC 가공" OR "절삭가공" OR "5축 가공"',
            '"machine tool" OR "machining center" OR "DMG MORI" OR Mazak OR Makino OR Okuma',
            '"화천기공" OR "DN솔루션즈" OR "디엔솔루션즈" OR "현대위아" OR "스맥"',
        ],
        "keywords": [
            "공작기계", "머시닝센터", "cnc", "절삭가공", "5축", "가공기", "machine tool",
            "machining center", "화천기공", "dn솔루션즈", "디엔솔루션즈", "현대위아", "스맥",
            "dmg mori", "mazak", "makino", "okuma", "야마자키", "마작"
        ],
    },
    "materials": {
        "queries": [
            '"절삭공구" OR "산업용 스핀들" OR "정밀 베어링" OR "정밀부품" OR "초경공구"',
            '"엔드밀" OR "인서트" OR "볼스크류" OR "리니어가이드" OR "산업용 모터"',
            '"신소재" 제조 OR "금속 소재" 제조 OR "첨단소재" 부품',
        ],
        "keywords": [
            "절삭공구", "공구", "스핀들", "베어링", "정밀부품", "초경", "엔드밀", "인서트",
            "볼스크류", "리니어가이드", "산업용모터", "신소재", "첨단소재", "금속소재", "합금"
        ],
    },
    "semi": {
        "queries": [
            '"반도체 장비" OR "반도체 제조장비" OR "노광장비" OR "식각장비" OR "증착장비"',
            '"HBM" 장비 OR "패키징 장비" OR "웨이퍼 장비" OR EUV',
            'ASML OR TSMC OR "반도체 팹"',
        ],
        "keywords": [
            "반도체", "장비", "노광", "식각", "증착", "패키징", "웨이퍼", "euv", "hbm",
            "asml", "tsmc", "파운드리", "팹"
        ],
    },
    "robotics": {
        "queries": [
            '"산업용 로봇" OR "협동로봇" OR "공장자동화" OR "스마트팩토리"',
            '"AMR" 공장 OR "AGV" 공장 OR "무인 자동화" 제조',
            '"로봇 자동화" 제조 OR "AI 팩토리"',
        ],
        "keywords": [
            "산업용로봇", "협동로봇", "공장자동화", "스마트팩토리", "amr", "agv", "무인자동화",
            "로봇자동화", "ai팩토리", "자동화", "로봇"
        ],
    },
}


@st.cache_data(ttl=1800)
def get_all_news():
    # 카테고리 간 기사 수를 갉아먹는 global_seen을 사용하지 않는다.
    # 각 카테고리는 자체적으로 충분히 채우며, 네 카테고리를 병렬 수집한다.
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(NEWS_CONFIG)) as executor:
        futures = {
            executor.submit(get_category_news, cfg["queries"], cfg["keywords"], 10): key
            for key, cfg in NEWS_CONFIG.items()
        }
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                result[key] = future.result()
            except Exception:
                result[key] = [{
                    "title": "최근 7일 이내 관련 뉴스를 불러오지 못했습니다.",
                    "link": "#",
                    "source": "알림",
                    "description": "",
                    "pub_dt": None,
                }]

    # 호출 순서와 무관하게 항상 모든 키가 존재하도록 보장
    for key in NEWS_CONFIG:
        result.setdefault(key, [])
    return result


# -----------------------------------------------------------------------------
# 전시회: 검색 결과에서 날짜를 스크래핑하지 않고 검증한 일정만 사용
# 사용자 요청대로 표시 순서는 무조건 한국 -> 일본 -> 중국 -> 미국 -> 유럽
# -----------------------------------------------------------------------------
def get_verified_exhibition_dates():
    return [
        {"country": "한국", "name": "SIMTOS", "start": "2028.04.03", "end": "2028.04.07"},
        {"country": "일본", "name": "JIMTOF", "start": "2026.10.26", "end": "2026.10.31"},
        {"country": "중국", "name": "CIMT", "start": "2027.04.19", "end": "2027.04.24"},
        {"country": "미국", "name": "IMTS", "start": "2026.09.14", "end": "2026.09.19"},
        {"country": "이탈리아", "name": "EMO", "start": "2027.10.04", "end": "2027.10.08"},
    ]


# -----------------------------------------------------------------------------
# 렌더링 데이터 준비
# -----------------------------------------------------------------------------
now = datetime.now(KST)
date_string = f"{now.strftime('%Y년 %m월 %d일')} 조간"
today_date = now.date()

with st.spinner("뉴스·전시회·시장 데이터를 불러오는 중입니다..."):
    weather_info = get_weather()
    news = get_all_news()
    fx_rows = get_fx_rows()
    domestic_rows, volume_surge_rows, usa_volume_rows = get_market_rows()


# -----------------------------------------------------------------------------
# HTML 렌더러
# -----------------------------------------------------------------------------
def f_pct(pct):
    if pct > 0:
        return f"<span style='color: #DC2626; font-weight: bold;'>▲ {pct:.2f}%</span>"
    if pct < 0:
        return f"<span style='color: #2563EB; font-weight: bold;'>▼ {abs(pct):.2f}%</span>"
    return "<span style='color: #4B5563;'>- 0.00%</span>"


def render_table(title, rows, currency="KRW", empty_message="데이터 조회 실패"):
    out = f"""
    <div style='flex: 1 1 230px; min-width: 230px; background-color: #ffffff; border: 1px solid #E5E7EB;
                border-radius: 8px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); overflow: hidden;'>
      <h4 style='font-size: 14px; color: #1E3A8A; margin: 0 0 10px 0; border-bottom: 2px solid #1E3A8A;
                 padding-bottom: 6px; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>
        {html.escape(title)}
      </h4>
      <table style='width: 100%; font-size: 12px; border-collapse: collapse; text-align: right; table-layout: fixed;'>
    """

    valid_rows = [r for r in rows if r.get("price", 0) > 0]
    if not valid_rows:
        out += f"<tr><td colspan='3' style='padding:18px 4px; text-align:center; color:#9CA3AF;'>{html.escape(empty_message)}</td></tr>"
    else:
        for row in valid_rows[:10]:
            name = html.escape(str(row.get("name", "")))
            price = to_float(row.get("price", 0))
            pct = to_float(row.get("pct", 0))
            if currency == "USD":
                price_str = f"${price:,.2f}"
            elif currency == "FX":
                price_str = f"{price:,.2f}원"
            else:
                price_str = f"{price:,.0f}원"

            out += (
                "<tr style='border-bottom: 1px solid #F3F4F6;'>"
                f"<td style='text-align: left; padding: 8px 0; font-weight: 600; color: #374151; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>{name}</td>"
                f"<td style='padding: 8px 15px 8px 0; white-space: nowrap;'>{price_str}</td>"
                f"<td style='padding: 8px 0; white-space: nowrap;'>{f_pct(pct)}</td>"
                "</tr>"
            )

    out += "</table></div>"
    return out


def format_news_date(pub_dt):
    if not pub_dt:
        return ""
    try:
        return pub_dt.astimezone(KST).strftime("%m/%d")
    except Exception:
        return ""


def render_news_list(title, news_list):
    safe_title = html.escape(title)
    out = f"""
    <div style='margin-bottom: 25px; overflow: hidden;'>
      <h3 style='font-size: 16px; color: #1E3A8A; border-left: 4px solid #1E3A8A; padding-left: 10px;
                 margin-top:0; margin-bottom: 12px; white-space: nowrap;'>{safe_title}</h3>
      <ul style='list-style: none; padding: 0; margin: 0; font-size: 14px; line-height: 1.8;'>
    """
    for n in news_list:
        safe_link = html.escape(n.get("link", "#"), quote=True)
        safe_news_title = html.escape(n.get("title", ""))
        safe_source = html.escape(n.get("source", "알수없음"))
        date_text = format_news_date(n.get("pub_dt"))
        date_html = f" <span style='color:#94A3B8; font-size:11px;'>({date_text})</span>" if date_text else ""
        out += (
            "<li style='margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>"
            f"<a href='{safe_link}' style='color: #1F2937; text-decoration: none;' target='_blank' rel='noopener noreferrer'>- {safe_news_title}</a> "
            f"<span style='color:#9CA3AF; font-size:12px; font-weight:600;'>[{safe_source}]</span>{date_html}"
            "</li>"
        )
    out += "</ul></div>"
    return out


# 전시회는 리스트에 적힌 순서를 그대로 유지한다. 날짜순 재정렬 금지.
exhib_html = ""
for ex in get_verified_exhibition_dates():
    s_date = datetime.strptime(ex["start"], "%Y.%m.%d").date()
    e_date = datetime.strptime(ex["end"], "%Y.%m.%d").date()

    if today_date < s_date:
        days = (s_date - today_date).days
        dday_str = f"D-{days}"
        color = "#DC2626" if days <= 30 else "#005CAB"
    elif s_date <= today_date <= e_date:
        dday_str = "진행중"
        color = "#DC2626"
    else:
        days = (today_date - e_date).days
        dday_str = f"D+{days}"
        color = "#9CA3AF"

    start_str = ex["start"][2:]
    end_str = ex["end"][2:]
    exhib_html += (
        "<div style='flex: 0 0 auto; text-align: center; font-size: 13px; color: #374151;'>"
        f"<span style='font-weight: bold;'>{html.escape(ex['country'])} {html.escape(ex['name'])}</span> "
        f"<span style='color: #6B7280; font-size: 12px; margin-left: 4px;'>({start_str} ~ {end_str})</span> "
        f"<span style='color: {color}; font-weight: bold; margin-left: 4px;'>[{dday_str}]</span>"
        "</div>"
    )


logo_html = ""
base_dir = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
logo_path = base_dir / "로고.png"
if logo_path.exists():
    with open(logo_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode()
    logo_html = f'<img src="data:image/png;base64,{encoded_string}" style="height: 35px; margin-right: 15px; vertical-align: middle;">'


html_content = f"""
<div class="container" style="font-family: 'Malgun Gothic', '맑은 고딕', sans-serif; background-color: #ffffff; border-top: 6px solid #1E3A8A; padding-top: 20px; color: #1F2937;">
    <div style="padding: 0 15px 15px 15px; display: flex; align-items: center;">
        <h1 style="color: #1E3A8A; font-size: 26px; font-weight: 900; margin: 0; letter-spacing: -1px; display: flex; align-items: center;">
            {logo_html}화천기공 NEWS CLIPPING
        </h1>
    </div>

    <div style="margin: 0 15px 10px 15px; background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px 16px;">
        <div style="display: flex; justify-content: space-between; align-items: center; font-weight: 600; color: #475569; font-size: 14px;">
            <span>발행일: {date_string}</span>
            <span>{html.escape(weather_info)}</span>
        </div>
    </div>

    <div style="margin: 0 15px 25px 15px; background-color: #ffffff; border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px 16px; display: flex; align-items: center; box-shadow: 0 1px 2px rgba(0,0,0,0.02); overflow-x: auto;">
        <div style="color: #1E3A8A; font-weight: 900; font-size: 14px; border-right: 2px solid #E2E8F0; padding-right: 15px; margin-right: 15px; white-space: nowrap; flex-shrink: 0;">주요 전시회 일정</div>
        <div style="flex: 1; display: flex; flex-wrap: nowrap; gap: 15px; justify-content: space-between; min-width: max-content;">
            {exhib_html}
        </div>
    </div>

    <div style="display: flex; flex-direction: column; gap: 25px; padding: 0 15px;">
        <div style="padding: 24px; background-color: #ffffff; border: 1px solid #E2E8F0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
            {render_news_list("기계 · 공작기계 동향", news['machinery'])}
            {render_news_list("신소재 · 부품 동향", news['materials'])}
            {render_news_list("반도체 장비 및 산업", news['semi'])}
            {render_news_list("산업용 로봇 · 자동화", news['robotics'])}
        </div>

        <div style="padding: 24px; background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
            <h3 style="font-size: 16px; color: #1E3A8A; border-bottom: 2px solid #1E3A8A; padding-bottom: 8px; margin-top:0; margin-bottom: 20px;">주요 지표 및 증시 현황</h3>
            <div style="display: flex; flex-wrap: wrap; gap: 15px; justify-content: space-between;">
                {render_table("주요 환율", fx_rows, "FX")}
                {render_table("국내 시가총액 TOP10", domestic_rows, "KRW", "Npay 증권 시가총액 랭킹 조회 실패")}
                {render_table("국내 거래량 급증 TOP10", volume_surge_rows, "KRW", "Npay 증권 거래량 급증 랭킹 조회 실패")}
                {render_table("미국 거래량 상위 TOP10", usa_volume_rows, "USD", "Npay 증권 미국 랭킹 조회 실패")}
            </div>
            <div style="font-size:11px; color:#94A3B8; margin-top:10px; text-align:right;">
                주식 랭킹은 실행 시점의 공개 시장 데이터를 새로 조회하며, 캐시는 최대 5분간 유지됩니다.
            </div>
        </div>
    </div>
</div>
<div style="margin-bottom: 50px;"></div>
"""

st.html(html_content)
