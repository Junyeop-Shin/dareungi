"""한눈에 보기(홈) 화면(08a 설계서 3절).

배치: 머리 영역 → KPI 4개 → 주별 고장 신고 · 부품별 고장 신고 예상
      → 자치구별 점검 권장 · 점검 권장 자전거 목록 · 예상 정확도 → 고지문.
카드 제목만 읽어도 무엇을 세는지 알 수 있게 쓴다. 점검 권장 수와 타일맵은 최근 REPORT_DAYS일 안에
신고된 자전거를 뺀 수다(그 자전거들은 점검 대상 화면에서 따로 본다).
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[2]))
from admin import components as ui       # noqa: E402
from common import data, texts, theme    # noqa: E402

P1, P2 = "views/parts_forecast.py", "views/inspection.py"
TOP_H, BOT_H = 360, 540        # 같은 줄 카드의 높이를 맞춘다(좁은 화면에서는 theme.css가 내용만큼 늘린다)
ui.header("따릉이 정비 현황")
p = theme.P()

fc, risk, alerts, wa = data.load("forecast_weekly"), data.load("bike_risk_latest"), data.load("alerts_live"), data.load("weekly_actual")
risk_un = ui.exclude_reported(risk)                    # 최근 신고된 자전거는 점검 권장 집계에서 뺀다
risk_f, alerts_f = data.apply_gu(risk_un), data.apply_gu(alerts)
fc = fc[fc.pred.notna()] if fc is not None and len(fc) else fc
has_fc = fc is not None and not fc.empty
latest_fc = fc[fc.issue_date == fc.issue_date.max()].set_index("part") if has_fc else None
period = texts.week_range(fc.issue_date.max()) if has_fc else ""
wk = ui.complete_weeks(data.apply_gu(wa)) if wa is not None and len(wa) else None
seoul_only = " · 서울 전체 기준" if data.get_gu() else ""

# ── KPI 4개 ─────────────────────────────────────────────
st.write("")
k1, k2, k3, k4 = st.columns(4)
with k1:
    if has_fc and "전체" in latest_fc.index:
        r = latest_fc.loc["전체"]
        so_far, upto = ui.week_so_far(fc.issue_date.max())
        done = f"{ui.upto_label(upto)} {so_far.get('전체', 0):,.0f}건" if so_far else "아직 이번 주 집계 전"
        ui.kpi("이번 주 예상 고장 신고", f"{r.pred:,.0f}건", f"{period} · {done}<br>예상 범위 {r.lo80:,.0f}~{r.hi80:,.0f}건{seoul_only}", help=texts.RANGE_HELP)
    else:
        ui.kpi("이번 주 예상 고장 신고", "—", "매주 월요일 아침에 나옵니다")
with k2:
    if wk is not None and len(wk):
        g = wk.groupby("week")[["rentals", "reports"]].sum().sort_index()
        g = g[g.rentals > 0]
        rate = g.reports / g.rentals * 1000
        if len(rate) >= 2:
            d = rate.iloc[-1] - rate.iloc[-2]
            trend = "그 전 주와 같음" if abs(d) < 0.05 else f"그 전 주보다 {abs(d):.1f}건 {'적음' if d < 0 else '많음'}"
            # 증감은 색 없이 회색으로 둔다. 신고율이 내려갔다고 고장이 줄었다는 뜻은 아니다(08a 3-3, 04b)
            ui.kpi("지난주 대여 1,000건당 고장 신고", f"{rate.iloc[-1]:.1f}건", f"{texts.week_range(g.index[-1])} 실제 값<br>{trend}", help=texts.RATE_NOTE)
        else:
            ui.kpi("지난주 대여 1,000건당 고장 신고", "—", "주별 기록이 더 쌓이면 표시")
    else:
        ui.kpi("지난주 대여 1,000건당 고장 신고", "—", "주별 기록 없음")
with k3:
    if risk_f is not None and len(risk_f):
        n = int((risk_f.grade == "점검 권장").sum())
        ui.kpi("점검 권장 자전거", f"{n:,}대", "아직 신고되지 않은 자전거 중<br>다음 주 고장 신고 가능성 상위 5%", help=texts.GRADE_HELP)
    else:
        ui.kpi("점검 권장 자전거", "—", "선택한 지역에 자전거 없음")
with k4:
    n = int((alerts_f.alert_ts >= data.since(48)).sum()) if alerts_f is not None and len(alerts_f) else 0
    ui.kpi("대여 직후 반납이 반복된 자전거", f"{n:,}대", "최근 48시간 · 2~3분 만에 같은 곳 반납이 2번 연속" if n else "최근 48시간 동안 없음",
           help=texts.QUICK_RETURN_NOTE)
st.write("")

# ── 주별 고장 신고 / 부품별 예상 고장 신고 ───────────────────
c1, c2 = st.columns(2)
with c1:
    with st.container(border=True, height=TOP_H):
        ui.card_title("주별 고장 신고 건수", P1, sub="최근 12주 실제 신고와 예상 건수")
        if wk is not None and len(wk):
            w = wk.groupby("week").reports.sum().sort_index().tail(12)
            fig = go.Figure()
            tr = data.load("forecast_track")
            if tr is not None and len(tr) and not data.get_gu():
                t = tr[tr.part == "전체"].copy(); t["week"] = pd.to_datetime(t.week)
                t = t[t.week.isin(w.index)]
                fig.add_scatter(x=t.week, y=t.pred, mode="lines", name="예상 건수", line=dict(color=p["secondary"], dash="dot", width=1.6),
                                hovertemplate="%{x|%-m/%-d} 주 예상 %{y:,.0f}건<extra></extra>")
            fig.add_scatter(x=w.index, y=w.values, mode="lines+markers", name="실제 신고", line=dict(color=p["primary"], width=2.4), marker=dict(size=7),
                            hovertemplate="%{x|%-m/%-d} 주 실제 %{y:,.0f}건<extra></extra>")
            ticks = list(w.index[::4])
            labels = [f"{x.month}/{x.day}" for x in ticks]
            if has_fc and "전체" in latest_fc.index and not data.get_gu():
                r = latest_fc.loc["전체"]
                nx = data.shift_days(w.index.max(), 7)
                fig.add_shape(type="rect", x0=data.shift_days(nx, -3), x1=data.shift_days(nx, 3), y0=r.lo80, y1=r.hi80,
                              fillcolor=p["primary_dark"], opacity=0.35, line_width=0)
                fig.add_scatter(x=[nx], y=[r.pred], mode="markers", name="이번 주 예상",
                                marker=dict(size=10, color="rgba(0,0,0,0)", line=dict(color=p["primary_mid"], width=2)),
                                hovertemplate=f"이번 주 예상 {r.pred:,.0f}건 (범위 {r.lo80:,.0f}~{r.hi80:,.0f})<extra></extra>")
                fig.add_scatter(x=[None], y=[None], mode="markers", name="예상 범위", hoverinfo="skip",      # 위 상자의 범례용(그려지는 점은 없다)
                                marker=dict(symbol="square", size=11, color=p["primary_dark"], opacity=0.35))
                ticks.append(nx); labels.append("이번 주")
            fig.update_layout(height=230, xaxis=dict(tickvals=ticks, ticktext=labels, showgrid=False),
                              yaxis=dict(tickformat=","), legend=dict(y=-0.2))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            if data.get_gu():
                st.markdown(f'<div class="card-caption">{ui.scope_label()} 신고만 표시. 예상은 서울 전체로만 냅니다.</div>', unsafe_allow_html=True)
        else:
            ui.empty("주별 신고 기록이 아직 없습니다.")
with c2:
    with st.container(border=True, height=TOP_H):
        ui.card_title("부품별 고장 신고 예상", P1, sub=f"이번 주({period}) 신고 건수 예상{seoul_only}" if has_fc else None)
        if has_fc:
            f = latest_fc.reindex(texts.PARTS).dropna(subset=["pred"]).sort_values("pred")
            fig = go.Figure(go.Bar(y=f.index, x=f.pred, orientation="h", marker_color=p["primary"], width=0.42,
                                   customdata=list(zip(f.lo80, f.hi80)),
                                   hovertemplate="%{y}: 예상 %{x:,.0f}건<br>범위 %{customdata[0]:,.0f}~%{customdata[1]:,.0f}건<extra></extra>",
                                   error_x=dict(type="data", symmetric=False, array=f.hi80 - f.pred, arrayminus=f.pred - f.lo80,
                                                color=p["whisker"], thickness=1.6, width=7)))
            xmax = f.hi80.max() * 1.2
            for y, v in zip(f.index, f.pred):
                fig.add_annotation(x=xmax, y=y, text=f"<b>{v:,.0f}</b>", showarrow=False, xanchor="right", font=dict(size=15, color=p["text"]))
            fig.update_layout(height=230, showlegend=False, xaxis=dict(visible=False, range=[0, xmax]),
                              yaxis=dict(tickfont=dict(size=15), gridcolor="rgba(0,0,0,0)"), bargap=0.5)
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.markdown('<div class="card-caption">막대·오른쪽 숫자: 예상 건수 · 가는 선: 예상 범위</div>',
                        unsafe_allow_html=True)
        else:
            ui.empty("매주 월요일 아침에 이번 주 예상이 나옵니다.")

# ── 자치구별 점검 권장 / 점검 권장 자전거 목록 / 예상 정확도 ──────
c3, c4, c5 = st.columns([1.05, 1.2, 0.85])
with c3:
    with st.container(border=True, height=BOT_H):
        dr = data.load("daily_reports")
        yday = pd.to_datetime(dr.date).max() if dr is not None and len(dr) else None
        # 제목이 먼저, 바꿔 보는 버튼은 그 아래. 버튼 값은 세션에 남아 있어서 버튼을 그리기 전에 읽을 수 있다
        by_report = st.session_state.get("tile_view") == "어제 고장 신고" and yday is not None
        if by_report:
            ui.card_title("자치구별 어제 고장 신고", P2, sub="어제 하루 동안 접수된 고장 신고 건수")
        else:
            ui.card_title("자치구별 점검 권장 자전거", P2, sub="신고되지 않은 자전거 중 점검 권장 자전거")
        st.segmented_control("타일맵에 표시", ["점검 권장 자전거", "어제 고장 신고"], default="점검 권장 자전거",
                             key="tile_view", label_visibility="collapsed")
        if by_report:
            y = dr[(pd.to_datetime(dr.date) == yday) & (dr.part == "전체")].groupby("district").reports.sum()
            ui.tilemap(y.to_dict(), what="어제 고장 신고 건수")
        else:
            if risk_un is not None:
                ui.tilemap(risk_un[risk_un.grade == "점검 권장"].groupby("district").size().to_dict(), what="점검 권장 자전거 수")
            else:
                ui.empty("점검 권장 등급이 아직 없습니다.")
with c4:
    with st.container(border=True, height=BOT_H):
        ui.card_title("점검 권장 자전거 목록", P2, sub="아직 신고되지 않은 자전거 중 다음 주 고장 신고 가능성이 높은 순")
        if risk_f is not None and len(risk_f):
            ui.bike_rows(risk_f[risk_f.grade != "산정 제외"].sort_values("risk_ratio", ascending=False), n=5)
        else:
            ui.empty("선택한 지역에 점검 권장 자전거가 없습니다.")
with c5:
    with st.container(border=True, height=BOT_H):
        ui.card_title("예상 정확도", P1, sub="지난 8주 동안 예상 신고 건수가 실제와 얼마나 달랐는지(서울 전체)")
        c = ui.confidence()
        if c is None:
            ui.empty("예상 기록이 2주 이상 쌓이면 보여 드립니다.")
        else:
            t = c["track"]
            st.markdown(f'<div class="kpi-label">예상과 실제의 평균 차이</div><div class="big-num">{c["mae8"]:.0f}%</div>'
                        f'<div class="kpi-sub">주 평균 {abs(t.pred - t.actual).mean():,.0f}건 차이</div>', unsafe_allow_html=True)
            x = [f"{pd.Timestamp(w).month}/{pd.Timestamp(w).day}" for w in t.week]
            fig = go.Figure([go.Bar(x=x, y=t.actual, marker_color=p["primary"], name="실제", hovertemplate="%{x} 주 실제 %{y:,.0f}건<extra></extra>"),
                             go.Bar(x=x, y=t.pred, marker_color=p["secondary"], name="예상", hovertemplate="%{x} 주 예상 %{y:,.0f}건<extra></extra>")])
            fig.update_layout(height=250, barmode="group", bargap=0.35, bargroupgap=0.1, showlegend=False,
                              xaxis=dict(visible=False), yaxis=dict(visible=False), margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.markdown('<div class="card-caption">주별 실제(초록)와 예상(회색) 신고 건수</div>', unsafe_allow_html=True)
ui.footer()
