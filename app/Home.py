"""따릉이 정비 대시보드 — 첫 화면 (런북 08). 실행: .venv/bin/streamlit run app/Home.py"""
import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent))
from lib import LABEL_NOTE, footer, load, status_bar   # noqa: E402

st.set_page_config(page_title="따릉이 정비 참고 정보", page_icon="🚲", layout="wide")
st.title("🚲 따릉이 정비 참고 정보")
status_bar()

st.markdown("""
서울시 공공데이터(대여이력·고장신고)로 만든 **비공식** 참고 화면입니다. 왼쪽 메뉴에서 고르세요.

| 화면 | 누구에게 | 무엇을 |
|---|---|---|
| **① 정비·부품 예측** | 정비센터 관리자 | 부품별 다음 7일 신고 예측과 준비 수량, 인력 계산 |
| **② 점검 우선순위** | 정비·순회 담당 | 자전거별 점검 권장 등급과 이유, 마지막 반납 대여소, 헛대여 경보 |
| **③ 자전거 조회** | 시민 | 번호 하나로 그 자전거의 등급·최근 이용·신고 이력(집계값만) |
""")
st.info(LABEL_NOTE)

risk, alerts = load("bike_risk_latest"), load("alerts_live")
c1, c2, c3 = st.columns(3)
if risk is not None:
    c1.metric("점검 권장 자전거", f"{int((risk.grade == '점검 권장').sum()):,}대", help="그 주 위험률 상위 5%")
    c2.metric("점수 산정 자전거", f"{int((risk.grade != '산정 제외').sum()):,}대")
if alerts is not None:
    c3.metric("최근 48시간 헛대여 경보", f"{int((alerts.alert_ts >= alerts.alert_ts.max() - __import__('pandas').Timedelta(hours=48)).sum()):,}건" if len(alerts) else "0건")

st.markdown("""
**어떻게 만들었나(요약)**
- 신고 건수 = 이용량 × 신고율로 나눠 보고, 자전거 한 대의 주 단위 상태(이용·마모·이력)로 다음 주 신고 위험을 추정합니다.
- 위험 등급은 그 주 전체 자전거 안의 **백분위**(점검 권장 = 상위 5%)입니다. 절대 확률이 아니며 '안전' 등급은 없습니다.
- 헛대여 경보(같은 대여소에 2 ~ 3분 안에 연속 두 번 반납)는 7일 안 신고로 이어진 비율이 46%(무작위의 5배)였습니다.
- 선행 서비스(RIDEY)가 실시간 헛대여 경보와 번호 조회를 제공합니다. 이 화면의 차이는 부품별 위험, 정비 수요 예측, 신고 기록으로 검증한 결과입니다.
""")
footer()
