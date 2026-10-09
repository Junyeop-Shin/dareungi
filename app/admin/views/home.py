"""한눈에 보기(홈) 화면(08a 설계서 3절).

배치: 머리 영역 → KPI 4개 → 주별 고장 신고 · 유형별 고장 신고 예측
      → 자치구별 점검 권장 · 점검 권장 자전거 목록 · 예측 정확도 → 고지문.
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

# ── KPI 4개: 상자 없이 세로선으로 나눈 한 줄(ui.kpi_strip) ─────────────
kpis = []
if has_fc and "전체" in latest_fc.index:
    r = latest_fc.loc["전체"]
    so_far, upto = ui.week_so_far(fc.issue_date.max())
    done = f"{ui.upto_label(upto)} {so_far.get('전체', 0):,.0f}건" if so_far else "아직 이번 주 집계 전"
    kpis.append(dict(label="이번 주 고장 신고 예측", value=f"{r.pred:,.0f}", unit="건", help=texts.RANGE_HELP,
                     sub=f"{period} · {done}<br>예측 구간 {r.lo80:,.0f}~{r.hi80:,.0f}건{seoul_only}"))
else:
    kpis.append(dict(label="이번 주 고장 신고 예측", value="—", sub="매주 월요일 아침에 나옵니다"))
rate_kpi = dict(label="지난주 대여 1,000건당 고장 신고", value="—", sub="주별 기록 없음")
if wk is not None and len(wk):
    g = wk.groupby("week")[["rentals", "reports"]].sum().sort_index()
    g = g[g.rentals > 0]
    rate = g.reports / g.rentals * 1000
    rate_kpi["sub"] = "주별 기록이 더 쌓이면 표시"
    if len(rate) >= 2:
        d = rate.iloc[-1] - rate.iloc[-2]
        trend = "그 전 주와 같음" if abs(d) < 0.05 else f"그 전 주보다 {abs(d):.1f}건 {'적음' if d < 0 else '많음'}"
        # 증감은 색 없이 회색으로 둔다. 신고율이 내려갔다고 고장이 줄었다는 뜻은 아니다(08a 3-3, 04b)
        rate_kpi = dict(label=rate_kpi["label"], value=f"{rate.iloc[-1]:.1f}", unit="건", help=texts.RATE_NOTE, sub=f"{texts.week_range(g.index[-1])} 실제 값<br>{trend}")
kpis.append(rate_kpi)
if risk_f is not None and len(risk_f):
    kpis.append(dict(label="점검 권장 자전거", value=f"{int((risk_f.grade == '점검 권장').sum()):,}", unit="대", help=texts.GRADE_HELP,
                     sub="아직 신고되지 않은 자전거 중<br>다음 주 고장 신고 가능성 상위 5%"))
else:
    kpis.append(dict(label="점검 권장 자전거", value="—", sub="선택한 지역에 자전거 없음"))
n = int((alerts_f.alert_ts >= data.since(48)).sum()) if alerts_f is not None and len(alerts_f) else 0
kpis.append(dict(label="대여 직후 반납이 반복된 자전거", value=f"{n:,}", unit="대", help=texts.QUICK_RETURN_NOTE,
                 sub="최근 48시간 · 2~3분 만에 같은 곳 반납이 2번 이상 연속" if n else "최근 48시간 동안 없음"))
ui.kpi_strip(kpis)

# ── 주별 고장 신고 / 유형별 예측 고장 신고 ───────────────────
c1, c2 = st.columns(2)
with c1:
    with st.container(border=True, height=TOP_H):
        ui.card_title("주별 고장 신고 건수", P1, sub="최근 12주 실제 신고와 예측 건수")
        if wk is not None and len(wk):
            w = wk.groupby("week").reports.sum().sort_index().tail(12)
            fig = go.Figure()
            tr = data.load("forecast_track")
            if tr is not None and len(tr) and not data.get_gu():
                t = tr[tr.part == "전체"].copy(); t["week"] = pd.to_datetime(t.week)
                t = t[t.week.isin(w.index)]
                fig.add_scatter(x=t.week, y=t.pred, mode="lines", name="예측 건수", line=dict(color=p["secondary"], dash="dot", width=1.6),
                                hovertemplate="%{x|%-m/%-d} 주 예측 %{y:,.0f}건<extra></extra>")
            fig.add_scatter(x=w.index, y=w.values, mode="lines+markers", name="실제 신고", line=dict(color=p["primary"], width=2.4), marker=dict(size=7),
                            hovertemplate="%{x|%-m/%-d} 주 실제 %{y:,.0f}건<extra></extra>")
            ticks = list(w.index[::4])
            labels = [f"{x.month}/{x.day}" for x in ticks]
            if has_fc and "전체" in latest_fc.index and not data.get_gu():
                r = latest_fc.loc["전체"]
                nx = data.shift_days(w.index.max(), 7)
                fig.add_shape(type="rect", x0=data.shift_days(nx, -3), x1=data.shift_days(nx, 3), y0=r.lo80, y1=r.hi80,
                              fillcolor=p["primary_dark"], opacity=0.35, line_width=0)
                fig.add_scatter(x=[nx], y=[r.pred], mode="markers", name="이번 주 예측",
                                marker=dict(size=10, color="rgba(0,0,0,0)", line=dict(color=p["primary_mid"], width=2)),
                                hovertemplate=f"이번 주 예측 {r.pred:,.0f}건 (범위 {r.lo80:,.0f}~{r.hi80:,.0f})<extra></extra>")
                fig.add_scatter(x=[None], y=[None], mode="markers", name="예측 구간", hoverinfo="skip",      # 위 상자의 범례용(그려지는 점은 없다)
                                marker=dict(symbol="square", size=11, color=p["primary_dark"], opacity=0.35))
                ticks.append(nx); labels.append("이번 주")
            fig.update_layout(height=225 if data.get_gu() else 252, xaxis=dict(tickvals=ticks, ticktext=labels, showgrid=False),      # 구를 고르면 아래에 안내 한 줄이 붙는다
                              yaxis=dict(tickformat=","))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            if data.get_gu():
                st.markdown(f'<div class="card-caption">{ui.scope_label()} 신고만 표시. 예측은 서울 전체로만 냅니다.</div>', unsafe_allow_html=True)
        else:
            ui.empty("주별 신고 기록이 아직 없습니다.")
with c2:
    with st.container(border=True, height=TOP_H):
        ui.card_title("유형별 고장 신고 예측", P1, sub=f"이번 주({period}) 신고 건수 예측{seoul_only}" if has_fc else None, help=texts.KIND_NOTE)
        if has_fc:
            f = latest_fc.reindex(ui.kind_order(latest_fc)[::-1])          # 위에서부터 예측 건수가 많은 유형 순, 맨 아래 기타(가로 막대는 아래부터 그려서 뒤집는다)
            fig = go.Figure(go.Bar(y=f.index, x=f.pred, orientation="h", marker_color=p["primary"], width=0.5,
                                   customdata=list(zip(f.lo80, f.hi80)),
                                   hovertemplate="예측 구간 %{customdata[0]:,.0f}~%{customdata[1]:,.0f}건<extra></extra>",      # 예측 건수는 옆 숫자에 있다
                                   error_x=dict(type="data", symmetric=False, array=f.hi80 - f.pred, arrayminus=f.pred - f.lo80,
                                                color=p["whisker"], thickness=1.6, width=7)))
            xmax = f.hi80.max() * 1.2
            for y, v in zip(f.index, f.pred):
                fig.add_annotation(x=xmax, y=y, text=f"<b>{v:,.0f}</b>", showarrow=False, xanchor="right", font=dict(size=14, color=p["text"]))
            fig.update_layout(height=236, showlegend=False, xaxis=dict(visible=False, range=[0, xmax]),
                              yaxis=dict(tickfont=dict(size=14), gridcolor="rgba(0,0,0,0)"), bargap=0.5)
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.markdown('<div class="card-caption">막대·오른쪽 숫자: 예측 건수 | 가는 선: 예측 구간</div>',
                        unsafe_allow_html=True)
        else:
            ui.empty("매주 월요일 아침에 이번 주 예측이 나옵니다.")

# ── 자치구별 점검 권장 / 점검 권장 자전거 목록 / 예측 정확도 ──────
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
        ui.card_title("점검 권장 자전거 목록", P2, sub="아직 신고되지 않은 자전거 중 다음 주 고장 신고 가능성이 높은 순", help=texts.SCORE_HELP)
        if risk_f is not None and len(risk_f):
            ui.bike_rows(risk_f[risk_f.grade != "산정 제외"].sort_values("risk_ratio", ascending=False), n=5)
        else:
            ui.empty("선택한 지역에 점검 권장 자전거가 없습니다.")
with c5:
    with st.container(border=True, height=BOT_H):
        ui.card_title("예측 정확도", P1, sub="지난 8주 동안 예측 신고 건수와 실제 신고 건수의 차이입니다(서울 전체).")
        c = ui.confidence()
        if c is None:
            ui.empty("예측 기록이 2주 이상 쌓이면 보여 드립니다.")
        else:
            t = c["track"]
            st.markdown(f'<div class="kpi-label">예측과 실제의 평균 차이</div><div class="big-num">{c["mae8"]:.0f}%</div>'
                        f'<div class="kpi-sub">주 평균 {abs(t.pred - t.actual).mean():,.0f}건 차이</div>', unsafe_allow_html=True)
            x = [f"{pd.Timestamp(w).month}/{pd.Timestamp(w).day}" for w in t.week]
            fig = go.Figure([go.Bar(x=x, y=t.actual, marker_color=p["primary"], name="실제", hovertemplate="%{x} 주 실제 %{y:,.0f}건<extra></extra>"),
                             go.Bar(x=x, y=t.pred, marker_color=p["secondary"], name="예측", hovertemplate="%{x} 주 예측 %{y:,.0f}건<extra></extra>")])
            fig.update_layout(height=296, barmode="group", bargap=0.35, bargroupgap=0.1, showlegend=False,      # 옆 두 카드의 아래 끝과 맞는 높이
                              xaxis=dict(type="category", showgrid=False, tickfont=dict(size=10), tickangle=0), yaxis=dict(visible=False), margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.markdown('<div class="card-caption">주별 실제(초록)와 예측(회색) 신고 건수</div>', unsafe_allow_html=True)
ui.footer()
