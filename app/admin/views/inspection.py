"""점검 대상 화면(08a 설계서 5절).

지역은 정비 구역(강남·강북) → 자치구 순서로 고른다. 다른 화면은 자치구 버튼을 그대로 쓴다.
목록은 셋으로 나눈다.
  ① 빌리자마자 반납이 반복된 자전거: 지금 고장일 가능성이 높다
  ② 최근 REPORT_DAYS일 안에 고장 신고된 자전거: 수리 대기로 보임 / 다시 운행 중
  ③ 점검 권장: 최근 신고가 없는 자전거 중 위험 순위 상위
④ 지도는 마지막 반납 대여소를 원으로 그린다. 원에 마우스를 올리면 대여소 번호·이름이, 누르면 아래에
그 대여소의 자전거 목록이 나온다. 점검 권장과 최근 신고를 바꿔 볼 수 있다.
이 화면의 순위는 예측 모형이 아니라서 예측 정확도는 보여 주지 않는다.
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[2]))
from admin import components as ui       # noqa: E402
from common import data, texts, theme    # noqa: E402
from common.seoul_tiles import DISTRICTS, SECTORS   # noqa: E402

ui.header("점검 대상", show_filter=False)
p = theme.P()
risk, alerts, rep = data.load("bike_risk_latest"), data.load("alerts_live"), data.load("reported_recent")
s = data.status()
basis = f"{s['대여이력']:%-m월 %-d일 %-H시}" if "대여이력" in s else "최근"

# ── 지역: 정비 구역 → 자치구 ──────────────────────────────
home_gu = data.get_gu()
default_sector = "전체"
if home_gu and all(g in SECTORS["강남"] for g in home_gu):
    default_sector = "강남"
elif home_gu and all(g in SECTORS["강북"] for g in home_gu):
    default_sector = "강북"
with st.container(border=True):
    a, b = st.columns([1, 3], vertical_alignment="bottom")
    sector = a.segmented_control("정비 구역", ["전체", "강남", "강북"], default=default_sector, key="insp_sector",
                                 help="따릉이 정비는 강남 6곳·강북 5곳 센터가 나눠 맡습니다. 여기서는 한강을 기준으로 나눴습니다.") or "전체"
    pool = DISTRICTS if sector == "전체" else SECTORS[sector]
    gus = b.multiselect(f"자치구 ({'서울 전체' if sector == '전체' else sector + ' ' + str(len(pool)) + '개 구'} 중에서 고르기, 비워 두면 전부)",
                        pool, default=[g for g in home_gu if g in pool], key=f"insp_gu_{sector}", placeholder="전부")
area = gus or pool
area_label = ", ".join(gus) if gus and len(gus) <= 3 else (f"{gus[0]} 외 {len(gus) - 1}곳" if gus else ("서울 전체" if sector == "전체" else f"{sector} 전체"))
in_area = lambda df: df if df is None else df[df.district.isin(area)]

# ── ① 빌리자마자 반납이 반복된 자전거 ─────────────────────
with st.container(border=True):
    a48 = in_area(alerts[alerts.alert_ts >= data.since(48)]) if alerts is not None and len(alerts) else None
    n = 0 if a48 is None else len(a48)
    ui.card_title(f"① 빌리자마자 반납이 반복된 자전거 — {n}대", sub=f"{area_label} · 최근 48시간 · {texts.QUICK_RETURN_NOTE}")
    if n:
        v = a48.sort_values("alert_ts", ascending=False)
        v = v.assign(자전거번호=v.bike_id.map(lambda x: f"SPB-{x:05d}"), 대여소=[ui.station_label(a_, b_) for a_, b_ in zip(v.last_st, v.st_name)])
        st.dataframe(v[["자전거번호", "alert_ts", "대여소", "district", "n_sham_7d", "reported_after"]], hide_index=True, width="stretch",
                     column_config={"alert_ts": st.column_config.DatetimeColumn("마지막으로 바로 반납된 시각", format="M/D HH:mm"),
                                    "대여소": "그때 반납된 대여소", "district": "자치구",
                                    "n_sham_7d": st.column_config.NumberColumn("최근 7일 바로 반납 횟수", format="%d회"),
                                    "reported_after": st.column_config.CheckboxColumn("그 뒤 고장 신고됨")})
    else:
        ui.empty(f"{area_label}에는 최근 48시간 동안 없습니다.")

# ── ② 최근 고장 신고된 자전거 ───────────────────────────
# 신고되면 대체로 대여가 막힌다(9월 신고의 2.5%만 1시간 안에 다시 대여, 다음 대여까지 중앙값 약 40시간).
# 그래서 신고 뒤 대여가 없으면 수리 대기, 있으면 점검·수리 뒤 다시 운행에 들어간 것으로 본다.
with st.container(border=True):
    r = in_area(rep) if rep is not None else None
    n = 0 if r is None else len(r)
    waiting = 0 if r is None else int((r.rentals_after == 0).sum())
    ui.card_title(f"② 최근 {texts.REPORT_DAYS}일 안에 고장 신고된 자전거 — {n:,}대",
                  sub=f"{area_label} · 이용자가 앱으로 신고한 자전거. 신고되면 대체로 바로 대여가 막히고, 점검·수리 뒤 다시 운행에 들어갑니다. "
                      f"이 중 <b>{waiting:,}대는 신고 뒤 아직 한 번도 대여되지 않아</b> 수리를 기다리는 것으로 보입니다. 점검 권장 목록(③)에서는 뺐습니다.")
    if n:
        status = st.segmented_control("보기", ["수리 대기로 보임", "다시 운행 중", "전부"], default="수리 대기로 보임", key="rep_status",
                                      help="수리 대기로 보임 = 신고 뒤 아직 대여 기록이 없음 · 다시 운행 중 = 신고 뒤 다시 대여됨(점검·수리를 마친 것으로 보임)")
        v = r[r.rentals_after == 0] if status == "수리 대기로 보임" else (r[r.rentals_after > 0] if status == "다시 운행 중" else r)
        v = v.sort_values("last_report_ts", ascending=False)          # 최근에 신고된 순
        v = v.assign(자전거번호=v.bike_id.map(lambda x: f"SPB-{x:05d}"), 대여소=[ui.station_label(a_, b_) for a_, b_ in zip(v.last_st, v.last_st_name)],
                     대기=((pd.Timestamp.now().value - pd.to_datetime(v.last_report_ts).astype("datetime64[ns]").astype("int64")) / 3.6e12).round(0))
        cols = ["자전거번호", "last_report_ts", "kinds", "n_reports"] + (["대기"] if status == "수리 대기로 보임" else ["first_rent_after", "rentals_after"]) + ["대여소", "district", "last_rtn_dt"]
        st.dataframe(v[cols], hide_index=True, width="stretch", height=320, column_config={
            "last_report_ts": st.column_config.DatetimeColumn("마지막 신고 시각", format="M/D HH:mm"),
            "kinds": "신고된 부품", "n_reports": st.column_config.NumberColumn("신고 건수", format="%d건"),
            "대기": st.column_config.NumberColumn("신고 뒤 지난 시간", format="%d시간"),
            "first_rent_after": st.column_config.DatetimeColumn("신고 뒤 처음 대여된 시각", format="M/D HH:mm"),
            "rentals_after": st.column_config.NumberColumn("신고 뒤 대여 횟수", format="%d회"),
            "대여소": "마지막 반납 대여소", "district": "자치구",
            "last_rtn_dt": st.column_config.DatetimeColumn("마지막 반납 시각", format="M/D HH:mm")})
        st.download_button("이 목록 내려받기 (CSV)", v[cols].to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"최근신고_{status}_{area_label}_{date.today():%Y%m%d}.csv", mime="text/csv", icon=":material/download:", key="dl_rep")
    else:
        ui.empty(f"{area_label}에는 최근 {texts.REPORT_DAYS}일 동안 고장 신고된 자전거가 없습니다.")

if risk is None:
    ui.empty("점검 권장 등급이 아직 없습니다.")
    ui.footer(); st.stop()

# ── ③ 점검 권장 (최근 신고 없는 자전거) ──────────────────
risk_un = in_area(ui.exclude_reported(risk))
wk = pd.to_datetime(risk.wk.max())
with st.container(border=True):
    ui.card_title("③ 점검 권장 자전거 — 아직 고장 신고되지 않은 자전거",
                  sub=f"{area_label} · {wk:%-m월 %-d일}이 있는 주까지의 이용·신고 기록으로 다음 주에 고장 신고될 가능성을 매긴 순위입니다. "
                      f"점검 권장 = 상위 5%, 주의 = 상위 20%. 최근 {texts.REPORT_DAYS}일 안에 신고된 자전거는 ②에 있습니다.")
    c1, c2, c3 = st.columns([1, 2, 1.2])
    part = c1.selectbox("부품", ["전체", "타이어", "체인", "안장", "페달"], help="부품을 고르면 그 부품의 고장 신고 가능성 순으로 정렬합니다")
    grades = c2.pills("등급", texts.GRADE_ORDER[:4], selection_mode="multi", default=["점검 권장", "주의"])
    top = c3.segmented_control("보여 줄 대수", [50, 200, 1000], default=200, format_func=lambda x: f"상위 {x}대")
    score_col = {"전체": "rate_1000km", "타이어": "rate_tire", "체인": "rate_chain", "안장": "rate_saddle", "페달": "rate_pedal"}[part]
    v = risk_un[risk_un.grade.isin(grades)] if grades else risk_un[risk_un.grade != "산정 제외"]
    v = v.sort_values(score_col, ascending=False).head(top or 200).copy()
    v["자전거번호"] = v.bike_id.map(lambda x: f"SPB-{x:05d}")
    v["등급"] = v.grade.map(ui.grade_badge)
    v["대여소"] = [ui.station_label(a_, b_) for a_, b_ in zip(v.last_st, v.last_st_name)]
    show = v[["자전거번호", "등급", score_col, "reasons", "km_since_rep", "rep_13w", "sham_2w", "대여소", "district", "last_rtn_dt"]]
    st.dataframe(show, hide_index=True, width="stretch", height=430, column_config={
        score_col: st.column_config.NumberColumn(f"{'' if part == '전체' else part + ' '}고장 신고 가능성", format="%.1f", help=texts.SCORE_HELP),
        "reasons": st.column_config.TextColumn("이렇게 본 이유", width="large"),
        "km_since_rep": st.column_config.NumberColumn("마지막 신고 뒤 달린 거리", format="%d km"),
        "rep_13w": st.column_config.NumberColumn("최근 3개월 신고", format="%d건"),
        "sham_2w": st.column_config.NumberColumn("최근 2주 바로 반납", format="%d회"),
        "대여소": "마지막 반납 대여소", "district": "자치구",
        "last_rtn_dt": st.column_config.DatetimeColumn("마지막 반납 시각", format="M/D HH:mm")})
    st.download_button("이 목록 내려받기 (CSV)", show.rename(columns={score_col: "고장 신고 가능성", "reasons": "이유", "km_since_rep": "마지막 신고 뒤 달린 거리(km)",
                                                                   "rep_13w": "최근 3개월 신고", "sham_2w": "최근 2주 바로 반납", "district": "자치구",
                                                                   "last_rtn_dt": "마지막 반납 시각"}).to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"점검권장_{area_label}_{part}_{date.today():%Y%m%d}.csv", mime="text/csv", icon=":material/download:", key="dl_risk")

# ── ④ 지도 ───────────────────────────────────────────────
with st.container(border=True):
    which = st.segmented_control("지도에 표시", ["점검 권장 (③)", f"최근 {texts.REPORT_DAYS}일 신고 (②)"], default="점검 권장 (③)", key="map_which") or "점검 권장 (③)"
    is_risk = which.startswith("점검")
    ui.card_title(f"④ {'점검 권장' if is_risk else '최근 고장 신고된'} 자전거가 마지막으로 반납된 대여소",
                  sub=f"{area_label} · {basis} 기준 마지막 반납 위치 · 원이 클수록 그 대여소에 많음 · <b>원을 누르면 아래에 대여소 번호·이름과 자전거 목록</b>이 나옵니다")
    sm_path = data.ROOT / "data/interim/station_master.csv"
    if sm_path.exists():
        sm = pd.read_csv(sm_path)
        src = risk_un[risk_un.grade == "점검 권장"] if is_risk else (in_area(rep) if rep is not None else pd.DataFrame(columns=["last_st", "bike_id"]))
        pts = src.groupby("last_st").size().rename("n").reset_index()
        pts = pts.merge(sm[["st_no", "st_name", "lat", "lon", "district"]], left_on="last_st", right_on="st_no").dropna(subset=["lat", "lon"])
        pts["label"] = [ui.station_label(a_, b_) for a_, b_ in zip(pts.st_no, pts.st_name)]
        pts["st_no"] = pts.st_no.astype(int)
        pts = pts[["st_no", "label", "district", "n", "lat", "lon"]]
        if len(pts):
            m1, m2 = st.columns([1.6, 1.4])
            with m1:
                layer = pdk.Layer("ScatterplotLayer", pts, id="stations", get_position="[lon, lat]", get_radius="70 + n * 55",
                                  get_fill_color=p["dot"] if is_risk else p["dot2"], pickable=True, auto_highlight=True,
                                  stroked=True, get_line_color=[255, 255, 255, 160], line_width_min_pixels=1)
                view = pdk.ViewState(latitude=float(pts.lat.mean()), longitude=float(pts.lon.mean()), zoom=11.3 if (gus or sector != "전체") else 10.3)
                deck = pdk.Deck(layers=[layer], initial_view_state=view, map_provider="carto", map_style=p["map_style"],
                                tooltip={"html": "<b>{label}</b><br/>{district} · " + ("점검 권장" if is_risk else "최근 신고") + " {n}대",
                                         "style": {"fontSize": "13px"}})
                ev = st.pydeck_chart(deck, height=400, on_select="rerun", selection_mode="single-object", key=f"map_{'risk' if is_risk else 'rep'}")
            with m2:
                st.dataframe(pts.sort_values("n", ascending=False)[["label", "district", "n"]].head(20), hide_index=True, width="stretch", height=400,
                             column_config={"label": "대여소(번호 이름)", "district": "자치구", "n": st.column_config.NumberColumn("대수", format="%d대")})
            picked = (ev.selection.get("objects", {}) or {}).get("stations", []) if ev is not None and getattr(ev, "selection", None) else []
            if picked:
                o = picked[0]
                st.markdown(f'<div class="card-title" style="margin-top:8px">📍 {o["label"]} · {o["district"]}</div>'
                            f'<div class="card-sub">{basis} 기준 이 대여소에 마지막으로 반납된 {"점검 권장" if is_risk else "최근 신고"} 자전거 {int(o["n"])}대</div>',
                            unsafe_allow_html=True)
                here = src[src.last_st == o["st_no"]].copy()
                here["자전거번호"] = here.bike_id.map(lambda x: f"SPB-{x:05d}")
                if is_risk:
                    here["등급"] = here.grade.map(ui.grade_badge)
                    st.dataframe(here.sort_values("rate_1000km", ascending=False)[["자전거번호", "등급", "reasons", "last_rtn_dt"]], hide_index=True, width="stretch",
                                 column_config={"reasons": st.column_config.TextColumn("이렇게 본 이유", width="large"),
                                                "last_rtn_dt": st.column_config.DatetimeColumn("마지막 반납 시각", format="M/D HH:mm")})
                else:
                    st.dataframe(here.sort_values("last_report_ts", ascending=False)[["자전거번호", "last_report_ts", "kinds", "rentals_after", "last_rtn_dt"]],
                                 hide_index=True, width="stretch",
                                 column_config={"last_report_ts": st.column_config.DatetimeColumn("마지막 신고 시각", format="M/D HH:mm"), "kinds": "신고된 부품",
                                                "rentals_after": st.column_config.NumberColumn("신고 뒤 대여", format="%d회"),
                                                "last_rtn_dt": st.column_config.DatetimeColumn("마지막 반납 시각", format="M/D HH:mm")})
            else:
                st.markdown('<div class="card-caption">지도에서 원을 누르면 그 대여소의 자전거 목록이 여기에 나옵니다. (마우스를 올리면 대여소 이름이 먼저 보입니다)</div>',
                            unsafe_allow_html=True)
        else:
            ui.empty(f"{area_label}에 표시할 자전거가 없습니다.")
    else:
        ui.empty("대여소 위치 정보가 없습니다.")
ui.footer()
