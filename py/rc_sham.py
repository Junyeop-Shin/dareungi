"""레짐 원인 사후 분석 R4a — 헛대여율(고장 신호) 대 신고율: 고장이 변했나, 신고가 변했나

[무엇을·왜]
헛대여(같은 대여소에 몇 분 안에 반납)는 자전거를 뽑았다가 고장을 발견하고 돌려놓는 행동을 많이 담고 있어
신고와 무관한 고장 신호로 쓴다. 신고율이 바뀐 시점(2021-04, 2025-03)에 헛대여율이
  - 그대로면         → 고장은 그대로, 신고(성향·경로)가 바뀌었다
  - 같이 움직였으면  → 실제 고장이 바뀌었다(또는 대여·반납 방식이 바뀌어 헛대여 정의 자체가 흔들렸다 → 민감도로 확인)
헛대여에는 마음이 바뀐 대여·잘못 뽑은 자전거도 섞이므로 수준이 아니라 **전후 변화**만 해석한다.

[입력]  data/processed/day_signal.parquet (py/day_signal.py), data/processed/daily_master_v2.parquet
[출력]  output/tables/rc_4a_monthly.csv   월별 헛대여율(대여 천 건당, 기준 2분 / 2 ~ 3분 / 2 ~ 5분)과 신고율(대여 만 건당)
        ※ 2026년 파일에만 0 ~ 1분 기록이 있어 아래쪽을 2분으로 잘라야 연도 비교가 된다(2026-10-07 확인)
        output/tables/rc_4a_ratios.csv    비교 기간별 P1/P0 비(헛대여율 기준별, 신고율 부품별)
        output/figures/rc_4a_sham_vs_report.png
[실행]  .venv/bin/python py/rc_sham.py
사후 분석이다. 예측 모형(1C) 선택에는 쓰지 않는다.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from rc_duplicates import PERIODS

plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False
ROOT = Path(__file__).resolve().parents[1]
PARTS = {"fail_tire": "타이어", "fail_chain": "체인", "fail_saddle": "안장", "fail_pedal": "페달", "total_faults": "전체"}
# 헛대여 기준. 2026년 파일에만 0 ~ 1분 기록이 있어(그 전에는 같은 대여소 2분 미만 반납이 기록에 없다) 아래쪽을 2분으로 자른다
SHAM = {"sham_2": "2분(정확히)", "sham_2_3": "2 ~ 3분", "sham_2_5": "2 ~ 5분", "same_any": "같은 대여소 반납(시간 무관)"}


def monthly(ds, m):
    """월별 헛대여율(천 건당)과 신고율(만 건당). 운영 중단일·신고 누락 기간은 둘 다에서 뺀다"""
    ok = m[(m.no_service == 0) & (m.report_anomaly == 0)][["date", "rentals"] + list(PARTS)]
    d = ds.merge(ok, on="date")
    g = d.groupby(d.date.dt.to_period("M")).sum(numeric_only=True)
    g["sham_2"], g["sham_2_3"], g["sham_2_5"] = g.sham2 - g.sham01, g.sham3 - g.sham01, g.sham5 - g.sham01
    out = pd.DataFrame(index=g.index)
    for s in SHAM:
        out[s + "_per1k"] = g[s] / g.n_trips * 1000
    for p, name in PARTS.items():
        out[name + "_per10k"] = g[p] / g.rentals * 1e4
    out["n_trips"] = g.n_trips
    return out


def main():
    ds = pd.read_parquet(ROOT / "data/processed/day_signal.parquet")
    m = pd.read_parquet(ROOT / "data/processed/daily_master_v2.parquet")
    mm = monthly(ds, m)
    mm.round(3).to_csv(ROOT / "output/tables/rc_4a_monthly.csv")

    rows = []
    for name, (p0, p1) in PERIODS.items():
        a = mm.loc[pd.Period(p0[0][:7], "M"):pd.Period(p0[1][:7], "M")]
        b = mm.loc[pd.Period(p1[0][:7], "M"):pd.Period(p1[1][:7], "M")]
        wa = lambda s: np.average(s, weights=a.n_trips)       # 월 평균을 대여 수로 가중
        wb = lambda s: np.average(s, weights=b.n_trips)
        r = {"비교": name}
        for s, lab in SHAM.items():
            r[f"헛대여율 비 {lab}"] = round(wb(b[s + "_per1k"]) / wa(a[s + "_per1k"]), 3)
        for p in PARTS.values():
            r[f"신고율 비 {p}"] = round(wb(b[p + "_per10k"]) / wa(a[p + "_per10k"]), 3)
        rows.append(r)
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "output/tables/rc_4a_ratios.csv", index=False)
    pd.set_option("display.width", 250)
    print(out.set_index("비교").T.to_string())

    fig, axes = plt.subplots(3, 1, figsize=(13, 10), sharex=True)
    t = mm.index.to_timestamp()
    for s, lab in (("sham_2_per1k", "2분"), ("sham_2_3_per1k", "2 ~ 3분"), ("sham_2_5_per1k", "2 ~ 5분")):
        axes[0].plot(t, mm[s], label=lab)
    axes[0].set_title("헛대여율 — 같은 대여소 반납, 대여 천 건당 (고장 신호의 대리 지표)  [사후 분석]"); axes[0].legend(); axes[0].grid(alpha=0.3)
    axes[1].plot(t, mm["전체_per10k"], color="k", label="전체")
    for p in ["타이어", "체인", "안장", "페달"]:
        axes[1].plot(t, mm[p + "_per10k"], lw=1, label=p)
    axes[1].set_title("신고율 — 대여 만 건당"); axes[1].legend(ncol=5); axes[1].grid(alpha=0.3)
    yoy = lambda s: s / s.shift(12)
    axes[2].plot(t, yoy(mm["sham_2_3_per1k"]), label="헛대여율(2 ~ 3분) 전년 동월비")
    axes[2].plot(t, yoy(mm["전체_per10k"]), color="k", label="신고율(전체) 전년 동월비")
    axes[2].axhline(1, color="gray", lw=0.8); axes[2].set_ylim(0, 3)
    axes[2].set_title("전년 동월비 — 신고율만 움직이면 '신고'가, 둘이 같이 움직이면 '고장'이 변한 것"); axes[2].legend(); axes[2].grid(alpha=0.3)
    for ax in axes:
        for x in ("2018-06-20", "2020-05-01", "2021-04-05", "2025-03-01"):
            ax.axvline(pd.Timestamp(x), color="red", ls=":", lw=1)
    fig.tight_layout()
    fig.savefig(ROOT / "output/figures/rc_4a_sham_vs_report.png", dpi=130)


if __name__ == "__main__":
    main()
