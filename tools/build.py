"""한국시장 국면판 생성기 — 표준 라이브러리만 사용.

하는 일
  1) 원자료 받기: 네이버 금융 일봉(코스피·코스닥·금 ETF 132030), Yahoo Finance 일봉
     (2014년 전후 네이버 범위 밖의 코스피·코스닥 과거, S&P500·나스닥·원달러·VIX)
  2) 계산: 200일선·50일선 대비, 최근 20거래일 변화, 국면(강세·혼조·약세), 국면 통계
  3) index.html · llms.txt 생성, data/state.json(계산 결과만, 원 시세 아님) 저장

원칙
  - 원 시세(종가 표·CSV)는 저장소에 남기지 않는다. 계산 결과만 남긴다.
  - 한국 지수를 못 받으면 아무것도 바꾸지 않고 실패로 끝낸다(이전 페이지 유지).
  - 미국·환율·VIX나 과거 자료를 못 받으면 data/state.json의 이전 계산값을 그대로 쓴다.
  - 장중(미완성) 봉은 쓰지 않는다.

사용: python3 tools/build.py [--asof YYYY-MM-DD]   (--asof 는 검증용: 그날까지 자료만 사용)
"""
import json, re, sys, time, math, statistics as st, urllib.request, urllib.error
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")
UA = {"User-Agent": "Mozilla/5.0 (compatible; kr-regime-board/1.0)"}

# ───────────────────────── 받기 ─────────────────────────
def http_get(url, tries=4):
    last = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return r.read()
        except Exception as e:  # 네트워크·429 등
            last = e; time.sleep(3 * (k + 1))
    raise RuntimeError(f"받기 실패: {url} ({last})")

def naver_daily(symbol, count=3000):
    raw = http_get(f"https://fchart.stock.naver.com/sise.nhn?symbol={symbol}&timeframe=day&count={count}&requestType=0")
    txt = raw.decode("euc-kr", errors="ignore")
    out = {}
    for row in re.findall(r'data="([^"]+)"', txt):
        f = row.split("|")
        if len(f) < 5 or not f[4]: continue
        out[date(int(f[0][:4]), int(f[0][4:6]), int(f[0][6:8]))] = float(f[4])
    if len(out) < 260: raise RuntimeError(f"네이버 {symbol} 일봉 부족: {len(out)}")
    return out

def yahoo_daily(symbol, period1=None, rng="2y", drop_open=True):
    """Yahoo 일봉. 날짜는 그 거래소 시간대 기준. 지금 진행 중인 거래 시간의 봉(미완성)은 뺀다."""
    q = f"period1={period1}&period2={int(time.time()) + 86400}" if period1 else f"range={rng}"
    raw = http_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{q}&interval=1d")
    r = json.loads(raw)["chart"]["result"][0]; meta = r["meta"]
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    out = {}
    for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
        if c is None: continue
        out[datetime.fromtimestamp(t, tz).date()] = float(c)
    reg = (meta.get("currentTradingPeriod") or {}).get("regular")
    if drop_open and reg and out:
        cur_day = datetime.fromtimestamp(reg["start"], tz).date()
        if time.time() < reg["end"] + 15 * 60 and max(out) == cur_day:
            del out[max(out)]
    return out

def drop_unfinished(series, tz, close_hhmm):
    """그 시장의 오늘 봉이 아직 마감 전이면 뺀다."""
    now = datetime.now(tz)
    if series and max(series) == now.date() and (now.hour, now.minute) < close_hhmm:
        series = dict(series); del series[max(series)]
    return series

def cut_asof(series, asof):
    return {d: v for d, v in series.items() if d <= asof} if asof else series

# ───────────────────────── 계산 ─────────────────────────
def gaps(series):
    ds = sorted(series); c = [series[d] for d in ds]
    g = lambda n: (c[-1] / (sum(c[-n:]) / n) - 1) * 100
    return {"date": ds[-1].isoformat(), "close": c[-1], "g200": g(200), "g50": g(50),
            "c20": (c[-1] / c[-21] - 1) * 100, "hi252": max(c[-252:]),
            "lo252": min(c[-252:]), "from_hi": (c[-1] / max(c[-252:]) - 1) * 100}

STATE_KO = {"both_up": "강세", "mixed": "혼조", "both_down": "약세"}

def regime_study(K, Q):
    """두 지수가 다 있는 날만 이어 붙여 200일선·국면·이후 코스피 수익률 계산."""
    dates = sorted(set(K) & set(Q)); k = [K[d] for d in dates]; q = [Q[d] for d in dates]
    n = len(dates)
    def ma_series(a):
        out = [None] * n; s = sum(a[:200])
        out[199] = s / 200
        for i in range(200, n):
            s += a[i] - a[i - 200]; out[i] = s / 200
        return out
    mk, mq = ma_series(k), ma_series(q)
    state = [None] * n; gk = [None] * n
    for i in range(199, n):
        ka, qa = k[i] > mk[i], q[i] > mq[i]
        state[i] = "both_up" if ka and qa else ("both_down" if not ka and not qa else "mixed")
        gk[i] = (k[i] / mk[i] - 1) * 100
    stats = {}
    for H in (63, 126):
        for s in STATE_KO:
            rr = sorted((k[i + H] / k[i] - 1) * 100 for i in range(199, n - H) if state[i] == s)
            m = len(rr)
            stats[f"{H}_{s}"] = {"n": m, "mean": st.mean(rr), "median": st.median(rr),
                                 "up": sum(x > 0 for x in rr) / m * 100,
                                 "p10": rr[int(m * 0.1)], "p90": rr[int(m * 0.9)]}
    # 국면 구간
    eps = []; i = 199
    while i < n:
        j = i
        while j + 1 < n and state[j + 1] == state[i]: j += 1
        eps.append((state[i], i, j)); i = j + 1
    cur_state, cur_i, _ = eps[-1]
    weak = [e for e in eps if e[0] == "both_down"]
    ended_weak = weak[:-1] if cur_state == "both_down" else weak
    long_weak = [{"start": dates[a].isoformat(), "end": dates[b].isoformat(), "len": b - a + 1}
                 for _, a, b in ended_weak if b - a + 1 >= 60]
    # 지금 코스피 거리와 같거나 더 먼 날이 이번 국면 전에 마지막으로 있었던 때
    g_now = gk[-1]; last_like = None
    for i in range(cur_i - 1, 198, -1):
        if (g_now < 0 and gk[i] <= g_now) or (g_now >= 0 and gk[i] >= g_now):
            last_like = dates[i].isoformat(); break
    return {"first": dates[199].isoformat(), "last": dates[-1].isoformat(),
            "state": cur_state, "state_start": dates[cur_i].isoformat(),
            "state_days": n - cur_i, "kospi_gap": g_now, "last_like": last_like,
            "weak_total": len(weak),
            "weak_short": sum(1 for _, a, b in weak if b - a + 1 < 20),
            "long_weak": long_weak, "stats": stats}

# ───────────────────────── 글 만들기 ─────────────────────────
def pct(x, d=1, sign=True):
    s = f"{abs(x):,.{d}f}%"
    if not sign: return s
    return ("+" if x > 0 else "−" if x < 0 else "") + s if round(abs(x), d) else f"{0:.{d}f}%"

def ro(x, d=2):
    """숫자 뒤 '로/으로' — 마지막 숫자를 한국어로 읽었을 때 받침(ㄹ 제외)이 있으면 '으로'."""
    last = f"{x:.{d}f}"[-1]
    return "으로" if last in "036" else "로"

def cls(x): return "upc" if round(x, 1) > 0 else ("dn" if round(x, 1) < 0 else "")
def ko_date(iso):
    d = date.fromisoformat(iso); return f"{d.year}년 {d.month}월 {d.day}일"
def md(iso):
    d = date.fromisoformat(iso); return f"{d.month:02d}-{d.day:02d}"

def layout(points, x_of, min_gap, rows, zx, clear, xmin, xmax):
    """라벨 위치 정하기. 200일선(zx)에서 clear 안쪽이면 선 바깥으로 비켜 두고, 가까운 라벨끼리는 줄을 나눈다.
    points: [(이름, 값)] → [(이름, 값, 점x, 라벨x, 줄)]"""
    items = []
    for name, v in points:
        x = x_of(v); lx = x
        if v <= 0 and lx > zx - clear: lx = zx - clear
        if v > 0 and lx < zx + clear: lx = zx + clear
        items.append((name, v, x, min(max(lx, xmin), xmax)))
    placed = []; out = []
    for name, v, x, lx in sorted(items, key=lambda t: t[3]):
        row = rows - 1
        for r in range(rows):
            if all(abs(lx - px) >= min_gap for px, pr in placed if pr == r): row = r; break
        placed.append((lx, row)); out.append((name, v, x, lx, row))
    return out

def ruler_svg(points, wide):
    vals = [v for _, v in points]
    lo = min(-40, math.floor((min(vals) - 2) / 10) * 10)
    hi = max(10, math.ceil((max(vals) + 2) / 10) * 10)
    if wide: W, x0, x1, axis_y, H, fs, afs, min_gap, row_y, clear = 640, 20, 620, 132, 195, 14, 12, 74, (34, 82), 38
    else:    W, x0, x1, axis_y, H, fs, afs, min_gap, row_y, clear = 360, 15, 345, 140, 210, 13, 11, 46, (32, 78), 26
    xo = lambda v: x0 + (v - lo) * (x1 - x0) / (hi - lo)
    font = 'font-family="IBM Plex Sans KR, sans-serif"'
    p = [f'<line x1="{x0}" y1="{axis_y}" x2="{x1}" y2="{axis_y}" stroke="#C9D1CF" stroke-width="2"/>',
         f'<g {font} font-size="{afs}" fill="#5E6B73" text-anchor="middle">']
    for t in range(lo, hi + 1, 10):
        if t == 0: continue
        x = xo(t); lab = ("+" if t > 0 else "−") + f"{abs(t)}%"
        p.append(f'<line x1="{x:.1f}" y1="{axis_y-6}" x2="{x:.1f}" y2="{axis_y+6}" stroke="#C9D1CF"/>'
                 f'<text x="{x:.1f}" y="{axis_y+24}">{lab}</text>')
    p.append("</g>")
    zx = xo(0)
    p.append(f'<line x1="{zx:.1f}" y1="18" x2="{zx:.1f}" y2="{axis_y+30}" stroke="#3E7A65" stroke-width="3"/>'
             f'<text x="{zx:.1f}" y="{axis_y+50}" text-anchor="middle" {font} font-size="13" font-weight="600" fill="#3E7A65">200일선</text>')
    p.append(f'<g {font} font-size="{fs}" text-anchor="middle">')
    for name, v, x, lx, row in layout(points, xo, min_gap, 2, xo(0), clear, x0 + 22, W - 26):
        ty = row_y[row]; vy = ty + (18 if wide else 17)
        col = "#C43A3A" if v > 0 else "#2B5BA8"
        dot = (f'<circle cx="{x:.1f}" cy="{axis_y}" r="{7 if wide else 6}" fill="#2B5BA8"/>' if v <= 0 else
               f'<circle cx="{x:.1f}" cy="{axis_y}" r="{6 if wide else 5}" fill="#F3F5F4" stroke="#C43A3A" stroke-width="2.5"/>')
        p.append(f'<line x1="{lx:.1f}" y1="{vy+8}" x2="{x:.1f}" y2="{axis_y}" stroke="{col}" stroke-width="1.5"/>{dot}'
                 f'<text x="{lx:.1f}" y="{ty}" fill="#17232B" font-weight="600">{name}</text>'
                 f'<text x="{lx:.1f}" y="{vy}" fill="{col}">{pct(v)}</text>')
    p.append("</g>")
    aria = ", ".join(f"{n} {pct(v)}" for n, v in points)
    return (f'<svg class="{"wide" if wide else "narrow"}" viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="200일선을 0으로 놓은 자. {aria}">' + "".join(p) + "</svg>")

def render(S):
    I, R = S["ind"], S["regime"]; ST = R["stats"]
    kp, kq, gd = I["kospi"], I["kosdaq"], I["gold"]
    sp, nq, fx, vx = I["spx"], I["ndx"], I["fx"], I["vix"]
    state = R["state"]; skr = STATE_KO[state]
    asof = kp["date"]; us_date = sp["date"]
    # 헤드라인
    if state == "both_down": h1 = "코스피와 코스닥이 둘 다 200일선 아래에 있습니다."
    elif state == "both_up": h1 = "코스피와 코스닥이 둘 다 200일선 위에 있습니다."
    elif kp["g200"] > 0: h1 = "코스피는 200일선 위, 코스닥은 아래에 있습니다."
    else: h1 = "코스닥은 200일선 위, 코스피는 아래에 있습니다."
    rule = {"both_down": "두 지수가 모두 200일선 아래면 약세 국면입니다.",
            "both_up": "두 지수가 모두 200일선 위면 강세 국면입니다.",
            "mixed": "두 지수 중 하나만 200일선 위면 혼조 국면입니다."}[state]
    def rel(name, g):
        josa = "은" if name == "코스닥" else "는"
        return f"{name}{josa} 200일선보다 {pct(g, sign=False)} {'높' if g > 0 else '낮'}"
    lead = f"{rule} {rel('코스피', kp['g200'])}고, {rel('코스닥', kq['g200'])}습니다. "
    lead += f"{skr} 국면은 {ko_date(R['state_start'])}부터 {R['state_days']}거래일째입니다."
    if abs(kp["g200"]) >= 10:
        word = "낮" if kp["g200"] < 0 else "높"
        if R["last_like"]:
            d = date.fromisoformat(R["last_like"])
            lead += f" 코스피가 200일선보다 이만큼 {word}은 날은 이번 국면 전에는 {d.year}년 {d.month}월이 마지막이었습니다."
        else:
            lead += f" 코스피가 200일선보다 이만큼 {word}은 것은 {R['first'][:4]}년 이후 처음입니다."
    on = {s: (' class="on" aria-current="true"' if s == state else "") for s in STATE_KO}
    pts = [("코스닥", kq["g200"]), ("코스피", kp["g200"]), ("금 ETF", gd["g200"]), ("나스닥", nq["g200"]), ("S&P500", sp["g200"])]
    pts_n = [(n if n != "S&P500" else "S&P", v) for n, v in pts]
    # 지표판
    def row(name, x, note=""):
        return (f'<tr><td>{name}{note}</td><td class="num {cls(x["g200"])}">{pct(x["g200"])}</td>'
                f'<td class="num {cls(x["g50"])}">{pct(x["g50"])}</td><td class="num {cls(x["c20"])}">{pct(x["c20"])}</td></tr>')
    usn = lambda x: f' <span class="small">(미국 {md(x["date"])})</span>'
    rows = "\n".join([row("코스피", kp), row("코스닥", kq), row("금 ETF (KODEX 골드선물(H))", gd),
                      row("S&amp;P500", sp, usn(sp)), row("나스닥", nq, usn(nq)),
                      row("원·달러 환율", fx, f' <span class="small">({md(fx["date"])})</span>')])
    pos = (vx["close"] - vx["lo252"]) / (vx["hi252"] - vx["lo252"]) if vx["hi252"] > vx["lo252"] else 0.5
    where = "아래쪽" if pos < 1/3 else ("위쪽" if pos > 2/3 else "가운데쯤")
    vix_p = (f"VIX(미국 S&amp;P500 옵션으로 계산한 앞으로 30일 변동성 예상치)는 {vx['close']:.2f}{ro(vx['close'])}, "
             f"최근 1년 범위({vx['lo252']:.2f}~{vx['hi252']:.2f})의 {where}입니다. "
             f"최근 20거래일 동안 {pct(vx['c20'], sign=False)} {'올랐' if vx['c20'] > 0 else '내렸'}습니다.")
    hi_p = (f"코스피는 최근 1년 중 가장 높았던 종가보다 {pct(kp['from_hi'], sign=False)}, "
            f"코스닥은 {pct(kq['from_hi'], sign=False)} 낮습니다.") if kp["from_hi"] < 0 or kq["from_hi"] < 0 else ""
    # 통계표
    def srow(H, s, first_col):
        x = ST[f"{H}_{s}"]; cur = ' class="cur"' if s == state else ""
        lab = STATE_KO[s] + (" (지금)" if s == state else "")
        nn = f'<td class="num hide-m">{x["n"]:,}일</td>' if first_col else ""
        return (f'<tr{cur}><td>{lab}</td>{nn}<td class="num">{pct(x["mean"])}</td><td class="num">{pct(x["median"])}</td>'
                f'<td class="num">{x["up"]:.0f}%</td><td class="num">{pct(x["p10"])}</td><td class="num">{pct(x["p90"])}</td></tr>')
    t6 = "\n".join(srow(126, s, True) for s in STATE_KO)
    t3 = "\n".join(srow(63, s, False) for s in STATE_KO)
    up = {s: round(ST[f"126_{s}"]["up"]) for s in STATE_KO}
    w6 = min(STATE_KO, key=lambda s: ST[f"126_{s}"]["p10"]); w3 = min(STATE_KO, key=lambda s: ST[f"63_{s}"]["p10"])
    cmp = "높았" if up["both_down"] > up["both_up"] else ("낮았" if up["both_down"] < up["both_up"] else "같았")
    call = [f"6개월 뒤 코스피가 오른 비율은 약세 국면 {up['both_down']}%, 강세 국면 {up['both_up']}%, 혼조 국면 {up['mixed']}%였습니다. 약세 국면이 강세 국면보다 {cmp}습니다."]
    call.append(f"6개월 뒤 하위10%가 가장 나빴던 국면은 {STATE_KO[w6]}({pct(ST[f'126_{w6}']['p10'])})이고, "
                f"3개월로 보면 {STATE_KO[w3]}({pct(ST[f'63_{w3}']['p10'])})입니다.")
    if w6 == "both_down" and up["both_down"] >= up["both_up"]:
        call.append('약세 국면은 "더 떨어진다"보다 "길게 보면 흔들림이 크다"에 가까웠습니다.')
    callout = "".join(f"<p>{c}</p>" for c in call)
    ep_rows = "\n".join(f'<tr><td class="nw">{e["start"]}</td><td class="nw">{e["end"]}</td><td class="num">{e["len"]}거래일</td></tr>'
                        for e in R["long_weak"])
    ongoing = (f" 지금 국면({R['state_start']} 시작)은 아직 진행 중이라 목록에 없습니다." if state == "both_down" else "")
    n6 = {s: ST[f"126_{s}"]["n"] for s in STATE_KO}
    first = date.fromisoformat(R["first"])
    note = (f"각 국면의 해당 거래일 수는 강세 {n6['both_up']:,}일, 혼조 {n6['mixed']:,}일, 약세 {n6['both_down']:,}일입니다"
            f"(6개월 뒤 값이 있는 날만). 한계: 이어지는 날들의 6개월 구간이 서로 겹쳐서, 거래일 수만큼 독립된 사례가 있는 것이 아닙니다. "
            f"약세 국면은 이 기간에 {R['weak_total']}번 시작됐고 그중 {R['weak_short']}번은 20거래일 안에 끝났습니다. "
            f"60거래일 이상 이어진 것은 위 {len(R['long_weak'])}번입니다.{ongoing} "
            f"일봉 출처는 네이버 금융 자료가 닿는 범위 이전은 Yahoo Finance, 이후는 네이버 금융이며 배당은 빠진 지수 값입니다. 계산 기준일 {asof}.")
    css = (ROOT / "templates" / "style.css").read_text()
    desc = ("코스피·코스닥이 200일 이동평균선 위인지 아래인지로 한국 시장 국면(강세·혼조·약세)을 나누고, 미국 지수·환율·VIX와 함께 기준일을 붙여 보여줍니다. "
            f"{first.year}년 이후 국면별 코스피 수익률 통계도 공개합니다. 매수·매도 권유가 아닙니다.")
    ld = json.dumps({"@context": "https://schema.org", "@type": "WebPage",
                     "name": "한국시장 국면판 — 코스피·코스닥 200일선으로 보는 시장 상황", "inLanguage": "ko",
                     "dateModified": asof,
                     "description": f"{asof} 종가 기준 한국 시장 국면({skr})과 보조 지표, {R['first'][:7]}~{asof[:7]} 국면별 코스피 이후 수익률 통계.",
                     "about": ["200일 이동평균선", "코스피", "코스닥", "시장 국면", "VIX", "원달러 환율"]}, ensure_ascii=False, indent=2)
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>한국시장 국면판 — 코스피·코스닥 200일선으로 보는 시장 상황</title>
<meta name="description" content="{desc}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&family=IBM+Plex+Sans+KR:wght@400;500;600&display=swap" rel="stylesheet">
<script type="application/ld+json">
{ld}
</script>
<style>
{css}</style>
</head>
<body>
<header class="top">
  <div class="wrap">
    <a class="brand" href="#">한국시장 국면판</a>
    <nav aria-label="페이지 안 이동">
      <a href="#pan">지표판</a><a href="#tonggye">국면 통계</a><a href="#bangbeop">읽는 법</a>
    </nav>
  </div>
</header>

<main>
  <div class="wrap">
    <section class="hero" style="border-top:0" aria-labelledby="h-hero">
      <p class="asof">{ko_date(asof)} 한국 종가 기준</p>
      <ul class="state" aria-label="국면 세 단계 중 지금">
        <li{on['both_up']}>강세</li><li{on['mixed']}>혼조</li><li{on['both_down']}>약세</li>
      </ul>
      <h1 id="h-hero">{h1}</h1>
      <p class="lead">{lead}</p>

      <figure class="ruler" aria-labelledby="ruler-cap">
        {ruler_svg(pts, True)}
        {ruler_svg(pts_n, False)}
        <figcaption id="ruler-cap">각 점은 그 지표의 종가가 자기 200일선보다 몇 % 떨어져 있는지입니다. 파란 점은 선 아래, 빨간 테두리 점은 선 위. 한국 시세판처럼 오르는 쪽을 빨강, 내리는 쪽을 파랑으로 칠했습니다.</figcaption>
      </figure>
    </section>

    <section id="pan" aria-labelledby="h-pan">
      <h2 id="h-pan">지표판</h2>
      <div class="tablewrap">
        <table>
          <thead><tr><th>지표</th><th class="num">200일선 대비</th><th class="num">50일선 대비</th><th class="num">최근 20거래일</th></tr></thead>
          <tbody>
{rows}
          </tbody>
        </table>
      </div>
      <p>{hi_p}</p>
      <p>{vix_p}</p>
      <p class="small">계산: (종가 ÷ 최근 200·50거래일 종가 평균 − 1), 최근 20거래일은 (종가 ÷ 20거래일 전 종가 − 1). 한국 지수·금 ETF는 {asof} 종가(네이버 금융 일봉). 미국 지수·환율·VIX는 현지 {us_date} 종가(Yahoo Finance).</p>
    </section>

    <section id="tonggye" aria-labelledby="h-tonggye">
      <h2 id="h-tonggye">국면 통계: 이런 날 뒤에 코스피는 어땠나</h2>
      <p>{first.year}년 {first.month}월부터 {date.fromisoformat(asof).year}년 {date.fromisoformat(asof).month}월까지 거래일마다 국면을 매기고, 그날부터 3개월(63거래일)·6개월(126거래일) 뒤 코스피가 얼마나 움직였는지 모았습니다. 강세는 둘 다 200일선 위, 혼조는 하나만 위, 약세는 둘 다 아래입니다. 하위10%는 열 번 중 가장 나빴던 한 번의 경계, 상위10%는 가장 좋았던 한 번의 경계입니다.</p>
      <div class="tablewrap">
        <table>
          <thead><tr><th>6개월 뒤</th><th class="num hide-m">해당 거래일</th><th class="num">평균</th><th class="num">중앙값</th><th class="num">오른 비율</th><th class="num">하위10%</th><th class="num">상위10%</th></tr></thead>
          <tbody>
{t6}
          </tbody>
        </table>
      </div>
      <div class="tablewrap">
        <table>
          <thead><tr><th>3개월 뒤</th><th class="num">평균</th><th class="num">중앙값</th><th class="num">오른 비율</th><th class="num">하위10%</th><th class="num">상위10%</th></tr></thead>
          <tbody>
{t3}
          </tbody>
        </table>
      </div>
      <div class="callout">
        <p><strong>숫자가 말하는 것</strong></p>
        {callout}
      </div>
      <h3>60거래일 이상 이어진 약세 국면 ({first.year}~{date.fromisoformat(asof).year})</h3>
      <div class="tablewrap">
        <table>
          <thead><tr><th>시작</th><th>끝</th><th class="num">길이</th></tr></thead>
          <tbody>
{ep_rows}
          </tbody>
        </table>
      </div>
      <p class="small">{note}</p>
    </section>

    <section id="bangbeop" aria-labelledby="h-bangbeop">
      <h2 id="h-bangbeop">읽는 법</h2>
      <dl class="terms">
        <dt>200일선</dt>
        <dd>최근 200거래일(약 10개월) 종가의 평균입니다. 종가가 이 선 위면 긴 추세가 위를 향한다고 보는 흔한 기준입니다.</dd>
        <dt>국면</dt>
        <dd>코스피와 코스닥이 둘 다 200일선 위면 강세, 하나만 위면 혼조, 둘 다 아래면 약세입니다. 대형주(코스피)와 중소형주(코스닥)를 함께 봐서, 한쪽만 버티는 장을 따로 나눕니다.</dd>
        <dt>50일선</dt>
        <dd>최근 50거래일(약 2.5개월) 종가의 평균으로, 200일선보다 짧은 흐름을 봅니다.</dd>
        <dt>VIX</dt>
        <dd>미국 S&amp;P500 옵션 가격으로 계산한, 앞으로 30일 동안 시장이 얼마나 흔들릴지에 대한 예상치입니다. 높을수록 불안이 크다는 뜻입니다.</dd>
      </dl>
    </section>
  </div>
</main>

<footer>
  <div class="wrap">
    <p>이 페이지는 공개된 지수 값으로 계산한 결과를 보여줄 뿐, 어떤 종목이나 자산의 매수·매도를 권하지 않습니다.</p>
    <p>마지막 갱신: 한국 {asof} 종가, 미국 현지 {us_date} 종가 기준. 한국 장이 열린 날 오후에 자동으로 갱신됩니다.</p>
  </div>
</footer>
</body>
</html>
"""
    s6 = ST["126_both_down"]
    llms = f"""# 한국시장 국면판

> 코스피·코스닥이 200일 이동평균선 위인지 아래인지로 한국 시장 국면(강세·혼조·약세)을 나누고, 미국 지수·원달러 환율·VIX와 함께 기준일을 붙여 보여주는 페이지입니다. {first.year}년 이후 국면별 코스피 이후 수익률 통계도 공개합니다. 한국 장이 열린 날 오후에 자동으로 갱신됩니다. 매수·매도 권유가 아닙니다.

## 페이지
- [지표판](index.html#pan): 코스피·코스닥·금 ETF·S&P500·나스닥·원달러 환율의 200일선·50일선 대비 거리와 최근 20거래일 변화, VIX.
- [국면 통계](index.html#tonggye): {R['first'][:7]}~{asof[:7]} 국면별 3개월·6개월 뒤 코스피 수익률(평균·중앙값·오른 비율·하위/상위 10%)과 60거래일 이상 이어진 약세 국면 목록.
- [읽는 법](index.html#bangbeop): 200일선·국면·50일선·VIX 뜻.

## 국면 정의
- 강세: 코스피와 코스닥 모두 종가가 200일선 위. 혼조: 하나만 위. 약세: 둘 다 아래.

## {asof} 한국 종가 기준
- 국면: {skr} ({R['state_start']}부터 {R['state_days']}거래일째).
- 코스피 200일선 대비 {pct(kp['g200'])}, 코스닥 {pct(kq['g200'])}.
- 미국 현지 {us_date} 종가: S&P500 {pct(sp['g200'])}, 나스닥 {pct(nq['g200'])} (200일선 대비). VIX {vx['close']:.2f}.
- {R['first'][:7]} 이후 약세 국면 거래일 중 6개월 뒤 값이 있는 {s6['n']:,}일의 6개월 뒤 코스피: 평균 {pct(s6['mean'])}, 중앙값 {pct(s6['median'])}, 오른 비율 {s6['up']:.0f}%, 하위 10% {pct(s6['p10'])}.
"""
    return html, llms

# ───────────────────────── 실행 ─────────────────────────
def main():
    asof = None
    if "--asof" in sys.argv: asof = date.fromisoformat(sys.argv[sys.argv.index("--asof") + 1])
    state_path = ROOT / "data" / "state.json"
    prev = json.loads(state_path.read_text()) if state_path.exists() else {}
    warn = []
    # 한국(필수)
    NK = drop_unfinished(naver_daily("KOSPI"), KST, (15, 40))
    NQ = drop_unfinished(naver_daily("KOSDAQ"), KST, (15, 40))
    NG = drop_unfinished(naver_daily("132030", 400), KST, (15, 40))
    NK, NQ, NG = (cut_asof(x, asof) for x in (NK, NQ, NG))
    if not (max(NK) == max(NQ) == max(NG)): raise RuntimeError(f"한국 마지막 날짜 불일치 {max(NK)} {max(NQ)} {max(NG)}")
    ind = {"kospi": gaps(NK), "kosdaq": gaps(NQ), "gold": gaps(NG)}
    # 미국·환율·VIX(실패 시 이전 값)
    for key, sym in (("spx", "%5EGSPC"), ("ndx", "%5EIXIC"), ("fx", "KRW%3DX"), ("vix", "%5EVIX")):
        try:
            s = cut_asof(yahoo_daily(sym), asof)
            ind[key] = gaps(s)
        except Exception as e:
            if key not in prev.get("ind", {}): raise
            ind[key] = prev["ind"][key]; warn.append(f"{key}: 이전 값 사용 ({e})")
    # 국면 통계(과거는 Yahoo, 네이버 범위부터는 네이버)
    try:
        cut = min(min(NK), min(NQ))
        YK = yahoo_daily("%5EKS11", period1=946684800, drop_open=False); YQ = yahoo_daily("%5EKQ11", period1=946684800, drop_open=False)
        K = {d: v for d, v in YK.items() if d < cut}; K.update(NK)
        Q = {d: v for d, v in YQ.items() if d < cut}; Q.update(NQ)
        regime = regime_study(K, Q)
        if regime["last"] != ind["kospi"]["date"]: raise RuntimeError("국면 마지막 날짜 불일치")
    except Exception as e:
        if "regime" not in prev: raise
        regime = prev["regime"]; warn.append(f"regime: 이전 값 사용 ({e})")
        if regime["last"] != ind["kospi"]["date"]: raise RuntimeError(f"국면 통계를 새로 못 만들었고 이전 값 날짜가 다름: {e}")
    S = {"ind": ind, "regime": regime, "warn": warn}
    html, llms = render(S)
    if not asof or "--write" in sys.argv:
        (ROOT / "index.html").write_text(html)
        (ROOT / "llms.txt").write_text(llms)
        state_path.parent.mkdir(exist_ok=True)
        keep = {k: ({kk: vv for kk, vv in v.items() if kk not in ("close", "hi252", "lo252")} if k != "vix" else v)
                for k, v in S["ind"].items()}  # 원 시세(종가)는 남기지 않는다 — VIX는 페이지에 쓰는 값이라 둔다
        state_path.write_text(json.dumps({**S, "ind": keep}, ensure_ascii=False, indent=1))
    else:
        out = ROOT / "data" / f"preview_{asof}.html"; out.write_text(html)
        (ROOT / "data" / f"preview_{asof}_llms.txt").write_text(llms)
    print(json.dumps({"asof": ind["kospi"]["date"], "state": regime["state"], "warn": warn}, ensure_ascii=False))

if __name__ == "__main__":
    main()
