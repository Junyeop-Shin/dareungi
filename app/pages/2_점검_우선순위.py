"""화면 ② 점검 우선순위(공단·정비 담당) — 런북 08 3B-2. 위험 등급 목록, 마지막 반납 대여소 지도, 헛대여 경보, CSV 내려받기"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))
from lib import GRADE_ORDER, LABEL_NOTE, ROOT, footer, load, status_bar   # noqa: E402

st.set_page_config(page_title="점검 우선순위", page_icon="🔧", layout="wide")
st.title("점검 우선순위")
status_bar()
st.caption(LABEL_NOTE + " 등급은 그 주 백분위(점검 권장 = 상위 5%, 주의 = 상위 20%). '산정 제외'는 최근 4주 이용이 없어 순위에 넣지 않은 자전거.")

risk = load("bike_risk_latest")
if risk is None:
    st.stop()
risk = risk.copy()
risk["district"] = risk.district.fillna("(위치 미상)")

c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
districts = c1.multiselect("자치구", sorted(risk.district.unique()), default=[])
part = c2.selectbox("부품", ["전체", "타이어", "체인", "안장", "페달"])
grades = c3.multiselect("등급", GRADE_ORDER, default=["점검 권장", "주의"])
top = c4.selectbox("표시 대수", [50, 200, 1000], index=1)
score_col = {"전체": "rate_1000km", "타이어": "rate_tire", "체인": "rate_chain", "안장": "rate_saddle", "페달": "rate_pedal"}[part]

v = risk[risk.grade.isin(grades)] if grades else risk
if districts:
    v = v[v.district.isin(districts)]
v = v.sort_values(score_col, ascending=False).head(top)
show = v[["bike_id", "grade", score_col, "expected_reports", "reasons", "km_since_rep", "rep_13w", "sham_2w", "last_st_name", "district"]].rename(columns={
    "bike_id": "자전거번호", "grade": "등급", score_col: f"{part} 위험률(1,000km당)", "expected_reports": "기대 신고 수", "reasons": "사유",
    "km_since_rep": "마지막 신고 이후 km", "rep_13w": "최근 13주 신고", "sham_2w": "최근 2주 헛대여", "last_st_name": "마지막 반납 대여소", "district": "자치구"})
st.dataframe(show.round(2), width='stretch', hide_index=True, height=420)
st.download_button("현재 목록 CSV 내려받기", show.to_csv(index=False).encode("utf-8-sig"), file_name=f"점검목록_{part}.csv", mime="text/csv")

st.subheader("마지막 반납 대여소별 '점검 권장' 대수")
st.caption("순회 동선 짜기용. 자전거는 그 뒤 옮겨졌을 수 있습니다.")
sm = pd.read_csv(ROOT / "data/interim/station_master.csv") if (ROOT / "data/interim/station_master.csv").exists() else None
if sm is not None:
    pts = risk[risk.grade == "점검 권장"].groupby("last_st").size().rename("n").reset_index()
    pts = pts.merge(sm[["st_no", "st_name", "lat", "lon", "district"]], left_on="last_st", right_on="st_no").dropna(subset=["lat", "lon"])
    if districts:
        pts = pts[pts.district.isin(districts)]
    import pydeck as pdk
    layer = pdk.Layer("ScatterplotLayer", pts, get_position="[lon, lat]", get_radius="60 + n * 40", get_fill_color="[220, 60, 60, 160]", pickable=True)
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=pdk.ViewState(latitude=37.55, longitude=126.98, zoom=10.3),
                             tooltip={"text": "{st_name}\n점검 권장 {n}대"}, map_style=None))
    st.dataframe(pts.sort_values("n", ascending=False)[["st_name", "district", "n"]].head(20).rename(columns={"st_name": "대여소", "district": "자치구", "n": "점검 권장 대수"}), hide_index=True)

st.subheader("헛대여 경보 (최근 48시간) — '지금 고장일 가능성' 신호")
alerts = load("alerts_live")
if alerts is not None and len(alerts):
    a = alerts[alerts.alert_ts >= alerts.alert_ts.max() - pd.Timedelta(hours=48)].copy()
    if districts:
        a = a[a.district.isin(districts)]
    st.dataframe(a.rename(columns={"bike_id": "자전거번호", "alert_ts": "경보 시각", "st_name": "마지막 반납 대여소", "district": "자치구",
                                   "n_sham_7d": "7일 헛대여 수", "reported_after": "이후 신고됨"}).drop(columns=["last_st"]), hide_index=True, width='stretch')
    st.caption("같은 대여소에 2 ~ 3분 안에 연속 두 번 반납된 자전거. 검증에서 46%가 7일 안에 신고로 이어졌습니다(무작위의 5배).")
else:
    st.write("경보 없음")
footer()
