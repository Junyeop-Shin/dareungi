"""스냅샷 발행(런북 07, 3A-6).

대시보드가 읽을 작은 Parquet 파일을 serving/에 만든다. DuckDB는 한 프로세스만 쓸 수 있어서
대시보드는 DB를 열지 않고 serving/*.parquet만 읽는다. 임시 파일에 쓴 뒤 os.replace로 바꿔치기해서
반쯤 쓰인 파일을 읽는 일이 없게 한다.

  bike_risk_latest  자전거별 위험 점수·등급·이유(weekly.py)     forecast_weekly  부품별 주간 예측(weekly.py)
  bike_card         시민 조회용 집계(daily.py)                  alerts_live      헛대여 경보(detect_sham.py)
  weekly_actual     주 × 자치구 대여·신고(actuals.py)           daily_reports    일 × 자치구 × 부품 신고(actuals.py)
  forecast_track    주별 예측·실제·나이브(actuals.py)           reported_recent  최근 7일 고장 신고된 자전거(daily.py)
  data_status       데이터별 최신 시각(화면의 '기준 시각')

실행: .venv/bin/python py/jobs/publish.py   # daily.py·weekly.py 끝에서도 부른다
"""
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import ROOT, connect, job_lock, log_run, table_exists   # noqa: E402

SERVING = ROOT / "serving"
SNAPSHOTS = ["bike_risk_latest", "bike_card", "forecast_weekly", "forecast_track", "alerts_live", "weekly_actual", "daily_reports", "reported_recent", "data_status"]


def publish(con, table, name=None):
    name = name or table
    SERVING.mkdir(exist_ok=True)
    tmp, dst = SERVING / f"{name}.parquet.tmp", SERVING / f"{name}.parquet"
    con.execute(f"COPY (SELECT * FROM {table}) TO '{tmp.as_posix()}' (FORMAT PARQUET)")
    os.replace(tmp, dst)


def build_status(con):
    """데이터별 최신 시각과 마지막 수집 시각을 data_status 표 하나로 모은다."""
    parts = []
    if table_exists(con, "rent_hours_done"):
        parts.append("SELECT '대여이력' AS data, CAST(max(q_hour) + INTERVAL 1 HOUR AS VARCHAR) AS latest, CAST(max(fetched_at) AS VARCHAR) AS fetched FROM rent_hours_done")
    if table_exists(con, "raw_fail"):
        parts.append("SELECT '고장신고', CAST(max(reg_ts) AS VARCHAR), CAST(max(ts) AS VARCHAR) FROM fail_live, (SELECT max(ts) AS ts FROM job_runs WHERE job = 'collect_fail' AND status = 'ok')")
    if table_exists(con, "wx_hourly_live"):
        parts.append("SELECT '날씨 관측', CAST(max(ts) AS VARCHAR), NULL FROM wx_hourly_live")
    if table_exists(con, "fcst_archive"):
        parts.append("SELECT '날씨 예보', CAST(max(issue_ts) AS VARCHAR), NULL FROM fcst_archive")
    if table_exists(con, "bike_risk_latest"):
        parts.append("SELECT '위험 점수', CAST(max(wk) AS VARCHAR), NULL FROM bike_risk_latest")
    if table_exists(con, "forecast_weekly"):
        parts.append("SELECT '주간 예측', CAST(max(issue_date) AS VARCHAR), NULL FROM forecast_weekly")
    if table_exists(con, "alerts_live"):
        parts.append("SELECT '헛대여 경보', CAST(max(alert_ts) AS VARCHAR), NULL FROM alerts_live")
    con.execute("CREATE OR REPLACE TABLE data_status AS " + " UNION ALL ".join(parts) + " UNION ALL SELECT '발행 시각', CAST(now() AS VARCHAR), NULL")


def run(names=None):
    with job_lock():
        con = connect()
        try:
            build_status(con)
            done = []
            for t in (names or SNAPSHOTS):
                if table_exists(con, t):
                    publish(con, t); done.append(t)
            log_run(con, "publish", "ok", ",".join(done))
            print("publish:", done)
        finally:
            con.close()


if __name__ == "__main__":
    run()
