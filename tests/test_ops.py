"""운영 파이프라인이 조용히 망가지지 않게 확인하는 작은 시험들.

검토(2026-10-09)에서 찾아 고친 문제가 다시 생기지 않는지 본다. 데이터 파일이나 운영 DB는 건드리지 않고,
메모리 DuckDB와 임시 잠금 파일로만 돈다. pytest가 없어도 python으로 바로 돌릴 수 있다.

  1. upsert_rows: 빈 응답이면 기존 행을 지우지 않는다. 넣다가 실패하면 지운 것도 되돌린다
  2. job_lock: 한 번에 하나만 잡는다. 죽은 프로세스가 남긴 잠금 파일은 막지 않는다
  3. pending_hours: 처음엔 최근 3일, 그 뒤엔 빠진 시간대 + 3시간 전 + 24시간 전
  4. fc_ops._amount: 단기예보 강수량 문자열 네 가지
  5. fc_ops.levels: 날짜 d의 신고율은 d − 7일까지의 자료만 쓴다
  6. hourly.refresh_positions: 점수 표의 위치 열만 최근 반납 대여소로 바뀌고 점수는 그대로다
  7. daily.data_checks: 새 신고 유형, 읽지 못한 신고, 이상한 대여 기록을 잡고, 정상 자료에는 조용하다
  8. risk_track: 점수 이력을 주 단위로 바꿔 넣고, 다음 주가 다 지난 주만 실제 신고로 채점한다
  9. actuals.retrain_check: 12주가 차고 예측 오차가 기준값보다 클 때만 재학습 검토를 알린다
 10. risk_track.retrain_note: 8주 전에는 조용하고, 8주째에 한 번 알리고, 그 뒤에는 기준 아래로 내려갈 때만 알린다

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
import daily           # noqa: E402
import risk_track      # noqa: E402
import actuals         # noqa: E402


def test_risk_retrain_note_stages():
    ev = lambda lifts: pd.DataFrame({"wk": pd.date_range("2026-09-28", periods=len(lifts), freq="7D"), "lift_top1000": lifts})
    assert risk_track.retrain_note(ev([3.0] * 7)) is None, "8주가 안 찼는데 알렸다"
    assert "그대로 쓴다" in risk_track.retrain_note(ev([3.0] * 8)) and "다시 정해야" in risk_track.retrain_note(ev([2.0] * 8))
    assert risk_track.retrain_note(ev([3.0] * 12)) is None, "기준을 넘는데 알렸다"
    assert "재학습 검토" in risk_track.retrain_note(ev([3.0] * 8 + [2.0] * 8)), "기준 아래로 내려갔는데 알리지 않았다"
    assert risk_track.retrain_note(ev([2.0] * 16)) is None, "처음부터 낮았던 경우는 사람이 기준을 정하기 전까지 자동으로 알리지 않는다"


def test_retrain_check_needs_12_weeks_and_worse_than_naive():
    wk = pd.date_range("2026-07-06", periods=12, freq="7D")
    good = pd.DataFrame({"week": wk, "part": "전체", "actual": 100.0, "pred": 105.0, "naive": 120.0})
    assert actuals.retrain_check(good)[0] is False, "예측이 기준값보다 나은데 알렸다"
    assert actuals.retrain_check(good.assign(pred=130.0))[0] is True, "예측이 기준값보다 나쁜데 알리지 않았다"
    assert actuals.retrain_check(good.assign(pred=130.0).head(11))[0] is False, "12주가 안 찼는데 알렸다"


def test_risk_track_history_and_eval():
    con = duckdb.connect()
    # 자전거 4대: 1번이 배수 가장 높음. 점수 주 2026-09-28 → 채점 대상은 10/5 ~ 10/11
    con.execute("""CREATE TABLE bike_risk_latest AS SELECT * FROM (VALUES (TIMESTAMP '2026-09-28', 1, '점검 권장', 9.0, 0.5, 'v1'), (TIMESTAMP '2026-09-28', 2, '주의', 3.0, 0.2, 'v1'),
                   (TIMESTAMP '2026-09-28', 3, '낮음', 0.5, 0.01, 'v1'), (TIMESTAMP '2026-09-28', 4, '산정 제외', NULL, NULL, 'v1')) t(wk, bike_id, grade, risk_ratio, prob_next, model_version)""")
    con.execute("CREATE TABLE reported_recent AS SELECT 2 AS bike_id")
    con.execute("""CREATE TABLE fail_live AS SELECT * FROM (VALUES (1, TIMESTAMP '2026-10-06 09:00', '체인'), (1, TIMESTAMP '2026-10-07 09:00', '타이어'),
                   (3, TIMESTAMP '2026-10-01 09:00', '안장'), (9, TIMESTAMP '2026-10-11 23:00', '기타')) t(bike_id, reg_ts, kind)""")
    assert risk_track.save_history(con)[1] == 4 and risk_track.save_history(con)[1] == 4, "같은 주를 다시 넣으면 줄이 늘면 안 된다"
    assert len(risk_track.evaluate(con)) == 0, "다음 주(10/5 ~ 10/11)가 아직 안 끝났는데 채점했다"
    con.execute("INSERT INTO fail_live VALUES (9, TIMESTAMP '2026-10-12 08:00', '기타')")          # 다음 주가 다 지났다
    e = risk_track.evaluate(con).iloc[0]
    assert e.n_ranked == 3 and abs(e.base_rate - 1 / 3) < 1e-9 and e.n_insp == 1 and e.hit_insp == 1.0, e.to_dict()          # 3번의 10/1 신고는 채점 주 밖
    assert abs(e.share_chain - 1 / 3) < 1e-9 and abs(e.share_tire - 1 / 3) < 1e-9, e.to_dict()          # 그 주 신고 3행 중 체인 1, 타이어 1(기타 1)


def test_data_checks_catch_new_kind_and_odd_rows():
    def db(kinds, rtn_shift_min):
        con = duckdb.connect()
        con.execute("CREATE TABLE raw_fail (x INT)"); con.execute("CREATE TABLE raw_rent (x INT)")          # 있다는 표시만. 점검은 아래 표(운영에서는 뷰)를 읽는다
        con.execute("CREATE TABLE fail_live AS SELECT 100 + i AS bike_id, now()::TIMESTAMP AS reg_ts, k AS kind, current_date AS reg_date "
                    "FROM (SELECT unnest(?) AS k, generate_subscripts(?, 1) AS i)", [kinds, kinds])
        con.execute(f"CREATE TABLE trips_live AS SELECT 1 AS bike_id, now()::TIMESTAMP - INTERVAL 3 HOUR AS rent_dt, "
                    f"now()::TIMESTAMP - INTERVAL 3 HOUR + to_minutes({rtn_shift_min}) AS rtn_dt, {rtn_shift_min}.0 AS use_min")
        return con
    assert daily.data_checks(db(list(daily.KNOWN_KINDS), 10)) == [], "정상 자료에 문제가 잡혔다"
    names = [n for n, _ in daily.data_checks(db(["타이어", "브레이크"], -5))]
    assert names == ["새 신고 유형", "이상한 대여 기록"], names


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
        with common.job_lock(wait_s=0, tries=2):                  # 파일이 남아 있어도 잡은 프로세스가 없으면 바로 잡힌다
            pass


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
