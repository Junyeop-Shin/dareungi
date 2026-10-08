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
from common.seoul_tiles import DISTRICTS, SECTORS, SEOUL_TILES   # noqa: E402

CONF_HIGH, CONF_MID = 0.20, 0.0                # 예측 정확도 등급 기준: '지난주와 같다'(나이브) 대비 오차 개선율(08a 9절)
CONF_ICON = {"높음": "✅", "보통": "⚠️", "낮음": "⛔"}
BADGE = {"점검 권장": ("g-insp", "⚠"), "주의": ("g-warn", "ⓘ"), "보통": ("g-norm", "•"), "낮음": ("g-low", "·"), "이용 적음": ("g-low", "–")}


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
    a, b = st.columns([2.3, 1.3], vertical_alignment="center")
    with a:
        st.segmented_control("권역", SCOPES, key="gu_scope", on_change=_scope_changed, label_visibility="collapsed")
    with b:
        n = len(st.session_state["gu_sel"])
        label = f"구 선택 · {n}곳" if n and not (sc != "서울 전체" and n == len(SECTORS[sc])) else ("구 선택" if sc == "서울 전체" else f"구 선택 · {sc} 전체")
        with st.popover(label, icon=":material/tune:", use_container_width=True):
            hint = "아무것도 안 고르면 서울 전체" if sc == "서울 전체" else f"아무것도 안 고르면 {sc} 전체"
            # 목록을 늘 펼쳐 두려고 multiselect 대신 pills를 쓴다. 다 비워도 구 이름이 그대로 보여 다시 고르기 쉽다
            st.pills(f"{sc} 자치구 ({hint})", _pool(sc), selection_mode="multi", key="gu_sel", on_change=_sel_changed)
            st.button("모두 비우기", on_click=_clear, use_container_width=True, disabled=not n, icon=":material/remove_done:")
            if sc != "서울 전체":
                st.button(f"{sc} 모두 선택", on_click=_select_all, use_container_width=True, disabled=n == len(SECTORS[sc]), icon=":material/done_all:")
    st.markdown(f'<div class="gu-picked">보는 지역: {scope_label()}</div>', unsafe_allow_html=True)


def week_so_far(monday):
    """이번 주 월요일부터 어제까지 실제 신고(서울 전체, 부품별). 반환: ({부품: 건수}, 마지막 날짜) 또는 (None, None)"""
    dr = data.load("daily_reports")
    if dr is None or dr.empty:
        return None, None
    d = dr[pd.to_datetime(dr.date) >= pd.Timestamp(monday)]
    if d.empty:
        return None, None
    return d.groupby("part").reports.sum().to_dict(), pd.to_datetime(d.date).max()


def scope_label():
    sc, gus = data.get_scope(), data.get_gu()
    if not gus:
        return "서울 전체"
    if sc != "서울 전체" and sorted(gus) == sorted(SECTORS[sc]):
        return f"{sc} 전체 ({len(gus)}개 구)"
    return ", ".join(gus) if len(gus) <= 3 else f"{gus[0]} 외 {len(gus) - 1}곳"


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
    링크를 HTML로 직접 그려야 오른쪽 끝에 정확히 붙는다. 고른 권역·자치구는 주소(?scope=, ?gu=)에 실어 다음 화면으로 넘긴다."""
    href = ""
    if page:
        from urllib.parse import urlencode
        sc = data.get_scope()
        q = urlencode({**({"scope": sc} if sc != "서울 전체" else {}), "gu": data.get_selected()}, doseq=True)
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
        ratio = getattr(r, "risk_ratio", None)
        score = f'<div class="loc">평균의 {ratio:.1f}배<br>주 {r.km_4w / 4:.0f}km 주행</div>' if ratio is not None and ratio == ratio else ""
        rows.append(f'<div class="bike-row"><div><div class="mono">SPB-{r.bike_id:05d}</div><div class="loc">{loc}</div></div>'
                    f'<div>{grade_badge_html(r.grade)}{score}</div><span class="reason" title="{reason}">{reason}</span></div>')
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
            v = int(round(counts.get(g, 0)))
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
