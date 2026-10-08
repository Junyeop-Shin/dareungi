"""대여소 마스터를 만들고 고도를 붙인다(런북 05, 2A-3).

서울 열린데이터광장 API 두 개로 대여소 표를 만든다.
  tbCycleStationInfo (OA-13252)  대여소번호(RENT_NO, 대여이력의 '대여 대여소번호'와 같은 체계), ID(ST-xxx),
                                 이름, 자치구, 좌표, 거치대 수
  bikeStationMaster (OA-21235)   ID, 주소, 좌표. 번호가 없어 ID로만 맞추고, 첫 표에 없는 대여소의 좌표를 보충한다
표준 열은 st_no, st_id, st_name, lat, lon, district(주소의 '○○구'), hold_num, han_river.
han_river는 이름에 '한강'이 들어가면 1인 임시 규칙이다(지도로 검수하지 않았다).

trips의 대여·반납 대여소번호 중 좌표를 못 찾는 비율을 재고(1% 미만이면 충분), OpenTopoData(SRTM 30m)로
고도를 붙인다. 요청당 100지점, 초당 1회 제한이 있다.

주의: 30m 격자 고도라 절댓값은 부정확하다. 대여소 사이 고도 차로만 쓴다.

입력: 서울 API(.env의 SEOUL_API_KEY), DuckDB trips
출력: data/interim/station_master.csv, DuckDB station, output/tables/panel_2a3_station_checks.csv
실행: .venv/bin/python py/panel/23_station.py            (API 수집, 커버리지 확인, 고도)
      .venv/bin/python py/panel/23_station.py --no-elev  (고도 생략)
"""
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from db import connect              # noqa: E402
from seoul_api import seoul_all     # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/interim/station_master.csv"


def fetch():
    a = seoul_all("tbCycleStationInfo")
    b = seoul_all("bikeStationMaster")
    st = pd.DataFrame({
        "st_no": pd.to_numeric(a.RENT_NO, errors="coerce").astype("Int64"), "st_id": a.RENT_ID, "st_name": a.RENT_NM,
        "lat": pd.to_numeric(a.STA_LAT, errors="coerce"), "lon": pd.to_numeric(a.STA_LONG, errors="coerce"),
        "district": a.STA_LOC, "addr": a.STA_ADD1, "hold_num": pd.to_numeric(a.HOLD_NUM, errors="coerce"), "src": "info"})
    extra = b[~b.RNTLS_ID.isin(st.st_id)]
    extra = pd.DataFrame({"st_no": pd.NA, "st_id": extra.RNTLS_ID, "st_name": pd.NA, "lat": pd.to_numeric(extra.LAT, errors="coerce"),
                          "lon": pd.to_numeric(extra.LOT, errors="coerce"), "district": extra.ADDR1.str.extract(r"(\S+구)\b")[0],
                          "addr": extra.ADDR1, "hold_num": pd.NA, "src": "master_only"})
    st = pd.concat([st, extra], ignore_index=True)
    st["district"] = st.district.fillna(st.addr.str.extract(r"(\S+구)\b")[0])
    st["han_river"] = st.st_name.fillna("").str.contains("한강").astype(int)
    # 서울 범위를 벗어난 좌표는 오류로 보고 비운다
    bad = ~st.lat.between(37.4, 37.72) | ~st.lon.between(126.75, 127.2)
    st.loc[bad, ["lat", "lon"]] = pd.NA
    return st, len(a), len(b), int(bad.sum())


def coverage(con, st):
    con.execute("CREATE OR REPLACE TABLE station AS SELECT * FROM st WHERE st_no IS NOT NULL")
    q = """
    SELECT 'rent_st' AS side, avg(CASE WHEN s.st_no IS NULL THEN 1.0 ELSE 0 END) AS unknown_share, count(DISTINCT t.rent_st) AS n_st
    FROM trips t LEFT JOIN station s ON t.rent_st = s.st_no
    UNION ALL
    SELECT 'rtn_st', avg(CASE WHEN s.st_no IS NULL THEN 1.0 ELSE 0 END), count(DISTINCT t.rtn_st)
    FROM trips t LEFT JOIN station s ON t.rtn_st = s.st_no
    """
    cov = con.sql(q).df()
    missing = con.sql("""
    SELECT t.rent_st AS st_no, count(*) AS n FROM trips t LEFT JOIN station s ON t.rent_st = s.st_no
    WHERE s.st_no IS NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 20""").df()
    return cov, missing


def elevation(st):
    """좌표가 있는 대여소에 OpenTopoData SRTM 30m 고도(elev_m)를 붙인다. 100개씩, 초당 1회"""
    ok = st[st.lat.notna() & st.lon.notna()].copy()
    elev = []
    for i in range(0, len(ok), 100):
        chunk = ok.iloc[i:i + 100]
        loc = "|".join(f"{a},{b}" for a, b in zip(chunk.lat, chunk.lon))
        for attempt in range(3):
            r = requests.get("https://api.opentopodata.org/v1/srtm30m", params={"locations": loc}, timeout=60)
            if r.status_code == 200:
                break
            time.sleep(5)
        r.raise_for_status()
        elev += [x["elevation"] for x in r.json()["results"]]
        time.sleep(1.1)
    ok["elev_m"] = elev
    return st.merge(ok[["st_id", "elev_m"]], on="st_id", how="left")


def main(do_elev=True):
    st, n_info, n_master, n_badcoord = fetch()
    con = connect()
    cov, missing = coverage(con, st)
    if do_elev:
        st = elevation(st)
        con.execute("CREATE OR REPLACE TABLE station AS SELECT * FROM st WHERE st_no IS NOT NULL")
    st.to_csv(OUT, index=False)
    checks = pd.DataFrame([
        {"항목": "tbCycleStationInfo 행", "값": n_info}, {"항목": "bikeStationMaster 행", "값": n_master},
        {"항목": "master에만 있는 ID(번호 없음)", "값": int((st.src == "master_only").sum())},
        {"항목": "서울 밖 좌표(비움)", "값": n_badcoord},
        {"항목": "대여 대여소번호 중 좌표 없는 비율", "값": float(cov.loc[cov.side == "rent_st", "unknown_share"].iloc[0])},
        {"항목": "반납 대여소번호 중 좌표 없는 비율", "값": float(cov.loc[cov.side == "rtn_st", "unknown_share"].iloc[0])},
        {"항목": "대여이력에 나온 대여 대여소 수", "값": int(cov.loc[cov.side == "rent_st", "n_st"].iloc[0])},
        {"항목": "번호 있는 대여소 수", "값": int(st.st_no.notna().sum())},
    ] + ([{"항목": "고도 중앙값(m)", "값": float(st.elev_m.median())}, {"항목": "고도 99%(m)", "값": float(st.elev_m.quantile(0.99))}] if do_elev else []))
    checks.to_csv(ROOT / "output/tables/panel_2a3_station_checks.csv", index=False)
    print(checks.to_string(index=False))
    print("좌표 없는 대여소 상위:", missing.to_string(index=False))
    if do_elev:
        print(st.sort_values("elev_m", ascending=False)[["st_name", "district", "elev_m"]].head(8).to_string(index=False))
        print(st[st.han_river == 1][["st_name", "elev_m"]].head(8).to_string(index=False))


if __name__ == "__main__":
    main(do_elev="--no-elev" not in sys.argv)
