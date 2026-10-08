"""레짐 원인 사후 분석 R1: 부품별 신고율 변화점이 언제, 같은 주에 일어났는지 찾는다.

주 단위 부품별 신고율(대여 만 건당)에서 수준이 계단처럼 바뀐 시점을 두 창(2020-07 ~ 2022-06,
2024-01 ~ 2026-06)에서 찾는다. 네 부품이 같은 주에 움직였으면 공통 원인(앱·신고 경로), 한 부품만 먼저
또는 서서히 움직였으면 그 부품의 조치(체인가드 등)로 본다.

  1. 로그 신고율에서 계절(52주 주기 푸리에 2항)을 회귀로 뺀 잔차에 ruptures Binseg(l2)로 변화점 1개
  2. 보조로 전년 같은 주 대비 로그 비(yoy)에서 변화점 2개(계단 + 52주 뒤 복귀)를 찾아 첫 번째를 적는다.
     2025 창에서는 창 시작 부근과 2023-09 신고 누락 구간 때문에 흔들려서 보조로만 본다
  3. 변화 주 전후 12주 계절 제거 신고율 평균의 비와 부트스트랩 95% 구간(주 단위 재표집)
  4. 원인을 아는 2018-06-20 앱 개편 급등을 2017-07 ~ 2019-06 창에서 잡는지로 방법을 검증

사후 분석이라 예측 모형(1C) 선택에는 쓰지 않는다.

입력: data/processed/daily_master_v2.parquet
출력: output/tables/rc_1_changepoints.csv, output/figures/rc_1_yoy.png
실행: .venv/bin/python py/regime/rc_changepoint.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import ruptures as rpt

plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False
ROOT = Path(__file__).resolve().parents[2]
PARTS = {"fail_tire": "타이어", "fail_chain": "체인", "fail_saddle": "안장", "fail_pedal": "페달", "total_faults": "전체"}
WINDOWS = {"2021-04 급증": ("2020-07-01", "2022-06-30"), "2025-03 하락": ("2024-01-01", "2026-06-30"),
           "검증용 2018-06 앱 개편": ("2017-07-01", "2019-06-30")}


def weekly_rate(m):
    """주(월요일 시작) 단위 부품별 신고율. 운영 중단일·신고 누락 기간 제외"""
    d = m[(m.no_service == 0) & (m.report_anomaly == 0)].set_index("date")
    w = d[["rentals"] + list(PARTS)].resample("W-MON", label="left", closed="left").sum()
    w = w[w.rentals > 0]
    return w[list(PARTS)].div(w.rentals, axis=0) * 1e4


def deseason(lograte):
    """로그 신고율에서 52주 주기 푸리에(2항) + 상수를 회귀로 뺀 잔차"""
    t = np.arange(len(lograte)) * 2 * np.pi / 52.18
    X = np.column_stack([np.ones_like(t), np.sin(t), np.cos(t), np.sin(2 * t), np.cos(2 * t)])
    ok = np.isfinite(lograte)
    beta = np.linalg.lstsq(X[ok], lograte[ok], rcond=None)[0]
    return lograte - X @ beta


def first_break(s, n_bkps):
    """Binseg(l2) 변화점 중 가장 앞의 것. 반환값은 새 구간이 시작하는 인덱스"""
    s = np.asarray(s, float)
    bk = rpt.Binseg(model="l2", min_size=8).fit(s.reshape(-1, 1)).predict(n_bkps=n_bkps)
    return min(b for b in bk if b < len(s))


def ratio_ci(before, after, n_boot=2000, seed=0):
    """전후 평균 신고율의 비와 부트스트랩 95% 구간"""
    rng = np.random.default_rng(seed)
    b, a = np.asarray(before), np.asarray(after)
    boots = [rng.choice(a, len(a)).mean() / rng.choice(b, len(b)).mean() for _ in range(n_boot)]
    return a.mean() / b.mean(), np.percentile(boots, 2.5), np.percentile(boots, 97.5)


def main():
    m = pd.read_parquet(ROOT / "data/processed/daily_master_v2.parquet")
    rate = weekly_rate(m)
    lograte = np.log(rate.replace(0, np.nan))
    yoy = lograte - lograte.shift(52)
    rows = []
    for win, (lo, hi) in WINDOWS.items():
        for part, name in PARTS.items():
            # 주 방법: 계절 제거 잔차의 변화점 1개. 전년비는 보조
            resid = deseason(lograte.loc[lo:hi, part].to_numpy())
            r = pd.Series(resid, index=lograte.loc[lo:hi].index).dropna()
            wk_res = r.index[first_break(r.to_numpy(), n_bkps=1)]
            y = yoy.loc[lo:hi, part].dropna()
            wk_yoy = y.index[first_break(y.to_numpy(), n_bkps=2)]
            # 전후 비는 계절 제거 신고율(exp(잔차) × 창 평균)로 잰다. 원래 값이면 봄·여름 계절 상승이 섞인다
            adj = np.exp(r) * np.exp(np.nanmean(lograte.loc[lo:hi, part]))
            pos = adj.index.get_loc(wk_res)
            before, after = adj.iloc[max(pos - 12, 0):pos], adj.iloc[pos:pos + 12]
            ratio, lo_ci, hi_ci = ratio_ci(before.dropna(), after.dropna())
            rows.append({"창": win, "부품": name, "변화 주(계절 제거 잔차, 주 방법)": wk_res.date(), "변화 주(전년비, 보조)": wk_yoy.date(),
                         "전 12주 평균(계절 제거)": round(before.mean(), 2), "후 12주 평균(계절 제거)": round(after.mean(), 2),
                         "후/전 비": round(ratio, 3), "95% 하한": round(lo_ci, 3), "95% 상한": round(hi_ci, 3)})
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "output/tables/rc_1_changepoints.csv", index=False)
    print(out.to_string(index=False))

    fig, axes = plt.subplots(2, 1, figsize=(13, 8))
    for ax, (win, (lo, hi)) in zip(axes, list(WINDOWS.items())[:2]):
        for part, name in PARTS.items():
            ax.plot(yoy.loc[lo:hi, part], label=name, lw=2 if part == "total_faults" else 1.2, color="k" if part == "total_faults" else None)
            r = out[(out["창"] == win) & (out["부품"] == name)].iloc[0]
            ax.axvline(pd.Timestamp(r["변화 주(계절 제거 잔차, 주 방법)"]), color="k" if part == "total_faults" else "gray", ls=":", lw=1)
        ax.axhline(0, color="k", lw=0.8)
        ax.set_title(f"{win}: 부품별 신고율의 전년 같은 주 대비 로그 비 (세로 점선 = 찾은 변화 주)  [사후 분석]")
        ax.legend(ncol=5, fontsize=9)
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(ROOT / "output/figures/rc_1_yoy.png", dpi=130)


if __name__ == "__main__":
    main()
