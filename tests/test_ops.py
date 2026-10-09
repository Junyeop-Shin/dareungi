"""운영 파이프라인이 조용히 망가지지 않게 확인하는 작은 시험들.

검토(2026-10-09)에서 찾아 고친 문제가 다시 생기지 않는지 본다. 데이터 파일이나 운영 DB는 건드리지 않고,
메모리 DuckDB와 임시 잠금 파일로만 돈다. pytest가 없어도 python으로 바로 돌릴 수 있다.

  1. upsert_rows: 빈 응답이면 기존 행을 지우지 않는다. 넣다가 실패하면 지운 것도 되돌린다
  2. job_lock: 한 번에 하나만 잡는다. 죽은 프로세스의 잠금은 지운다
  3. pending_hours: 처음엔 최근 3일, 그 뒤엔 빠진 시간대 + 3시간 전 + 24시간 전
  4. fc_ops._amount: 단기예보 강수량 문자열 네 가지
  5. fc_ops.levels: 날짜 d의 신고율은 d − 7일까지의 자료만 쓴다
  6. hourly.refresh_positions: 점수 표의 위치 열만 최근 반납 대여소로 바뀌고 점수는 그대로다

실행: .venv/bin/python tests/test_ops.py
"""
import datetime as dt
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "py"), str(ROOT / "py/jobs")]
import common          # noqa: E402
import collect_rent    # noqa: E402
import fc_ops          # noqa: E402
import hourly          # noqa: E402


def test_refresh_positions_moves_only_location():
    con = duckdb.connect()
    con.execute("CREATE TABLE station_base AS SELECT * FROM (VALUES (1, '가', '중구'), (2, '나', '마포구')) t(st_no, st_name, district)")
    con.execute("""CREATE TABLE trips_live AS SELECT * FROM (VALUES (10, TIMESTAMP '2026-10-09 08:00', 1), (10, TIMESTAMP '2026-10-09 12:00', 2),
                   (11, TIMESTAMP '2026-10-09 09:00', 1)) t(bike_id, rtn_dt, rtn_st)""")
    con.execute("""CREATE TABLE bike_risk_latest AS SELECT * FROM (VALUES (10, 3.5, 1, TIMESTAMP '2026-10-09 08:00', '가', '중구'),
                   (11, 1.2, 1, TIMESTAMP '2026-10-09 09:00', '가', '중구'), (12, 0.7, NULL, NULL, NULL, NULL))
                   t(bike_id, risk_ratio, last_st, last_rtn_dt, last_st_name, district)""")
    assert hourly.refresh_positions(con) == 1, "위치가 바뀐 자전거는 10번 하나다"
    got = con.execute("SELECT bike_id, risk_ratio::DOUBLE, last_st, last_st_name, district FROM bike_risk_latest ORDER BY 1").fetchall()
    assert got == [(10, 3.5, 2, '나', '마포구'), (11, 1.2, 1, '가', '중구'), (12, 0.7, None, None, None)], got


def test_upsert_keeps_rows_on_empty_and_rolls_back():
    con = duckdb.connect()
    common.upsert_rows(con, "t", pd.DataFrame({"k": ["a", "a"], "v": ["1", "2"]}), "k = ?", ("a",))
    assert common.upsert_rows(con, "t", pd.DataFrame(), "k = ?", ("a",)) == 0
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 2, "빈 응답에 기존 행이 지워졌다"
    try:
        common.upsert_rows(con, "t", pd.DataFrame({"k": ["a"], "v": ["3"], "new_col": ["x"]}), "k = ?", ("a",))
        raise AssertionError("새 필드인데 오류가 나지 않았다")
    except duckdb.Error:
        pass
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 2, "넣기 실패 뒤 지운 행이 되돌려지지 않았다"


def test_job_lock_exclusive_and_stale():
    with tempfile.TemporaryDirectory() as d:
        common.LOCK = Path(d) / ".ops.lock"
        with common.job_lock():
            try:
                with common.job_lock(wait_s=0, tries=2):
                    raise AssertionError("잠금을 두 번 잡았다")
            except RuntimeError:
                pass
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        common.LOCK.write_text(str(dead.pid))                     # 끝난 프로세스가 남긴 잠금
        with common.job_lock(wait_s=0, tries=2):
            assert common.LOCK.read_text() == str(__import__("os").getpid())
        assert not common.LOCK.exists()


def test_pending_hours():
    con = duckdb.connect()
    now = dt.datetime(2026, 10, 9, 10, 15)
    first = collect_rent.pending_hours(con, now)
    assert first[0] == dt.datetime(2026, 10, 6, 9) and first[-1] == dt.datetime(2026, 10, 9, 9)
    con.execute("INSERT INTO rent_hours_done VALUES ('2026-10-09 08:00', 1, now())")
    assert collect_rent.pending_hours(con, now) == [dt.datetime(2026, 10, 8, 10), dt.datetime(2026, 10, 9, 7), dt.datetime(2026, 10, 9, 9)]


def test_amount():
    assert fc_ops._amount("강수없음") == 0 and fc_ops._amount("적설없음") == 0
    assert fc_ops._amount("1mm 미만") == 0.5
    assert fc_ops._amount("30.0~50.0mm") == 40
    assert fc_ops._amount("50.0mm 이상") == 50 and fc_ops._amount("2.0mm") == 2


def test_levels_use_only_data_before_7_days():
    idx = pd.date_range("2026-01-01", periods=80)
    d = pd.DataFrame({"rentals": 1000.0, **{p: 3.0 for p in fc_ops.PARTS}}, index=idx)
    a = fc_ops.levels(d)
    d2 = d.copy(); d2.loc[idx[-7]:, list(fc_ops.PARTS)] = 99.0     # 마지막 7일만 바꾼다
    b = fc_ops.levels(d2)
    assert np.allclose(a.iloc[-1].to_numpy(), b.iloc[-1].to_numpy()), "7일 안쪽 자료가 신고율에 들어갔다"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t(); print("통과:", t.__name__)
    print(f"{len(tests)}개 모두 통과")
