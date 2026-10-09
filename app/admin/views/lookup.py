"""자전거 조회 화면: 자전거 번호 하나로 점검 대상 여부, 위치, 선정 사유, 최근 이용·신고 기록을 본다.

왜: 점검 대상 화면은 목록과 지도로 '어디에 몇 대'를 보여 주지만, 현장에서 자전거 앞에 섰을 때 '이 번호가 왜 대상인가'를
바로 찾을 길이 없었다. 예전 시민용 조회 앱(app/citizen)을 없애고 담당자용으로 다시 만들었다(사람 결정 2026-10-10).
시민용과 달리 숨기는 정보가 없다(최근 반납 대여소, 평균 대비 배수, 신고 후 상태를 그대로 보여 준다).

들어오는 길: 번호를 직접 넣거나, 홈의 점검 권장 목록·점검 대상 화면의 표에서 번호를 누른다(주소 ?bike=74216).
배치: 입력칸(빈 화면에서는 바로 조회할 번호, 잘못 넣었으면 안내) → 번호와 결론 한 줄(어느 목록의 대상인지 + 마지막 반납 위치) → 숫자 요약 띠
      → 왼쪽 점검 대상 여부·최근 90일 신고, 오른쪽 위치 지도·선정 사유(점검 권장·주의일 때만)
점검 대상이 아닌 자전거를 조회했을 때도 어색하지 않게 한다(2026-10-10 design 플러그인 검토): 큰 글씨는 숫자에만, 해당 없는 항목은 줄인다.
입력: serving/bike_card(최근 이용·신고 집계), bike_risk_latest(등급·배수·사유·위치), reported_recent(최근 7일 신고), alerts_live(대여 직후 반납 반복)
이 화면과 관련해 홈에 따로 두는 지표는 없다.
"""
import datetime as dt
import re
import sys
from pathlib import Path

from urllib.parse import quote

import pandas as pd
import pydeck as pdk
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[2]))
from admin import components as ui       # noqa: E402
from common import data, texts, theme    # noqa: E402

ui.header("자전거 조회", show_filter=False)
p = theme.P()
card, risk, rep, alerts = data.load("bike_card"), data.load("bike_risk_latest"), data.load("reported_recent"), data.load("alerts_live")
one = lambda df, bid: None if df is None or df[df.bike_id == bid].empty else df[df.bike_id == bid].iloc[0]      # 그 자전거의 한 줄(없으면 None)
has = lambda v: v is not None and not pd.isna(v)
insp = None          # 점검 권장 목록(최근 신고 자전거 제외, 서울 전체). 순위와 바로 조회 번호에 쓴다
if risk is not None:
    insp = ui.exclude_reported(risk)
    insp = insp[insp.grade == "점검 권장"].sort_values("risk_ratio", ascending=False)


def rows(pairs):
    """이름(왼쪽, 회색) · 값(오른쪽) 줄들을 그린다"""
    st.markdown("".join(f'<div class="drow"><span>{k}</span><span>{v}</span></div>' for k, v in pairs), unsafe_allow_html=True)


def timeline(reports, days=90):
    """최근 90일 신고를 가로 띠 위의 점으로 그린다(같은 날 여러 건은 위로 쌓는다). reports: '10/01 타이어, 09/14 기타' 꼴(연도 없음).
    연도는 오늘부터 거꾸로 90일 안에 드는 쪽으로 정한다. 반환: (SVG 문자열, 유형별 건수 문장)"""
    today = dt.date.today(); start = today - dt.timedelta(days=days)
    marks, kinds = [], {}
    for x in reports.split(", "):
        md, _, kind = x.partition(" ")
        mth, day = (int(v) for v in md.split("/"))
        d = dt.date(today.year, mth, day)
        d = d if d <= today else dt.date(today.year - 1, mth, day)
        marks.append((d, kind)); kinds[kind] = kinds.get(kind, 0) + 1
    seen, dots = {}, ""
    for d, kind in sorted(marks):
        k = seen[d] = seen.get(d, 0) + 1
        x = 2 + 96 * max((d - start).days, 0) / days
        dots += f'<circle cx="{x:.1f}%" cy="{30 - 11 * (k - 1)}" r="5" class="tl-dot"><title>{d.month}/{d.day} {kind}</title></circle>'
    svg = (f'<svg class="tl" width="100%" height="52" role="img" aria-label="최근 {days}일 신고 {len(marks)}건">'
           f'<line x1="2%" x2="98%" y1="30" y2="30" class="tl-axis"/>{dots}</svg>'
           f'<div class="tl-ends"><span>{start.month}/{start.day}</span><span>오늘 {today.month}/{today.day}</span></div>')
    return svg, " · ".join(f"{k} {n}건" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))


with st.container(border=True, key="lookup_box"):          # key: 휴대폰에서 누르는 곳을 44px 이상으로 키우는 CSS가 이 상자를 가리킨다
    ui.card_title("자전거 번호로 조회", sub="자전거에 적힌 번호(SPB-로 시작)를 넣으면 점검 대상 여부와 최근 이용·신고 기록을 볼 수 있습니다.")
    with st.form("lookup", border=False):
        with st.container(horizontal=True, vertical_alignment="bottom", gap="small"):
            # 처음 값은 주소의 ?bike= (다른 화면에서 번호를 눌러 들어온 경우). 조회하면 주소에도 번호를 남겨 새로고침·공유가 되게 한다
            # value=로 주소 값을 넣으면 안 된다: 조회할 때마다 주소의 번호가 바뀌어 Streamlit이 입력칸을 새것으로 보고, 방금 친 값을 버린다
            # (그래서 두 번째 조회부터는 두 번 눌러야 했다). 값은 key로 세션에 두고, 화면을 처음 열 때만 주소에서 읽는다
            st.session_state.setdefault("lk_q", st.query_params.get("bike", ""))
            q = st.text_input("자전거 번호", key="lk_q", placeholder="예: 74216 또는 SPB-74216", max_chars=12, label_visibility="collapsed", width=260)
            st.form_submit_button("조회", type="primary")
    m = re.search(r"\d+", q or "")
    bid = int(m.group()) if m else None
    c, r, rp = (one(card, bid), one(risk, bid), one(rep, bid)) if bid is not None else (None, None, None)
    found = not (c is None and r is None and rp is None)
    if not q:          # 바로 눌러 볼 번호: 빈 화면에서 무엇을 넣을지 몰라 멈추지 않게. 조회한 뒤에는 결과가 바로 보이도록 숨긴다
        picks = [(int(b), f"점검 권장 {i}위") for i, b in enumerate(insp.bike_id.head(3), 1)] if insp is not None else []
        if rep is not None and len(rep):
            un = rep[rep.same_st_after.eq(True)].sort_values("rentals_after", ascending=False).head(2)
            picks += [(int(b), "미조치 의심") for b in un.bike_id]
        if picks:
            st.markdown('<div class="legend" style="margin-top:2px"><span>바로 조회</span>' + "".join(
                f'<a class="pick" href="./lookup?bike={b}" target="_self">SPB-{b:05d} <span>{why}</span></a>' for b, why in picks) + '</div>', unsafe_allow_html=True)
    if q and not (bid is not None and found):          # 결과가 없을 때도 탭 이름은 이 화면 이름으로(넣은 값을 주석에 넣어 매번 다시 실행되게 한다)
        st.html(f"<script>document.title = '자전거 조회 · 따릉이 정비 현황' /* {re.sub(r'[^0-9A-Za-z-]', '', q)} */</script>", unsafe_allow_javascript=True)
    if q and bid is None:          # 안내는 입력칸 바로 아래에 두 줄로(무엇이 문제인지 / 어떻게 하면 되는지)
        st.markdown('<div class="lk-msg" role="status"><b>번호를 읽지 못했습니다.</b><br>번호의 숫자 부분을 넣어 주세요. 예: SPB-74216 → 74216</div>', unsafe_allow_html=True)
    elif q and not found:
        st.markdown(f'<div class="lk-msg" role="status"><b>SPB-{bid:05d}의 최근 90일 기록이 없습니다.</b><br>'
                    '번호를 다시 확인해 주세요. 번호가 맞다면 최근 90일 동안 이용되지 않은 자전거입니다.</div>', unsafe_allow_html=True)

if bid is not None:
    st.query_params["bike"] = str(bid)
if bid is not None and found:
    al7 = alerts[alerts.bike_id == bid] if alerts is not None and len(alerts) else None          # 경보 표는 최근 7일치
    al48 = al7[al7.alert_ts >= data.since(48)] if al7 is not None else None
    grade = texts.grade_name(r.grade) if r is not None else "—"
    src = r if r is not None and has(r.last_st) else rp          # 위치: 점수 표에 없으면 최근 신고 표의 것
    st_label = ui.station_label(src.last_st, src.last_st_name) if src is not None and has(src.last_st) else None
    # ── 결론 한 줄: 어느 목록의 대상인지(해당하는 것 모두) + 지금 있는 곳. 지시가 아니라 사실만 적는다 ──
    state = ui.report_status(rp.to_frame().T).iloc[0] if rp is not None else None
    rank = int((insp.risk_ratio > r.risk_ratio).sum()) + 1 if r is not None and r.grade == "점검 권장" and rp is None else None
    hit = ([f"최근 {texts.REPORT_DAYS}일 고장 신고({state})"] if state else []) + ([f"점검 권장 {rank:,}위"] if rank else []) \
        + (["대여 직후 반납 반복"] if al48 is not None and len(al48) else [])
    verdict = " · ".join(hit) if hit else "점검 대상 목록에 없음"
    # '있음'이라고 단정하지 않는다: 알 수 있는 것은 마지막 반납 대여소와 그 시각뿐이다
    where_short = f"마지막 반납 {st_label} ({pd.Timestamp(src.last_rtn_dt):%-m/%-d %H:%M})" if st_label and has(src.last_rtn_dt) else "위치 확인 안 됨"
    st.html(f"<script>document.title = 'SPB-{bid:05d} · 자전거 조회'</script>", unsafe_allow_javascript=True)          # 표의 링크로 탭을 여러 개 열어도 구분되게
    st.markdown(f'<div class="lk-head" role="status"><span class="mono" role="heading" aria-level="2">SPB-{bid:05d}</span>'
                f'<span class="lk-verdict{"" if hit else " none"}">{verdict}</span><span class="lk-where">{where_short}</span></div>', unsafe_allow_html=True)
    age_ok = c is not None and has(c.age_years)          # 번호가 처음 나타난 날을 도입일로 보는 것은 2022-03 이후 번호에서만 믿는다(그 전 번호는 값이 비어 있다)
    ui.kpi_strip([
        # 요약 띠는 숫자만 크게. 등급 이름은 위 결론 줄과 겹치므로 여기서는 배수 아래 작은 글씨로
        dict(label="다음 주 고장 신고 가능성", value=f"{r.risk_ratio:.1f}", unit="배", sub=f"전체 자전거 평균 대비 · 등급 {grade}", help=texts.SCORE_HELP)
        if r is not None and has(r.risk_ratio) else dict(label="다음 주 고장 신고 가능성", value="—", sub="최근 4주 이용이 적어 순위를 매기지 않음"),
        dict(label="주간 주행거리", value=f"{r.km_4w / 4:,.0f}" if r is not None and has(r.km_4w) else "—", unit="km", sub="최근 4주 평균"),
        dict(label="최근 30일 이용", value=f"{int(c.trips_30d):,}" if c is not None and has(c.trips_30d) else "—", unit="회",
             sub=f"{c.km_30d:,.0f}km 주행" if c is not None and has(c.km_30d) else ""),
        dict(label="운행 기간", value=f"{c.age_years:g}" if age_ok else "확인 어려움", unit="년" if age_ok else "",
             sub="" if age_ok else "도입 시기를 알 수 없음",          # 왜 알 수 없는지는 마우스를 올리면 뜨는 설명(help)에
             help="자전거 번호는 2020 ~ 2022년 2월에 단말기 교체로 모두 새로 붙었습니다. 그래서 2022년 3월 이후에 처음 나타난 번호만 운행 기간을 계산합니다."),
    ])
    def card_lists():
        """점검 대상 화면의 세 목록 가운데 어디에 들어 있는지"""
        ui.card_title("점검 대상 여부", sub="점검 대상 화면의 세 목록 가운데 어디에 들어 있는지")
        if rp is not None:
            after = f" · 신고 후 {int(rp.rentals_after)}회 대여" if rp.rentals_after else ""
            rep_txt = f"<b>{state}</b> · {pd.Timestamp(rp.last_report_ts):%-m/%-d %H:%M} 신고({rp.kinds}){after}"
        else:
            rep_txt = "해당 없음"
        if rank:
            risk_txt = f"<b>{rank:,}위</b> (서울 전체 {len(insp):,}대 중)"
        elif r is not None and r.grade == "점검 권장":
            risk_txt = "최근 신고가 있어 목록에서 제외(등급은 점검 권장)"
        else:
            risk_txt = f"해당 없음(등급 {grade})"
        if al48 is not None and len(al48):          # 목록(최근 48시간)에 있으면 '해당', 그보다 전이라도 7일 안에 있었으면 그 사실을 적는다
            sham_txt = f"<b>해당</b> · {pd.Timestamp(al48.alert_ts.max()):%-m/%-d %H:%M} · 7일간 {int(al48.n_sham_7d.max())}회"
        elif al7 is not None and len(al7):
            sham_txt = f"48시간 안에는 없음 · 최근 7일간 {int(al7.n_sham_7d.max())}회(마지막 {pd.Timestamp(al7.alert_ts.max()):%-m/%-d %H:%M})"
        else:
            sham_txt = "해당 없음"
        if hit or (al7 is not None and len(al7)) or (r is not None and r.grade == "점검 권장"):
            rows([(f"최근 {texts.REPORT_DAYS}일 고장 신고", rep_txt), ("점검 권장", risk_txt), ("대여 직후 반납 반복(48시간)", sham_txt)])
        else:          # 셋 다 해당 없으면 같은 말을 세 줄 되풀이하지 않는다
            rows([("세 목록 모두", f"해당 없음(등급 {grade})")])

    def card_reports():
        """최근 90일 신고: 가로 띠와 날짜별 목록"""
        reports = c.reports_90d if c is not None else None
        if isinstance(reports, str) and reports:
            svg, by_kind = timeline(reports)
            ui.card_title("최근 90일 고장 신고", sub=by_kind)
            st.markdown(svg, unsafe_allow_html=True)
            rows([tuple(x.split(" ", 1)) if " " in x else (x, "") for x in re.sub(r"\b0(\d)", r"\1", reports).split(", ")])
        else:
            ui.card_title("최근 90일 고장 신고")
            ui.empty("최근 90일 동안 고장 신고가 없습니다.")

    def card_place():
        """마지막 반납 대여소: 지도와 대여소·시각"""
        gu = src.district if st_label and isinstance(src.district, str) else None
        ui.card_title("위치", sub="마지막으로 반납된 대여소입니다(실제보다 1~2시간 늦을 수 있음).",
                      link=(f"{gu} 점검 대상 보기", f"./inspection?gu={quote(gu)}") if gu else None)
        base = ui.gu_layers() if st_label else None
        spot = base[0][base[0].st_no == src.last_st].dropna(subset=["lat", "lon"]) if base else None
        if spot is not None and len(spot):          # 점검 대상 화면과 같은 바탕(자치구 경계 · 구 이름)에 이 대여소 하나만
            spot = spot.assign(label=st_label)
            dot = pdk.Layer("ScatterplotLayer", spot, id="spot", get_position="[lon, lat]", get_radius=60, radius_min_pixels=9, get_fill_color=p["dot"],
                            stroked=True, get_line_color=[255, 255, 255, 160], line_width_min_pixels=1)
            st.pydeck_chart(pdk.Deck(layers=[base[1], dot, base[2], ui.label_layer(spot, "label", "spot_name", 15, offset=-30)], map_provider="carto", map_style=p["map_style"],
                                     initial_view_state=pdk.ViewState(latitude=float(spot.lat.iloc[0]), longitude=float(spot.lon.iloc[0]), zoom=12)), height=250)
        rows([("최근 반납 대여소", f"{st_label} · {src.district}" if st_label else "확인 안 됨"),
              ("반납 시각", f"{pd.Timestamp(src.last_rtn_dt):%-m/%-d %H:%M}" if st_label and has(src.last_rtn_dt) else "—")])

    def card_reasons():
        """선정 사유와 의심 고장 부품(점검 권장·주의일 때만)"""
        # 선정 사유와 의심 고장 부품은 점검 권장·주의일 때만 보여 준다. 등급이 낮은 자전거에 '선정 사유'가 있으면 고장을 의심하는 것처럼 읽힌다
        if r is not None and r.grade in texts.GRADE_ORDER[:2] and isinstance(r.reasons, str) and r.reasons:
            ui.card_title("선정 사유", sub="등급을 매길 때 크게 작용한 요인입니다.")
            # 사유는 점검 대상 화면과 같은 짧은 말로(같은 것을 같은 말로 부른다). 여기서는 두 개로 자르지 않고 모두 보여 준다
            why = "".join(f"<div>{texts.short_reason(x)}</div>" for x in r.reasons.split(" / ") if x)
            pr = pd.Series({k: r[f"ratio_{e}"] for k, e in zip(texts.PARTS, ("tire", "chain", "saddle", "pedal"))}).dropna()
            st.markdown(f'<div class="lk-why">{why}</div>', unsafe_allow_html=True)
            rows([("의심 고장 부품", pr.idxmax() if len(pr) else "—")])
        else:
            ui.card_title("선정 사유")
            ui.empty(f"등급이 {grade}이라 선정 사유를 표시하지 않습니다.")

    # 두 줄 × 두 칸. 같은 줄의 두 카드는 높이를 맞춘다(height="stretch"): 위 줄은 글자 카드끼리, 아래 줄은 신고 띠와 지도
    for left, right in ((card_lists, card_reasons), (card_reports, card_place)):
        a, b = st.columns(2)
        for col, draw in ((a, left), (b, right)):
            with col:
                with st.container(border=True, height="stretch"):
                    draw()
ui.footer()
