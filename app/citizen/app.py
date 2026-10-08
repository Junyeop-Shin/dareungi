"""시민 조회 앱(별도 주소) — 08a 설계서 1절, 08 문서 3B-3. 휴대폰에서 대여소 앞에서 보는 화면이라 짧고 크게.
번호 하나로 집계값만 보여 준다. 읽는 것: bike_card, bike_risk_latest(등급·이유만), data_status.
관리자 전용 정보(마지막 반납 대여소·위험도 숫자)는 읽지 않는다.
실행: .venv/bin/streamlit run app/citizen/app.py --server.port 8502"""
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import data, texts, theme    # noqa: E402

st.set_page_config(page_title="따릉이 자전거 조회", page_icon="🚲", layout="centered")
p = theme.apply(top_pad="1.6rem")                    # 시민 앱은 위쪽 메뉴가 없다
st.markdown(f"""<style>
.c-title {{ font-size: 1.6rem; font-weight: 800; margin-bottom: 2px; }}
.c-sub {{ color: {p['muted']}; font-size: 0.95rem; margin-bottom: 14px; }}
.c-grade {{ font-size: 1.35rem; font-weight: 800; margin: 4px 0 6px; }}
.c-row {{ display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid {p['border']}; font-size: 1rem; }}
.c-row:last-child {{ border-bottom: none; }}
.c-row span:first-child {{ color: {p['muted']}; white-space: nowrap; }}
.c-row span:last-child {{ text-align: right; }}
</style>""", unsafe_allow_html=True)
GRADE_TEXT = {
    "점검 권장": "비슷한 따릉이보다 고장 신고가 나올 가능성이 높은 편이에요.",
    "주의": "비슷한 따릉이보다 고장 신고 가능성이 조금 높은 편이에요.",
    "보통": "다른 따릉이와 비슷한 편이에요.",
    "낮음": "다른 따릉이보다 고장 신고 가능성이 낮은 편이에요.",
    "이용 적음": "최근 이용이 적어서 등급을 매기지 않았어요.",
}

st.markdown('<div class="c-title">🚲 자전거 상태 조회</div>', unsafe_allow_html=True)
st.markdown('<div class="c-sub">자전거에 적힌 번호(SPB-로 시작)를 넣으면 최근 이용과 고장 신고 기록을 볼 수 있어요.</div>',
            unsafe_allow_html=True)
for name, days in data.stale_items(data.status()):
    st.warning(f"{name} 기록이 {days}일째 갱신되지 않았어요.")

with st.form("q", border=False):
    a, b = st.columns([3, 1], vertical_alignment="bottom")
    q = a.text_input("자전거 번호", placeholder="예: 12345", max_chars=12, label_visibility="collapsed")
    go_ = b.form_submit_button("조회", use_container_width=True, type="primary")

num = lambda v: 0 if pd.isna(v) else v
if q:
    m = re.search(r"\d+", q)
    if not m:
        st.warning("번호의 숫자 부분을 넣어 주세요. 예: SPB-12345 → 12345")
    else:
        bid = int(m.group())
        card = data.load("bike_card")
        risk = data.load("bike_risk_latest")
        risk = risk[["bike_id", "grade", "reasons"]] if risk is not None else None          # 등급·이유만
        c = card[card.bike_id == bid] if card is not None else pd.DataFrame()
        r = risk[risk.bike_id == bid] if risk is not None else pd.DataFrame()
        reports = None if c.empty else c.iloc[0].reports_90d
        if c.empty or (num(c.iloc[0].trips_30d) == 0 and not isinstance(reports, str)):
            st.info(f"SPB-{bid:05d}의 최근 90일 기록이 없어요. 번호를 다시 확인해 주세요.")
        else:
            c = c.iloc[0]
            with st.container(border=True):
                st.markdown(f'<div class="mono" style="color:{p["muted"]}">SPB-{bid:05d}</div>', unsafe_allow_html=True)
                if not r.empty:
                    g = texts.grade_name(r.grade.iloc[0])
                    st.markdown(f'<div class="c-grade">{texts.GRADE_ICON.get(g, "")} {g}</div><div>{GRADE_TEXT.get(g, "")}</div>', unsafe_allow_html=True)
                    reasons = r.reasons.iloc[0]
                    if isinstance(reasons, str) and reasons and g not in ("이용 적음", "낮음"):
                        st.markdown("<div class='card-caption' style='margin-top:8px'>이렇게 본 이유</div>" +
                                    "".join(f"<div>· {x}</div>" for x in reasons.split(" / ")), unsafe_allow_html=True)
                age = f"약 {c.age_years:g}년" if not pd.isna(c.age_years) else "확인 어려움"
                rows = [("최근 30일 이용", f"{int(num(c.trips_30d))}회 · {num(c.km_30d):.0f}km"),
                        ("마지막 고장 신고 뒤 달린 거리", f"{num(c.km_since_rep):.0f}km" if not pd.isna(c.km_since_rep) else "—"),
                        ("최근 90일 고장 신고(날짜·부품)", reports if isinstance(reports, str) and reports else "없음"),
                        ("운행 시작 후", age)]
                st.markdown("".join(f'<div class="c-row"><span>{k}</span><span>{v}</span></div>' for k, v in rows), unsafe_allow_html=True)
            st.success("타기 전에 브레이크와 타이어를 한 번 확인하고, 이상하면 따릉이 앱에서 신고해 주세요.", icon=":material/pedal_bike:")
            st.caption("등급은 이용자들이 남긴 고장 신고 기록으로 추정한 것이라, 신고되지 않은 고장은 반영되지 않아요.")

s = data.status()
if "대여이력" in s:
    st.caption(f"이용 기록 {s['대여이력']:%-m/%-d %-H}시까지 · 신고 기록 {s.get('고장신고', s['대여이력']):%-m/%-d}까지")
st.caption(texts.NOTICE_SHORT)
with st.expander("데이터 출처 · 이용 안내"):
    st.caption(texts.DISCLAIMER)
    st.caption(texts.SOURCE)
