import streamlit as st
import pandas as pd
import yfinance as yf
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime
import concurrent.futures
import difflib
import re
import base64
import os

st.set_page_config(page_title="화천기공 NEWS CLIPPING", layout="wide")

st.markdown("""
<style>
.block-container { padding-top: 1rem; padding-bottom: 2rem; max-width: 1400px; }
header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=1800)
def get_weather():
    try:
        url = "https://search.naver.com/search.naver?query=광주+날씨"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")
        temp = soup.select_one(".temperature_text > strong").text.replace("현재 온도", "").strip()
        desc = soup.select_one(".weather_main").text.strip()
        dust_items = soup.select(".today_chart_list .txt")
        dust = dust_items[0].text.strip() if dust_items else "보통"
        return f"광주 날씨: {temp} ({desc}) | 미세먼지: {dust}"
    except:
        return "광주 날씨: 정보를 불러올 수 없습니다."

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
                return ticker, curr, pct
        if len(hist) >= 2:
            current = hist['Close'].iloc[-1]
            prev = hist['Close'].iloc[-2]
            if is_jpy:
                current *= 100
                prev *= 100
            pct = ((current - prev) / prev) * 100
            return ticker, current, pct
        return ticker, 0.0, 0.0
    except:
        return ticker, 0.0, 0.0

@st.cache_data(ttl=600)
def get_all_financial_data():
    tickers = {
        "FX": [("KRW=X", "미국 달러(USD)"), ("EURKRW=X", "유럽 유로(EUR)"), ("JPYKRW=X", "일본 엔(100)"), ("CNYKRW=X", "중국 위안(CNY)")],
        "Domestic": [("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"), ("373220.KS", "LG엔솔"), ("207940.KS", "삼성바이오로직스"), ("005380.KS", "현대차"), ("000270.KS", "기아"), ("068270.KS", "셀트리온"), ("105560.KS", "KB금융"), ("005490.KS", "POSCO홀딩스"), ("035420.KS", "NAVER")],
        "Trending": [("035720.KS", "카카오"), ("086520.KS", "에코프로"), ("196170.KS", "알테오젠"), ("028300.KS", "HLB"), ("034020.KS", "두산에너빌리티"), ("042700.KS", "한미반도체"), ("003230.KS", "삼양식품"), ("352820.KS", "하이브"), ("259960.KS", "크래프톤"), ("011200.KS", "에이치엠엠(HMM)")],
        "Foreign": [("AAPL", "애플"), ("MSFT", "마이크로소프트"), ("NVDA", "엔비디아"), ("TSLA", "테슬라"), ("GOOGL", "구글"), ("AMZN", "아마존"), ("META", "메타"), ("TSM", "TSMC"), ("AVGO", "브로드컴"), ("WMT", "월마트")]
    }
    all_symbols = []
    for category, items in tickers.items():
        for sym, name in items:
            all_symbols.append((sym, sym == "JPYKRW=X"))
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_single_ticker, sym, is_jpy): sym for sym, is_jpy in all_symbols}
        for future in concurrent.futures.as_completed(futures):
            sym, price, pct = future.result()
            results[sym] = {"price": price, "pct": pct}
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

def fetch_google_rss(query, limit=20):
    query_with_time = f"{query} when:1d"
    safe_query = urllib.parse.quote(query_with_time)
    url = f"https://news.google.com/rss/search?q={safe_query}&hl=ko&gl=KR&ceid=KR:ko"
    
    blacklist = [
        '주요활동', '다아라', '인사말', '회원사', '조사통계', '협회소개', '직거래', 
        '기계장터', '전시관', '오시는길', '그래픽뉴스', '문화 속 산업이야기', 
        '비철금속 시황', '전체뉴스', '게시판', '블로그', 'blog', '포스트', '티스토리'
    ]
    
    try:
        res = requests.get(url)
        soup = BeautifulSoup(res.content, "xml")
        news_list = []
        items = soup.find_all("item")
        
        for item in items:
            raw_title = item.title.text
            
            if any(b.lower() in raw_title.lower() for b in blacklist):
                continue
                
            if " - " in raw_title:
                clean_title = raw_title.rsplit(" - ", 1)[0].strip()
            else:
                clean_title = raw_title.strip()
                
            if any(bio_word in clean_title for bio_word in ['바이오', '신약', '제약', '임상', '식약처']):
                if not any(machinery_word in clean_title for machinery_word in ['기계', '장비', '가공', '의료기기', '부품', '제조', '로봇']):
                    continue

            source = item.source.text if item.source else "주요매체"
            
            if '블로그' in source or 'blog' in source.lower():
                continue
                
            if len(clean_title) < 10 or clean_title.lower() == source.lower():
                continue
                
            link = item.link.text
            news_list.append({"title": clean_title, "link": link, "source": source})
        return news_list
    except:
        return []

global_seen_titles = []

@st.cache_data(ttl=3600)
def get_hybrid_news(general_query, specialized_query, target_limit=10):
    specialized_news = fetch_google_rss(specialized_query, 20)
    general_news = fetch_google_rss(general_query, 20)
    
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
        combined_news.append({"title": "최근 1일 이내 관련 적합 뉴스가 없습니다.", "link": "#", "source": "알림"})
        
    return combined_news

now = datetime.now()
date_string = f"{now.strftime('%Y년 %m월 %d일')} 조간"

def f_pct(pct):
    if pct > 0: return f"<span style='color: #DC2626; font-weight: bold;'>▲ {pct:.2f}%</span>"
    elif pct < 0: return f"<span style='color: #2563EB; font-weight: bold;'>▼ {abs(pct):.2f}%</span>"
    else: return f"<span style='color: #4B5563;'>- 0.00%</span>"

with st.spinner("최종 레이아웃에 맞추어 데이터를 렌더링 중입니다..."):
    weather_info = get_weather()
    
    q_machinery_gen = "\"공작기계\" OR \"머시닝센터\" OR \"선반\" OR \"밀링\""
    q_machinery_spec = f"({q_machinery_gen}) (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com)"
    
    q_materials_gen = "\"산업용 부품\" OR \"절삭공구\" OR \"스핀들\" OR \"초정밀 가공\" OR \"의료기기 부품\""
    q_materials_spec = f"({q_materials_gen}) (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com)"
    
    q_semi_gen = "\"반도체 장비\" OR \"노광장비\" OR \"패키징 장비\""
    q_semi_spec = f"({q_semi_gen}) (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com)"
    
    q_robotics_gen = "\"산업용 로봇\" OR \"협동로봇\" OR \"공장자동화\""
    q_robotics_spec = f"({q_robotics_gen}) (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com)"

    news_machinery = get_hybrid_news(q_machinery_gen, q_machinery_spec, 10)
    news_materials = get_hybrid_news(q_materials_gen, q_materials_spec, 10)
    news_semi = get_hybrid_news(q_semi_gen, q_semi_spec, 10)
    news_robotics = get_hybrid_news(q_robotics_gen, q_robotics_spec, 10)
    
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
    target = datetime.strptime(ex["date"], "%Y.%m.%d")
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
    
    exhib_html += f"<div style='text-align: center; font-size: 13px; color: #374151;'><span style='font-weight: bold;'>{ex['country']} {ex['name']}</span> <span style='color: #6B7280; font-size: 12px; margin-left: 4px;'>({ex['date'][2:]})</span> <span style='color: {color}; font-weight: bold; margin-left: 4px;'>[{dday_str}]</span></div>"

def render_table(title, category_key, currency="KRW"):
    html = f"<div style='background-color: #ffffff; border: 1px solid #E5E7EB; border-radius: 8px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);'><h4 style='font-size: 14px; color: #1E3A8A; margin: 0 0 10px 0; border-bottom: 2px solid #1E3A8A; padding-bottom: 6px; text-align: center;'>{title}</h4><table style='width: 100%; font-size: 12px; border-collapse: collapse; text-align: right;'>"
    for sym, name in tickers_dict[category_key]:
        price = fin_data[sym]['price']
        pct = fin_data[sym]['pct']
        if price == 0.0:
            continue
        if currency == "USD":
            price_str = f"${price:,.2f}"
        elif currency == "FX":
            price_str = f"{price:,.2f}원"
        else:
            price_str = f"{price:,.0f}원"
        
        html += f"<tr style='border-bottom: 1px solid #F3F4F6;'><td style='text-align: left; padding: 8px 0; font-weight: 600; color: #374151; word-break: keep-all;'>{name}</td><td style='padding: 8px 15px 8px 0; white-space: nowrap;'>{price_str}</td><td style='padding: 8px 0; white-space: nowrap;'>{f_pct(pct)}</td></tr>"
    html += "</table></div>"
    return html

def render_news_list(title, news_list):
    html = f"<div style='margin-bottom: 25px;'><h3 style='font-size: 16px; color: #1E3A8A; border-left: 4px solid #1E3A8A; padding-left: 10px; margin-top:0; margin-bottom: 12px;'>{title}</h3><ul style='list-style: none; padding: 0; margin: 0; font-size: 14px; line-height: 1.8;'>"
    for n in news_list:
        html += f"<li style='margin-bottom: 6px;'><a href='{n['link']}' style='color: #1F2937; text-decoration: none;' target='_blank'>- {n['title']}</a> <span style='color:#9CA3AF; font-size:12px; font-weight: 600;'>[{n['source']}]</span></li>"
    html += "</ul></div>"
    return html

# 로컬 로고 파일을 Base64로 인코딩하여 HTML에 삽입
logo_html = ""
if os.path.exists("로고.png"):
    with open("로고.png", "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode()
    logo_html = f'<img src="data:image/png;base64,{encoded_string}" style="height: 35px; margin-right: 15px; vertical-align: middle;">'

html_content = f"""<div style="font-family: 'Malgun Gothic', '맑은 고딕', sans-serif; background-color: #ffffff; border-top: 6px solid #1E3A8A; padding-top: 20px; color: #1F2937;">
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

<div style="margin: 0 15px 25px 15px; background-color: #ffffff; border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px 16px; display: flex; align-items: center; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">
<div style="color: #1E3A8A; font-weight: 900; font-size: 14px; border-right: 2px solid #E2E8F0; padding-right: 15px; margin-right: 15px; white-space: nowrap;">주요 전시회 일정</div>
<div style="flex: 1; display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px;">
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
<div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px;">
{render_table("주요 환율", "FX", "FX")}
{render_table("국내 시총 Top10", "Domestic", "KRW")}
{render_table("시장 핫이슈", "Trending", "KRW")}
{render_table("해외 테크 대장주", "Foreign", "USD")}
</div>
</div>
</div>
</div>
<div style="margin-bottom: 50px;"></div>"""

st.html(html_content)