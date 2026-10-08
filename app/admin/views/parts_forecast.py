"""부품별 고장 예측(상세 ①) — 08a 설계서 4절. 2026-10-08 사람 요청: 인력 계산 삭제(근거 정보 없음), 제목만 읽어도 무엇을 세는지 알게, 라이트·다크.
요약 카드 5개 → 부품별 주별 신고 → 준비 수량 → 대여 1,000건당 고장 신고 → 요일별 신고 → 예측 정확도"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[2]))
from admin import components as ui       # noqa: E402
from common import data, texts, theme    # noqa: E402

ui.header("부품별 고장 예측", show_confidence=True)
p = theme.P()
st.markdown(f'<div class="card-caption">{texts.LABEL_NOTE}</div>', unsafe_allow_html=True)
st.write("")
PARTS5 = texts.PARTS + ["전체"]
fc, tr, wa, dr = data.load("forecast_weekly"), data.load("forecast_track"), data.load("weekly_actual"), data.load("daily_reports")
has_fc = fc is not None and not fc.empty and fc.pred.notna().any()
latest = fc[fc.issue_date == fc.issue_date.max()].set_index("part").reindex(PARTS5) if has_fc else None
period = texts.week_range(fc.issue_date.max()) if has_fc else ""
wk, last_actual = None, {}
if wa is not None and len(wa):
    wk = ui.complete_weeks(data.apply_gu(wa))
    lw = wk[wk.week == wk.week.max()]
    last_actual = {q: lw[q].sum() for q in texts.PARTS if q in lw} | {"전체": lw.reports.sum()}
gu_note = f" · 신고 기록은 {ui.scope_label()}, 예측은 서울 전체" if data.get_gu() else ""

# 1. 요약 카드
if has_fc:
    st.markdown(f'<div class="card-title">다음 7일({period}) 예상 고장 신고 건수</div>'
                f'<div class="card-sub">{pd.to_datetime(fc.issue_date.max()):%-m월 %-d일} 발행 · 서울 전체{gu_note}</div>', unsafe_allow_html=True)
    for c, q in zip(st.columns(5), PARTS5):
        r = latest.loc[q]
        d = f"<br>지난주 실제 {last_actual[q]:,.0f}건" if q in last_actual and not data.get_gu() else ""
        with c:
            with st.container(border=True):
                ui.kpi(q, f"{r.pred:,.0f}건", f"범위 {r.lo80:,.0f}~{r.hi80:,.0f}건{d}", help=texts.RANGE_HELP)
else:
    ui.empty("매주 월요일 아침에 다음 7일 예측이 나옵니다.")

# 2. 부품별 주별 신고
with st.container(border=True):
    ui.card_title("부품별 주별 고장 신고 건수 (최근 26주)", sub="부품을 고르면 그 부품의 실제 신고, 그때 예상한 건수, 다음 7일 예상을 함께 봅니다")
    part = st.pills("부품", PARTS5, default="전체", key="trend_part", label_visibility="collapsed") or "전체"
    if wk is not None and len(wk):
        col = "reports" if part == "전체" else part
        s = wk.groupby("week")[col].sum().sort_index().tail(26)
        fig = go.Figure()
        if tr is not None and len(tr) and not data.get_gu():
            t = tr[tr.part == part].copy(); t["week"] = pd.to_datetime(t.week)
            t = t[t.week.isin(s.index)]
            fig.add_scatter(x=t.week, y=t.pred, mode="lines", name="그때 예상한 건수", line=dict(color=p["secondary"], dash="dot"),
                            hovertemplate="%{x|%-m/%-d} 주 예상 %{y:,.0f}건<extra></extra>")
        fig.add_scatter(x=s.index, y=s.values, mode="markers+lines", name="실제 신고", line=dict(color=p["primary"], width=2.2),
                        hovertemplate="%{x|%-m/%-d} 주 실제 %{y:,.0f}건<extra></extra>")
        if has_fc and not data.get_gu():
            r = latest.loc[part]; nx = data.shift_days(s.index.max(), 7)
            fig.add_shape(type="rect", x0=data.shift_days(nx, -3), x1=data.shift_days(nx, 3), y0=r.lo80, y1=r.hi80,
                          fillcolor=p["primary_dark"], opacity=0.35, line_width=0)
            fig.add_scatter(x=[nx], y=[r.pred], mode="markers", name="다음 7일 예상", marker=dict(color=p["primary_mid"], size=10),
                            hovertemplate=f"다음 7일 예상 {r.pred:,.0f}건 (범위 {r.lo80:,.0f}~{r.hi80:,.0f})<extra></extra>")
        fig.update_layout(height=300, xaxis=dict(tickformat="%-m월", dtick="M1", showgrid=False), yaxis=dict(tickformat=","), legend=dict(y=-0.15))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    else:
        ui.empty("주별 신고 기록이 아직 없습니다.")

# 3. 준비 수량
with st.container(border=True):
    ui.card_title("다음 7일 부품 준비 수량", sub=f"{period} 동안의 예상 고장 신고 건수와, 부족하지 않으려면 준비해 둘 수량" if has_fc else None)
    if has_fc:
        fmt = lambda v: "—" if pd.isna(v) else f"{v:,.0f}건"            # 카드와 같은 반올림·쉼표
        tab = pd.DataFrame({"부품": PARTS5, "예상 신고": [fmt(v) for v in latest.pred],
                            "예상 범위": [f"{a:,.0f} ~ {b:,.0f}건" for a, b in zip(latest.lo80, latest.hi80)],
                            "준비 권장 수량": [fmt(v) for v in latest.hi80], "지난주 실제 신고": [fmt(last_actual.get(q, np.nan)) for q in PARTS5]})
        st.dataframe(tab, hide_index=True, width="stretch", column_config={"준비 권장 수량": st.column_config.TextColumn(help=texts.STOCK_HELP)})
        st.markdown(f'<div class="card-caption">준비 권장 수량 = {texts.STOCK_HELP} 신고 1건에 부품 1개가 든다고 가정했습니다.</div>', unsafe_allow_html=True)
    else:
        ui.empty("예측이 나오면 부품별 준비 수량을 보여 드립니다.")

# 4·5. 대여 1,000건당 고장 신고 / 요일별 신고
c3, c4 = st.columns(2)
with c3:
    with st.container(border=True):
        ui.card_title("대여 1,000건당 고장 신고 (최근 1년)", sub="이용량이 달라도 비교할 수 있게, 대여 1,000건마다 고장 신고가 몇 건 들어왔는지")
        if wk is not None and len(wk):
            g = wk.groupby("week")[["rentals", "reports"]].sum().sort_index().tail(52)
            g = g[g.rentals > 0]
            fig = go.Figure(go.Scatter(x=g.index, y=g.reports / g.rentals * 1000, mode="lines", line=dict(color=p["primary"], width=2),
                                       hovertemplate="%{x|%-m/%-d} 주 대여 1,000건당 %{y:.1f}건<extra></extra>"))
            fig.update_layout(height=230, xaxis=dict(tickformat="%-m월", showgrid=False), yaxis=dict(rangemode="tozero"))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        else:
            ui.empty("주별 신고 기록이 아직 없습니다.")
        st.markdown(f'<div class="card-caption">{texts.RATE_NOTE}</div>', unsafe_allow_html=True)
with c4:
    with st.container(border=True):
        ui.card_title("요일별 고장 신고 건수 (최근 12주)", sub="어느 요일에 신고가 몰리는지. 칸 하나 = 그 주 그 요일의 신고 건수")
        if dr is not None and len(dr):
            d = data.apply_gu(dr[dr.part == "전체"]).groupby("date").reports.sum().reset_index()
            d["date"] = pd.to_datetime(d.date)
            d = d[d.date >= data.shift_days(d.date.max(), -83)]
            d["week"] = pd.to_datetime(d.date.dt.to_period("W-SUN").dt.start_time)
            d["dow"] = d.date.dt.weekday
            pv = d.pivot_table(index="dow", columns="week", values="reports", aggfunc="sum")
            fig = go.Figure(go.Heatmap(z=pv.values, x=[f"{c.month}/{c.day}" for c in pv.columns], y=[list("월화수목금토일")[i] for i in pv.index],
                                       colorscale=[[0, p["primary_light"]], [1, p["primary_dark"]]], xgap=2, ygap=2, showscale=False,
                                       hovertemplate="%{x} 주 %{y}요일 %{z:,.0f}건<extra></extra>"))
            fig.update_yaxes(autorange="reversed")
            fig.update_layout(height=230)
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.markdown('<div class="card-caption">색이 진할수록 신고가 많은 날 · 아래 날짜 = 그 주 월요일</div>', unsafe_allow_html=True)
        else:
            ui.empty("일별 신고 기록이 아직 없습니다.")

# 6. 예측 정확도
with st.container(border=True):
    ui.card_title("예측 정확도 (최근 8주, 서울 전체)", sub="예상한 신고 건수가 실제와 얼마나 달랐는지. '지난주와 같다'고 봤을 때와 비교합니다")
    if tr is not None and tr.dropna(subset=["actual"]).week.nunique() >= 2:
        t = tr.dropna(subset=["actual"])
        t = t[t.week.isin(sorted(t.week.unique())[-8:])]
        rows = []
        for q in PARTS5:
            x = t[t.part == q]
            mae = (abs(x.pred - x.actual) / x.actual).mean() * 100
            imp = 1 - abs(x.pred - x.actual).mean() / abs(x.naive - x.actual).mean()
            rows.append({"부품": q, "예상과 실제의 평균 차이": mae, "'지난주와 같다'보다": imp * 100})
        acc = pd.DataFrame(rows)
        a, b = st.columns([1.1, 1.4])
        with a:
            st.dataframe(acc, hide_index=True, width="stretch", column_config={
                "예상과 실제의 평균 차이": st.column_config.NumberColumn(format="%.0f%%"),
                "'지난주와 같다'보다": st.column_config.NumberColumn(format="%+.0f%%", help="+면 예측이 실제에 더 가까웠고, −면 '지난주와 같다'가 더 가까웠습니다")})
        with b:
            e = t[t.part == "전체"].sort_values("week")
            x = [f"{pd.Timestamp(w).month}/{pd.Timestamp(w).day}" for w in e.week]
            fig = go.Figure([go.Bar(x=x, y=abs(e.pred - e.actual) / e.actual * 100, name="예측", marker_color=p["primary"],
                                    hovertemplate="%{x} 주 예측의 차이 %{y:.0f}%<extra></extra>"),
                             go.Bar(x=x, y=abs(e.naive - e.actual) / e.actual * 100, name="지난주와 같다고 봤을 때", marker_color=p["naive"],
                                    hovertemplate="%{x} 주 '지난주와 같다'의 차이 %{y:.0f}%<extra></extra>")])
            fig.update_layout(height=220, barmode="group", yaxis=dict(ticksuffix="%"), legend=dict(y=-0.2))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        st.markdown('<div class="card-caption">예측 방법: 최근 4주 동안 대여 1건당 신고 비율 × 지난주 대여 건수. 지난 주들은 그 시점까지의 기록만으로 같은 방법을 적용해 다시 계산했습니다.</div>',
                    unsafe_allow_html=True)
    else:
        ui.empty("예측 기록이 2주 이상 쌓이면 보여 드립니다.")
ui.footer()
