"""신고 뒤 자전거는 어떻게 되나: 다음 대여까지 걸린 시간, 그 대여가 같은 대여소에서 시작했는지, 다시 신고됐는지.

위험 모형의 '지난 신고 이후' 변수와 last_repaired(신고 뒤 48시간 넘게 대여 없음 = 수리 추정)를 해석하려고 본다.
자전거는 빌리지 않으면 움직이지 않으므로, 다음 대여가 마지막 반납 대여소가 아닌 곳에서 시작했으면 그사이 트럭으로
옮겨진 것(수거·재배치)이다. 같은 대여소에서 곧바로 다시 대여됐으면 수거되지 않은 채 다시 풀린 것으로 본다.

  - 신고: 2025-07-01 ~ 11-30(48시간 중복 병합 뒤). 30일 뒤까지 따라가도 테스트 구간(2026 ~)에 닿지 않게 11월까지만
  - 신고 뒤 위치: 신고 직전(또는 신고하던 중) 대여의 반납 대여소. 시간은 신고 시각과 그 반납 시각 중 늦은 쪽부터 잰다
  - 다시 신고: 다음 대여 시작부터 7일 안에 같은 자전거의 다른 신고가 있었나

입력: 분석 DB fail, trips_seq(읽기 전용)
출력: output/tables/rb_13_after_report.csv
실행: .venv/bin/python py/risk/after_report.py  (1 ~ 2분)
"""
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SQL = """
WITH r AS (SELECT DISTINCT bike_id, reg_ts FROM fail WHERE keep AND reg_ts >= TIMESTAMP '2025-07-01' AND reg_ts < TIMESTAMP '2025-12-01'),
t AS (SELECT bike_id, rent_dt, rtn_dt, rent_st, rtn_st FROM trips_seq
      WHERE rent_dt >= TIMESTAMP '2025-06-01' AND rent_dt < TIMESTAMP '2026-01-01' AND NOT bad_time),
prev AS (SELECT r.*, t.rtn_dt AS prev_rtn, t.rtn_st AS loc_st
         FROM r ASOF LEFT JOIN t ON r.bike_id = t.bike_id AND r.reg_ts >= t.rent_dt),
base AS (SELECT *, greatest(reg_ts, coalesce(prev_rtn, reg_ts)) AS t0 FROM prev),
nxt AS (SELECT b.*, t.rent_dt AS next_rent, t.rent_st AS next_st
        FROM base b ASOF LEFT JOIN t ON b.bike_id = t.bike_id AND t.rent_dt > b.t0)
SELECT n.*, date_diff('minute', t0, next_rent) / 60.0 AS gap_h,
       EXISTS (SELECT 1 FROM r r2 WHERE r2.bike_id = n.bike_id AND r2.reg_ts > n.next_rent AND r2.reg_ts <= n.next_rent + INTERVAL 7 DAY) AS rerep_7d
FROM nxt n"""
BINS = [-0.01, 1, 6, 24, 48, 24 * 7, 24 * 30]
LABELS = ["1시간 안", "1 ~ 6시간", "6 ~ 24시간", "24 ~ 48시간", "2 ~ 7일", "7 ~ 30일"]


def main():
    con = duckdb.connect(str(ROOT / "data/db/dareungi.duckdb"), read_only=True)
    con.execute("SET memory_limit = '8GB'")
    d = con.execute(SQL).df()
    con.close()
    d["구간"] = pd.cut(d.gap_h, BINS, labels=LABELS).astype(str).where(d.gap_h <= 24 * 30, "30일 넘게 없음")
    d["같은 대여소"] = (d.next_st == d.loc_st) & d.next_st.notna()
    rows = []
    for b in LABELS + ["30일 넘게 없음"]:
        g = d[d.구간 == b]
        same, moved = g[g["같은 대여소"]], g[~g["같은 대여소"]]
        rows.append({"다음 대여까지": b, "신고 수": len(g), "비율": len(g) / len(d),
                     "같은 대여소에서 시작": g["같은 대여소"].mean() if len(g) and b != "30일 넘게 없음" else None,
                     "7일 안 재신고(같은 대여소)": same.rerep_7d.mean() if len(same) and b != "30일 넘게 없음" else None,
                     "7일 안 재신고(옮겨진 뒤)": moved.rerep_7d.mean() if len(moved) and b != "30일 넘게 없음" else None})
    t = pd.DataFrame(rows).set_index("다음 대여까지")
    t["누적 비율"] = t.비율.cumsum()
    t.round(4).to_csv(ROOT / "output/tables/rb_13_after_report.csv")
    pd.set_option("display.width", 200)
    print(f"신고 {len(d):,}건(2025-07 ~ 11), 신고 뒤 위치를 못 찾은 신고 {d.loc_st.isna().sum():,}건")
    print(t.round(3).to_string())


if __name__ == "__main__":
    main()
