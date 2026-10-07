"""대여이력(건별 원본, 약 1억 건) 적재 — 단계 2A-1 (런북 05) + 레짐 원인 분석용 2020-21 상세(04b R3)

[무엇을]
연도 ZIP 안의 월별 CSV를 한 달치씩 꺼내 UTF-8로 바꾼 뒤 DuckDB로 읽어
  - 2015-09 ~ 2023-06 : 자전거별 연간 요약만(bike_year 테이블: 처음 등장일, 대여 횟수, km) — 나이·누적 사용량 추정용
  - 2020-10 ~ 2021-09 : 상세를 data/processed/rent_2021/ 에 Parquet로(레짐 원인 분석 R3 ~ R7용. 별도 폴더에 둔다)
  - 2023-07 ~ 2026-06 : 상세를 data/processed/rent/ 에 Parquet로(자전거×주 패널용)
처리한 파일은 docs/load_log.csv에 적어 두어 중간에 멈춰도 다시 실행하면 이어서 한다(같은 파일을 두 번 넣으면 중복이 생긴다).

[왜 2020-21 상세를 따로 두나]
같은 폴더에 넣으면 2A-9 패널이 2020년부터 시작하고 2021-10 ~ 2023-06 공백을 연속 주로 착각해 '마지막 신고 이후 누적'이 틀어진다.

[입력]  data/raw/rent_history/서울특별시 공공자전거 대여이력 정보_<연도>.zip (2015 ~ 2025), ..._26MM.csv (2026)
        열 17개(2026년은 자전거구분 없는 16개), cp949. 날짜 형식 'YYYY-MM-DD HH:MM:SS'
[출력]  data/db/dareungi.duckdb 의 bike_year 테이블, data/processed/rent/y=…/m=…/*.parquet, data/processed/rent_2021/…, docs/load_log.csv
[실행]  .venv/bin/python py/21_rent_to_parquet.py            (저장소 루트, 월 파일 하나에 1 ~ 3분 × 130개 → 몇 시간. 터미널에서 돌려 둔다)
        .venv/bin/python py/21_rent_to_parquet.py 1509 1510  (특정 달만, 시험용)
"""
import csv
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db import connect          # noqa: E402
from prep import to_utf8        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/raw/rent_history"
LOG = ROOT / "docs/load_log.csv"
FMT = "['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']"       # 날짜 형식 후보(단계 0에서 확인)
DETAIL_FROM = "2307"                                    # 이 달부터 상세(패널용)
R3_RANGE = ("2010", "2109")                             # 레짐 원인 분석용 상세(별도 폴더)
SUMMARY_UNTIL = "2023-07-01"                            # 요약은 이 날 전까지만(상세와 겹치지 않게)

# 열 이름은 전 기간 같다. 2026 파일만 자전거구분이 없다
C17 = dict(bike_no="자전거번호", rent_dt="대여일시", rent_st="대여 대여소번호", rtn_dt="반납일시", rtn_st="반납대여소번호",
           use_min="이용시간(분)", use_m="이용거리(M)", user_type="이용자종류", bike_type="자전거구분")
C16 = {**C17, "bike_type": None}

con = connect()
con.execute("CREATE TABLE IF NOT EXISTS bike_year (bike_id INTEGER, y INTEGER, first_dt TIMESTAMP, n_trips BIGINT, km DOUBLE)")


def _select(csv_path, c):
    """원본 열을 표준 이름·타입으로 바꾸는 SELECT. 자전거번호 'SPB-12345' → 12345"""
    bt = f'"{c["bike_type"]}"' if c.get("bike_type") else "NULL::VARCHAR"
    return f"""
    SELECT TRY_CAST(regexp_extract("{c['bike_no']}", '[0-9]+') AS INTEGER) AS bike_id,
           try_strptime("{c['rent_dt']}", {FMT}) AS rent_dt,
           TRY_CAST("{c['rent_st']}" AS INTEGER) AS rent_st,
           try_strptime("{c['rtn_dt']}", {FMT})  AS rtn_dt,
           TRY_CAST("{c['rtn_st']}" AS INTEGER)  AS rtn_st,
           TRY_CAST("{c['use_min']}" AS DOUBLE)  AS use_min,
           TRY_CAST("{c['use_m']}" AS DOUBLE)    AS use_m,
           {bt} AS bike_type,
           "{c['user_type']}" AS user_type
    FROM read_csv('{csv_path}', header = true, all_varchar = true, ignore_errors = true)
    """


def to_parquet(csv_path, c, out):
    """상세를 연·월 폴더로 나눠 Parquet 저장. 돌려주는 것: (저장한 행 수, 날짜를 못 읽은 행 수)"""
    con.execute(f"""
      COPY (SELECT *, year(rent_dt) AS y, month(rent_dt) AS m FROM ({_select(csv_path, c)}) WHERE rent_dt IS NOT NULL)
      TO '{(ROOT / out).as_posix()}'
      (FORMAT PARQUET, PARTITION_BY (y, m), OVERWRITE_OR_IGNORE, FILENAME_PATTERN 'part_{{uuid}}')
    """)
    n_ok, n_bad = con.execute(f"SELECT count(rent_dt), count(*) - count(rent_dt) FROM ({_select(csv_path, c)})").fetchone()
    return n_ok, n_bad


def summarize_year(csv_path, c, year):
    """자전거별 연간 요약을 bike_year에 더한다(SUMMARY_UNTIL 전 대여만)"""
    con.execute(f"""
    INSERT INTO bike_year
    SELECT bike_id, {year} AS y, min(rent_dt) AS first_dt, count(*) AS n_trips, sum(use_m) / 1000 AS km
    FROM ({_select(csv_path, c)})
    WHERE rent_dt IS NOT NULL AND rent_dt < TIMESTAMP '{SUMMARY_UNTIL}' AND bike_id IS NOT NULL
    GROUP BY 1
    """)
    return con.execute(f"SELECT count(rent_dt), count(*) - count(rent_dt) FROM ({_select(csv_path, c)})").fetchone()


def process(csv_path, name, c):
    """한 달 파일을 어디로 보낼지 정해 처리한다. 돌려주는 것: 처리 방식 문자열과 행 수"""
    yymm = name[-8:-4]                                   # '..._2306.csv' → '2306'
    how, rows = [], {}
    if yymm < DETAIL_FROM:
        rows["summary"] = summarize_year(csv_path, c, 2000 + int(yymm[:2])); how.append("summary")
    if R3_RANGE[0] <= yymm <= R3_RANGE[1]:
        rows["detail2021"] = to_parquet(csv_path, c, "data/processed/rent_2021"); how.append("detail2021")
    if yymm >= DETAIL_FROM:
        rows["detail"] = to_parquet(csv_path, c, "data/processed/rent"); how.append("detail")
    n_ok = max([r[0] for r in rows.values()], default=None)
    n_bad = max([r[1] for r in rows.values()], default=None)
    return "+".join(how), n_ok, n_bad


def done_set():
    if not LOG.exists():
        return set()
    with open(LOG, encoding="utf-8") as fh:
        return {r["file"] for r in csv.DictReader(fh)}


def log_done(name, how, n_ok, n_bad):
    new = not LOG.exists()
    with open(LOG, "a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["file", "how", "rows", "bad_date_rows"])
        w.writerow([name, how, n_ok if n_ok is not None else "", n_bad if n_bad is not None else ""])


def _zip_name(info):
    return info.filename if info.flag_bits & 0x800 else info.filename.encode("cp437").decode("cp949", "replace")


def main(only=None):
    """only: 처리할 YYMM 목록(없으면 전부). 이미 load_log에 있는 파일은 건너뛴다"""
    done = done_set()
    tmp = ROOT / "data/interim/rent_csv"; tmp.mkdir(parents=True, exist_ok=True)
    raw, utf = tmp / "month_raw.csv", tmp / "month_utf8.csv"
    jobs = []                                            # (표시 이름, 원본을 raw로 복사하는 함수, 열 묶음)
    for z in sorted(RAW.glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                name = _zip_name(info)
                if name.lower().endswith(".csv"):
                    jobs.append((name, (z, info), C17))
    for f in sorted(RAW.glob("*.csv")):
        jobs.append((f.name, f, C16))
    jobs.sort(key=lambda j: j[0][-8:-4])
    for name, src, c in jobs:
        yymm = name[-8:-4]
        if name in done or (only and yymm not in only):
            continue
        if isinstance(src, tuple):
            z, info = src
            with zipfile.ZipFile(z) as zf, zf.open(info) as s, open(raw, "wb") as d:
                shutil.copyfileobj(s, d)                 # 한 달치만 디스크에
            to_utf8(raw, utf); raw.unlink()
        else:
            to_utf8(src, utf)
        how, n_ok, n_bad = process(utf.as_posix(), name, c)
        utf.unlink()
        log_done(name, how, n_ok, n_bad)
        print(f"완료 {name}  {how}  행 {n_ok}  날짜 실패 {n_bad}", flush=True)


if __name__ == "__main__":
    main(only=set(sys.argv[1:]) or None)
