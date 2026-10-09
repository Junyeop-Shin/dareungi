"""분석의 바탕이 되는 정의가 바뀌지 않았는지 확인하는 작은 시험들(tests/test_ops.py는 운영 파이프라인 쪽).

원본 데이터나 DB는 건드리지 않고, 임시 파일과 메모리 DuckDB로만 돈다. pytest가 없어도 python으로 바로 돌릴 수 있다.

  1. prep.to_utf8: CP949 원본을 UTF-8 사본으로 만들고 원본은 그대로 둔다. 확장자만 .csv인 엑셀은 변환하지 않는다(단계 0에서 찾은 함정)
  2. prep.std_name: 후보 열 이름 가운데 처음 맞는 것을 표준 이름으로 바꾸고, 없으면 그대로 둔다
  3. actuals._daily_sql: 신고 건수 = 고유한 (자전거, 등록 시각), 유형별 = 행 수(단계 0의 정의).
     같은 대여소에 1분 이하로 반납한 대여는 대여 건수에서 뺀다(2026년 파일에만 있는 0 ~ 1분 기록)

실행: .venv/bin/python tests/test_analysis.py
"""
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "py"), str(ROOT / "py/jobs")]
import actuals   # noqa: E402
import prep      # noqa: E402


def test_to_utf8_keeps_original_and_skips_fake_csv():
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / "raw.csv", Path(d) / "utf8/raw.csv"
        raw = "자전거번호,고장구분\nSPB-00001,타이어\n".encode("cp949")
        src.write_bytes(raw)
        enc, broken = prep.to_utf8(src, dst)
        assert dst.read_text(encoding="utf-8").splitlines()[1] == "SPB-00001,타이어" and not broken, (enc, broken)
        assert src.read_bytes() == raw, "원본이 바뀌었다"
        fake = Path(d) / "excel.csv"
        fake.write_bytes(b"PK\x03\x04" + b"\x00" * 100)          # 엑셀(zip) 파일의 머리
        assert "변환 안 함" in prep.to_utf8(fake, Path(d) / "utf8/excel.csv")[0] and not (Path(d) / "utf8/excel.csv").exists()


def test_std_name_first_candidate_wins():
    import pandas as pd
    df = pd.DataFrame({"대여일시": [1], "대여 대여소번호": [2]})
    assert list(prep.std_name(df, "rent_dt", ["대여 일시", "대여일시"]).columns) == ["rent_dt", "대여 대여소번호"]
    assert list(prep.std_name(df, "x", ["없는 열"]).columns) == list(df.columns)


def test_daily_counts_reports_by_bike_and_time_and_drops_sham_rentals():
    con = duckdb.connect()
    con.execute("CREATE TABLE station AS SELECT * FROM (VALUES (1, '중구'), (2, '마포구')) t(st_no, district)")
    # 대여 3건: 정상 2건 + 같은 대여소에 1분 만에 반납 1건(빼야 한다)
    con.execute("""CREATE TABLE trips AS SELECT * FROM (VALUES (10, TIMESTAMP '2026-07-01 09:00', 1, 2, 12.0), (11, TIMESTAMP '2026-07-01 10:00', 1, 1, 1.0),
                   (12, TIMESTAMP '2026-07-01 11:00', 1, 1, 30.0)) t(bike_id, rent_dt, rent_st, rtn_st, use_min)""")
    # 신고: 10번 자전거가 한 번에 타이어·체인 두 유형(1건, 2행), 11번 자전거가 타이어 1건
    con.execute("""CREATE TABLE fail AS SELECT * FROM (VALUES (10, TIMESTAMP '2026-07-01 12:00', '타이어', 1), (10, TIMESTAMP '2026-07-01 12:00', '체인', 1),
                   (11, TIMESTAMP '2026-07-01 13:00', '타이어', 1)) t(bike_id, reg_ts, kind, last_st)""")
    r = con.sql(actuals._daily_sql("trips", "fail", "station", "2026-07-01", "2026-07-01")).df().set_index("district").loc["중구"]
    assert r.rentals == 2, f"1분 이하 같은 대여소 반납이 빠지지 않았다: {r.rentals}"
    assert r.reports == 2 and r.n_tire == 2 and r.n_chain == 1, r.to_dict()          # 신고 2건, 유형 행은 타이어 2 · 체인 1


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t(); print("통과:", t.__name__)
    print(f"{len(tests)}개 모두 통과")
