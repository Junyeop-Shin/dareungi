"""관리자 앱 화면들이 같이 쓰는 조각(08a 설계서 3절).

머리 영역(제목·기준 시각·자치구 필터), KPI, 카드 제목, 등급 배지, 자전거 목록, 자치구 타일맵, 예측 정확도 계산, 고지문.
빈 상태는 오류처럼 보이지 않게 회색 한 줄로, 고지문은 접어서 둔다.
상태 표시는 색만으로 구분하지 않고 글자와 아이콘을 같이 쓴다(색각 이상 배려).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import data, theme, texts            # noqa: E402
from common.seoul_tiles import DISTRICTS, SEOUL_TILES   # noqa: E402

QUICK_GU = ["강남구", "송파구", "마포구"]       # 머리 영역에 버튼으로 바로 보이는 자치구. 나머지는 '구 선택'에서
CONF_HIGH, CONF_MID = 0.20, 0.0                # 예측 정확도 등급 기준: '지난주와 같다'(나이브) 대비 오차 개선율(08a 9절)
CONF_ICON = {"높음": "✅", "보통": "⚠️", "낮음": "⛔"}
BADGE = {"점검 권장": ("g-insp", "⚠"), "주의": ("g-warn", "ⓘ"), "보통": ("g-norm", "•"), "낮음": ("g-low", "·"), "이용 적음": ("g-low", "–")}


# ── 자치구 필터 ───────────────────────────────────────────
# 버튼은 '서울 전체'와 자주 쓰는 3개 구만 두고, 나머지는 '구 선택' 팝오버에서 고른다.
# 고른 구가 많아도 버튼이 늘어나지 않도록 고른 구 이름은 버튼 아래 한 줄로 보여 준다.
def _pills_changed():
    sel = [g for g in (st.session_state.get("gu_pills") or []) if g != "서울 전체"]
    prev = data.get_gu()
    if "서울 전체" in (st.session_state.get("gu_pills") or []) and prev:
        gus = []                                                      # 구를 고른 상태에서 '서울 전체'를 누르면 초기화
    else:
        gus = sel + [g for g in prev if g not in QUICK_GU]            # 팝오버에서 고른 다른 구는 유지
    data.set_gu(gus)
    st.session_state["gu_more"] = gus


def _more_changed():
    data.set_gu(st.session_state.get("gu_more") or [])


def gu_filter():
    gus = data.get_gu()
    st.session_state["gu_pills"] = [g for g in gus if g in QUICK_GU] or ([] if gus else ["서울 전체"])
    st.session_state["gu_more"] = gus
    a, b = st.columns([3.2, 1.3], vertical_alignment="center")
    with a:
        st.pills("자치구", ["서울 전체"] + QUICK_GU, selection_mode="multi", key="gu_pills", on_change=_pills_changed, label_visibility="collapsed")
    with b:
        with st.popover(f"구 선택 · {len(gus)}곳" if gus else "구 선택", icon=":material/tune:", use_container_width=True):
            st.multiselect("볼 자치구(여러 개 가능, 비우면 서울 전체)", DISTRICTS, key="gu_more", on_change=_more_changed, placeholder="자치구 이름")
    if gus:
        st.markdown(f'<div class="gu-picked">보는 지역: {", ".join(gus)}</div>', unsafe_allow_html=True)


def scope_label():
    gus = data.get_gu()
    return "서울 전체" if not gus else (", ".join(gus) if len(gus) <= 3 else f"{gus[0]} 외 {len(gus) - 1}곳")


# ── 머리 영역 ─────────────────────────────────────────────
def status_line():
    s = data.status()
    parts = []
    if "대여이력" in s: parts.append(f"대여 {s['대여이력']:%-m/%-d %-H}시")
    if "고장신고" in s: parts.append(f"신고 {s['고장신고']:%-m/%-d}")
    if "주간 예측" in s: parts.append(f"예측 {s['주간 예측']:%-m/%-d}({'월화수목금토일'[s['주간 예측'].weekday()]}) 발행")
    return ("기준 " + " · ".join(parts)) if parts else "데이터를 불러오지 못했습니다"


def header(title, show_confidence=False, show_filter=True):
    theme.apply()
    left, right = st.columns([1.1, 1], vertical_alignment="top")
    with left:
        st.markdown(f'<div class="page-title">{title}</div>', unsafe_allow_html=True)
        st.markdown(f":material/schedule: <span class='status-line'>{status_line()}</span>", unsafe_allow_html=True)
    with right:
        if show_filter:
            gu_filter()
        if show_confidence:
            c = confidence()
            if c:
                st.markdown(f"<div style='text-align:right' class='status-line'>{CONF_ICON[c['grade']]} 예측 정확도 <b>{c['grade']}</b> · "
                            f"최근 {c['weeks']}주 예측과 실제의 평균 차이 {c['mae8']:.0f}%</div>", unsafe_allow_html=True, help=texts.CONFIDENCE_HELP)
    for name, days in data.stale_items(data.status()):
        st.warning(f"{name} 데이터가 {days}일째 갱신되지 않았습니다. 숫자가 최신이 아닐 수 있어요.", icon=":material/history:")
    return data.get_gu()


# ── 카드·KPI ──────────────────────────────────────────────
def kpi(label, value, sub="", value_color=None, sub_color=None, help=None):
    vc = f' style="color:{value_color}"' if value_color else ""
    sc = f' style="color:{sub_color}"' if sub_color else ""
    tip = f' title="{help}"' if help else ""
    st.markdown(f'<div class="kpi-label"{tip}>{label}</div><div class="kpi-value"{vc}>{value}</div><div class="kpi-sub"{sc}>{sub}</div>',
                unsafe_allow_html=True)


PAGE_URL = {"views/parts_forecast.py": "parts_forecast", "views/inspection.py": "inspection", "views/home.py": ""}


def card_title(title, page=None, link="자세히", sub=None):
    """카드 제목(왼쪽)과 이동 링크(오른쪽 끝)를 한 줄에 두고, 그 아래 무엇을 세는지 한 줄(sub)을 붙인다.
    링크를 HTML로 직접 그려야 오른쪽 끝에 정확히 붙는다. 고른 자치구는 주소(?gu=)에 실어 다음 화면으로 넘긴다."""
    href = ""
    if page:
        from urllib.parse import urlencode
        q = urlencode({"gu": data.get_gu()}, doseq=True)
        href = f'<a class="card-link" href="./{PAGE_URL.get(page, "")}{"?" + q if q else ""}" target="_self">{link} →</a>'
    st.markdown(f'<div class="card-head"><span class="card-title">{title}</span>{href}</div>', unsafe_allow_html=True)
    if sub:
        st.markdown(f'<div class="card-sub">{sub}</div>', unsafe_allow_html=True)


def empty(msg):
    """데이터가 없을 때. st.info 같은 큰 상자는 오류처럼 보여서 회색 한 줄로 쓴다."""
    st.markdown(f'<div class="empty">{msg}</div>', unsafe_allow_html=True)


def grade_badge_html(g):
    g = texts.grade_name(g)
    cls, icon = BADGE.get(g, ("g-low", ""))
    return f'<span class="gbadge {cls}">{icon} {g}</span>'


def grade_badge(g):
    g = texts.grade_name(g)
    return f"{texts.GRADE_ICON.get(g, '')} {g}"


def station_label(no, name):
    """대여소 표기. 예: '108번 서교동 사거리'"""
    if pd.isna(no):
        return "확인 안 됨"
    return f"{int(no)}번 {name}" if isinstance(name, str) and name else f"{int(no)}번"


def recent_reported_ids():
    """최근 REPORT_DAYS일 안에 고장 신고된 자전거 번호. 점검 권장 목록에서 뺄 때 쓴다."""
    rr = data.load("reported_recent")
    return set() if rr is None else set(rr.bike_id)


def exclude_reported(df):
    if df is None:
        return df
    return df[~df.bike_id.isin(recent_reported_ids())]


def bike_rows(df, n=5):
    """먼저 점검할 자전거 목록. 한 줄에 번호 · 등급 · 첫 번째 이유, 둘째 줄에 마지막 반납 대여소(찾으러 갈 곳)."""
    rows = []
    for r in df.head(n).itertuples():
        reason = (r.reasons or "").split(" / ")[0] or "—"
        where = station_label(getattr(r, "last_st", None), getattr(r, "last_st_name", None))
        gu = getattr(r, "district", None)
        loc = f"마지막 반납 {where} · {gu}" if isinstance(gu, str) else "마지막 반납 위치 확인 안 됨"
        rows.append(f'<div class="bike-row"><div><div class="mono">SPB-{r.bike_id:05d}</div><div class="loc">{loc}</div></div>'
                    f'{grade_badge_html(r.grade)}<span class="reason" title="{reason}">{reason}</span></div>')
    st.markdown("".join(rows), unsafe_allow_html=True)


def tilemap(counts, what="점검 권장 자전거 수"):
    """자치구 타일맵(HTML 6 × 6 격자). 색 3단계는 25개 구 값의 3분위로 나누고, 고른 구는 테두리로 표시한다."""
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
            v = counts.get(g, 0)
            p = theme.P()
            bg, fg = (p["primary_light"], "#0F4F3E") if v <= q1 else ((p["primary_mid"], "#0B2E24") if v <= q2 else (p["primary_dark"], "#FFFFFF"))
            name = g[:-1] if g.endswith("구") and len(g) > 2 else g
            cells.append(f'<div class="tile{" sel" if g in sel else ""}" style="background:{bg};color:{fg}" title="{g} {what} {v:,}">'
                         f'{name}<br><b>{v:,}</b></div>')
    st.markdown(f'<div class="tiles">{"".join(cells)}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="card-caption" style="margin-top:10px">숫자 = {what} · 색이 진할수록 많음 · 파란 테두리 = 고른 구</div>', unsafe_allow_html=True)


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


def improve_text(improve):
    word = "더 가까움" if improve >= 0 else "더 멂"
    return f"'지난주와 같다'고 봤을 때보다 실제에 {abs(improve) * 100:.0f}% {word}"


def complete_weeks(wa):
    """weekly_actual에서 7일이 다 찬 주만 남긴다(진행 중인 이번 주는 뺀다)."""
    dr = data.load("daily_reports")
    last = pd.to_datetime(dr.date).max() if dr is not None and len(dr) else pd.to_datetime(wa.week).max()
    w = wa.copy()
    w["week"] = pd.to_datetime(w.week)
    return w[data.shift_days(w.week, 6) <= last]


def footer():
    st.write("")
    st.markdown(f'<div class="card-caption">{texts.NOTICE_SHORT}</div>', unsafe_allow_html=True)
    with st.expander("데이터 출처 · 이용 안내"):
        st.caption(texts.DISCLAIMER)
        st.caption(texts.LABEL_NOTE)
        st.caption(texts.SOURCE)
