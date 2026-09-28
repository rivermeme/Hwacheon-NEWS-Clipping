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

@st.cache_data(ttl=1800)
def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=35.1547&longitude=126.9156&current_weather=true"
        res = requests.get(url, timeout=5)
        res.raise_for_status()
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
    except Exception:
        return "광주 날씨 통신 오류"
        
def fetch_single_ticker(ticker, is_jpy=False):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1mo").dropna()

        # CNY/KRW가 직접 조회되지 않을 때 USD/KRW ÷ USD/CNY로 교차환율 계산
        if len(hist) < 2 and ticker == "CNYKRW=X":
            krw = yf.Ticker("KRW=X").history(period="5d").dropna()
            cny = yf.Ticker("CNY=X").history(period="5d").dropna()
            if len(krw) >= 2 and len(cny) >= 2:
                curr = krw['Close'].iloc[-1] / cny['Close'].iloc[-1]
                prev = krw['Close'].iloc[-2] / cny['Close'].iloc[-2]
                pct = ((curr - prev) / prev) * 100
                return ticker, curr, pct, 0, 0.0

        if len(hist) >= 2:
            current = hist['Close'].iloc[-1]
            prev = hist['Close'].iloc[-2]
            vol = float(hist['Volume'].iloc[-1]) if 'Volume' in hist.columns else 0.0

            # '거래량 급증'은 절대 거래량이 아니라 최근 평균 대비 배수로 판단
            vol_ratio = 0.0
            if 'Volume' in hist.columns and len(hist) >= 6:
                prev_volumes = hist['Volume'].iloc[:-1].tail(20)
                avg_vol = float(prev_volumes.mean()) if len(prev_volumes) else 0.0
                if avg_vol > 0:
                    vol_ratio = vol / avg_vol

            if is_jpy:
                current *= 100
                prev *= 100

            pct = ((current - prev) / prev) * 100
            return ticker, current, pct, vol, vol_ratio

        return ticker, 0.0, 0.0, 0.0, 0.0
    except Exception:
        return ticker, 0.0, 0.0, 0.0, 0.0

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
        ("005490.KS", "POSCO홀딩스", 84000000), ("035420.KS", "NAVER", 162000000),
        ("055550.KS", "신한지주", 508000000), ("051910.KS", "LG화학", 70592343),
        ("028260.KS", "삼성물산", 185592850), ("006400.KS", "삼성SDI", 68764530),
        ("012330.KS", "현대모비스", 92837302)
    ]

    trend_candidates = [
        ("035720.KS", "카카오"), ("086520.KQ", "에코프로"), ("196170.KQ", "알테오젠"),
        ("028300.KQ", "HLB"), ("034020.KS", "두산에너빌리티"), ("042700.KS", "한미반도체"),
        ("003230.KS", "삼양식품"), ("352820.KS", "하이브"), ("259960.KS", "크래프톤"),
        ("011200.KS", "HMM"), ("001570.KS", "금양"), ("022100.KQ", "포스코DX"),
        ("010140.KS", "삼성중공업"), ("041510.KQ", "에스엠"), ("247540.KQ", "에코프로비엠")
    ]

    tech_candidates = [
        ("AAPL", "애플"), ("MSFT", "마이크로소프트"), ("NVDA", "엔비디아"), ("GOOGL", "구글"),
        ("AMZN", "아마존"), ("META", "메타"), ("TSM", "TSMC"), ("AVGO", "브로드컴"),
        ("ASML", "ASML"), ("TSLA", "테슬라"), ("AMD", "AMD"), ("QCOM", "퀄컴"), 
        ("NFLX", "넷플릭스"), ("INTC", "인텔"), ("ARM", "ARM")
    ]

    all_symbols = [sym for sym, _ in fx_list] + [sym for sym, _, _ in dom_candidates] + \
                  [sym for sym, _ in trend_candidates] + [sym for sym, _ in tech_candidates]
    unique_symbols = list(set(all_symbols))

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_single_ticker, sym, sym=="JPYKRW=X"): sym for sym in unique_symbols}
        for future in concurrent.futures.as_completed(futures):
            sym, price, pct, vol, vol_ratio = future.result()
            results[sym] = {"price": price, "pct": pct, "vol": vol, "vol_ratio": vol_ratio}

    dom_sort = []
    for sym, name, shares in dom_candidates:
        price = results[sym]["price"]
        if price > 0:
            mcap = price * shares
            dom_sort.append((mcap, sym, name))
    dom_sort.sort(key=lambda x: x[0], reverse=True)
    tickers["Domestic"] = [(sym, name) for _, sym, name in dom_sort[:10]]

    trend_sort = []
    for sym, name in trend_candidates:
        price = results[sym]["price"]
        vol_ratio = results[sym]["vol_ratio"]
        if price > 0:
            trend_sort.append((vol_ratio, sym, name))
    trend_sort.sort(key=lambda x: x[0], reverse=True)
    tickers["Trending"] = [(sym, name) for _, sym, name in trend_sort[:10]]

    tech_sort = []
    for sym, name in tech_candidates:
        price = results[sym]["price"]
        vol = results[sym]["vol"]
        if price > 0:
            tech_sort.append((vol, sym, name))
    tech_sort.sort(key=lambda x: x[0], reverse=True)
    tickers["Foreign"] = [(sym, name) for _, sym, name in tech_sort[:10]]
    
    return tickers, results

def get_bigrams(text):
    clean_text = re.sub(r'[^\w\s]', '', text).replace(" ", "")
    if len(clean_text) < 2:
        return set(clean_text)
    return set([clean_text[i:i+2] for i in range(len(clean_text)-1)])

def is_duplicate(title, seen_list, threshold=0.50):
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

def contains_strict_keyword(title, keywords):
    """짧은 영문 키워드(AI 등)의 부분문자열 오탐을 줄이면서 한글 키워드는 자연스럽게 포함 검색."""
    normalized = re.sub(r'\[.*?\]', '', title).lower()
    compact = normalized.replace(" ", "")

    for keyword in keywords:
        kw = keyword.lower().strip()
        if not kw:
            continue
        if re.fullmatch(r'[a-z0-9+#.-]+', kw):
            if re.search(rf'(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])', normalized):
                return True
        elif kw in compact:
            return True
    return False

@st.cache_data(ttl=1800)
def fetch_google_rss(query, strict_keywords=None, limit=20):
    query_with_time = f"{query} when:1d"
    safe_query = urllib.parse.quote(query_with_time)
    url = f"https://news.google.com/rss/search?q={safe_query}&hl=ko&gl=KR&ceid=KR:ko"
    
    # [강력 추가] 도박, 카지노 스팸 관련 키워드 파이썬 내부 블랙리스트 
    blacklist = [
        '주요활동', '다아라', '인사말', '회원사', '조사통계', '협회소개', '직거래', 
        '기계장터', '전시관', '오시는길', '그래픽뉴스', '블로그', 'blog', '포스트', '티스토리',
        '가구', '인테리어', '한지', '장판', '창호', '조명', '일회용', '종이', '공방', 
        '고등학교', '특성화고', '마이스터고', '신입생', '입학', '구인', '구직', '채용', '알바',
        '아이돌', '연예', '앨범', '가수', '배우', '방송', '뮤직', '콘서트', '드라마', '영화',
        '야구', '축구', '농구', '스포츠', '호투', '홈런', '양키스', '차관', '장관', '교육부',
        '환율', '금리', '코스피', '코스닥', '공모주', '시황', '증시', '유가', '달러', '특징주',
        '투자', '기관', '외국인', '순매수', '주간',
        '카지노', '바카라', '도박', '슬롯', '토토', '룰렛', '베팅', '배팅', '도메인', '꽁머니', '우회', '사이트'
    ]
    
    # [핵심 방어막] 공식 언론사 이름 검증 (여기에 없는 이름이면 무조건 찌라시 스팸 처리)
    valid_media_keywords = [
        '일보', '신문', '뉴스', '방송', '통신', '미디어', '경제', '저널', '비즈', 
        'tv', '투데이', '데일리', '타임즈', '헤럴드', '매거진', '네트워크', '인포맥스',
        '연합', '뉴시스', 'kbs', 'sbs', 'mbc', 'ytn', 'jtbc', 'mbn', '채널a', 
        'tv조선', 'ebs', '블로터', '지디넷', '테크m', '조선', '중앙', '동아', '매경', '한경',
        'reuters', 'bloomberg', 'bbc', 'cnn', 'zdnet', 'etnews', '전자신문', '산업일보'
    ]
    
    try:
        res = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        res.raise_for_status()
        soup = BeautifulSoup(res.content, "xml")
        news_list = []
        items = soup.find_all("item")
        now_utc = datetime.now(timezone.utc)
        
        for item in items:
            pub_date_tag = item.find("pubDate")
            if not pub_date_tag: continue
            try:
                pub_dt = parsedate_to_datetime(pub_date_tag.text)
                pub_dt_utc = pub_dt.astimezone(timezone.utc)
                diff_seconds = (now_utc - pub_dt_utc).total_seconds()
                if diff_seconds > 86400 or diff_seconds < 0:
                    continue
            except Exception:
                continue

            # 1. 언론사 출처 검증 (Histoire pour tous 같은 쓰레기 사이트 컷)
            source = item.source.text if item.source else "알수없음"
            is_valid_media = False
            for kw in valid_media_keywords:
                if kw.lower() in source.lower():
                    is_valid_media = True
                    break
            
            # 전문 매체(kidd.co.kr 등 site: 명령어)는 예외 통과
            if not is_valid_media and "site:" not in query:
                continue

            raw_title = item.title.text
            if " - " in raw_title:
                clean_title = raw_title.rsplit(" - ", 1)[0].strip()
            else:
                clean_title = raw_title.strip()
                
            # 2. 불건전(도박 등) 및 무관 단어 블랙리스트 검사
            if any(b.lower() in clean_title.lower() for b in blacklist):
                continue
            
            # 3. 필수 공작기계/산업 단어 포함 여부 검사
            if strict_keywords and not contains_strict_keyword(clean_title, strict_keywords):
                continue

            link = item.link.text
            news_list.append({"title": clean_title, "link": link, "source": source})
            if len(news_list) >= limit:
                break

        return news_list
    except Exception:
        return []

def get_hybrid_news(general_query, specialized_query, strict_keys, seen_titles, target_limit=10):
    """RSS 조회 결과를 섞고, 이번 렌더링 안에서 카테고리 간 중복까지 제거."""
    general_news = fetch_google_rss(general_query, strict_keywords=strict_keys)
    specialized_news = fetch_google_rss(specialized_query, strict_keywords=strict_keys)

    combined_news = []
    max_len = max(len(specialized_news), len(general_news))

    for i in range(max_len):
        if len(combined_news) >= target_limit:
            break

        if i < len(specialized_news):
            n = specialized_news[i]
            if not is_duplicate(n['title'], seen_titles):
                combined_news.append(n)
                seen_titles.append(n['title'])

        if len(combined_news) >= target_limit:
            break

        if i < len(general_news):
            n = general_news[i]
            if not is_duplicate(n['title'], seen_titles):
                combined_news.append(n)
                seen_titles.append(n['title'])

    if not combined_news:
        combined_news.append({"title": "최근 24시간 이내 발행된 관련 뉴스가 없습니다.", "link": "#", "source": "알림"})
    return combined_news


def get_verified_exhibition_dates():
    """
    전시 일정은 검색페이지 스크래핑을 하지 않는다.
    검색 결과의 다른 날짜(등록기간/기사일/타 행사일)를 잡아 D-day가 틀어지는 문제를 방지한다.
    아래 값은 2026-09-28 기준 공식/주최 측 일정으로 확인한 값이다.
    일정이 공식 변경되면 이 표만 갱신하면 된다.
    """
    events = [
        {"country": "한국", "name": "SIMTOS", "start": "2028.04.03", "end": "2028.04.07"},
        {"country": "일본", "name": "JIMTOF", "start": "2026.10.26", "end": "2026.10.31"},
        {"country": "중국", "name": "CIMT", "start": "2027.04.19", "end": "2027.04.24"},
        {"country": "미국", "name": "IMTS", "start": "2026.09.14", "end": "2026.09.19"},
        {"country": "이탈리아", "name": "EMO", "start": "2027.10.04", "end": "2027.10.08"},
    ]
    return events

KST = timezone(timedelta(hours=9))
now = datetime.now(KST)
date_string = f"{now.strftime('%Y년 %m월 %d일')} 조간"

def f_pct(pct):
    if pct > 0: return f"<span style='color: #DC2626; font-weight: bold;'>▲ {pct:.2f}%</span>"
    elif pct < 0: return f"<span style='color: #2563EB; font-weight: bold;'>▼ {abs(pct):.2f}%</span>"
    else: return f"<span style='color: #4B5563;'>- 0.00%</span>"

with st.spinner("최종 레이아웃에 맞추어 데이터를 렌더링 중입니다..."):
    weather_info = get_weather()
    
    # 카테고리 간 중복 뉴스 제거용 (캐시 밖에서 매 렌더링마다 새로 생성)
    seen_titles = []

    # [수정] 구글 검색 1차 차단에도 도박/카지노 단어 유지, '마작'은 절대 금지어에 넣지 않음
    neg = "-카지노 -도박 -성범죄 -유출 -몰카 -가구 -인테리어 -고등학교 -신입생 -구인 -알바 -아이돌 -연예 -스포츠 -환율 -금리 -코스피 -코스닥 -시황"
    
    # [수정] 필수 키워드에 '마작', '야마자키', 'mazak' 명시적으로 추가
    k_machinery = ['공작기계', '머시닝', '선반', '밀링', 'cnc', '화천기공', '디엔솔루션즈', '스맥', '현대위아', '마작', '야마자키', 'mazak', '절삭', '금형', '5축', '가공기', '레이저', '판금']
    k_materials = ['부품', '공구', '스핀들', '정밀', '베어링', '모터', '엔진', '소재', '합금', '스크류', '가이드', '센서', '철강', '금속', '엔드밀', '인서트']
    k_semi = ['반도체', '노광', '패키징', '웨이퍼', 'euv', 'tsmc', 'asml', '디스플레이', '식각', '증착', '팹리스', '파운드리', 'hbm', 'd램']
    k_robotics = ['로봇', '자동화', '팩토리', 'agv', 'amr', '무인', '공장', 'ai', '스마트팩토리', '협동로봇']

    q_machinery_gen = f'("공작기계" OR "머시닝센터" OR "선반" OR "밀링") {neg}'
    q_machinery_spec = f'("공작기계" OR "머시닝센터" OR "선반" OR "밀링") (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com) {neg}'
    news_machinery = get_hybrid_news(q_machinery_gen, q_machinery_spec, k_machinery, seen_titles)
    
    q_materials_gen = f'("산업용 부품" OR "절삭공구" OR "스핀들" OR "초정밀 가공" OR "의료기기 부품") {neg}'
    q_materials_spec = f'("산업용 부품" OR "절삭공구" OR "스핀들" OR "초정밀 가공" OR "의료기기 부품") (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com) {neg}'
    news_materials = get_hybrid_news(q_materials_gen, q_materials_spec, k_materials, seen_titles)
    
    q_semi_gen = f'("반도체 장비" OR "노광장비" OR "패키징 장비") {neg}'
    q_semi_spec = f'("반도체 장비" OR "노광장비" OR "패키징 장비") (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com) {neg}'
    news_semi = get_hybrid_news(q_semi_gen, q_semi_spec, k_semi, seen_titles)
    
    q_robotics_gen = f'("산업용 로봇" OR "협동로봇" OR "공장자동화") {neg}'
    q_robotics_spec = f'("산업용 로봇" OR "협동로봇" OR "공장자동화") (site:kidd.co.kr OR site:mtnews.net OR site:komma.org OR site:mmsonline.com) {neg}'
    news_robotics = get_hybrid_news(q_robotics_gen, q_robotics_spec, k_robotics, seen_titles)
    
    tickers_dict, fin_data = get_all_financial_data()

exhib_data = get_verified_exhibition_dates()
exhib_html = ""
today_date = now.date()

# 진행중/예정 전시회를 날짜순으로 먼저, 종료된 전시회는 뒤로 보낸다.
def exhibition_sort_key(ex):
    s_date = datetime.strptime(ex["start"], "%Y.%m.%d").date()
    e_date = datetime.strptime(ex["end"], "%Y.%m.%d").date()
    if e_date >= today_date:
        return (0, s_date.toordinal())
    return (1, -e_date.toordinal())

exhib_data.sort(key=exhibition_sort_key)

for ex in exhib_data:
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
    
    exhib_html += f"<div style='flex: 0 0 auto; text-align: center; font-size: 13px; color: #374151;'><span style='font-weight: bold;'>{ex['country']} {ex['name']}</span> <span style='color: #6B7280; font-size: 12px; margin-left: 4px;'>({start_str} ~ {end_str})</span> <span style='color: {color}; font-weight: bold; margin-left: 4px;'>[{dday_str}]</span></div>"

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
    safe_title = html.escape(title)
    rendered = f"<div style='margin-bottom: 25px; overflow: hidden;'><h3 style='font-size: 16px; color: #1E3A8A; border-left: 4px solid #1E3A8A; padding-left: 10px; margin-top:0; margin-bottom: 12px; white-space: nowrap;'>{safe_title}</h3><ul style='list-style: none; padding: 0; margin: 0; font-size: 14px; line-height: 1.8;'>"
    for n in news_list:
        safe_link = html.escape(n['link'], quote=True)
        safe_news_title = html.escape(n['title'])
        safe_source = html.escape(n['source'])
        rendered += f"<li style='margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'><a href='{safe_link}' style='color: #1F2937; text-decoration: none;' target='_blank' rel='noopener noreferrer'>- {safe_news_title}</a> <span style='color:#9CA3AF; font-size:12px; font-weight: 600;'>[{safe_source}]</span></li>"
    rendered += "</ul></div>"
    return rendered

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
                {render_table("국내 주요 대형주", "Domestic", "KRW")}
                {render_table("관심종목 거래량 급증", "Trending", "KRW")}
                {render_table("해외 테크 거래량 상위", "Foreign", "USD")}
            </div>
        </div>
    </div>
</div>
<div style="margin-bottom: 50px;"></div>
"""

st.html(html_content)
