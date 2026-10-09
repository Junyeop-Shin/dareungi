"""관리자 대시보드 진입점(08a 설계서 1절).

st.navigation으로 홈, 부품별 고장 신고 예상, 점검 대상 세 화면을 묶는다. 시민 조회는 별도 앱(app/citizen/app.py)이다.

실행: .venv/bin/streamlit run app/admin/Home.py --server.port 8501
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))
st.set_page_config(page_title="따릉이 정비 현황", layout="wide")

pg = st.navigation([                              # 메뉴는 위쪽에. 사이드바를 쓰면 본문 폭이 줄어든다
    st.Page("views/home.py", title="한눈에 보기", icon=":material/space_dashboard:", default=True),
    st.Page("views/parts_forecast.py", title="부품별 고장 신고 예상", icon=":material/inventory_2:"),
    st.Page("views/inspection.py", title="점검 대상", icon=":material/build:"),
], position="top")
pg.run()
