"""운영 DB 초기화(런북 07, 3A-5). 처음 한 번, 그리고 반기 파일을 새로 맞춰 본 뒤에 돌린다.

분석 DB(data/db/dareungi.duckdb, 2A에서 만든 것)의 과거 주별 요약을 운영 DB(data/db/ops.duckdb)로 복사한다.
그래야 운영 중에 계산하는 '마지막 신고 이후 누적'이 2023-07부터 이어진다.
복사하는 표: bw_usage, bw_fail, bike_info, station(→ station_base), wx_w(주별 강수일·동결융해일), station_cluster

주의: 분석 DB는 19GB라 통째로 옮기지 않고 주별 요약(수백만 행)만 가져온다.
분석 DB를 다른 프로세스가 열고 있으면 실패한다.

실행: .venv/bin/python py/jobs/init_ops.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from common import ROOT, connect, job_lock, log_run   # noqa: E402

TABLES = {"bw_usage": "bw_usage", "bw_fail": "bw_fail", "bike_info": "bike_info", "station": "station_base", "wx_w": "wx_w", "station_cluster": "station_cluster"}


def run():
    with job_lock():
        con = connect()
        try:
            con.execute(f"ATTACH '{(ROOT / 'data/db/dareungi.duckdb').as_posix()}' AS ana (READ_ONLY)")
            for src, dst in TABLES.items():
                con.execute(f"CREATE OR REPLACE TABLE {dst} AS SELECT * FROM ana.{src}")
                n = con.execute(f"SELECT count(*) FROM {dst}").fetchone()[0]
                print(f"{dst}: {n:,}행")
            con.execute("DETACH ana")
            last = con.execute("SELECT max(wk) FROM bw_usage").fetchone()[0]
            log_run(con, "init_ops", "ok", f"주별 요약 ~{last}")
            print("주별 요약 마지막 주:", last)
        finally:
            con.close()


if __name__ == "__main__":
    run()
