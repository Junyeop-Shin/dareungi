"""레짐 원인 사후 분석 R7 — 자전거 코호트(처음 등장 시기 ≈ 프레임 형태)별 신고율과 폐기 기준

[무엇을·왜]
2025년 신고 감소가 특정 코호트(오래된 L자형 프레임 등)에 몰려 있으면 하드웨어 교체·폐기(구성 효과)이고,
모든 코호트에서 비슷하게 줄었으면 신고 쪽(성향·경로)이다. 안장은 "안장 아래 프레임 균열"이 안장 신고로 들어왔을 수 있어
코호트별로 따로 본다. 폐기 기준(도입 6년 + 대여 3,000회 이상)에 도달한 자전거와 아닌 자전거도 비교한다.

[코호트 정의 — 자전거번호가 처음 나타난 시기]
  ~2019          옛 번호를 그대로 쓰는 자전거 (실제 도입 연도)
  2020 ~ 2022-02 QR 단말기 교체 때 번호가 새로 붙은 것이 섞여 있어 **실제 나이를 모른다**
  2022-03 ~      이후 번호는 처음 등장 = 도입으로 믿을 수 있다(docs/notes/02). 연도별로 나눈다
[입력]  data/db/dareungi.duckdb 의 bike_year(2015 ~ 2023-06 요약), data/processed/rent/ (2023-07 ~ 상세), data/processed/fail_std.parquet
[출력]  output/tables/rc_7_cohort_half.csv (코호트 × 반기: 대수, 대여, 부품별 만 건당 신고율), rc_7_cohort_ratio.csv (전년 같은 반기 대비 비),
        rc_7_scrap_rule.csv (폐기 기준 도달 여부별 신고율), rc_7_cohort_exit.csv (코호트별 마지막 등장 = 폐기 추정)
[실행]  .venv/bin/python py/rc_cohort.py   (약 1 ~ 2분)
사후 분석이다. 예측 모형(1C) 선택에는 쓰지 않는다.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
from db import connect   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PARTS = {"타이어": "tire", "체인": "chain", "안장": "saddle", "페달": "pedal"}
HALVES = [("2024H1", "2024-01-01", "2024-06-30"), ("2024H2", "2024-07-01", "2024-12-31"), ("2025H1", "2025-01-01", "2025-06-30"),
          ("2025H2", "2025-07-01", "2025-12-31"), ("2026H1", "2026-01-01", "2026-06-30")]
COHORT_SQL = """
CASE WHEN first_dt < '2020-01-01' THEN '~2019(옛 번호)'
     WHEN first_dt < '2022-03-01' THEN '2020~2022-02(QR 재부여, 나이 불명)'
     WHEN first_dt < '2023-01-01' THEN '2022-03~12'
     WHEN first_dt < '2024-01-01' THEN '2023'
     WHEN first_dt < '2025-01-01' THEN '2024'
     WHEN first_dt < '2026-01-01' THEN '2025'
     ELSE '2026' END"""


def main():
    con = connect()
    rent = (ROOT / "data/processed/rent/*/*/*.parquet").as_posix()
    fail = (ROOT / "data/processed/fail_std.parquet").as_posix()
    # 자전거별 처음 등장일, 2023-06까지 누적 대여, 마지막 등장일
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE bike AS
    WITH a AS (SELECT bike_id, min(first_dt) AS first_dt, sum(n_trips) AS trips_to_2306 FROM bike_year GROUP BY 1),
         b AS (SELECT bike_id, min(rent_dt) AS first_dt, max(rent_dt) AS last_dt FROM read_parquet('{rent}') WHERE bike_id IS NOT NULL GROUP BY 1)
    SELECT coalesce(a.bike_id, b.bike_id) AS bike_id,
           least(coalesce(a.first_dt, b.first_dt), coalesce(b.first_dt, a.first_dt)) AS first_dt,
           coalesce(a.trips_to_2306, 0) AS trips_to_2306, b.last_dt
    FROM a FULL OUTER JOIN b USING (bike_id)
    """)
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE fail AS
    SELECT CAST(bike AS INTEGER) AS bike_id, CAST(reg_ts AS TIMESTAMP) AS reg_ts, kind
    FROM read_parquet('{fail}') WHERE reg_ts >= TIMESTAMP '2024-01-01'
    """)
    rows, scrap = [], []
    for h, lo, hi in HALVES:
        con.execute(f"""
        CREATE OR REPLACE TEMP TABLE per AS
        SELECT b.bike_id, {COHORT_SQL} AS cohort, b.first_dt,
               b.trips_to_2306 + coalesce(pre.n, 0) AS cum_trips_start,                     -- 반기 시작 전까지 누적 대여
               date_diff('day', b.first_dt, TIMESTAMP '{lo}') / 365.25 AS age_y,
               coalesce(t.n, 0) AS trips,
               {", ".join(f"coalesce(f.{v}, 0) AS {v}" for v in PARTS.values())}, coalesce(f.rows, 0) AS rows
        FROM bike b
        LEFT JOIN (SELECT bike_id, count(*) AS n FROM read_parquet('{rent}') WHERE rent_dt >= TIMESTAMP '2023-07-01' AND rent_dt < TIMESTAMP '{lo}' GROUP BY 1) pre USING (bike_id)
        LEFT JOIN (SELECT bike_id, count(*) AS n FROM read_parquet('{rent}') WHERE rent_dt BETWEEN TIMESTAMP '{lo}' AND TIMESTAMP '{hi} 23:59:59' GROUP BY 1) t USING (bike_id)
        LEFT JOIN (SELECT bike_id, {", ".join(f"sum(kind = '{k}') AS {v}" for k, v in PARTS.items())}, count(*) AS rows
                   FROM fail WHERE reg_ts BETWEEN TIMESTAMP '{lo}' AND TIMESTAMP '{hi} 23:59:59' GROUP BY 1) f USING (bike_id)
        WHERE coalesce(t.n, 0) > 0
        """)
        g = con.sql(f"""
        SELECT '{h}' AS half, cohort, count(*) AS bikes, sum(trips) AS trips,
               {", ".join(f"sum({v}) * 1e4 / sum(trips) AS {v}_per10k" for v in PARTS.values())}, sum(rows) * 1e4 / sum(trips) AS rows_per10k
        FROM per GROUP BY 1, 2 ORDER BY 2
        """).df()
        rows.append(g)
        s = con.sql(f"""
        SELECT '{h}' AS half, (age_y >= 6 AND cum_trips_start >= 3000) AS scrap_rule, count(*) AS bikes, sum(trips) AS trips,
               {", ".join(f"sum({v}) * 1e4 / sum(trips) AS {v}_per10k" for v in PARTS.values())}, sum(rows) * 1e4 / sum(trips) AS rows_per10k
        FROM per WHERE first_dt >= '2022-03-01' OR first_dt < '2020-01-01'      -- 나이를 아는 자전거만
        GROUP BY 1, 2 ORDER BY 2
        """).df()
        scrap.append(s)
    coh = pd.concat(rows, ignore_index=True)
    coh.round(2).to_csv(ROOT / "output/tables/rc_7_cohort_half.csv", index=False)
    pd.concat(scrap, ignore_index=True).round(2).to_csv(ROOT / "output/tables/rc_7_scrap_rule.csv", index=False)
    # 전년 같은 반기 대비 비
    piv = coh.set_index(["cohort", "half"])
    out = []
    for a, b in (("2024H1", "2025H1"), ("2024H2", "2025H2"), ("2025H1", "2026H1")):
        for c in coh.cohort.unique():
            if (c, a) in piv.index and (c, b) in piv.index:
                r = {"비교": f"{a} → {b}", "cohort": c, "bikes_P1": int(piv.loc[(c, b), "bikes"])}
                for v in list(PARTS.values()) + ["rows"]:
                    r[f"{v} 비"] = round(piv.loc[(c, b), f"{v}_per10k"] / piv.loc[(c, a), f"{v}_per10k"], 3)
                out.append(r)
    ratio = pd.DataFrame(out)
    ratio.to_csv(ROOT / "output/tables/rc_7_cohort_ratio.csv", index=False)
    # 코호트별 마지막 등장(폐기 추정): 2024년에 활동한 자전거 중 2025년·2026년에 안 보이는 비율
    exit_ = con.sql(f"""
    SELECT {COHORT_SQL} AS cohort, count(*) AS bikes_2024,
           avg(CASE WHEN last_dt < '2025-01-01' THEN 1.0 ELSE 0.0 END) AS gone_by_2025,
           avg(CASE WHEN last_dt < '2026-01-01' THEN 1.0 ELSE 0.0 END) AS gone_by_2026
    FROM bike WHERE last_dt >= '2024-01-01' AND bike_id IN (SELECT DISTINCT bike_id FROM read_parquet('{rent}') WHERE rent_dt BETWEEN '2024-01-01' AND '2024-12-31')
    GROUP BY 1 ORDER BY 1
    """).df()
    exit_.round(3).to_csv(ROOT / "output/tables/rc_7_cohort_exit.csv", index=False)
    pd.set_option("display.width", 250)
    print(coh[coh.half.isin(["2024H1", "2025H1", "2026H1"])].round(1).to_string(index=False))
    print(ratio.to_string(index=False))
    print(pd.concat(scrap).round(1).to_string(index=False))
    print(exit_.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
