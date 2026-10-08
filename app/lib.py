"""대시보드 공통 — 단계 3B (런북 08)

[무엇을] serving/*.parquet 스냅샷을 읽고(10분 캐시), 화면 공통 문구와 기준 시각 표시를 제공한다.
[원칙]  앱은 DB를 열지 않는다(수집기와 잠금 충돌). 인증키를 쓰지 않는다. 스냅샷이 없거나 48시간 넘게 오래되면 경고를 띄운다.
[실행]  .venv/bin/streamlit run app/Home.py   (저장소 루트에서)
"""
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SERVING = ROOT / "serving"

DISCLAIMER = ("이 서비스는 서울시 공공데이터로 만든 **비공식 참고 정보**입니다. 서울특별시·서울시설공단의 공식 서비스가 아니며, "
              "자전거의 안전을 보증하지 않습니다. 등급은 이용자 고장신고 기록을 바탕으로 한 통계적 추정이며, 신고되지 않은 고장은 반영되지 않습니다. "
              "이상이 있으면 따릉이 앱으로 신고해 주세요.")
SOURCE = ("본 서비스는 서울특별시에서 작성하여 공공누리 제1유형으로 개방한 '서울특별시 공공자전거 대여이력 정보', "
          "'서울특별시 공공자전거 고장신고 내역'을 이용하였으며, 해당 저작물은 서울 열린데이터광장(https://data.seoul.go.kr)에서 무료로 내려받을 수 있습니다. "
          "날씨: 기상청(공공데이터포털).")
LABEL_NOTE = "이용자 고장신고 기록 기준입니다. 실제 수거·정비는 신고보다 많습니다(약 2배)."
GRADE_ORDER = ["점검 권장", "주의", "보통", "낮음", "산정 제외"]
GRADE_ICON = {"점검 권장": "🔴 점검 권장", "주의": "🟠 주의", "보통": "🟡 보통", "낮음": "⚪ 낮음", "산정 제외": "— 산정 제외(최근 4주 이용 없음)"}


@st.cache_data(ttl=600)
def load(name):
    p = SERVING / f"{name}.parquet"
    if not p.exists():
        return None
    return pd.read_parquet(p)


def status_bar():
    """기준 시각 띠. 스냅샷이 없거나 48시간 넘게 오래되면 경고"""
    s = load("data_status")
    if s is None:
        st.error("스냅샷(serving/)이 없습니다. 수집·발행 작업(py/jobs)을 먼저 실행하세요.")
        return
    d = dict(zip(s.data, s.latest))
    pub = pd.to_datetime(d.get("발행 시각")).tz_localize(None) if d.get("발행 시각") else None
    age_h = (pd.Timestamp.now() - pub).total_seconds() / 3600 if pub is not None else None
    parts = [f"대여 데이터 {pd.to_datetime(d['대여이력']):%m/%d %H시}까지" if d.get("대여이력") else None,
             f"고장신고 {pd.to_datetime(d['고장신고']):%m/%d}까지" if d.get("고장신고") else None,
             f"위험 점수 {pd.to_datetime(d['위험 점수']):%m/%d} 주" if d.get("위험 점수") else None,
             f"발행 {pub:%m/%d %H:%M}" if pub is not None else None]
    st.caption(" · ".join(p for p in parts if p))
    if age_h is not None and age_h > 48:
        st.warning(f"스냅샷이 {age_h / 24:.0f}일 전 것입니다. 수집 작업이 멈췄을 수 있습니다.")


def footer():
    st.divider()
    st.caption(DISCLAIMER)
    st.caption(SOURCE)
