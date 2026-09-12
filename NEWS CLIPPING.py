import streamlit as st
import pandas as pd
import yfinance as yf
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import concurrent.futures
import re
import base64
import os

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

@st.cache_data(ttl=1800)
def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=35.1547&longitude=126.9156&current_weather=true"
        res = requests.get(url, timeout=5)
        data = res.json()
        
        if "current_weather" in data:
            temp = data["current_weather"]["temperature"]
            code = data["current_weather"]["weathercode"]
            
            if code == 0: wf = "맑음"
            elif code in [1, 2, 3]: wf = "구름많음/흐림"
            elif code in [45, 48]: wf = "안개"
            elif code in [51, 53, 55, 61, 63, 65, 80, 81, 82]: wf = "비"
            elif code in [71, 73, 75, 85, 86]: wf = "눈"
            elif code in [95, 96, 99]: wf = "천둥번개"
            else: wf = "알수없음"
            
            return f"광주 날씨: {temp}℃ ({wf})"
            
        return "광주 날씨: 데이터 파싱 실패"
    except:
        return "광주 날씨 통신 오류"
        
def fetch_single_ticker(ticker, is_jpy=False):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1mo").dropna()
        if len(hist) < 2 and ticker == "CNYKRW=X":
            krw = yf.Ticker("KRW=X").history(period="5d").dropna()
            cny = yf.Ticker("CNY=X").history(period="5d").dropna()
            if len(krw) >= 2 and len(cny) >= 2:
                curr = krw['Close'].iloc[-1] / cny['Close'].iloc[-1]
                prev = krw['Close'].iloc[-2] / cny['Close'].iloc[-2]
                pct = ((curr - prev) / prev) * 100
                return ticker, curr, pct, 0
        if len(hist) >= 2:
            current = hist['Close'].iloc[-1]
            prev = hist['Close'].iloc[-2]
            vol = hist['Volume'].iloc[-1] if 'Volume' in hist.columns else 0
            if is_jpy:
                current *= 100
                prev *= 100
            pct = ((current - prev) / prev) * 100
            return ticker, current, pct, vol
        return ticker, 0.0, 0.0, 0
    except:
        return ticker, 0.0, 0.0, 0

@st.cache_data(ttl=600)
def get_all_financial_data():
    tickers = {}
    
    fx_list = [("KRW=X", "미국 달러(USD)"), ("EURKRW=X", "유럽 유로(EUR)"), ("JPYKRW=X", "일본 엔(100)"), ("CNYKRW=X", "중국 위안(CNY)")]
    tickers["FX"] = fx_list

    dom_candidates = [
        ("005930.KS", "삼성전자", 5969782550), ("000660.KS", "SK하이닉스", 728002365),
        ("373220.KS", "LG엔솔", 234000000), ("207940.KS", "삼성바이오로직스", 71174000),
        ("005380.KS", "현대차", 208000000), ("000270.KS", "기아", 398000000),
        ("068270.KS", "셀트리온", 218000000), ("105560.KS", "KB금융", 400000000),
        ("005490.KS", "POSCO홀딩스", 84000000), ("035420.KS", "NAVER", 162000000)
    ]

    trend_candidates = [
        ("035720.KS", "카카오"), ("086520.KQ", "에코프로"), ("196170.KQ", "알테오젠"),
        ("028300.KQ", "HLB"), ("034020.KS", "두산에너빌리티"), ("042700.KS", "한미반도체"),
        ("003230.KS", "삼양식품"), ("352820.KS", "하이브"), ("259960.KS", "크래프톤"),
        ("011200.KS", "HMM")
    ]

    tech_candidates = [
        ("AAPL", "애플"), ("MSFT", "마이크로소프트"), ("NVDA", "엔비디아"), ("GOOGL", "구글"),
        ("AMZN", "아마존"), ("META", "메타"), ("TSM", "TSMC"), ("AVGO", "브로드컴"),
        ("ASML", "ASML"), ("TSLA", "테슬라")
    ]

    all_symbols = [sym for sym, _ in fx_list] + [sym for sym, _, _ in dom_candidates] + \
                  [sym for sym, _ in trend_candidates] + [sym for sym, _ in tech_candidates]
    unique_symbols = list(set(all_symbols))

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_single_ticker, sym, sym=="JPYKRW=X"): sym for sym in unique_symbols}
        for future in concurrent.futures.as_completed(futures):
            sym, price, pct, vol = future.result()
            results[sym] = {"price": price, "pct": pct, "vol": vol}

    dom_sort = []
    for sym, name, shares in dom_candidates:
        price = results[sym]["price"]
        mcap = price * shares
        dom_sort.append((mcap, sym, name))
    dom_sort.sort(key=lambda x: x[0], reverse=True)
    tickers["Domestic"] = [(sym, name) for _, sym, name in dom_sort[:10]]

    trend_sort = []
    for sym, name in trend_candidates:
        vol = results[sym]["vol"]
        trend_sort.append((vol, sym, name))
    trend_sort.sort(key=lambda x: x[0], reverse=True)
    tickers["Trending"] = [(sym, name) for _, sym, name in trend_sort[:10]]

    tech_sort = []
    for sym, name in tech_candidates:
        vol = results[sym]["vol"]
        tech_sort.append((vol, sym, name))
    tech_sort.sort(key=lambda x: x[0], reverse=True)
    tickers["Foreign"] = [(sym, name) for _, sym, name in tech_sort[:10]]
    
    return tickers, results

def get_bigrams(text):
    clean_text = re.sub(r'[^\w\s]', '', text).replace(" ", "")
    if len(clean_text) < 2:
        return set(clean_text)
    return set([clean_text[i:i+2] for i in range(len(clean_text)-1)])

def is_duplicate(title, seen_list, threshold=0.35):
    bg1 = get_bigrams(title)
    if not bg1: return False
    for seen_title in seen_list:
        bg2 = get_bigrams(seen_title)
        if not bg2: continue
        intersection = bg1.intersection(bg2)
        overlap_ratio = len(intersection) / min(len(bg1), len(bg2))
        if overlap_ratio > threshold:
            return True
    return False

# =========================================================================
# [핵심 수정] strict_keywords 파라미터 추가
# =========================================================================
def fetch_google_rss(query, strict_keywords=None, limit=20):
    query_with_time = f"{query} when:1d"
    safe_query = urllib.parse.quote(query_with_time)
    url = f"https://news.google.com/rss/search?q={safe_query}&hl=ko&gl=KR&ceid=KR:ko"
    
    blacklist = [
        '주요활동', '다아라', '인사말', '회원사', '조사통계', '협회소개', '직거래', 
        '기계장터', '전시관', '오시는길', '그래픽뉴스', '블로그', 'blog', '포스트', '티스토리',
        '가구', '인테리어', '한지', '장판', '창호', '조명', '일회용', '종이', '공방', 
        '고등학교', '특성화고', '마이스터고', '신입생', '입학', '구인', '구직', '채용', '알바',
        '아이돌', '연예', '앨범', '가수', '배우', '방송', '뮤직', '콘서트', '드라마', '영화',
        '야구', '축구', '농구', '스포츠', '호투', '홈런', '양키스', '차관', '장관', '교육부'
    ]
    
    try:
        res = requests.get(url, timeout=10)
        soup = BeautifulSoup(res.content, "xml")
        news_list = []
        items = soup.find_all("item")
        
        now_utc = datetime.now(timezone.utc)
        
        for item in items:
            # 1. 24시간 절대 방어막
            pub_date_tag = item.find("pubDate")
            if not pub_date_tag:
                continue
            try:
                pub_dt = parsedate_to_datetime(pub_date_tag.text)
                pub_dt_utc = pub_dt.astimezone(timezone.utc)
                diff_seconds = (now_utc - pub_dt_utc).total_seconds()
                if diff_seconds > 86400 or diff_seconds < 0:
                    continue
            except:
                continue

            raw_title = item.title.text
            
            # 2. 블랙리스트 방어막
            if any(b.lower() in raw_title.lower() for b in blacklist):
                continue
            
            # 3. [초강력 방어막] 타이틀 스나이퍼 (화이트리스트)
            # -> 기사 "제목"에 필수 키워드가 없으면 얄짤없이 삭제
            if strict_keywords:
                title_no_space = raw_title.replace(" ", "").lower()
                # strict_keywords 중 하나라도 제목에 포함되어 있는지 검사
                if not any(k.lower() in title_no_space for k in strict_keywords):
                    continue
                
            if " - " in raw_title:
                clean_title = raw_title.rsplit(" - ", 1)[0].strip()
            else:
                clean_title = raw_title.strip()

            source = item.source.text if item.source else "주요매체"
            link = item.link.text
            news_list.append({"title": clean_title, "link": link, "source": source})
            
        return news_list
    except:
        return []

global_seen_titles = []

@st.cache_data(ttl=3600)
def get_hybrid_news(general_query, specialized_query, strict_keys, target_limit=10):
    # 일반 뉴스 검색 시에만 strict_keys(타이틀 스나이퍼)를 빡세게 적용합니다.
    general_news = fetch_google_rss(general_query, strict_keywords=strict_keys)
    # 전문 매체(산업일보 등)는 비교적 안전하므로 일반 필터만 적용합니다.
    specialized_news = fetch_google_rss(specialized_query)
    
    combined_news = []
    max_len = max(len(specialized_news), len(general_news))
    
    for i in range(max_len):
        if len(combined_news) >= target_limit:
            break
        if i < len(specialized_news):
            n = specialized_news[i]
            if not is_duplicate(n['title'], global_seen_titles):
                combined_news.append(n)
                global_seen_titles.append(n['title'])
        if len(combined_news) >= target_limit:
            break
        if i < len(general_news):
            n = general_news[i]
            if not is_duplicate(n['title'], global_seen_titles):
                combined_news.append(n)
                global_seen_titles.append(n['title'])
                
    if not combined_news:
        combined_news.append({"title": "최근 24시간 이내 발행된 관련 뉴스가 없습니다.", "link": "#", "source": "알림"})
        
    return combined_news

KST = timezone(timedelta(hours=9))
now = datetime.now(KST)
date_string = f"{now.strftime('%Y년 %m월 %d일')} 조간"

def f_pct(pct):
    if pct > 0: return f"<span style='color: #DC2626; font-weight: bold;'>▲ {pct:.2f}%</span>"
    elif pct < 0: return f"<span style='color: #2563EB; font-weight: bold;'>▼ {abs(pct):.2f}%</span>"
    else: return f"<span style='color: #4B5563;'>- 0.00%</span>"

with st.spinner("최종 레이아웃에 맞추어 데이터를 렌더링 중입니다..."):
    weather_info = get_weather()
    
    neg = "-카지노 -도박 -성범죄 -유출 -몰카 -가구 -인테리어 -고등학교 -신입생 -구인 -알바 -아이돌 -연예 -스포츠 -야구 -축구"
    
    # ---------------------------------------------------------------------
    # 카테고리별 필수 포함 단어 (이 단어들이 기사 제목에 없으면 버림)
    k_machinery = ['기계', '머시닝', '선반', '밀링', 'cnc', '화천', '두산', '스맥', '위아', '절삭', '금형', '가공', '제조', '장비', '설비', '산업']
    k_materials = ['부품', '공구', '스핀들', '정밀', '베어링', '모터', '엔진', '소재', '합금', '스크류', '가이드', '센서', '철강', '금속']
    k_semi = ['반도체', '노광', '패키징', '웨이퍼', 'euv', 'tsmc', 'asml', '디스플레이', '식각', '증착', '팹리스', '파운드리', 'hbm', 'd램']
    k_robotics = ['로봇', '자동화', '팩토리', 'agv', 'amr', '무인', '공장', 'ai', '인공지능', '스마트']
    # ---------------------------------------------------------------------

    q_machinery_gen = f'("공작기계" OR "머시닝센터" OR "선반" OR "밀링") {neg}'
    q_machinery_spec = f'("공작기계" OR "머시닝센터" OR "선반" OR "밀링") (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com) {neg}'
    news_machinery = get_hybrid_news(q_machinery_gen, q_machinery_spec, strict_keys=k_machinery)
    
    q_materials_gen = f'("산업용 부품" OR "절삭공구" OR "스핀들" OR "초정밀 가공" OR "의료기기 부품") {neg}'
    q_materials_spec = f'("산업용 부품" OR "절삭공구" OR "스핀들" OR "초정밀 가공" OR "의료기기 부품") (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com) {neg}'
    news_materials = get_hybrid_news(q_materials_gen, q_materials_spec, strict_keys=k_materials)
    
    q_semi_gen = f'("반도체 장비" OR "노광장비" OR "패키징 장비") {neg}'
    q_semi_spec = f'("반도체 장비" OR "노광장비" OR "패키징 장비") (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com) {neg}'
    news_semi = get_hybrid_news(q_semi_gen, q_semi_spec, strict_keys=k_semi)
    
    q_robotics_gen = f'("산업용 로봇" OR "협동로봇" OR "공장자동화") {neg}'
    q_robotics_spec = f'("산업용 로봇" OR "협동로봇" OR "공장자동화") (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com) {neg}'
    news_robotics = get_hybrid_news(q_robotics_gen, q_robotics_spec, strict_keys=k_robotics)
    
    tickers_dict, fin_data = get_all_financial_data()

exhib_data = [
    {"country": "한국", "name": "SIMTOS", "date": "2028.04.03"},
    {"country": "일본", "name": "JIMTOF", "date": "2026.10.26"},
    {"country": "중국", "name": "CIMT", "date": "2027.04.19"},
    {"country": "미국", "name": "IMTS", "date": "2026.09.14"},
    {"country": "독일", "name": "EMO", "date": "2027.09.20"}
]

exhib_html = ""
for ex in exhib_data:
    target = datetime.strptime(ex["date"], "%Y.%m.%d").replace(tzinfo=KST)
    days = (target.date() - now.date()).days
    if days > 0:
        dday_str = f"D-{days}"
        color = "#DC2626" if days <= 30 else "#005CAB"
    elif days == 0:
        dday_str = "D-Day"
        color = "#DC2626"
    else:
        dday_str = "종료"
        color = "#9CA3AF"
    
    exhib_html += f"<div style='flex: 0 0 auto; text-align: center; font-size: 13px; color: #374151;'><span style='font-weight: bold;'>{ex['country']} {ex['name']}</span> <span style='color: #6B7280; font-size: 12px; margin-left: 4px;'>({ex['date'][2:]})</span> <span style='color: {color}; font-weight: bold; margin-left: 4px;'>[{dday_str}]</span></div>"

def render_table(title, category_key, currency="KRW"):
    html = f"<div style='flex: 1 1 230px; min-width: 230px; background-color: #ffffff; border: 1px solid #E5E7EB; border-radius: 8px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); overflow: hidden;'><h4 style='font-size: 14px; color: #1E3A8A; margin: 0 0 10px 0; border-bottom: 2px solid #1E3A8A; padding-bottom: 6px; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>{title}</h4><table style='width: 100%; font-size: 12px; border-collapse: collapse; text-align: right; table-layout: fixed;'>"
    for sym, name in tickers_dict[category_key]:
        price = fin_data.get(sym, {}).get('price', 0.0)
        pct = fin_data.get(sym, {}).get('pct', 0.0)
        if price == 0.0:
            continue
        if currency == "USD":
            price_str = f"${price:,.2f}"
        elif currency == "FX":
            price_str = f"{price:,.2f}원"
        else:
            price_str = f"{price:,.0f}원"
        
        html += f"<tr style='border-bottom: 1px solid #F3F4F6;'><td style='text-align: left; padding: 8px 0; font-weight: 600; color: #374151; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>{name}</td><td style='padding: 8px 15px 8px 0; white-space: nowrap;'>{price_str}</td><td style='padding: 8px 0; white-space: nowrap;'>{f_pct(pct)}</td></tr>"
    html += "</table></div>"
    return html

def render_news_list(title, news_list):
    html = f"<div style='margin-bottom: 25px; overflow: hidden;'><h3 style='font-size: 16px; color: #1E3A8A; border-left: 4px solid #1E3A8A; padding-left: 10px; margin-top:0; margin-bottom: 12px; white-space: nowrap;'>{title}</h3><ul style='list-style: none; padding: 0; margin: 0; font-size: 14px; line-height: 1.8;'>"
    for n in news_list:
        html += f"<li style='margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'><a href='{n['link']}' style='color: #1F2937; text-decoration: none;' target='_blank'>- {n['title']}</a> <span style='color:#9CA3AF; font-size:12px; font-weight: 600;'>[{n['source']}]</span></li>"
    html += "</ul></div>"
    return html

logo_html = ""
if os.path.exists("로고.png"):
    with open("로고.png", "rb") as image_file:
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
            <span>{weather_info}</span>
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
            {render_news_list("기계 · 공작기계 동향", news_machinery)}
            {render_news_list("신소재 · 부품 동향", news_materials)}
            {render_news_list("반도체 장비 및 산업", news_semi)}
            {render_news_list("산업용 로봇 · 자동화", news_robotics)}
        </div>

        <div style="padding: 24px; background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
            <h3 style="font-size: 16px; color: #1E3A8A; border-bottom: 2px solid #1E3A8A; padding-bottom: 8px; margin-top:0; margin-bottom: 20px;">주요 지표 및 증시 현황</h3>
            <div style="display: flex; flex-wrap: wrap; gap: 15px; justify-content: space-between;">
                {render_table("주요 환율", "FX", "FX")}
                {render_table("국내 시총 Top10", "Domestic", "KRW")}
                {render_table("시장 핫이슈 (거래량 급증)", "Trending", "KRW")}
                {render_table("해외 테크 거래량 Top10", "Foreign", "USD")}
            </div>
        </div>
    </div>
</div>
<div style="margin-bottom: 50px;"></div>
"""

st.html(html_content)
