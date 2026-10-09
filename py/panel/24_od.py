"""출발·도착 대여소 쌍(OD)마다 직선거리와 고도 차를 계산한다(런북 05, 2A-4).

trips에 실제로 나온 (대여 대여소, 반납 대여소) 쌍만 만든다. 가능한 모든 쌍(약 2,800² = 800만)을 만들 필요가 없다.
직선거리는 두 대여소 좌표의 하버사인 거리(m), 고도 차는 반납 대여소 고도 − 대여 대여소 고도(m, 양수면 오르막)다.
좌표가 없는 대여소가 낀 쌍은 빠진다(27_trips_x.py에서 그 통행의 지형 열은 비어 있게 된다).

입력: DuckDB trips(22_trips_clean.py), station(23_station.py, 고도 포함)
출력: DuckDB od(rent_st, rtn_st, n, straight_m, delta_elev)
실행: .venv/bin/python py/panel/24_od.py  (1분 안쪽)
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from db import connect   # noqa: E402


def main():
    con = connect()
    con.execute("""
    CREATE OR REPLACE TABLE od AS
    WITH pairs AS (SELECT rent_st, rtn_st, count(*) AS n FROM trips GROUP BY ALL)
    SELECT p.rent_st, p.rtn_st, p.n,
      2 * 6371000 * asin(sqrt(pow(sin(radians(b.lat - a.lat) / 2), 2) +
          cos(radians(a.lat)) * cos(radians(b.lat)) * pow(sin(radians(b.lon - a.lon) / 2), 2))) AS straight_m,
      b.elev_m - a.elev_m AS delta_elev
    FROM pairs p JOIN station a ON p.rent_st = a.st_no JOIN station b ON p.rtn_st = b.st_no
    """)
    print(con.sql("SELECT count(*) AS pairs, sum(n) AS trips, round(median(straight_m)) AS median_m, round(quantile_cont(delta_elev, 0.99), 1) AS elev_p99 FROM od").df().to_string(index=False))


if __name__ == "__main__":
    main()
