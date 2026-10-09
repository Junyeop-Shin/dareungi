"""매시 화면 갱신(3A 추가, 사람 결정 2026-10-09). 매시 25분 cron — collect_rent(15분)·detect_sham(20분) 뒤.

왜: 대시보드 스냅샷은 daily(07:00)·weekly에서만 발행해서, 점검 대상 화면의 지도가 하루 종일 아침 위치를 보여 줬다.
정비 담당자가 보는 시점에 가까운 위치여야 지도가 쓸모 있으므로, 자주 바뀌는 것만 매시 다시 만들어 발행한다.

무엇을:
  1. 고장신고를 다시 받는다(collect_fail, 최근 7일. API는 신고가 거의 바로 올라온다)
  2. 최근 신고 자전거 목록(reported_recent)을 다시 만든다(daily.build_reported_recent)
  3. 점검 권장 자전거(bike_risk_latest)의 최근 반납 대여소·시각·자치구만 새로 채운다(refresh_positions).
     점수·등급·순위는 건드리지 않는다(주 1회 weekly가 정한다)
  4. reported_recent, bike_risk_latest, alerts_live, data_status를 발행한다

한계: 대여 기록은 반납한 뒤에야, 시간대가 끝나야 API에 나오므로 위치는 실제보다 1 ~ 2시간 늦다.
      공개 API에는 자전거별 현재 위치가 없어 '마지막으로 반납된 대여소'가 가장 가까운 값이다.

입력: ops.duckdb trips_live, fail_live, bike_risk_latest   출력: 같은 DB의 표 2개 + serving/*.parquet 4개
실행: .venv/bin/python py/jobs/hourly.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))
from common import alert, connect, job_lock, log_run, table_exists   # noqa: E402
import collect_fail   # noqa: E402
from daily import build_reported_recent   # noqa: E402
from publish import build_status, publish   # noqa: E402

PUBLISH = ("reported_recent", "bike_risk_latest", "alerts_live", "data_status")


def refresh_positions(con):
    """bike_risk_latest의 위치 열(last_st, last_rtn_dt, last_st_name, district)을 지금까지의 대여 기록으로 다시 채운다.
    weekly.score_risk가 표를 만들 때 붙인 것과 같은 정의(반납 시각이 가장 늦은 대여의 반납 대여소). 반환: 위치가 바뀐 자전거 수"""
    station = "station_live" if table_exists(con, "station_live") else "station_base"
    # 자전거별 마지막 반납을 임시 표에 한 번만 뽑아, 바뀐 대수 세기와 갱신에 같이 쓴다(대여 기록 1,300만 행을 두 번 훑지 않게)
    con.execute("""CREATE OR REPLACE TEMP TABLE last_rtn AS
                   SELECT bike_id, arg_max(rtn_st, rtn_dt) AS last_st, max(rtn_dt) AS last_rtn_dt FROM trips_live WHERE rtn_dt IS NOT NULL GROUP BY 1""")
    n = con.execute("SELECT count(*) FROM bike_risk_latest r JOIN last_rtn l USING (bike_id) WHERE r.last_rtn_dt IS DISTINCT FROM l.last_rtn_dt").fetchone()[0]
    con.execute(f"""
    UPDATE bike_risk_latest AS r
    SET last_st = l.last_st, last_rtn_dt = l.last_rtn_dt, last_st_name = s.st_name, district = s.district
    FROM last_rtn l LEFT JOIN {station} s ON l.last_st = s.st_no
    WHERE r.bike_id = l.bike_id""")
    return n


def run():
    try:
        collect_fail.run()            # 잠금·기록·경보는 안에서 한다. 실패해도 대여 기록으로 위치는 갱신한다
    except Exception as e:
        print(f"hourly: 고장신고 수집 실패({e}) — 있는 신고로 계속")
    with job_lock():
        con = connect()
        try:
            if not table_exists(con, "raw_rent") or not table_exists(con, "raw_fail"):
                raise RuntimeError("raw_rent 또는 raw_fail 없음 — collect_rent / collect_fail 먼저")
            build_reported_recent(con)
            moved = refresh_positions(con) if table_exists(con, "bike_risk_latest") else 0
            build_status(con)
            done = [t for t in PUBLISH if table_exists(con, t)]
            for t in done:
                publish(con, t)
            n = con.execute("SELECT count(*) FROM reported_recent").fetchone()[0]
            log_run(con, "hourly", "ok", f"최근 신고 {n}대, 위치 바뀐 점검 대상 {moved}대")
            print(f"hourly: 최근 신고 {n:,}대, 위치가 바뀐 점수 자전거 {moved:,}대, 발행 {done}")
        except Exception as e:
            log_run(con, "hourly", "fail", str(e)); alert(f"hourly 실패: {e}")
            raise
        finally:
            con.close()


if __name__ == "__main__":
    run()
