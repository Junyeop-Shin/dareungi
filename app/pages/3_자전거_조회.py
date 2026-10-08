"""화면 ③ 자전거 조회(시민) — 런북 08 3B-3. 번호 하나로 집계값만 보여 준다(개별 대여 기록·위치·절대 확률은 보여 주지 않는다)"""
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))
from lib import GRADE_ICON, footer, load, status_bar   # noqa: E402

st.set_page_config(page_title="자전거 조회", page_icon="🔎")
st.title("따릉이 자전거 상태 조회")
status_bar()
num = lambda v: 0 if pd.isna(v) else v
txt = lambda v: "없음" if (v is None or (isinstance(v, float) and pd.isna(v)) or v == "") else v

q = st.text_input("자전거 번호", placeholder="예: SPB-12345 또는 12345", max_chars=12)
if q:
    m = re.search(r"\d+", q)
    if not m:
        st.warning("번호의 숫자 부분을 입력해 주세요.")
    else:
        bid = int(m.group())
        risk, card = load("bike_risk_latest"), load("bike_card")
        c = card[card.bike_id == bid] if card is not None else pd.DataFrame()
        r = risk[risk.bike_id == bid] if risk is not None else pd.DataFrame()
        if c.empty or (num(c.iloc[0].trips_30d) == 0 and txt(c.iloc[0].reports_90d) == "없음"):
            st.info("최근 90일 이용 기록이 없는 번호입니다. 번호를 다시 확인해 주세요.")
        else:
            c = c.iloc[0]
            if not r.empty:
                g = r.grade.iloc[0]
                st.subheader(f"점검 필요 가능성: {GRADE_ICON.get(g, g)}")
                st.caption("비슷한 따릉이보다 신고될 가능성이 높은 편인지로 매긴 등급입니다(그 주 전체 자전거 안의 백분위). 고장 여부를 뜻하지 않습니다.")
                reasons = r.reasons.iloc[0]
                if isinstance(reasons, str) and reasons and g != "산정 제외":
                    for s in reasons.split(" / "):
                        st.write(f"· {s}")
            col1, col2, col3 = st.columns(3)
            col1.metric("최근 30일 이용", f"{int(num(c.trips_30d))}회")
            col2.metric("최근 30일 주행", f"{num(c.km_30d):.0f}km")
            col3.metric("마지막 신고 이후 주행", f"{num(c.km_since_rep):.0f}km")
            st.write(f"최근 90일 신고: {txt(c.reports_90d)}")
            st.write(f"도입 후 경과: {'약 ' + str(c.age_years) + '년' if not pd.isna(c.age_years) else '확인 불가(번호 재부여)'}")
            st.success("타기 전에 브레이크와 타이어를 확인하고, 이상하면 따릉이 앱으로 신고해 주세요.")
footer()
