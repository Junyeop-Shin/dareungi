"""한눈에 보기(홈) — 사람이 준 시안(2026-10-08) + 08a 설계서 3절. 제목만 읽어도 무엇을 세는지 알게 고침(2026-10-08 사람 요청),
라이트·다크 두 테마, 점검 권장 목록·타일맵은 최근 14일 신고된 자전거를 뺀 기준.
머리 영역 → KPI 4개 → 부품별 예상 고장 신고 · 주별 고장 신고 → 자치구별 점검 권장 · 먼저 점검할 자전거 · 예측 정확도 → 고지"""
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[2]))
from admin import components as ui       # noqa: E402
from common import data, texts, theme    # noqa: E402

P1, P2 = "views/parts_forecast.py", "views/inspection.py"
ui.header("따릉이 정비 현황")
p = theme.P()

fc, risk, alerts, wa = data.load("forecast_weekly"), data.load("bike_risk_latest"), data.load("alerts_live"), data.load("weekly_actual")
risk_un = ui.exclude_reported(risk)                    # 최근 14일 신고된 자전거는 '이미 신고된 자전거'로 따로 본다
risk_f, alerts_f = data.apply_gu(risk_un), data.apply_gu(alerts)
has_fc = fc is not None and not fc.empty and fc.pred.notna().any()
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
        ui.kpi("다음 7일 예상 고장 신고", f"{r.pred:,.0f}건", f"{period}<br>예상 범위 {r.lo80:,.0f}~{r.hi80:,.0f}건{seoul_only}", help=texts.RANGE_HELP)
    else:
        ui.kpi("다음 7일 예상 고장 신고", "—", "매주 월요일 아침에 나옵니다")
with k2:
    if wk is not None and len(wk):
        g = wk.groupby("week")[["rentals", "reports"]].sum().sort_index()
        g = g[g.rentals > 0]
        rate = g.reports / g.rentals * 1000
        if len(rate) >= 2:
            d = rate.iloc[-1] - rate.iloc[-2]
            trend = "그 전 주와 같음" if abs(d) < 0.05 else f"그 전 주보다 {abs(d):.1f}건 {'적음' if d < 0 else '많음'}"
            # 증감 색은 중립(회색): 신고율 하락이 고장 감소를 뜻하지 않으므로(08a 3-3, 04b 결과)
            ui.kpi("지난주 대여 1,000건당 고장 신고", f"{rate.iloc[-1]:.1f}건", f"{g.index[-1]:%-m/%-d}~{data.shift_days(g.index[-1], 6):%-m/%-d} 실제 값<br>{trend}", help=texts.RATE_NOTE)
        else:
            ui.kpi("지난주 대여 1,000건당 고장 신고", "—", "주별 기록이 더 쌓이면 표시")
    else:
        ui.kpi("지난주 대여 1,000건당 고장 신고", "—", "주별 기록 없음")
with k3:
    if risk_f is not None and len(risk_f):
        n = int((risk_f.grade == "점검 권장").sum())
        n_rep = len(ui.recent_reported_ids())
        ui.kpi("점검 권장 자전거", f"{n:,}대", f"고장 신고 가능성 상위 5%<br>최근 {texts.REPORT_DAYS}일 신고된 {n_rep:,}대는 따로 집계", help=texts.GRADE_HELP)
    else:
        ui.kpi("점검 권장 자전거", "—", "선택한 지역에 자전거 없음")
with k4:
    n = int((alerts_f.alert_ts >= data.since(48)).sum()) if alerts_f is not None and len(alerts_f) else 0
    ui.kpi("빌리자마자 반납이 반복된 자전거", f"{n:,}대", f"최근 48시간 · 2~3분 만에 같은 곳 반납이 2번 연속" if n else "최근 48시간 동안 없음",
           value_color=p["danger"] if n else None, help=texts.QUICK_RETURN_NOTE)
st.write("")

# ── 부품별 예상 고장 신고 / 주별 고장 신고 ───────────────────
c1, c2 = st.columns(2)
with c1:
    with st.container(border=True):
        ui.card_title("부품별 예상 고장 신고", P1, "부품별 고장 예측", sub=f"다음 7일({period}) 동안 들어올 것으로 예상되는 신고 건수{seoul_only}" if has_fc else None)
        if has_fc:
            f = latest_fc.reindex(texts.PARTS).dropna(subset=["pred"]).sort_values("pred")
            fig = go.Figure(go.Bar(y=f.index, x=f.pred, orientation="h", marker_color=p["primary"], width=0.42,
                                   customdata=list(zip(f.lo80, f.hi80)),
                                   hovertemplate="%{y}: 예상 %{x:,.0f}건<br>범위 %{customdata[0]:,.0f}~%{customdata[1]:,.0f}건<extra></extra>",
                                   error_x=dict(type="data", symmetric=False, array=f.hi80 - f.pred, arrayminus=f.pred - f.lo80,
                                                color=p["whisker"], thickness=1.6, width=7)))
            xmax = f.hi80.max() * 1.2
            for y, v in zip(f.index, f.hi80):
                fig.add_annotation(x=xmax, y=y, text=f"<b>{v:,.0f}</b>", showarrow=False, xanchor="right", font=dict(size=15, color=p["text"]))
            fig.update_layout(height=230, showlegend=False, xaxis=dict(visible=False, range=[0, xmax]),
                              yaxis=dict(tickfont=dict(size=15), gridcolor="rgba(0,0,0,0)"), bargap=0.5)
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.markdown('<div class="card-caption">막대: 예상 건수 · 가는 선: 예상 범위 · 오른쪽 숫자: 준비해 두면 좋은 부품 수(범위의 위쪽 끝)</div>',
                        unsafe_allow_html=True)
        else:
            ui.empty("매주 월요일 아침에 다음 7일 예측이 나옵니다.")
with c2:
    with st.container(border=True):
        ui.card_title("주별 고장 신고 건수", P1, "자세히", sub=f"최근 12주 실제 신고와 그때 예상한 건수{'' if data.get_gu() else ', 다음 7일 예상'}")
        if wk is not None and len(wk):
            w = wk.groupby("week").reports.sum().sort_index().tail(12)
            fig = go.Figure()
            tr = data.load("forecast_track")
            if tr is not None and len(tr) and not data.get_gu():
                t = tr[tr.part == "전체"].copy(); t["week"] = pd.to_datetime(t.week)
                t = t[t.week.isin(w.index)]
                fig.add_scatter(x=t.week, y=t.pred, mode="lines", name="그때 예상한 건수", line=dict(color=p["secondary"], dash="dot", width=1.6),
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
                fig.add_scatter(x=[nx], y=[r.pred], mode="markers", name="다음 7일 예상",
                                marker=dict(size=10, color="rgba(0,0,0,0)", line=dict(color=p["primary_mid"], width=2)),
                                hovertemplate=f"다음 7일 예상 {r.pred:,.0f}건 (범위 {r.lo80:,.0f}~{r.hi80:,.0f})<extra></extra>")
                ticks.append(nx); labels.append("다음 7일")
            fig.update_layout(height=230, xaxis=dict(tickvals=ticks, ticktext=labels, showgrid=False),
                              yaxis=dict(tickformat=","), legend=dict(y=-0.2))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            if data.get_gu():
                st.markdown(f'<div class="card-caption">{ui.scope_label()} 신고만 표시. 예측은 서울 전체로만 냅니다.</div>', unsafe_allow_html=True)
        else:
            ui.empty("주별 신고 기록이 아직 없습니다.")

# ── 자치구별 점검 권장 / 먼저 점검할 자전거 / 예측 정확도 ──────
c3, c4, c5 = st.columns([1.05, 1.2, 0.85])
with c3:
    with st.container(border=True):
        dr = data.load("daily_reports")
        yday = pd.to_datetime(dr.date).max() if dr is not None and len(dr) else None
        view = st.segmented_control("타일맵에 표시", [f"점검 권장 자전거", "어제 고장 신고"], default="점검 권장 자전거",
                                    key="tile_view", label_visibility="collapsed") or "점검 권장 자전거"
        if view == "어제 고장 신고" and yday is not None:
            ui.card_title("자치구별 어제 고장 신고", P2, "지도",
                          sub=f"{yday.month}/{yday.day}({'월화수목금토일'[yday.weekday()]}) 하루 동안 접수된 신고 건수(신고 직전 마지막 반납 대여소의 구)")
            y = dr[(pd.to_datetime(dr.date) == yday) & (dr.part == "전체")].groupby("district").reports.sum()
            ui.tilemap(y.to_dict(), what="어제 고장 신고 건수")
        else:
            ui.card_title("자치구별 점검 권장 자전거", P2, "지도", sub=f"최근 {texts.REPORT_DAYS}일 안에 신고된 자전거는 뺀 수")
            if risk_un is not None:
                ui.tilemap(risk_un[risk_un.grade == "점검 권장"].groupby("district").size().to_dict(), what="점검 권장 자전거 수")
            else:
                ui.empty("점검 권장 등급이 아직 없습니다.")
with c4:
    with st.container(border=True):
        ui.card_title("먼저 점검할 자전거", P2, "전체 목록", sub=f"아직 고장 신고되지 않은 자전거 중 고장 신고 가능성 상위 5대")
        if risk_f is not None and len(risk_f):
            ui.bike_rows(risk_f[risk_f.grade != "산정 제외"].sort_values("rate_1000km", ascending=False), n=5)
        else:
            ui.empty("선택한 지역에 점검 권장 자전거가 없습니다.")
with c5:
    with st.container(border=True):
        ui.card_title("예측 정확도", sub="지난 8주 동안 예상 신고 건수가 실제와 얼마나 달랐는지")
        c = ui.confidence()
        if c is None:
            ui.empty("예측 기록이 2주 이상 쌓이면 보여 드립니다.")
        else:
            t = c["track"]
            col = p["ok"] if c["improve"] >= 0 else p["danger"]
            st.markdown(f'<div class="kpi-label">예상과 실제의 평균 차이</div><div class="big-num">{c["mae8"]:.0f}%</div>'
                        f'<div style="color:{col};font-weight:600;font-size:0.9rem">{ui.improve_text(c["improve"])}</div>',
                        unsafe_allow_html=True, help=texts.CONFIDENCE_HELP)
            e_m = abs(t.pred - t.actual) / t.actual * 100
            e_n = abs(t.naive - t.actual) / t.actual * 100
            x = [f"{pd.Timestamp(w).month}/{pd.Timestamp(w).day}" for w in t.week]
            fig = go.Figure([go.Bar(x=x, y=e_m, marker_color=p["primary"], name="예측", hovertemplate="%{x} 주 예측의 차이 %{y:.0f}%<extra></extra>"),
                             go.Bar(x=x, y=e_n, marker_color=p["naive"], name="지난주와 같다고 봄",
                                    hovertemplate="%{x} 주 '지난주와 같다'의 차이 %{y:.0f}%<extra></extra>")])
            fig.update_layout(height=130, barmode="group", bargap=0.35, bargroupgap=0.1, showlegend=False,
                              xaxis=dict(visible=False), yaxis=dict(visible=False), margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.markdown('<div class="card-caption">주별로 실제와 차이 난 정도 · 초록 = 예측, 회색 = 지난주와 같다고 봤을 때</div>', unsafe_allow_html=True)
ui.footer()
