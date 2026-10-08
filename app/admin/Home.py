"""관리자 앱 진입점 — 08a 설계서 1절. st.navigation으로 홈 + 상세 ① + 상세 ②를 묶는다. 시민 조회는 별도 앱(app/citizen/app.py).
실행: .venv/bin/streamlit run app/admin/Home.py --server.port 8501"""
import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))
st.set_page_config(page_title="따릉이 정비 현황", page_icon="🚲", layout="wide")

pg = st.navigation([                              # 위쪽 메뉴(사이드바가 화면 폭을 차지하지 않게)
    st.Page("views/home.py", title="한눈에 보기", icon=":material/space_dashboard:", default=True),
    st.Page("views/parts_forecast.py", title="부품별 고장 예측", icon=":material/inventory_2:"),
    st.Page("views/inspection.py", title="점검 대상", icon=":material/build:"),
], position="top")
pg.run()
