"""레짐 원인 사후 분석 R4b·R5·R6: 자전거 단위로 신고 성향, 수거 속도, 신고 없는 수거를 잰다.

R4a(헛대여율 vs 신고율)는 총량 비교라 고장 자전거를 마주친 사람이 신고할 확률을 직접 재지 못한다.
상세 대여이력으로 자전거 단위로 본다.
  R4b 신고 성향: 헛대여(같은 대여소 반납, 2~3분)가 난 자전거가 같은 날·다음 날 신고되는 비율 p_rep.
      2025-03 전후로 내려가면 신고 성향이 떨어졌다는 직접 증거다.
  R5  수거 속도: 신고 뒤 다음 대여까지의 공백(일). 다음 날 재대여 비율(신고됐는데 계속 운행),
      3일 이상 공백 비율(수거·정비 추정), 공백 중앙값. 2025-03 이후 공백이 짧아지면 고장 자전거를 더 빨리
      뺀다는 뜻이고, 중복 신고 감소(H5)로 이어진다.
  R6  신고 없는 운행 중단: 7일(또는 14일) 이상 대여가 없다가 다른 대여소에서 다시 나타났고, 공백 앞뒤 3일
      안에 신고가 없는 경우. 직원이 신고 없이 수거·정비했을 가능성(H6)으로 보고 월별 활동 자전거 천 대당으로 센다.
      재배치 트럭 이동·장기 보관과 섞여서 7일·14일 두 기준으로 본다.

주의: 2021-02~12 신고는 등록일시에 시각이 없어(0시) 두 기간 모두 날짜 단위로 비교한다.

사후 분석이라 예측 모형(1C) 선택에는 쓰지 않는다.

입력: data/processed/rent_2021/ (2020-10 ~ 2021-09), data/processed/rent/ (2023-07 ~ 2026-06), data/processed/fail_std.parquet
출력: output/tables/rc_4b_monthly.csv, rc_5_monthly.csv, rc_6_monthly.csv (월별), rc_456_periods.csv (비교 기간별 요약)
실행: .venv/bin/python py/regime/rc_bike_level.py            (두 폴더 모두)
      .venv/bin/python py/regime/rc_bike_level.py rent_2021  (한 폴더만)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
import duckdb                            # noqa: E402
from rc_duplicates import PERIODS        # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FOLDERS = {"rent_2021": ("2020-10-01", "2021-09-30"), "rent": ("2023-07-01", "2026-06-30")}


def build(con, folder, lo, hi):
    """폴더 하나의 상세로 임시 테이블 trips(중복 제거, 헛대여 표시), rep(신고 단위), seq(자전거별 궤적)를 만든다"""
    pq = (ROOT / "data/processed" / folder / "*/*/*.parquet").as_posix()
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE trips AS
    SELECT DISTINCT ON (bike_id, rent_dt, rent_st) bike_id, rent_dt, rent_st, rtn_dt, rtn_st, use_min, use_m,
           CAST(rent_dt AS DATE) AS d,
           (rent_st = rtn_st AND use_min BETWEEN 2 AND 3) AS sham
    FROM read_parquet('{pq}', hive_partitioning = true)
    WHERE bike_id IS NOT NULL AND rent_dt BETWEEN TIMESTAMP '{lo}' AND TIMESTAMP '{hi} 23:59:59'
    """)
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE rep AS
    SELECT DISTINCT CAST(bike AS INTEGER) AS bike_id, CAST(reg_ts AS TIMESTAMP) AS reg_ts, CAST(reg_ts AS DATE) AS rd
    FROM read_parquet('{(ROOT / "data/processed/fail_std.parquet").as_posix()}')
    WHERE reg_ts BETWEEN TIMESTAMP '{lo}' AND TIMESTAMP '{hi} 23:59:59'
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE seq AS
    SELECT *, date_diff('day', prev_rtn_dt, rent_dt) AS idle_d,
           prev_rtn_st IS NOT NULL AND rent_st <> prev_rtn_st AS relocated
    FROM (SELECT bike_id, rent_dt, rent_st, d,
                 lag(rtn_st) OVER w AS prev_rtn_st, lag(rtn_dt) OVER w AS prev_rtn_dt
          FROM trips WINDOW w AS (PARTITION BY bike_id ORDER BY rent_dt))
    """)


def r4b(con):
    """헛대여 뒤 같은 날·다음 날 신고 비율(월별)"""
    return con.sql("""
    SELECT strftime(t.d, '%Y-%m') AS ym, count(*) AS n_sham,
           avg(CASE WHEN EXISTS (SELECT 1 FROM rep r WHERE r.bike_id = t.bike_id AND r.rd BETWEEN t.d AND t.d + INTERVAL 1 DAY)
                    THEN 1.0 ELSE 0.0 END) AS p_rep          -- 같은 날·다음 날 둘 다 신고돼도 헛대여 한 건은 한 번만 센다
    FROM (SELECT * FROM trips WHERE sham) t
    GROUP BY 1 ORDER BY 1
    """).df()


def r5(con):
    """신고 뒤 다음 대여까지의 공백(일). 같은 날 대여는 신고 전일 수 있어 다음 날 이후 첫 대여로 잰다"""
    return con.sql("""
    WITH g AS (
      SELECT r.bike_id, r.rd, min(t.d) AS next_d
      FROM rep r LEFT JOIN trips t ON t.bike_id = r.bike_id AND t.d > r.rd
      GROUP BY 1, 2)
    SELECT strftime(rd, '%Y-%m') AS ym, count(*) AS n_rep,
           avg(CASE WHEN next_d = rd + INTERVAL 1 DAY THEN 1.0 ELSE 0.0 END) AS p_next_day,
           avg(CASE WHEN next_d IS NULL OR next_d >= rd + INTERVAL 3 DAY THEN 1.0 ELSE 0.0 END) AS p_gap3,
           median(date_diff('day', rd, next_d)) AS gap_median_d
    FROM g GROUP BY 1 ORDER BY 1
    """).df()


def r6(con, min_idle, hi):
    """신고 없는 운행 중단(활동 자전거 천 대당): min_idle일 이상 공백, 다른 대여소에서 재등장, 앞뒤 3일 신고 없음.

    자료 끝(hi) 근처에서 시작한 공백은 재등장이 잘리므로 공백 시작이 hi − min_idle − 14일 이전인 것만 센다.
    겨울에는 자전거가 오래 쉬어서 전년 같은 반기끼리만 비교한다.
    """
    out = con.sql(f"""
    WITH gaps AS (
      SELECT s.bike_id, s.rent_dt, s.prev_rtn_dt, strftime(CAST(s.prev_rtn_dt AS DATE), '%Y-%m') AS ym
      FROM seq s WHERE s.idle_d >= {min_idle} AND s.relocated
        AND CAST(s.prev_rtn_dt AS DATE) <= DATE '{hi}' - INTERVAL {min_idle + 14} DAY),
    noreport AS (
      SELECT g.* FROM gaps g
      WHERE NOT EXISTS (SELECT 1 FROM rep r WHERE r.bike_id = g.bike_id
                        AND r.rd BETWEEN CAST(g.prev_rtn_dt AS DATE) - INTERVAL 3 DAY AND CAST(g.rent_dt AS DATE) + INTERVAL 3 DAY)),
    act AS (SELECT strftime(d, '%Y-%m') AS ym, count(DISTINCT bike_id) AS bikes FROM trips GROUP BY 1)
    SELECT a.ym, a.bikes,
           coalesce(x.n_all, 0) AS n_gap_{min_idle}d, coalesce(y.n_nr, 0) AS n_gap_{min_idle}d_noreport,
           coalesce(y.n_nr, 0) * 1000.0 / a.bikes AS per_1k_bikes_{min_idle}d
    FROM act a
    LEFT JOIN (SELECT ym, count(*) AS n_all FROM gaps GROUP BY 1) x USING (ym)
    LEFT JOIN (SELECT ym, count(*) AS n_nr FROM noreport GROUP BY 1) y USING (ym)
    ORDER BY 1
    """).df()
    # 마지막 달은 절단으로 과소 집계되므로 비워서 평균에서 뺀다
    last = pd.Timestamp(hi).strftime("%Y-%m")
    out.loc[out.ym == last, [f"n_gap_{min_idle}d", f"n_gap_{min_idle}d_noreport", f"per_1k_bikes_{min_idle}d"]] = np.nan
    return out


def period_summary(tabs):
    """비교 기간별로 월 값을 건수 가중 평균해 P1/P0 비를 만든다"""
    rows = []
    for name, (p0, p1) in PERIODS.items():
        r = {"비교": name}
        for key, (df, val, wcol) in tabs.items():
            d = df.set_index("ym")
            def avg(a, b):
                s = d.loc[a[:7]:b[:7]].dropna(subset=[val])
                return np.average(s[val], weights=s[wcol]) if len(s) and s[wcol].sum() > 0 else np.nan
            a, b = avg(*p0), avg(*p1)
            r[f"{key} P0"], r[f"{key} P1"], r[f"{key} 비"] = round(a, 4), round(b, 4), round(b / a, 3) if a else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def main(folders):
    con = duckdb.connect()                                   # 메모리 DB. Parquet만 읽어서 적재 중인 DB 파일 잠금과 충돌하지 않는다
    con.execute("SET memory_limit = '6GB'"); con.execute("SET threads = 4")
    out = {"4b": [], "5": [], "6": []}
    for folder in folders:
        lo, hi = FOLDERS[folder]
        build(con, folder, lo, hi)
        out["4b"].append(r4b(con).assign(folder=folder))
        out["5"].append(r5(con).assign(folder=folder))
        out["6"].append(r6(con, 7, hi).merge(r6(con, 14, hi), on=["ym", "bikes"]).assign(folder=folder))
        print(f"{folder} 완료", flush=True)
    t4, t5, t6 = (pd.concat(out[k], ignore_index=True) for k in ("4b", "5", "6"))
    t4.round(4).to_csv(ROOT / "output/tables/rc_4b_monthly.csv", index=False)
    t5.round(4).to_csv(ROOT / "output/tables/rc_5_monthly.csv", index=False)
    t6.round(4).to_csv(ROOT / "output/tables/rc_6_monthly.csv", index=False)
    s = period_summary({"R4b 헛대여 뒤 신고 확률": (t4, "p_rep", "n_sham"),
                        "R5 신고 다음 날 재대여": (t5, "p_next_day", "n_rep"),
                        "R5 3일 이상 공백": (t5, "p_gap3", "n_rep"),
                        "R6 신고 없는 중단(7일) 천 대당": (t6, "per_1k_bikes_7d", "bikes"),
                        "R6 신고 없는 중단(14일) 천 대당": (t6, "per_1k_bikes_14d", "bikes")})
    s.to_csv(ROOT / "output/tables/rc_456_periods.csv", index=False)
    pd.set_option("display.width", 250)
    print(s.set_index("비교").T.to_string())


if __name__ == "__main__":
    main(sys.argv[1:] or list(FOLDERS))
