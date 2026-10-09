"""부품별 고장 신고 예상 화면(08a 설계서 4절).

배치: 요약 카드 5개(전체 → 부품) → 부품별 주별 신고 → 예상 정확도 → 예상 방법.
예상은 서울 전체로만 낸다. 그래서 이 화면에는 지역 선택기를 두지 않고, 다른 화면에서 고른 자치구도 적용하지 않는다
(구를 고르면 카드는 그대로인데 그래프 실제값만 바뀌어 헷갈렸다. 2026-10-09 UX 검토).
주의: 정비 인력 환산은 근거 자료(인당 처리량 등)가 없어 넣지 않았다. 준비 수량 표, 대여 1,000건당 추이,
요일별 히트맵은 위 카드·그래프와 겹치거나 이 화면의 목적(부품별 예측)과 멀어 뺐다.
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[2]))
from admin import components as ui       # noqa: E402
from common import data, texts, theme    # noqa: E402

ui.header("부품별 고장 신고 예상", show_confidence=True, show_filter=False)
p = theme.P()
st.markdown(f'<div class="card-caption">{texts.LABEL_NOTE}</div>', unsafe_allow_html=True)
st.write("")
PARTS5 = ["전체"] + texts.PARTS          # 전체를 맨 왼쪽에
fc, tr, wa = data.load("forecast_weekly"), data.load("forecast_track"), data.load("weekly_actual")
fc = fc[fc.pred.notna()] if fc is not None and len(fc) else fc
has_fc = fc is not None and not fc.empty
latest = fc[fc.issue_date == fc.issue_date.max()].set_index("part").reindex(PARTS5) if has_fc else None
period = texts.week_range(fc.issue_date.max()) if has_fc else ""
wk, last_actual = None, {}
if wa is not None and len(wa):
    wk = ui.complete_weeks(wa)
    lw = wk[wk.week == wk.week.max()]
    last_actual = {q: lw[q].sum() for q in texts.PARTS if q in lw} | {"전체": lw.reports.sum()}

# 1. 요약 카드
if has_fc:
    so_far, upto = ui.week_so_far(fc.issue_date.max())
    upto_label = ui.upto_label(upto)                   # 일 단위 집계라 보통 '어제까지'. 수집이 밀렸으면 날짜로
    st.markdown(f'<div class="card-title" role="heading" aria-level="2">이번 주({period}) 예상 고장 신고 건수</div>'
                f'<div class="card-sub">{pd.to_datetime(fc.issue_date.max()):%-m/%-d} 계산 · 서울 전체</div>', unsafe_allow_html=True)
    for c, q in zip(st.columns(5), PARTS5):
        r = latest.loc[q]
        lines = []
        if q in last_actual:
            lines.append(f"지난주 {last_actual[q]:,.0f}건")
        if so_far:
            lines.append(f"{upto_label} {so_far.get(q, 0):,.0f}건")
        with c:
            with st.container(border=True):
                st.markdown(f'<div class="kpi-center" title="{texts.RANGE_HELP}"><div class="kpi-label">{q}</div>'
                            f'<div class="kpi-value">{r.pred:,.0f}건</div><div class="kpi-sub">예상 범위 {r.lo80:,.0f}~{r.hi80:,.0f}건</div>'
                            f'<div class="kpi-sub" style="margin-top:0">{"<br>".join(lines)}</div></div>', unsafe_allow_html=True)
else:
    ui.empty("매주 월요일 아침에 이번 주 예상이 나옵니다.")

# 2. 부품별 주별 신고
with st.container(border=True):
    ui.card_title("부품별 주별 고장 신고 건수 (최근 26주)", sub="부품을 고르면 그 부품의 실제 신고, 그때 예상한 건수, 이번 주 예상을 함께 봅니다")
    part = st.pills("부품", PARTS5, default="전체", key="trend_part", label_visibility="collapsed") or "전체"
    if wk is not None and len(wk):
        col = "reports" if part == "전체" else part
        s = wk.groupby("week")[col].sum().sort_index().tail(26)
        fig = go.Figure()
        if tr is not None and len(tr):
            t = tr[tr.part == part].copy(); t["week"] = pd.to_datetime(t.week)
            t = t[t.week.isin(s.index)]
            fig.add_scatter(x=t.week, y=t.pred, mode="lines", name="예상 건수", line=dict(color=p["secondary"], dash="dot"),
                            hovertemplate="%{x|%-m/%-d} 주 예상 %{y:,.0f}건<extra></extra>")
        fig.add_scatter(x=s.index, y=s.values, mode="markers+lines", name="실제 신고", line=dict(color=p["primary"], width=2.2),
                        hovertemplate="%{x|%-m/%-d} 주 실제 %{y:,.0f}건<extra></extra>")
        if has_fc:
            r = latest.loc[part]; nx = data.shift_days(s.index.max(), 7)
            fig.add_shape(type="rect", x0=data.shift_days(nx, -3), x1=data.shift_days(nx, 3), y0=r.lo80, y1=r.hi80,
                          fillcolor=p["primary_dark"], opacity=0.35, line_width=0)
            fig.add_scatter(x=[nx], y=[r.pred], mode="markers", name="이번 주 예상", marker=dict(color=p["primary_mid"], size=10),
                            hovertemplate=f"이번 주 예상 {r.pred:,.0f}건 (범위 {r.lo80:,.0f}~{r.hi80:,.0f})<extra></extra>")
            fig.add_scatter(x=[None], y=[None], mode="markers", name="예상 범위", hoverinfo="skip",      # 위 상자의 범례용(그려지는 점은 없다)
                            marker=dict(symbol="square", size=11, color=p["primary_dark"], opacity=0.35))
        fig.update_layout(height=300, xaxis=dict(tickformat="%-m월", dtick="M1", showgrid=False), yaxis=dict(tickformat=","), legend=dict(y=-0.15))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    else:
        ui.empty("주별 신고 기록이 아직 없습니다.")

# 3. 예상 정확도
with st.container(border=True):
    ui.card_title("예상 정확도 (최근 8주, 서울 전체)", sub="그 주 월요일에 낸 예상 신고 건수와 실제 신고 건수가 얼마나 달랐는지")
    if tr is not None and tr.dropna(subset=["actual"]).week.nunique() >= 2:
        t = tr.dropna(subset=["actual"])
        t = t[t.week.isin(sorted(t.week.unique())[-8:])]
        acc = pd.DataFrame([{"부품": q, "평균 차이(%)": (abs(x.pred - x.actual) / x.actual).mean() * 100, "평균 차이(건)": abs(x.pred - x.actual).mean()}
                            for q in PARTS5 for x in [t[t.part == q]]])
        a, b = st.columns([1.1, 1.4])
        with a:
            st.dataframe(acc, hide_index=True, width="stretch", column_config={
                "평균 차이(%)": st.column_config.NumberColumn(format="%.0f%%", help="주마다 |예상 − 실제| ÷ 실제를 8주 평균한 값"),
                "평균 차이(건)": st.column_config.NumberColumn(format="%,.0f건", help="주마다 |예상 − 실제|를 8주 평균한 값")})
        with b:
            e = t[t.part == "전체"].sort_values("week")
            x = [f"{pd.Timestamp(w).month}/{pd.Timestamp(w).day}" for w in e.week]
            fig = go.Figure([go.Bar(x=x, y=e.actual, name="실제 신고", marker_color=p["primary"], hovertemplate="%{x} 주 실제 %{y:,.0f}건<extra></extra>"),
                             go.Bar(x=x, y=e.pred, name="예상 신고", marker_color=p["secondary"], hovertemplate="%{x} 주 예상 %{y:,.0f}건<extra></extra>")])
            fig.update_layout(height=220, barmode="group", yaxis=dict(tickformat=","), legend=dict(y=-0.2))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    else:
        ui.empty("예상 기록이 2주 이상 쌓이면 보여 드립니다.")

# 4. 예상 방법: 만드는 법 · 얼마나 맞나 · 주의할 점은 늘 보이고, 기술적인 내용(구간·변수·학습)은 접어 둔다
# 소제목·글자 크기·색은 위 카드들과 같게(카드 제목 + 설명 글자). 크기·색은 theme.css의 .st-key-method_box
with st.container(border=True, key="method_box"):
    ui.card_title("예상 방법")
    st.markdown(texts.METHOD_HOW)
    ui.card_title("과거 기록으로 확인한 정확도")
    acc_hist = data.load("forecast_accuracy")           # 저장된 결과 표에서 발행 작업이 뽑아 둔 숫자(손으로 옮겨 적지 않는다)
    if acc_hist is not None and len(acc_hist):
        st.markdown(texts.METHOD_CHECK.strip() + "\n" + "\n".join(f"| {r.period} | 약 {r.pred:.0f}% | 약 {r.naive:.0f}% |" for r in acc_hist.itertuples()))
    else:
        ui.empty("검증 결과 표를 아직 발행하지 않았습니다.")
    st.caption(texts.METHOD_NOTE)
    ui.card_title("유의 사항")
    st.markdown(texts.METHOD_CAUTION)
    with st.expander("자세한 방법"):
        st.markdown(texts.METHOD_DETAIL)
ui.footer()
