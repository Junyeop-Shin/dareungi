"""대여이력 원본(건별, 약 1억 건)을 DuckDB 요약과 Parquet로 적재한다(런북 05, 2A-1).

연도별 ZIP에서 월 CSV를 한 달치씩 꺼내 UTF-8로 바꾼 뒤 기간에 따라 나눠 저장한다.
  2015-09 ~ 2023-06  자전거별 연간 요약만 bike_year 테이블에(처음 등장일, 대여 수, km). 나이·누적 사용량 추정용
  2020-10 ~ 2021-09  상세를 data/processed/rent_2021/에. 레짐 원인 분석(04b R3~R7)용
  2023-07 ~ 2026-06  상세를 data/processed/rent/에. 자전거×주 패널용
처리한 파일은 docs/load_log.csv에 남기고, 다시 실행하면 거기서부터 이어 간다.
달 하나를 쓰다가 죽으면 기록에는 없는데 자료는 일부 들어가 있다. 그래서 달마다 쓰기 전에 그 달이 남긴 것(bike_year의
src가 그 달인 행, part_<YYMM>_*.parquet)을 먼저 지운다. 다시 실행해도 두 번 들어가지 않는다.

주의: 2026-10-10 전에 적재한 Parquet(part_<uuid>)과 bike_year 행(src 없음)은 달 표시가 없어 이 방법으로 못 지운다.
그 달을 load_log.csv에서 지우고 다시 넣으면 중복된다. 2020-21 상세를 rent/에 같이 두면 2A-9 패널이
2021-10 ~ 2023-06 공백을 연속된 주로 보고 '마지막 신고 이후 누적'이 틀어지므로 폴더를 나눴다.

입력: data/raw/rent_history/서울특별시 공공자전거 대여이력 정보_<연도>.zip (2015~2025), ..._26MM.csv (2026)
      cp949, 17열(2026년은 자전거구분이 없는 16열), 날짜 'YYYY-MM-DD HH:MM:SS'
출력: DuckDB bike_year, data/processed/rent/y=…/m=…/*.parquet, data/processed/rent_2021/…, docs/load_log.csv
실행: .venv/bin/python py/panel/21_rent_to_parquet.py            (월 파일당 1~3분 × 약 130개, 몇 시간 걸린다)
      .venv/bin/python py/panel/21_rent_to_parquet.py 1509 1510  (지정한 달만)
"""
import csv
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from db import connect          # noqa: E402
from prep import to_utf8        # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/rent_history"
LOG = ROOT / "docs/load_log.csv"
FMT = "['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']"       # 대여·반납일시 형식(단계 0에서 확인)
DETAIL_FROM = "2307"                                    # 이 달부터 상세(패널용)
R3_RANGE = ("2010", "2109")                             # 레짐 원인 분석용 상세(별도 폴더)
SUMMARY_UNTIL = "2023-07-01"                            # 요약은 이 날 전까지만(상세와 겹치지 않게)

# 열 이름은 전 기간 같다. 2026 파일만 자전거구분이 없다
C17 = dict(bike_no="자전거번호", rent_dt="대여일시", rent_st="대여 대여소번호", rtn_dt="반납일시", rtn_st="반납대여소번호",
           use_min="이용시간(분)", use_m="이용거리(M)", user_type="이용자종류", bike_type="자전거구분")
C16 = {**C17, "bike_type": None}

con = connect()
con.execute("CREATE TABLE IF NOT EXISTS bike_year (bike_id INTEGER, y INTEGER, first_dt TIMESTAMP, n_trips BIGINT, km DOUBLE)")
con.execute("ALTER TABLE bike_year ADD COLUMN IF NOT EXISTS src VARCHAR")    # 어느 달 파일에서 온 행인지(YYMM). 중간에 죽은 달을 다시 넣을 때 지우는 기준


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


def to_parquet(csv_path, c, out, yymm):
    """상세를 연·월 파티션으로 Parquet에 쓴다. 파일 이름에 달(YYMM)을 넣어, 쓰다 죽은 달의 조각을 다음 실행에서 지운다.

    반환: (저장한 행 수, 날짜 파싱 실패 행 수)"""
    for old in (ROOT / out).glob(f"y=*/m=*/part_{yymm}_*.parquet"):
        old.unlink()
    con.execute(f"""
      COPY (SELECT *, year(rent_dt) AS y, month(rent_dt) AS m FROM ({_select(csv_path, c)}) WHERE rent_dt IS NOT NULL)
      TO '{(ROOT / out).as_posix()}'
      (FORMAT PARQUET, PARTITION_BY (y, m), OVERWRITE_OR_IGNORE, FILENAME_PATTERN 'part_{yymm}_{{uuid}}')
    """)
    n_ok, n_bad = con.execute(f"SELECT count(rent_dt), count(*) - count(rent_dt) FROM ({_select(csv_path, c)})").fetchone()
    return n_ok, n_bad


def summarize_year(csv_path, c, yymm):
    """자전거별 연간 요약을 bike_year에 더한다(SUMMARY_UNTIL 전 대여만). 그 달이 전에 남긴 행은 먼저 지운다"""
    con.execute("DELETE FROM bike_year WHERE src = ?", [yymm])
    con.execute(f"""
    INSERT INTO bike_year
    SELECT bike_id, {2000 + int(yymm[:2])} AS y, min(rent_dt) AS first_dt, count(*) AS n_trips, sum(use_m) / 1000 AS km, '{yymm}' AS src
    FROM ({_select(csv_path, c)})
    WHERE rent_dt IS NOT NULL AND rent_dt < TIMESTAMP '{SUMMARY_UNTIL}' AND bike_id IS NOT NULL
    GROUP BY 1
    """)
    return con.execute(f"SELECT count(rent_dt), count(*) - count(rent_dt) FROM ({_select(csv_path, c)})").fetchone()


def process(csv_path, name, c):
    """월 파일 하나를 기간에 맞는 곳(요약/상세)으로 보낸다. 반환: (처리 방식, 행 수, 날짜 실패 행 수)"""
    yymm = name[-8:-4]                                   # '..._2306.csv' → '2306'
    how, rows = [], {}
    if yymm < DETAIL_FROM:
        rows["summary"] = summarize_year(csv_path, c, yymm); how.append("summary")
    if R3_RANGE[0] <= yymm <= R3_RANGE[1]:
        rows["detail2021"] = to_parquet(csv_path, c, "data/processed/rent_2021", yymm); how.append("detail2021")
    if yymm >= DETAIL_FROM:
        rows["detail"] = to_parquet(csv_path, c, "data/processed/rent", yymm); how.append("detail")
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
    """only: 처리할 YYMM 집합(None이면 전부). load_log에 있는 파일은 건너뛴다"""
    done = done_set()
    tmp = ROOT / "data/interim/rent_csv"; tmp.mkdir(parents=True, exist_ok=True)
    raw, utf = tmp / "month_raw.csv", tmp / "month_utf8.csv"
    jobs = []                                            # (파일 이름, 원본 위치, 열 대응표)
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
                shutil.copyfileobj(s, d)                 # 디스크에는 한 달치만 풀어 둔다
            to_utf8(raw, utf); raw.unlink()
        else:
            to_utf8(src, utf)
        how, n_ok, n_bad = process(utf.as_posix(), name, c)
        utf.unlink()
        log_done(name, how, n_ok, n_bad)
        print(f"완료 {name}  {how}  행 {n_ok}  날짜 실패 {n_bad}", flush=True)


if __name__ == "__main__":
    main(only=set(sys.argv[1:]) or None)
