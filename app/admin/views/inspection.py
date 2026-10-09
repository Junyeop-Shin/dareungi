"""점검 대상 화면(08a 설계서 5절).

지역은 다른 화면과 같은 머리 영역 선택기(서울 전체 / 강북 / 강남 + 구 선택)로 고른다.
목록 셋을 한꺼번에 펼치면 화면이 길고 글자가 많아, 위쪽 버튼(목록 이름 + 대수)으로 하나씩만 본다(2026-10-09 UX 검토).
  최근 REPORT_DAYS일 고장 신고 자전거: 신고 후 대여 기록으로 수리 대기 / 미조치 의심 / 재배치 후 운행을 추정한다 + 위치 지도
  점검 권장(미신고): 최근 신고가 없는 자전거 중 다음 주 고장 신고 가능성 순위 상위 + 위치 지도
  대여 직후 반납이 반복된 자전거: 신고는 안 됐지만 지금 고장일 가능성이 높다 + 위치 지도
지도는 그 목록의 자전거가 최근 반납된 대여소를 원으로, 자치구 경계선과 한글 구 이름을 함께 그린다.
원을 누르거나 옆 표에서 대여소를 고르면 지도가 그 대여소로 확대되어 그 대여소만 남고, 아래에 그 대여소의 자전거 목록이 나온다
(표로도 고를 수 있어야 키보드만으로 쓸 수 있다).
설명은 한 줄로 두고 근거는 제목 옆 물음표에 넣는다. 표에는 현장에서 바로 쓰는 열만 보인다(CSV 내려받기는 뺐다. 사람 결정 2026-10-09).
이 화면의 순위는 주간 예상과 다른 모형이라 예상 정확도는 보여 주지 않는다.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[2]))
from admin import components as ui       # noqa: E402
from common import data, texts, theme    # noqa: E402
from common.seoul_tiles import DISTRICTS   # noqa: E402

ui.header("점검 대상")
p = theme.P()
risk, alerts, rep = data.load("bike_risk_latest"), data.load("alerts_live"), data.load("reported_recent")
s = data.status()
basis = f"{s['대여이력']:%-m/%-d %-H시}" if "대여이력" in s else "최근"
area_label = ui.scope_label()
in_area = data.apply_gu          # 구를 고르지 않으면 거르지 않는다(자치구가 비어 있는 자전거도 서울 전체에는 들어간다)
short2 = lambda t: " · ".join(texts.short_reason(x) for x in (t or "").split(" / ")[:2])      # 선정 사유는 홈 목록처럼 짧은 말 두 개만


# ── 지도: 목록 바로 아래에 그 목록의 자전거가 있는 대여소를 그린다 ──────────
KIND = {   # 목록 종류별 지도 표시: 이름, 원 색, 대여소를 골랐을 때 보여 줄 열
    "sham": ("대여 직후 반납 반복", [201, 138, 0, 190], ["자전거번호", "alert_ts", "n_sham_7d"]),
    "rep": ("최근 신고", p["dot2"], ["자전거번호", "last_report_ts", "kinds", "rentals_after", "last_rtn_dt"]),
    "risk": ("점검 권장", p["dot"], ["자전거번호", "등급", "reasons", "last_rtn_dt"]),
}
DETAIL_COLS = {
    "alert_ts": st.column_config.DatetimeColumn("최근 반납 시각", format="M/D HH:mm"),
    "n_sham_7d": st.column_config.NumberColumn("7일간 직후 반납 횟수", format="%d회"),
    "last_report_ts": st.column_config.DatetimeColumn("최근 신고 시각", format="M/D HH:mm"), "kinds": "신고 부품",
    "rentals_after": st.column_config.NumberColumn("신고 후 대여 횟수", format="%d회"),
    "reasons": st.column_config.TextColumn("선정 사유", width="large"),
    "last_rtn_dt": st.column_config.DatetimeColumn("최근 반납 시각", format="M/D HH:mm"),
}


def station_map(src, kind, title, note):
    """src(자전거 목록, last_st 열)의 최근 반납 대여소를 지도와 표로 그린다. 세 목록 아래에서 각각 부른다.
    kind: KIND의 키(sham 대여 직후 반납 반복, rep 최근 신고, risk 점검 권장). note: 설명 줄 맨 앞에 붙일 말.
    옆 표나 지도에서 대여소를 고르면 지도가 그 대여소로 확대되고 그 대여소만 남으며, 아래에 그 대여소의 자전거 목록이 나온다.
    고른 대여소는 표·지도 위젯의 선택 상태에서 매번 읽는다. '전체 지도 보기'를 누르면 위젯 키의 번호(ver)를 올려 두 위젯의 선택을 함께 지운다
    (대여소 하나만 남은 지도에서는 빈 곳을 눌러도 선택이 풀리지 않는다)."""
    name, color, detail = KIND[kind]
    st.divider()
    ui.card_title(title, sub=f"{note} · {basis} 기준 최근 반납 대여소 · 원이 클수록 대수가 많음")
    sm_path = data.ROOT / "data/interim/station_master.csv"
    if not sm_path.exists():
        return ui.empty("대여소 위치 정보가 없습니다.")
    sm = pd.read_csv(sm_path)
    pts = src.groupby("last_st").size().rename("n").reset_index()
    pts = pts.merge(sm[["st_no", "st_name", "lat", "lon", "district"]], left_on="last_st", right_on="st_no").dropna(subset=["lat", "lon"])
    if pts.empty:
        return ui.empty(f"{area_label}에 표시할 자전거가 없습니다.")
    pts["label"] = [ui.station_label(a_, b_) for a_, b_ in zip(pts.st_no, pts.st_name)]
    pts["st_no"] = pts.st_no.astype(int)
    pts = pts[["st_no", "label", "district", "n", "lat", "lon"]]
    top20 = pts.sort_values("n", ascending=False).head(20)
    # 표에서 고른 대여소가 있으면 그것을, 없으면 지도에서 누른 원을 쓴다. 위젯을 그리기 전이라 세션에 남은 선택 상태를 읽는다
    ver = st.session_state.setdefault(f"ver_{kind}", 0)
    tsel = getattr(st.session_state.get(f"tbl_{kind}_{ver}"), "selection", None)
    msel = getattr(st.session_state.get(f"map_{kind}_{ver}"), "selection", None)
    picked = [top20.iloc[i].to_dict() for i in (tsel.rows if tsel else []) if i < len(top20)] or ((msel.get("objects", {}) or {}).get("stations", []) if msel else [])
    o = picked[0] if picked and (pts.st_no == picked[0]["st_no"]).any() else None
    shown = pts[pts.st_no == o["st_no"]] if o else pts          # 고른 대여소가 있으면 그 대여소만 그리고 그곳으로 확대한다
    # 자치구 경계선: app/common/seoul_gu.geojson(통계청 센서스용 행정구역경계 2013, github.com/southkorea/seoul-maps, Apache 2.0)
    line = [70, 76, 82, 190] if theme.mode() == "light" else [215, 215, 215, 170]
    border = pdk.Layer("GeoJsonLayer", json.load(open(Path(__file__).resolve().parents[2] / "common/seoul_gu.geojson")), id="gu_border",
                       stroked=True, filled=False, get_line_color=line, line_width_min_pixels=1.6, pickable=False)
    gu_c = sm[sm.district.isin(DISTRICTS)].groupby("district")[["lat", "lon"]].mean().reset_index()      # 구 이름을 얹을 자리(대여소 좌표 평균)
    m1, m2 = st.columns([1.6, 1.4])
    with m1:
        # 바탕 지도(carto)는 지명이 영어뿐이라 지명 없는 바탕을 쓰고 한글 구 이름을 직접 얹는다
        names = pdk.Layer("TextLayer", gu_c, id="gu_names", get_position="[lon, lat]", get_text="district", get_size=14,
                          get_color=[40, 44, 48, 200] if theme.mode() == "light" else [230, 230, 230, 200],
                          character_set=pdk.types.String("auto"), font_family=pdk.types.String("sans-serif"), font_weight=700,
                          font_settings={"sdf": True}, outline_width=4,      # 원에 묻히지 않게 글자에 바탕색 테두리를 두른다
                          outline_color=[255, 255, 255, 230] if theme.mode() == "light" else [20, 20, 20, 230])
        # pdk.types.String: 그냥 문자열로 주면 pydeck이 열 이름(식)으로 읽는다. auto = 쓰인 한글 글자만 그린다
        layer = pdk.Layer("ScatterplotLayer", shown, id="stations", get_position="[lon, lat]", get_radius="70 + n * 55",
                          get_fill_color=color, pickable=True, auto_highlight=True, radius_min_pixels=9 if o else 5,      # 한두 대뿐이어도 보이게
                          stroked=True, get_line_color=[255, 255, 255, 160], line_width_min_pixels=1)
        view = pdk.ViewState(latitude=float(shown.lat.mean()), longitude=float(shown.lon.mean()), zoom=12 if o else (11.3 if data.get_gu() else 10.3))      # 12: 고른 대여소가 속한 구의 경계와 이름이 함께 보이는 정도
        layers = [border, layer, names]
        if o:        # 고른 대여소 이름을 원 위에 적는다
            layers.append(pdk.Layer("TextLayer", shown, id="st_name", get_position="[lon, lat]", get_text="label", get_size=15, get_pixel_offset=[0, -34],
                                    get_color=names.get_color, character_set=pdk.types.String("auto"), font_family=pdk.types.String("sans-serif"),
                                    font_weight=700, font_settings={"sdf": True}, outline_width=4, outline_color=names.outline_color))
        deck = pdk.Deck(layers=layers, initial_view_state=view, map_provider="carto", map_style=p["map_style"],
                        tooltip={"html": "<b>{label}</b><br/>{district} · " + name + " {n}대", "style": {"fontSize": "13px"}})
        st.pydeck_chart(deck, height=400, on_select="rerun", selection_mode="single-object", key=f"map_{kind}_{ver}")
    with m2:
        st.dataframe(top20[["label", "district", "n"]], hide_index=True, width="stretch", height=400,
                     on_select="rerun", selection_mode="single-row", key=f"tbl_{kind}_{ver}",
                     column_config={"label": "대여소", "district": "자치구", "n": st.column_config.NumberColumn("대수", format="%d대")})
    if o is None:
        return st.markdown('<div class="card-caption">원을 누르거나 표에서 대여소를 고르면 지도가 그 대여소로 확대되고, 그 대여소의 자전거 목록이 나옵니다.</div>',
                           unsafe_allow_html=True)
    h1, h2 = st.columns([4, 1], vertical_alignment="center")
    h1.markdown(f'<div class="card-title" style="margin-top:8px">{o["label"]} · {o["district"]}</div>'
                f'<div class="card-sub">{basis} 기준 이 대여소에 있는 {name} 자전거 {int(o["n"])}대</div>', unsafe_allow_html=True)
    h2.button("전체 지도 보기", key=f"reset_{kind}", use_container_width=True,
              on_click=lambda: st.session_state.update({f"ver_{kind}": ver + 1}))
    here = src[src.last_st == o["st_no"]].copy()
    here["자전거번호"] = here.bike_id.map(lambda x: f"SPB-{x:05d}")
    if kind == "risk":
        here["등급"] = here.grade.map(ui.grade_badge)
        here["reasons"] = here.reasons.map(short2)
    st.dataframe(here.sort_values(detail[1] if kind != "risk" else "risk_ratio", ascending=False)[detail], hide_index=True, width="stretch",
                 column_config={k: v for k, v in DETAIL_COLS.items() if k in detail})


# ── 세 목록의 대수를 먼저 세고, 어느 목록을 볼지 고른다 ──────────────────
a48 = in_area(alerts[alerts.alert_ts >= data.since(48)]) if alerts is not None and len(alerts) else None
r = in_area(rep) if rep is not None else None
risk_un = in_area(ui.exclude_reported(risk)) if risk is not None else None
insp = risk_un[risk_un.grade == "점검 권장"] if risk_un is not None else None
LISTS = [f"최근 {texts.REPORT_DAYS}일 고장 신고", "점검 권장", "대여 직후 반납 반복"]      # 버튼 순서(사람 결정 2026-10-09): 신고 → 점검 권장 → 대여 직후 반납
cnt = dict(zip(LISTS, [0 if x is None else len(x) for x in (r, insp, a48)]))
which = st.segmented_control("볼 목록", LISTS, default=LISTS[0], key="insp_list", label_visibility="collapsed",
                             format_func=lambda x: f"{x} {cnt[x]:,}대") or LISTS[0]

with st.container(border=True):
    # ── 대여 직후 반납이 반복된 자전거 ─────────────────────
    if which == LISTS[2]:
        ui.card_title("대여 직후 반납이 반복된 자전거", sub=f"최근 48시간 동안 {texts.QUICK_RETURN}이 2번 연속", help=texts.QUICK_RETURN_NOTE)
        if cnt[which]:
            v = a48.sort_values("alert_ts", ascending=False)
            v = v.assign(자전거번호=v.bike_id.map(lambda x: f"SPB-{x:05d}"), 대여소=[ui.station_label(a_, b_) for a_, b_ in zip(v.last_st, v.st_name)])
            st.dataframe(v[["자전거번호", "alert_ts", "대여소", "district", "n_sham_7d", "reported_after"]], hide_index=True, width="stretch",
                         column_config={"alert_ts": st.column_config.DatetimeColumn("최근 반납 시각", format="M/D HH:mm"),
                                        "대여소": "반납 대여소", "district": "자치구",
                                        "n_sham_7d": st.column_config.NumberColumn("7일간 직후 반납 횟수", format="%d회"),
                                        "reported_after": st.column_config.CheckboxColumn("이후 고장 신고 여부")})
            station_map(v, "sham", "대여 직후 반납 반복 자전거 위치", f"{len(v):,}대")
        else:
            ui.empty(f"{area_label}에는 최근 48시간 동안 없습니다.")

    # ── 최근 고장 신고 자전거 ───────────────────────────
    # 신고가 곧바로 대여를 막지는 않는다(신고의 30%가 24시간 안에, 대부분 그 자리에서 다시 대여. docs/notes/07 R13·R18).
    # 신고 후 대여가 없으면 수리 대기, 신고 당시 자리에서 다시 대여됐으면 미조치 의심, 다른 곳에서 대여됐으면 수거·수리 후 재배치로 본다.
    # 보기 버튼, 버튼의 대수, 표의 상태 칸이 모두 이 세 이름을 쓴다(세 대수를 더하면 목록의 대수).
    elif which == LISTS[0]:
        STATUSES = ["수리 대기", "미조치 의심", "재배치 후 운행"]
        # '최근 7일'은 달력 주가 아니라 목록을 만든 시각(발행 시각)부터 거꾸로 7일이다. 헷갈리지 않게 실제 기간을 적는다
        span = (f"{data.shift_days(s['발행 시각'], -texts.REPORT_DAYS):%-m/%-d %-H시} ~ {s['고장신고']:%-m/%-d %-H시} 신고 · "
                if "발행 시각" in s and "고장신고" in s else "")
        ui.card_title(f"최근 {texts.REPORT_DAYS}일 고장 신고 자전거", sub=f"{span}이용자가 앱으로 신고한 자전거 · 점검 권장 목록에는 넣지 않음")
        if cnt[which]:
            same = r.same_st_after.eq(True) if "same_st_after" in r else False
            r = r.assign(조치=np.select([r.rentals_after == 0, same], STATUSES[:2], STATUSES[2]))
            n_by = r.조치.value_counts()
            status = st.segmented_control("신고 후 상태(추정)", STATUSES + ["전체"], default=STATUSES[0], key="rep_status",
                                          format_func=lambda x: f"{x} {n_by.get(x, len(r) if x == '전체' else 0):,}대",
                                          help="신고 후 대여 기록으로 추정한 상태입니다.  \n"
                                               "수리 대기: 신고 후 대여 기록이 없음  \n"
                                               "미조치 의심: 신고 당시 자리에서 다시 대여됨. 신고의 30% 정도가 하루 안에 그 자리에서 다시 대여되고, "
                                               "이런 자전거는 다시 신고될 위험이 약 2배입니다  \n"
                                               "재배치 후 운행: 다른 대여소에서 대여됨. 수거·수리 후 재배치된 것으로 봅니다") or "전체"
            v = r if status == "전체" else r[r.조치 == status]
            # 미조치 의심은 신고된 채로 많이 탄 순(먼저 볼 자전거), 나머지는 최근 신고 순. 전체에서는 미조치 의심을 위로
            v = v.assign(_u=v.조치 == STATUSES[1]).sort_values(["_u", "rentals_after" if status == STATUSES[1] else "last_report_ts"], ascending=False)
            v = v.assign(자전거번호=v.bike_id.map(lambda x: f"SPB-{x:05d}"), 대여소=[ui.station_label(a_, b_) for a_, b_ in zip(v.last_st, v.last_st_name)])
            # 열은 상태마다 쓸모 있는 것만: 신고 후 대여 횟수는 미조치 의심(조치 없이 몇 번 더 탔나)과 전체에서, 최근 반납 시각은 수리 대기에서만.
            # 재배치 후 운행은 할 일이 없는 목록이라 신고 시각 · 부품 · 대여소만 둔다(사람 결정 2026-10-09)
            cols = (["자전거번호", "last_report_ts", "kinds"] + (["rentals_after"] if status in (STATUSES[1], "전체") else [])
                    + (["조치"] if status == "전체" else []) + ["대여소", "district"] + (["last_rtn_dt"] if status == STATUSES[0] else []))
            st.dataframe(v[cols], hide_index=True, width="stretch", height=320, column_config={
                "last_report_ts": st.column_config.DatetimeColumn("최근 신고 시각", format="M/D HH:mm"),
                "kinds": "신고 부품", "조치": "신고 후 상태(추정)",
                "rentals_after": st.column_config.NumberColumn("신고 후 대여 횟수", format="%d회"),
                "대여소": "최근 반납 대여소", "district": "자치구",
                "last_rtn_dt": st.column_config.DatetimeColumn("최근 반납 시각", format="M/D HH:mm")})
            station_map(v, "rep", "최근 신고 자전거 위치", f"{status} {len(v):,}대")
        else:
            ui.empty(f"{area_label}에는 최근 {texts.REPORT_DAYS}일 동안 고장 신고된 자전거가 없습니다.")

    # ── 점검 권장 (최근 신고 없는 자전거) ──────────────────
    elif risk_un is None:
        ui.empty("점검 권장 등급이 아직 없습니다.")
    else:
        ui.card_title("점검 권장 자전거 (미신고)", sub=f"{texts.week_range(risk.wk.max())} 주까지의 기록으로 매긴 다음 주 고장 신고 가능성 순위",
                      help=texts.GRADE_HELP + f" 최근 {texts.REPORT_DAYS}일 안에 신고된 자전거는 이 목록에 넣지 않습니다.")
        c1, c2, c3 = st.columns([1, 2, 1.2])
        part = c1.selectbox("부품", ["전체", "타이어", "체인", "안장", "페달"], help="부품을 고르면 그 부품의 고장 신고 가능성 순으로 정렬합니다")
        grades = c2.pills("등급", texts.GRADE_ORDER[:2], selection_mode="multi", default=texts.GRADE_ORDER[:2]) or texts.GRADE_ORDER[:2]
        top = c3.segmented_control("표시 대수", [50, 200, 1000], default=200, format_func=lambda x: f"상위 {x:,}대")
        score_col = {"전체": "risk_ratio", "타이어": "ratio_tire", "체인": "ratio_chain", "안장": "ratio_saddle", "페달": "ratio_pedal"}[part]
        v = risk_un[risk_un.grade.isin(grades)].sort_values(score_col, ascending=False).head(top or 200).copy()
        v["자전거번호"] = v.bike_id.map(lambda x: f"SPB-{x:05d}")
        v["등급"] = v.grade.map(ui.grade_badge)
        v["대여소"] = [ui.station_label(a_, b_) for a_, b_ in zip(v.last_st, v.last_st_name)]
        v["주행"] = v.km_4w / 4
        v["사유"] = v.reasons.map(short2)
        # 화면에는 찾아가서 점검하는 데 쓰는 열만(등급을 하나만 골랐으면 등급 칸도 뺀다)
        on_screen = ["자전거번호"] + (["등급"] if len(grades) != 1 else []) + [score_col, "주행", "사유", "대여소", "district"]
        st.dataframe(v[on_screen], hide_index=True, width="stretch", height=430, column_config={
            score_col: st.column_config.NumberColumn(f"{'' if part == '전체' else part + ' '}신고 가능성 (평균 대비)", format="%.1f배", width="medium", help=texts.SCORE_HELP),
            "주행": st.column_config.NumberColumn("주간 주행거리", format="%d km", width="small", help="최근 4주 평균"),
            "사유": st.column_config.TextColumn("선정 사유", width="large"),
            "대여소": "최근 반납 대여소", "district": "자치구"})
        station_map(insp, "risk", "점검 권장 자전거 위치", f"점검 권장 {len(insp):,}대 전체")
ui.footer()
