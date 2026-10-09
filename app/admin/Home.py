"""관리자 대시보드 진입점(08a 설계서 1절).

st.navigation으로 홈, 유형별 고장 신고 예측, 점검 대상, 자전거 조회 네 화면을 묶는다. 시민용 조회 앱은 없앴다(2026-10-10).

실행: .venv/bin/streamlit run app/admin/Home.py --server.port 8501
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))
st.set_page_config(page_title="따릉이 정비 현황", layout="wide")

pg = st.navigation([                              # 메뉴는 위쪽에. 사이드바를 쓰면 본문 폭이 줄어든다. 아이콘 없이 글자만(모양은 theme.css의 '위쪽 메뉴')
    st.Page("views/home.py", title="한눈에 보기", default=True),
    st.Page("views/parts_forecast.py", title="유형별 고장 신고 예측"),
    st.Page("views/inspection.py", title="점검 대상"),
    st.Page("views/lookup.py", title="자전거 조회"),
], position="top")
pg.run()
