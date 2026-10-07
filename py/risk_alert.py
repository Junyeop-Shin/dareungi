"""헛대여 경보 검증 — 단계 2B-5 (런북 06): "지금 고장 났을 것 같은 자전거" 신호가 실제 신고로 이어지는가

[무엇을]
같은 자전거가 **연속 두 번** 헛대여(같은 대여소 반납, 2 ~ 3분)되면 경보를 낸다(같은 자전거 24시간 안 중복 경보는 첫 번째만).
경보 뒤 7일 안에 신고된 비율(적중률)과 경보 → 신고까지의 선행 시간을 잰다. 비교 기준은 "아무 통행이나 고른 뒤 7일 안 신고 비율".
민감도: 헛대여 기준 2 ~ 3분 / 2 ~ 5분, 연속 2회 / 3회.
서울은 자전거별 신고가 공개돼 이런 검증이 가능하다(선행 사례 RIDEY의 "신고보다 중앙값 20 ~ 25시간 앞섬" 주장을 독립 확인).
[입력]  DuckDB trips_seq(2A-7), fail(2A-8)
[출력]  output/tables/risk_alert_valid.csv (기준별 경보 수·적중률·선행 시간·기준선 대비 배수), risk_alert_by_year.csv
[실행]  .venv/bin/python py/risk_alert.py   (1 ~ 2분)
검증은 학습 + 검증 기간(~ 2025-12)으로 한다. 2026년은 테스트 기간이라 표에서 따로 둔다.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
from db import connect   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def alerts(con, sham_col, k):
    """연속 k회 헛대여 → 경보 시각. 24시간 안 중복은 첫 번째만"""
    lags = " AND ".join(f"lag({sham_col}, {i}) OVER w" for i in range(1, k)) or "TRUE"      # k = 1이면 헛대여 한 번마다
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE alerts AS
    WITH s AS (SELECT bike_id, rent_dt, {sham_col} AS sh, {lags} AS prev_ok FROM trips_seq WINDOW w AS (PARTITION BY bike_id ORDER BY rent_dt)),
         a AS (SELECT bike_id, rent_dt AS alert_ts, lag(rent_dt) OVER (PARTITION BY bike_id ORDER BY rent_dt) AS prev_alert
               FROM s WHERE sh AND prev_ok)
    SELECT bike_id, alert_ts FROM a WHERE prev_alert IS NULL OR alert_ts - prev_alert > INTERVAL 24 HOUR""")
    return con.sql("""
    SELECT year(a.alert_ts) AS y, count(*) AS n_alerts,
           avg(CASE WHEN f.reg_ts <= a.alert_ts + INTERVAL 7 DAY THEN 1.0 ELSE 0 END) AS hit_7d,
           avg(CASE WHEN f.reg_ts <= a.alert_ts + INTERVAL 1 DAY THEN 1.0 ELSE 0 END) AS hit_1d,
           median(date_diff('hour', a.alert_ts, f.reg_ts)) FILTER (WHERE f.reg_ts <= a.alert_ts + INTERVAL 7 DAY) AS median_lead_h
    FROM alerts a ASOF LEFT JOIN (SELECT bike_id, reg_ts FROM fail WHERE keep) f ON a.bike_id = f.bike_id AND a.alert_ts <= f.reg_ts
    GROUP BY 1 ORDER BY 1""").df()


def baseline(con):
    """기준선: 무작위 통행 뒤 7일 안 신고 비율(연도별, 표본 30만 건)"""
    return con.sql("""
    SELECT year(t.rent_dt) AS y, count(*) AS n,
           avg(CASE WHEN f.reg_ts <= t.rent_dt + INTERVAL 7 DAY THEN 1.0 ELSE 0 END) AS base_7d,
           avg(CASE WHEN f.reg_ts <= t.rent_dt + INTERVAL 1 DAY THEN 1.0 ELSE 0 END) AS base_1d
    FROM (SELECT bike_id, rent_dt FROM trips_seq USING SAMPLE 300000 ROWS) t
    ASOF LEFT JOIN (SELECT bike_id, reg_ts FROM fail WHERE keep) f ON t.bike_id = f.bike_id AND t.rent_dt <= f.reg_ts
    GROUP BY 1 ORDER BY 1""").df()


def main():
    con = connect()
    base = baseline(con)
    rows = []
    for sham_col, lab in (("sham", "2 ~ 3분"), ("sham_wide", "2 ~ 5분")):
        for k in (1, 2, 3):
            a = alerts(con, sham_col, k).merge(base[["y", "base_7d", "base_1d"]], on="y")
            a["기준"] = f"{lab} 연속 {k}회"
            rows.append(a)
    t = pd.concat(rows, ignore_index=True)
    t["배수_7d"] = t.hit_7d / t.base_7d
    t["split"] = t.y.map(lambda y: "테스트(2026)" if y >= 2026 else "학습·검증")
    t.round(4).to_csv(ROOT / "output/tables/risk_alert_by_year.csv", index=False)
    s = t[t.split == "학습·검증"].groupby("기준").apply(lambda g: pd.Series({
        "n_alerts": g.n_alerts.sum(), "hit_7d": (g.hit_7d * g.n_alerts).sum() / g.n_alerts.sum(),
        "hit_1d": (g.hit_1d * g.n_alerts).sum() / g.n_alerts.sum(), "base_7d": (g.base_7d * g.n_alerts).sum() / g.n_alerts.sum(),
        "median_lead_h": (g.median_lead_h * g.n_alerts).sum() / g.n_alerts.sum()})).reset_index()
    s["배수_7d"] = s.hit_7d / s.base_7d
    s.round(4).to_csv(ROOT / "output/tables/risk_alert_valid.csv", index=False)
    pd.set_option("display.width", 200)
    print(base.round(4).to_string(index=False)); print(s.round(3).to_string(index=False)); print(t.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
