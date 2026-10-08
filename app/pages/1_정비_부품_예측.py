"""화면 ① 정비·부품 예측(정비센터 관리자) — 런북 08 3B-1. 부품별 다음 7일 예측·구간·준비 수량·인력 계산기·정확도 추적"""
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))
from lib import LABEL_NOTE, footer, load, status_bar   # noqa: E402

st.set_page_config(page_title="정비·부품 예측", page_icon="📦", layout="wide")
st.title("정비·부품 예측 — 다음 7일")
status_bar()
st.caption(LABEL_NOTE)

fc = load("forecast_weekly")
PARTS = ["타이어", "체인", "안장", "페달", "전체"]
if fc is None or fc.empty or fc.pred.isna().all():
    note = fc.note.iloc[0] if fc is not None and len(fc) else "예측 스냅샷이 없습니다."
    st.warning(f"주간 예측이 아직 없습니다: {note}")
    st.markdown("예측은 API로 모은 **일별 자료가 35일 이상** 쌓이면 매주 월요일 산출됩니다(최근 4주 신고율 × 지난주 대여량, 80% 구간). "
                "그 뒤 기상청 예보를 쓰는 음이항 모형으로 바꿉니다.")
else:
    latest = fc[fc.issue_date == fc.issue_date.max()].set_index("part").reindex(PARTS)
    st.caption(f"예측 발행 {pd.to_datetime(fc.issue_date.max()):%m/%d} · 방법: {latest.method.iloc[0]}")
    cols = st.columns(5)
    for c, p in zip(cols, PARTS):
        r = latest.loc[p]
        c.metric(p, f"{r.pred:,.0f}건", help=f"80% 구간 {r.lo80:,.0f} ~ {r.hi80:,.0f}")
    st.subheader("준비 수량")
    tab = pd.DataFrame({"부품": PARTS, "예측": latest.pred.round(0).values, "80% 구간": [f"{a:,.0f} ~ {b:,.0f}" for a, b in zip(latest.lo80, latest.hi80)],
                        "권장 확보 수량(구간 상단)": latest.hi80.round(0).values})
    st.dataframe(tab, hide_index=True, width='stretch')
    st.caption("권장 확보 수량을 80% 구간 상단으로 잡으면 결품 가능성이 약 10%입니다. 신고 건수 기준이라 실제 수거(신고의 약 2배)보다 작게 나옵니다.")

    st.subheader("인력 계산기")
    c1, c2 = st.columns(2)
    minutes = c1.number_input("대당 평균 수리 시간(분)", 5, 120, 30, help="현장 보도: 한 대 10분 ~ 1시간 이상")
    hours_pp = c2.number_input("정비원 1명의 주당 수리 가능 시간", 10, 60, 30)
    total = latest.loc["전체"].pred
    need = total * minutes / 60 / hours_pp
    st.metric("필요 인원(주)", f"{need:.1f}명", help=f"{total:,.0f}건 × {minutes}분 ÷ 60 ÷ {hours_pp}시간")
    st.caption("신고 건수 기준이라 실제 수거(신고의 약 2배)보다 작게 나옵니다. 수거 기준으로 보려면 두 배로 읽으세요.")

    track = load("forecast_track")
    st.subheader("예측을 믿어도 되나 — 최근 8주")
    if track is not None and len(track):
        st.dataframe(track, hide_index=True, width='stretch')
        ch = alt.Chart(track).mark_line(point=True).encode(x="week:T", y="actual:Q", color="part:N")
        st.altair_chart(ch, width='stretch')
    else:
        st.write("예측 실적이 8주 이상 쌓이면 부품별 오차율과 나이브 대비 개선율을 여기에 보여 줍니다. "
                 "참고(과거 테스트, 2025-01 ~ 2026-06): 구조 나이브의 주간 오차율 12 ~ 15%, 단순 나이브 16 ~ 19%.")
footer()
