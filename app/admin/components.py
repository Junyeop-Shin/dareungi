"""관리자 앱 화면들이 같이 쓰는 조각(08a 설계서 3절).

머리 영역(제목·기준 시각·자치구 필터), KPI, 카드 제목, 자전거 목록, 자치구 타일맵, 예측 정확도 계산, 고지문.
빈 상태는 오류처럼 보이지 않게 회색 한 줄로, 고지문은 접어서 둔다.
상태 표시는 색만으로 구분하지 않고 글자·숫자를 같이 쓴다(색각 이상 배려). 아이콘·이모지는 쓰지 않는다.
제목에는 role="heading"을 달아 화면낭독기가 구역을 건너뛰며 읽을 수 있게 한다.
"""
import json
import sys
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import data, theme, texts            # noqa: E402
from common.seoul_tiles import DISTRICTS, SECTORS, SEOUL_TILES   # noqa: E402

APP_NAME = "따릉이 정비 현황"                  # 브라우저 탭 이름 뒤에 붙는 앱 이름(홈 화면의 제목과 같다)
CONF_HIGH, CONF_MID = 0.20, 0.0                # 예측 정확도 등급 기준: '지난주와 같다'(나이브) 대비 오차 개선율(08a 9절)
CONF_COLOR = {"높음": "ok", "낮음": "danger"}       # 예측 정확도 등급 글자색. 보통은 기본색


# ── 자치구 필터 ───────────────────────────────────────────
# 서울 전체 / 강북 / 강남을 먼저 고르고, 구는 '구 선택' 팝오버에서 고른다.
# 강북·강남을 누르면 그 권역의 구가 모두 선택된 상태로 시작하고, 하나씩 빼거나 '모두 비우기' 뒤 다시 고를 수 있다.
# 권역 구분은 seoul_tiles.SECTORS(한강 기준 임시 구분)를 쓴다.
SCOPES = ["서울 전체", "강북", "강남"]


def _pool(scope):
    return DISTRICTS if scope == "서울 전체" else SECTORS[scope]


def _scope_changed():
    sc = st.session_state.get("gu_scope")
    if not sc:                                   # 이미 고른 버튼을 다시 누르면 선택이 풀리는데, 그때는 그대로 둔다
        return
    data.set_region(sc, [] if sc == "서울 전체" else SECTORS[sc])


def _sel_changed():
    data.set_region(data.get_scope(), st.session_state.get("gu_sel") or [])


def _clear():
    data.set_region(data.get_scope(), [])


def _select_all():
    sc = data.get_scope()
    data.set_region(sc, [] if sc == "서울 전체" else SECTORS[sc])


def gu_filter():
    sc, sel = data.get_scope(), data.get_selected()
    st.session_state["gu_scope"] = sc
    st.session_state["gu_sel"] = [g for g in sel if g in _pool(sc)]
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):      # 권역 버튼과 구 선택을 오른쪽 끝에 나란히
        st.segmented_control("권역", SCOPES, key="gu_scope", on_change=_scope_changed, label_visibility="collapsed", width="content")
        n = len(st.session_state["gu_sel"])
        label = f"구 선택 · {n}곳" if n and not (sc != "서울 전체" and n == len(SECTORS[sc])) else ("구 선택" if sc == "서울 전체" else f"구 선택 · {sc} 전체")
        with st.popover(label, width="content"):
            hint = "아무것도 안 고르면 서울 전체" if sc == "서울 전체" else f"아무것도 안 고르면 {sc} 전체"
            # 목록을 늘 펼쳐 두려고 multiselect 대신 pills를 쓴다. 다 비워도 구 이름이 그대로 보여 다시 고르기 쉽다
            st.pills(f"{sc} 자치구 ({hint})", _pool(sc), selection_mode="multi", key="gu_sel", on_change=_sel_changed)
            st.button("모두 비우기", on_click=_clear, use_container_width=True, disabled=not n)
            if sc != "서울 전체":
                st.button(f"{sc} 모두 선택", on_click=_select_all, use_container_width=True, disabled=n == len(SECTORS[sc]))
    if data.get_gu():                            # 서울 전체일 때는 버튼이 이미 말해 주므로 구를 골랐을 때만 적는다
        st.markdown(f'<div class="gu-picked">보는 지역: {scope_label()}</div>', unsafe_allow_html=True)


def week_so_far(monday):
    """이번 주 월요일부터 어제까지 실제 신고(서울 전체, 유형별). 반환: ({유형: 건수}, 마지막 날짜) 또는 (None, None)"""
    dr = data.load("daily_reports")
    if dr is None or dr.empty:
        return None, None
    d = dr[pd.to_datetime(dr.date) >= pd.Timestamp(monday)]
    if d.empty:
        return None, None
    return d.groupby("part").reports.sum().to_dict(), pd.to_datetime(d.date).max()


def upto_label(upto):
    """'지금까지' 집계의 마지막 날을 사람 말로. 어제면 '어제까지', 아니면 'm/d까지'. 오늘 신고는 하루가 끝난 뒤 집계된다."""
    if upto is None:
        return "지금까지"
    import datetime as dt
    d = pd.Timestamp(upto).date()
    return "어제까지" if d == dt.date.today() - dt.timedelta(days=1) else f"{d.month}/{d.day}까지"


def scope_label():
    sc, gus = data.get_scope(), data.get_gu()
    if not gus:
        return "서울 전체"
    if sc != "서울 전체" and sorted(gus) == sorted(SECTORS[sc]):
        return f"{sc} 전체 ({len(gus)}개 구)"
    return ", ".join(gus) if len(gus) <= 3 else f"{gus[0]} 외 {len(gus) - 1}곳"


# ── 머리 영역 ─────────────────────────────────────────────
def status_cells():
    """머리 영역의 '자료 기준' 띠에 들어갈 칸들을 [(이름, 값, 밀렸나)]로 돌려준다.
    늘 보이는 칸은 대여 기록·고장 신고·이번 주 예측 셋. 48시간 넘게 갱신되지 않은 자료(data.stale_items)는 그 칸을 주황으로 바꾸고
    며칠째인지 적는다. 날씨 관측처럼 평소에는 안 보이는 자료도 밀렸을 때는 칸이 하나 더 생긴다(전에는 화면 위에 큰 노란 상자로 띄웠다)."""
    s = data.status()
    late = dict(data.stale_items(s))
    note = lambda k: f" · {late[k]}일째 갱신 안 됨" if k in late else ""
    cells = []
    if "대여이력" in s: cells.append(("대여 기록", f"{s['대여이력']:%-m/%-d %-H}시까지 반영{note('대여이력')}", "대여이력" in late))
    if "고장신고" in s: cells.append(("고장 신고", f"{s['고장신고']:%-m/%-d}까지 반영{note('고장신고')}", "고장신고" in late))
    if "주간 예측" in s: cells.append(("이번 주 예측", f"{s['주간 예측']:%-m/%-d}({'월화수목금토일'[s['주간 예측'].weekday()]}) 계산", False))
    for k in late:
        if k not in ("대여이력", "고장신고"):
            cells.append((k, f"{s[k]:%-m/%-d %-H}시까지{note(k)}", True))
    return cells


def header(title, show_confidence=False, show_filter=True):
    """화면 맨 위: 제목 줄(왼쪽 제목, 오른쪽에 지역 선택 또는 예측 정확도), 그 아래 '자료 기준' 띠(자료별 기준 시각, 밀린 자료는 주황)."""
    theme.apply()
    tab = title if title == APP_NAME else f"{title} · {APP_NAME}"
    st.html(f"<script>document.title = '{tab}'</script>", unsafe_allow_javascript=True)          # 브라우저 탭 이름을 화면마다 다르게
    conf = ""          # 예측 정확도(유형별 화면): 제목 줄 오른쪽, 다른 화면에서 지역 선택이 놓이는 자리에 둔다
    c = confidence() if show_confidence else None
    if c:
        gc = theme.P().get(CONF_COLOR.get(c["grade"], ""), "inherit")
        # 물음표 설명: 같은 잣대의 숫자 두 개만 나란히 보여 준다(이 예측의 오차, 기준값의 오차). 퍼센트가 셋이면 무엇이 무엇인지 알 수 없었다.
        # 등급 기준은 아래에 작게, 지금 등급만 진하게
        t = c["track"]
        naive = (abs(t.naive - t.actual) / t.actual).mean() * 100
        rule = "".join(f'<div class="{"on" if g == c["grade"] else ""}">{g}: {txt}</div>' for g, txt in texts.CONFIDENCE_GRADES)
        tip = (f'<div class="qt-head">최근 {c["weeks"]}주 평균 오차</div>'
               f'<div class="qt-pair"><div><div class="qt-num">{c["mae8"]:.0f}%</div>이 예측</div>'
               f'<div><div class="qt-num dim">{naive:.0f}%</div>기준값<br>(지난주와 같다고 가정)</div></div>'
               f'<div>오차는 예측한 건수가 실제 신고 건수와 얼마나 달랐는지입니다. 작을수록 정확합니다.</div>'
               f'<div class="qt-rule">{rule}</div>')
        plain = f'최근 {c["weeks"]}주 평균 오차: 이 예측 {c["mae8"]:.0f}%, 기준값 {naive:.0f}%, 정확도 {c["grade"]}'
        conf = f'<div class="conf-top"><span class="fl">예측 정확도</span><span class="fv" style="color:{gc}">{c["grade"]}</span>{qhelp(tip, plain, "to-left")}</div>'
    left, right = st.columns([1.1, 1], vertical_alignment="center")
    with left:
        st.markdown(f'<div class="page-title" role="heading" aria-level="1">{title}</div>', unsafe_allow_html=True)
    with right:
        if show_filter:
            gu_filter()
        elif conf:
            st.markdown(conf, unsafe_allow_html=True)
    cells = "".join(f'<div class="fc{" late" if bad else ""}"><span class="dot"></span><span class="fl">{k}</span><span class="fv">{v}</span></div>'
                    for k, v, bad in status_cells()) or '<div class="fc late"><span class="fv">데이터를 불러오지 못했습니다</span></div>'
    st.markdown(f'<div class="fresh" role="group" aria-label="자료 기준 시각">{cells}</div>', unsafe_allow_html=True)
    return data.get_gu()


# ── 카드·KPI ──────────────────────────────────────────────
def kpi_strip(items, narrow=False):
    """KPI 여러 개를 상자 없이 세로선으로만 나눈 한 줄로 그린다.
    items: [dict(label 이름, value 숫자 문자열, unit 단위(건·대·없음), sub 아래 설명(HTML 가능), help 마우스를 올리면 뜨는 설명)]
    숫자에는 색을 넣지 않는다(작은 숫자 하나만 튀면 거기부터 보게 된다). narrow: 칸이 많을 때 글자를 조금 줄인다"""
    txt = lambda v: " txt" if v != "—" and not v[:1].isdigit() else ""          # 숫자가 아닌 값('확인 어려움' 등)은 큰 숫자 글씨로 쓰지 않는다
    cells = "".join(
        f'<div class="kc"{" title=" + chr(34) + escape(it["help"]) + chr(34) if it.get("help") else ""}><div class="kpi-label">{it["label"]}</div>'
        f'<div class="kpi-value{txt(it["value"])}">{it["value"]}<span class="unit">{it.get("unit", "") if it["value"] != "—" else ""}</span></div><div class="kpi-sub">{it.get("sub", "")}</div></div>' for it in items)
    st.markdown(f'<div class="kstrip{" narrow" if narrow else ""}" style="--n:{len(items)}">{cells}</div>', unsafe_allow_html=True)


PAGE_URL = {"views/parts_forecast.py": "parts_forecast", "views/inspection.py": "inspection", "views/home.py": ""}


def qhelp(html, plain, cls=""):
    """물음표와 그 설명 상자(마우스를 올리거나 탭으로 가면 뜬다). html은 상자 안에 넣을 내용, plain은 화면낭독기용 한 줄"""
    return f'<span class="qhelp {cls}" tabindex="0" role="note" aria-label="{escape(plain)}">?<span class="qtip">{html}</span></span>'


def card_title(title, page=None, sub=None, help=None, link=None):
    """카드 제목(왼쪽)과 이동 링크(오른쪽 끝)를 한 줄에 두고, 그 아래 무엇을 세는지 한 줄(sub)을 붙인다.
    링크를 HTML로 직접 그려야 오른쪽 끝에 정확히 붙는다. 고른 권역·자치구는 주소(?scope=, ?gu=)에 실어 다음 화면으로 넘긴다.
    링크 글자는 모두 '자세히 보기'라서, 화면낭독기용 이름(aria-label)에 카드 제목을 붙여 어디로 가는지 구분한다.
    link=(글자, 주소)를 주면 '자세히 보기' 대신 그 글자와 주소로 링크를 건다(page가 가리키지 못하는 곳으로 갈 때).
    help를 주면 제목 바로 오른쪽에 물음표가 붙고, 마우스를 올리거나 탭으로 가면 설명이 뜬다(긴 근거 설명을 화면에 늘어놓지 않으려고).
    Streamlit의 help는 물음표를 칸의 맨 오른쪽에 두어 제목과 떨어지므로 직접 그린다(theme.css의 .qhelp)."""
    href = ""
    if page:
        from urllib.parse import urlencode
        sc = data.get_scope()
        q = urlencode({**({"scope": sc} if sc != "서울 전체" else {}), "gu": data.get_selected()}, doseq=True)
        href = f'<a class="card-link" href="./{PAGE_URL.get(page, "")}{"?" + q if q else ""}" target="_self" aria-label="{title} 자세히 보기">자세히 보기<span class="plus">+</span></a>'
    if link:
        href = f'<a class="card-link" href="{link[1]}" target="_self">{link[0]}<span class="plus">+</span></a>'
    tip = qhelp(escape(help), help) if help else ""
    st.markdown(f'<div class="card-head"><span class="card-title-wrap"><span class="card-title" role="heading" aria-level="2">{title}</span>{tip}</span>{href}</div>', unsafe_allow_html=True)
    if sub:
        st.markdown(f'<div class="card-sub">{sub}</div>', unsafe_allow_html=True)


def empty(msg):
    """데이터가 없을 때. st.info 같은 큰 상자는 오류처럼 보여서 회색 한 줄로 쓴다."""
    st.markdown(f'<div class="empty">{msg}</div>', unsafe_allow_html=True)


def station_label(no, name):
    """대여소 표기. 예: '108번 서교동 사거리'"""
    if pd.isna(no):
        return "확인 안 됨"
    return f"{int(no)}번 {name}" if isinstance(name, str) and name else f"{int(no)}번"


def recent_reported_ids():
    """최근 REPORT_DAYS일 안에 고장 신고된 자전거 번호. 점검 권장 목록에서 뺄 때 쓴다."""
    rr = data.load("reported_recent")
    return set() if rr is None else set(rr.bike_id)


# ── 자전거 조회 화면으로 가는 링크 ────────────────────────────
# 표의 자전거번호 칸: 값은 조회 주소(…lookup?bike=74216&n=SPB-74216)이고 화면에는 n= 뒤의 번호만 보인다. 누르면 새 탭으로 열린다
BIKE_COL = st.column_config.LinkColumn("자전거번호", display_text=r"n=(.+)$", help="누르면 이 자전거의 조회 화면이 새 탭으로 열립니다")


def bike_links(ids):
    """자전거 번호들을 표의 링크 칸(BIKE_COL)에 넣을 주소로 바꾼다"""
    return [f"lookup?bike={int(b)}&n=SPB-{int(b):05d}" for b in ids]


# ── 지도 공통 바탕 ─────────────────────────────────────────
@st.cache_data(ttl=3600)
def _map_files():
    """대여소 위치 표와 자치구 경계선 파일. 화면을 다시 그릴 때마다(버튼 하나만 눌러도) 읽지 않도록 한 시간 동안 기억해 둔다"""
    sm_path = data.ROOT / "data/interim/station_master.csv"
    geo = json.load(open(Path(__file__).resolve().parents[1] / "common/seoul_gu.geojson"))
    return (pd.read_csv(sm_path) if sm_path.exists() else None), geo


def gu_layers():
    """지도에 늘 까는 층: 자치구 경계선과 한글 구 이름. 점검 대상 화면과 자전거 조회 화면이 같이 쓴다.
    바탕 지도(carto)는 지명이 영어뿐이라 지명 없는 바탕을 쓰고 구 이름을 직접 얹는다. 구 이름 자리는 그 구 대여소 좌표의 평균이다.
    경계선 자료: app/common/seoul_gu.geojson(통계청 센서스용 행정구역경계 2013, github.com/southkorea/seoul-maps, Apache 2.0)
    반환: (대여소 표, 경계선 층, 구 이름 층). 대여소 위치 파일이 없으면 None"""
    sm, geo = _map_files()
    if sm is None:
        return None
    light = theme.mode() == "light"
    border = pdk.Layer("GeoJsonLayer", geo, id="gu_border",
                       stroked=True, filled=False, get_line_color=[70, 76, 82, 190] if light else [215, 215, 215, 170], line_width_min_pixels=1.6, pickable=False)
    gu_c = sm[sm.district.isin(DISTRICTS)].groupby("district")[["lat", "lon"]].mean().reset_index()
    return sm, border, label_layer(gu_c, "district", "gu_names", 14)


def label_layer(df, col, layer_id, size, offset=0):
    """지도 위 한글 글자 층. 원에 묻히지 않게 글자에 바탕색 테두리를 두른다.
    pdk.types.String: 그냥 문자열로 주면 pydeck이 열 이름(식)으로 읽는다. auto = 쓰인 한글 글자만 그린다"""
    light = theme.mode() == "light"
    return pdk.Layer("TextLayer", df, id=layer_id, get_position="[lon, lat]", get_text=col, get_size=size, get_pixel_offset=[0, offset],
                     get_color=[40, 44, 48, 200] if light else [230, 230, 230, 200], character_set=pdk.types.String("auto"),
                     font_family=pdk.types.String("sans-serif"), font_weight=700, font_settings={"sdf": True}, outline_width=4,
                     outline_color=[255, 255, 255, 230] if light else [20, 20, 20, 230])


REPORT_STATUSES = ["수리 대기", "미조치 의심", "재배치 후 운행"]


def report_status(rep):
    """최근 신고 자전거의 신고 후 상태(추정)를 돌려준다. 점검 대상 화면과 자전거 조회 화면이 같은 기준을 쓴다.
    신고 후 대여가 없으면 수리 대기, 신고 당시 자리에서 다시 대여됐으면 미조치 의심, 다른 곳에서 대여됐으면 재배치 후 운행(docs/notes/07 R13·R18)."""
    same = rep.same_st_after.eq(True) if "same_st_after" in rep else False
    return pd.Series(np.select([rep.rentals_after.astype(float) == 0, same], REPORT_STATUSES[:2], REPORT_STATUSES[2]), index=rep.index)


def exclude_reported(df):
    if df is None:
        return df
    return df[~df.bike_id.isin(recent_reported_ids())]


def bike_rows(df, n=5):
    """점검 권장 자전거 목록. 맨 왼쪽에 순위, 가운데 번호 · 자치구와 최근 반납 대여소(찾으러 갈 곳) · 짧은 선정 사유 두 개
    (전체 사유는 마우스를 올리면), 오른쪽에 평균 대비 배수 · 주행. 줄마다 같은 등급 배지 대신 줄마다 다른 순위와 배수를 보여 준다."""
    rows = []
    for i, r in enumerate(df.head(n).itertuples(), 1):
        full = [x for x in (r.reasons or "").split(" / ") if x]
        reason = " · ".join(texts.short_reason(x).removeprefix("최근 ") for x in full[:2]) or "—"      # 이 목록은 모두 최근 일이라 '최근'은 뺀다
        where = station_label(getattr(r, "last_st", None), getattr(r, "last_st_name", None))
        gu = getattr(r, "district", None)
        loc = f"{gu} · {where}" if isinstance(gu, str) else "위치 확인 안 됨"
        ratio = getattr(r, "risk_ratio", None)
        score = f'평균보다 <b>{ratio:.1f}배</b><div class="loc">주 {r.km_4w / 4:.0f}km 주행</div>' if ratio is not None and ratio == ratio else ""
        rows.append(f'<div class="bike-row"><div class="rank">{i}</div>'
                    f'<div><a class="mono bike-link" href="./lookup?bike={r.bike_id}" target="_self" aria-label="SPB-{r.bike_id:05d} 조회">SPB-{r.bike_id:05d}</a><div class="loc">{loc}</div><div class="reason" title="{" / ".join(full)}">{reason}</div></div>'
                    f'<div class="score">{score}</div></div>')
    st.markdown("".join(rows), unsafe_allow_html=True)


def tilemap(counts, what="점검 권장 자전거 수"):
    """자치구 타일맵(HTML 6 × 6 격자). 색 3단계는 25개 구 값의 3분위로 나눈다. 구를 고르면 나머지 구는 흐리게 한다."""
    vals = [counts.get(g, 0) for g in DISTRICTS]
    q1, q2 = np.quantile(vals, [1 / 3, 2 / 3]) if vals else (0, 0)
    sel = set(data.get_gu())
    pos = {v: k for k, v in SEOUL_TILES.items()}
    cells = []
    for r in range(6):
        for c in range(6):
            g = pos.get((r, c))
            if not g:
                cells.append('<div></div>'); continue
            v = int(round(counts.get(g, 0)))
            p = theme.P()
            bg, fg = (p["primary_light"], "#0F4F3E") if v <= q1 else ((p["primary_mid"], "#0B2E24") if v <= q2 else (p["primary_dark"], "#FFFFFF"))
            name = g[:-1] if g.endswith("구") and len(g) > 2 else g
            cells.append(f'<div class="tile{" dim" if sel and g not in sel else ""}" style="background:{bg};color:{fg}" title="{g} {what} {v:,}">'
                         f'{name}<br><b>{v:,}</b></div>')
    st.markdown(f'<div class="tiles">{"".join(cells)}</div>', unsafe_allow_html=True)
    # 색 세 단계의 기준(25개 구 값의 3분위)을 범례로 적는다
    p, a, b = theme.P(), int(np.floor(q1)), int(np.floor(q2))
    steps = [(p["primary_light"], f"{a:,} 이하"), (p["primary_mid"], f"{a + 1:,} ~ {b:,}"), (p["primary_dark"], f"{b + 1:,} 이상")]
    st.markdown('<div class="legend"><span>' + what + '</span>' + "".join(
        f'<span><i style="width:12px;height:12px;border-radius:3px;background:{c}"></i>{t}</span>' for c, t in steps) + '</div>', unsafe_allow_html=True)


def kind_order(latest):
    """주간 예측을 보여 줄 유형 순서: 예측 건수가 많은 유형부터, '기타'는 늘 맨 끝(무엇이 고장인지 특정할 수 없는 묶음이라).
    latest: part를 인덱스로 한 이번 주 예측 표(pred 열). 예측이 없으면 texts.FC_KINDS 순서 그대로"""
    kinds = [k for k in texts.FC_KINDS if latest is None or (k in latest.index and pd.notna(latest.pred[k]))]
    rest = [k for k in kinds if k != "기타"]
    if latest is not None:
        rest.sort(key=lambda k: -latest.pred[k])
    return rest + [k for k in kinds if k == "기타"]


# ── 예측 정확도 ───────────────────────────────────────────
def track_complete(part="전체", weeks=8):
    tr = data.load("forecast_track")
    if tr is None or tr.empty:
        return None
    t = tr[(tr.part == part) & tr.actual.notna()].sort_values("week").tail(weeks)
    return t if len(t) >= 2 else None


def confidence(part="전체"):
    """forecast_track 최근 완결 8주로 지난주 오차, 8주 평균 오차, 나이브 대비 개선율, 등급을 낸다. 기록이 2주 미만이면 None."""
    t = track_complete(part)
    if t is None:
        return None
    last_err = abs(t.pred.iloc[-1] - t.actual.iloc[-1]) / t.actual.iloc[-1] * 100
    mae8 = (abs(t.pred - t.actual) / t.actual).mean() * 100
    improve = 1 - abs(t.pred - t.actual).mean() / abs(t.naive - t.actual).mean()
    grade = "높음" if improve >= CONF_HIGH else ("보통" if improve >= CONF_MID else "낮음")
    return dict(last_err=last_err, mae8=mae8, improve=improve, grade=grade, weeks=len(t), track=t)


def complete_weeks(wa):
    """weekly_actual에서 7일이 다 찬 주만 남긴다(진행 중인 이번 주는 뺀다)."""
    dr = data.load("daily_reports")
    last = pd.to_datetime(dr.date).max() if dr is not None and len(dr) else pd.to_datetime(wa.week).max()
    w = wa.copy()
    w["week"] = pd.to_datetime(w.week)
    return w[data.shift_days(w.week, 6) <= last]


def footer():
    """화면 아래: 가는 선 위에 작은 글씨 한 줄(비공식 안내 · 마지막 발행 시각), 그 아래 접어 둔 출처·이용 안내"""
    pub = data.status().get("발행 시각")
    when = f" 화면 자료는 {pub:%-m/%-d %H:%M}에 마지막으로 발행했습니다." if pub is not None else ""
    st.markdown(f'<div class="foot">{texts.NOTICE_SHORT}{when}</div>', unsafe_allow_html=True)
    with st.expander("데이터 출처 · 이용 안내"):
        st.caption(texts.DISCLAIMER)
        st.caption(texts.LABEL_NOTE)
        st.caption(texts.SOURCE)
