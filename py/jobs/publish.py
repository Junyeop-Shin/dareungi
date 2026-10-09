"""스냅샷 발행(런북 07, 3A-6).

대시보드가 읽을 작은 Parquet 파일을 serving/에 만든다. DuckDB는 한 프로세스만 쓸 수 있어서
대시보드는 DB를 열지 않고 serving/*.parquet만 읽는다. 임시 파일에 쓴 뒤 os.replace로 바꿔치기해서
반쯤 쓰인 파일을 읽는 일이 없게 한다.

  bike_risk_latest  자전거별 위험 점수·등급·이유(weekly.py)     forecast_weekly  부품별 주간 예측(weekly.py)
  bike_card         자전거 조회용 집계(daily.py)                alerts_live      헛대여 경보(detect_sham.py)
  weekly_actual     주 × 자치구 대여·신고(actuals.py)           daily_reports    일 × 자치구 × 부품 신고(actuals.py)
  forecast_track    주별 예측·실제·나이브(actuals.py)           reported_recent  최근 7일 고장 신고된 자전거(daily.py)
  data_status       데이터별 최신 시각(화면의 '기준 시각')
  forecast_accuracy 주간 예상의 과거 평균 오차(화면 '과거 기록으로 확인한 정확도' 표). DB가 아니라 output/tables의 결과 표에서 만든다

실행: .venv/bin/python py/jobs/publish.py   # daily.py·weekly.py 끝에서도 부른다. 자주 바뀌는 4개는 hourly.py가 매시 발행한다
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import ROOT, alert, connect, job_lock, log_run, table_exists   # noqa: E402

SERVING = ROOT / "serving"
SNAPSHOTS = ["bike_risk_latest", "bike_card", "forecast_weekly", "forecast_track", "alerts_live", "weekly_actual", "daily_reports", "reported_recent", "data_status"]


def publish(con, table, name=None):
    name = name or table
    SERVING.mkdir(exist_ok=True)
    tmp, dst = SERVING / f"{name}.parquet.tmp", SERVING / f"{name}.parquet"
    con.execute(f"COPY (SELECT * FROM {table}) TO '{tmp.as_posix()}' (FORMAT PARQUET)")
    os.replace(tmp, dst)


LIVE_FROM = "2026-07-06"     # fc_ops_compare.py의 FROM과 같다(테스트 구간 뒤 첫 월요일). 운영 비교 표의 시작 주


def publish_accuracy():
    """화면에 숫자를 손으로 옮겨 적지 않도록, 저장된 결과 표에서 '이 예상'과 '단순 짐작'의 평균 오차(전체 신고, %)를 읽어 내보낸다.

    입력: output/tables/fc_ops_replay_valid.csv(2024 검증: S1_kma, B1 나이브. 2023년까지로 맞춘 모형. py/forecast/fc_ops_fit.py)
          output/tables/fc_ops_compare_live.csv(2026-07-06 주부터 실제 결과가 나온 주: 예보판, 나이브. py/forecast/fc_ops_compare.py)
    출력: serving/forecast_accuracy.parquet(period 확인한 기간, train 그 숫자를 낸 모형의 학습 기간, pred 이 예상, naive 단순 짐작). 표가 없으면 그 줄은 뺀다.
    학습 기간: 2024 검증은 2023년까지로 맞춘 모형(fc.TRAIN_DAYS), 2026-07 이후는 운영 모형(fc_ops_spec.json의 train)."""
    tab, rows = ROOT / "output/tables", []
    if (tab / "fc_ops_replay_valid.csv").exists():
        v = pd.read_csv(tab / "fc_ops_replay_valid.csv", index_col=0)["전체"]
        rows.append({"period": "2024년 1 ~ 12월", "train": "2021-04 ~ 2023-12", "pred": v["S1_kma"], "naive": v["B1 나이브"]})
    if (tab / "fc_ops_compare_live.csv").exists():
        c = pd.read_csv(tab / "fc_ops_compare_live.csv", index_col=0)
        n = int(c["주 수"].iloc[0])
        a = pd.Timestamp(LIVE_FROM); b = a + pd.Timedelta(days=7 * n - 1)
        tr = json.load(open(ROOT / "output/models/fc_ops_spec.json"))["train"]
        rows.append({"period": f"{a.year}년 {a.month}/{a.day} ~ {b.month}/{b.day} ({n}주)", "train": f"{tr[0][:7]} ~ {tr[1][:7]}", "pred": c.loc["예보판", "전체"], "naive": c.loc["나이브(지난주 그대로)", "전체"]})
    if rows:
        SERVING.mkdir(exist_ok=True)
        tmp = SERVING / "forecast_accuracy.parquet.tmp"
        pd.DataFrame(rows).to_parquet(tmp, index=False); os.replace(tmp, SERVING / "forecast_accuracy.parquet")
    return len(rows)


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
            if publish_accuracy():          # 작은 표라 발행할 때마다 다시 만든다(결과 표가 바뀌면 다음 발행에 반영)
                done.append("forecast_accuracy")
            log_run(con, "publish", "ok", ",".join(done))
            print("publish:", done)
        except Exception as e:          # 발행이 실패하면 화면은 옛 자료를 계속 보여 준다. 기록하고 알린다
            log_run(con, "publish", "fail", str(e)); alert(f"publish 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    run()
