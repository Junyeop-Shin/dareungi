"""레짐 원인 사후 분석 R2 — 신고 '행'의 변화를 조각으로 나눈다: 자전거 수 × 자전거당 신고 × 신고당 유형

[무엇을·왜]
고장신고 한 행 = 신고 1건 × 고장유형 1개. 행 수가 줄었을 때 무엇이 줄었는지에 따라 원인이 갈린다.
  log(행) = log(신고된 서로 다른 자전거 수) + log(자전거당 신고 수) + log(신고당 유형 수)
  - 신고된 자전거 수만 줄었다  → 고장 난 자전거가 줄었거나, 신고하는 사람이 줄었다 (R4에서 가른다)
  - 자전거당 신고 수가 줄었다  → 같은 고장에 여러 명이 신고하던 중복이 줄었다 (고장 자전거를 빨리 수거하면 이렇게 된다)
  - 신고당 유형 수가 줄었다    → 한 신고에 여러 유형을 고르는 앱 입력 방식이 바뀌었다
48시간 중복(같은 자전거·같은 유형이 48시간 안에 다시) 비율은 네 번째 지표로 따로 본다.
신고된 자전거 수는 '달마다 세어 더한 값'이고(기간 전체 고유 수는 포화한다), 대여 만 건당으로도 본다.

[단위 주의] 신고 1건 = 고유 (자전거, 등록일시). 2021-02 ~ 12는 등록일시에 시각이 없어 같은 자전거의 같은 날 신고가
한 건으로 합쳐진다 → 2021-04 비교에서 '자전거당 신고'는 날짜 단위 근사라 믿지 않는다.

[입력]  data/processed/fail_std.parquet (bike, reg_ts, kind, keep), data/processed/daily_master_v2.parquet (rentals)
[출력]  output/tables/rc_2_decomp.csv (비교 기간별 조각 분해), output/tables/rc_2_monthly.csv (월별 지표)
[실행]  .venv/bin/python py/rc_duplicates.py
사후 분석이다. 예측 모형(1C) 선택에는 쓰지 않는다.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
# 비교 기간 후보. 사람이 고른 것을 보고서에 쓰고, 나머지는 민감도
# 주 기준(사람 제안, 2026-10-07): 반기 단위로 전년 같은 반기와 비교한다. 계절이 같고 2026년 상반기까지 보인다.
# 2025 상반기에는 변화 전(1 ~ 2월)이 섞여 비가 희석되므로 3 ~ 8월 비교를 옆에 둔다. 12개월·6개월은 민감도.
PERIODS = {
    "2025 상반기 vs 2024 상반기": (("2024-01-01", "2024-06-30"), ("2025-01-01", "2025-06-30")),
    "2025 하반기 vs 2024 하반기": (("2024-07-01", "2024-12-31"), ("2025-07-01", "2025-12-31")),
    "2026 상반기 vs 2025 상반기": (("2025-01-01", "2025-06-30"), ("2026-01-01", "2026-06-30")),
    "2026 상반기 vs 2024 상반기(2년)": (("2024-01-01", "2024-06-30"), ("2026-01-01", "2026-06-30")),
    "2025-03 하락, 전년 동기 3 ~ 8월": (("2024-03-01", "2024-08-31"), ("2025-03-01", "2025-08-31")),
    "2025-03 하락, 12개월": (("2024-03-01", "2025-02-28"), ("2025-03-01", "2026-02-28")),
    "2025-03 하락, 6개월(계절 다름)": (("2024-09-01", "2025-02-28"), ("2025-03-01", "2025-08-31")),
    "2021-04 급증, 6개월": (("2020-10-01", "2021-03-31"), ("2021-04-01", "2021-09-30")),
    "2021-04 급증, 전년 동기 4 ~ 9월": (("2020-04-01", "2020-09-30"), ("2021-04-01", "2021-09-30")),
    "2021-04 급증, 2019 동기 4 ~ 9월": (("2019-04-01", "2019-09-30"), ("2021-04-01", "2021-09-30")),
}


def monthly_metrics(f, m):
    """월별: 행, 48시간 중복 뺀 행, 신고 건수, 신고된 자전거 수, 대여 건수와 파생 지표"""
    f = f.assign(d=f.reg_ts.dt.normalize(), ym=f.reg_ts.dt.to_period("M"))
    rep = f.drop_duplicates(["bike", "reg_ts"])
    g = pd.DataFrame({"rows": f.groupby("ym").size(), "rows_dedup": f[f.keep].groupby("ym").size(),
                      "reports": rep.groupby("ym").size(), "bikes": rep.groupby("ym").bike.nunique()})
    g["rentals"] = m.set_index(m.date.dt.to_period("M")).groupby(level=0).rentals.sum().reindex(g.index)
    g["rep_per_bike"] = g.reports / g.bikes
    g["rows_per_rep"] = g.rows / g.reports
    g["dup48"] = 1 - g.rows_dedup / g.rows
    g["bikes_per_10k"] = g.bikes / g.rentals * 1e4
    g["rows_per_10k"] = g.rows / g.rentals * 1e4
    return g


def decompose(mm, p0, p1):
    """두 기간의 로그 변화를 조각으로 나눈다. 단위는 '월': 신고된 자전거 수는 달마다 세어 더한다(기간 전체의 고유 자전거 수로
    세면 기간이 길수록 거의 모든 자전거가 한 번은 신고돼 포화하고, 기간 길이에 따라 결과가 뒤집힌다 — 2026-10-07 확인)"""
    out = {}
    for name, (a, b) in (("P0", p0), ("P1", p1)):
        s = mm.loc[pd.Period(a[:7], "M"):pd.Period(b[:7], "M")]
        out[name] = dict(rows=s.rows.sum(), rows_dedup=s.rows_dedup.sum(), reports=s.reports.sum(), bikes=s.bikes.sum(), rentals=s.rentals.sum())
    a, b = out["P0"], out["P1"]
    L = lambda k: np.log(b[k] / a[k])
    d_rows, d_bikes = L("rows"), L("bikes")
    d_rpb = np.log((b["reports"] / b["bikes"]) / (a["reports"] / a["bikes"]))
    d_rpr = np.log((b["rows"] / b["reports"]) / (a["rows"] / a["reports"]))
    return {"행 비(P1/P0)": round(np.exp(d_rows), 3), "대여 비": round(np.exp(L("rentals")), 3),
            "행/대여 비": round(np.exp(d_rows - L("rentals")), 3),
            "월별 신고된 자전거 수 비": round(np.exp(d_bikes), 3), "자전거/대여 비": round(np.exp(d_bikes - L("rentals")), 3),
            "자전거당 신고 비(월)": round(np.exp(d_rpb), 3), "신고당 유형 비": round(np.exp(d_rpr), 3),
            "48시간 중복 비율 P0": round(1 - a["rows_dedup"] / a["rows"], 4), "48시간 중복 비율 P1": round(1 - b["rows_dedup"] / b["rows"], 4),
            "로그 변화 중 자전거 수 몫": round(d_bikes / d_rows, 2) if d_rows else np.nan,
            "로그 변화 중 자전거당 신고 몫": round(d_rpb / d_rows, 2) if d_rows else np.nan,
            "로그 변화 중 신고당 유형 몫": round(d_rpr / d_rows, 2) if d_rows else np.nan,
            "검산(세 몫 합)": round((d_bikes + d_rpb + d_rpr) / d_rows, 2) if d_rows else np.nan}


def main():
    f = pd.read_parquet(ROOT / "data/processed/fail_std.parquet")
    m = pd.read_parquet(ROOT / "data/processed/daily_master_v2.parquet")[["date", "rentals"]]
    mm = monthly_metrics(f, m)
    mm.round(4).to_csv(ROOT / "output/tables/rc_2_monthly.csv")
    rows = []
    for name, (p0, p1) in PERIODS.items():
        rows.append({"비교": name, "P0": f"{p0[0]} ~ {p0[1]}", "P1": f"{p1[0]} ~ {p1[1]}", **decompose(mm, p0, p1)})
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "output/tables/rc_2_decomp.csv", index=False)
    pd.set_option("display.width", 250)
    print(out.set_index("비교").T.to_string())


if __name__ == "__main__":
    main()
