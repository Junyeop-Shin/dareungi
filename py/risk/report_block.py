"""신고하면 대여가 막히는가, 막힌다면 어떤 구조인가(2025-07 ~ 11 신고, 48시간 병합 전 원래 기록).

after_report.py에서 신고의 30%가 24시간 안에, 대부분 같은 대여소에서 다시 대여됐다. 그래서 '한 번 신고로 바로 막힌다'
대신 가능한 구조를 데이터로 가려 본다.
  가설 1(누적 신고): 몇 번 이상 신고돼야 막힌다 → 같은 자전거의 최근 72시간 안 몇 번째 신고인지에 따라 곧바로 재대여되는
                     비율이 어느 지점에서 뚝 떨어져야 한다
  가설 2(담당자 확인): 담당자가 확인해야 막힌다 → 근무 시간 밖(밤·주말) 신고에서 곧바로 재대여가 더 많아야 한다
  가설 3(고장 종류): 종류에 따라 막는다 → 신고 유형별로 재대여 비율이 크게 달라야 한다
  보조: 이용 중 신고(대여 중에 접수)인지 반납 뒤 신고인지

'곧바로 재대여' = 신고 뒤(이용 중 신고면 그 반납 뒤) 6시간 안에 같은 대여소에서 다음 대여가 시작됨(옮겨지지 않음).
신고 시각은 앱 접수 시각, 대여 기록은 대여이력 파일 기준이다. 30일 뒤까지 따라가도 테스트 구간에 닿지 않게 11월까지만 쓴다.

입력: 분석 DB fail, trips_seq(읽기 전용)
출력: output/tables/rb_18_block_by_count.csv, rb_18_block_by_time.csv, rb_18_block_by_kind.csv
실행: .venv/bin/python py/risk/report_block.py
"""
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SQL = """
WITH r0 AS (SELECT bike_id, reg_ts, min(kind) AS kind, count(*) AS n_kinds FROM fail
            WHERE reg_ts >= TIMESTAMP '2025-06-28' AND reg_ts < TIMESTAMP '2025-12-01' GROUP BY 1, 2),
r AS (SELECT *, count(*) OVER (PARTITION BY bike_id ORDER BY reg_ts RANGE BETWEEN INTERVAL 72 HOUR PRECEDING AND CURRENT ROW) AS nth_72h
      FROM r0),
t AS (SELECT bike_id, rent_dt, rtn_dt, rent_st, rtn_st FROM trips_seq
      WHERE rent_dt >= TIMESTAMP '2025-06-01' AND rent_dt < TIMESTAMP '2026-01-01' AND NOT bad_time),
prev AS (SELECT r.*, t.rent_dt AS prev_rent, t.rtn_dt AS prev_rtn, t.rtn_st AS loc_st
         FROM r ASOF LEFT JOIN t ON r.bike_id = t.bike_id AND r.reg_ts >= t.rent_dt),
base AS (SELECT *, prev_rtn > reg_ts AS during_ride, greatest(reg_ts, coalesce(prev_rtn, reg_ts)) AS t0 FROM prev),
nxt AS (SELECT b.*, t.rent_dt AS next_rent, t.rent_st AS next_st
        FROM base b ASOF LEFT JOIN t ON b.bike_id = t.bike_id AND t.rent_dt > b.t0)
SELECT *, date_diff('minute', t0, next_rent) / 60.0 AS gap_h FROM nxt WHERE reg_ts >= TIMESTAMP '2025-07-01'"""


def summarize(g):
    quick = (g.gap_h <= 6) & (g.next_st == g.loc_st)
    return pd.Series({"신고 수": len(g), "6시간 안 같은 곳 재대여": quick.mean(), "24시간 안 재대여": (g.gap_h <= 24).mean(),
                      "24시간 안 재대여 중 같은 곳": ((g.gap_h <= 24) & (g.next_st == g.loc_st)).sum() / max((g.gap_h <= 24).sum(), 1),
                      "다음 대여까지 중앙값(시간)": g.gap_h.median()})


def main():
    con = duckdb.connect(str(ROOT / "data/db/dareungi.duckdb"), read_only=True)
    con.execute("SET memory_limit = '8GB'")
    d = con.execute(SQL).df()
    con.close()
    d = d[d.loc_st.notna()]
    d["몇 번째(72시간)"] = d.nth_72h.clip(upper=5).astype(int).astype(str).replace({"5": "5 이상"})
    by_n = d.groupby("몇 번째(72시간)").apply(summarize, include_groups=False)
    ts = pd.to_datetime(d.reg_ts)
    weekend = ts.dt.dayofweek >= 5
    hour = ts.dt.hour
    d["접수 시간"] = np.select([hour < 6, hour < 9, hour < 18, hour < 22], ["0 ~ 6시", "6 ~ 9시", "9 ~ 18시", "18 ~ 22시"], "22 ~ 24시")
    d["요일"] = np.where(weekend, "주말", "평일")
    by_t = d.groupby(["요일", "접수 시간"]).apply(summarize, include_groups=False)
    by_t = pd.concat([by_t, d.groupby("during_ride").apply(summarize, include_groups=False)
                      .rename(index={True: "이용 중 신고", False: "반납 뒤 신고"}).assign(요일="전체").set_index("요일", append=True).swaplevel()])
    by_k = d.groupby("kind").apply(summarize, include_groups=False).sort_values("신고 수", ascending=False)
    by_n.round(4).to_csv(ROOT / "output/tables/rb_18_block_by_count.csv")
    by_t.round(4).to_csv(ROOT / "output/tables/rb_18_block_by_time.csv")
    by_k.round(4).to_csv(ROOT / "output/tables/rb_18_block_by_kind.csv")
    pd.set_option("display.width", 200)
    print(f"원래 신고 {len(d):,}건(2025-07 ~ 11, 신고 뒤 위치를 찾은 것)")
    print("\n가설 1: 같은 자전거 최근 72시간 안 몇 번째 신고인가"); print(by_n.round(3).to_string())
    print("\n가설 2: 접수 요일·시간, 이용 중 / 반납 뒤"); print(by_t.round(3).to_string())
    print("\n가설 3: 신고 유형"); print(by_k.round(3).to_string())


if __name__ == "__main__":
    main()
