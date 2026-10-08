"""운영 파이프라인 공통 모듈 — 단계 3A (런북 07)

[무엇을] 수집·갱신 작업(py/jobs/*.py)이 함께 쓰는 것: 운영 DB 연결, 서울 열린데이터 API 호출(재시도·페이지), 경보, 실행 기록, 잠금.
[원칙]  인증키는 .env에서만(SEOUL_API_KEY, DATA_GO_KR_KEY, 선택 ALERT_WEBHOOK). 코드·로그·스냅샷에 키를 쓰지 않는다.
        운영 DB는 분석 DB(dareungi.duckdb, 19GB)와 따로 둔다(data/db/ops.duckdb). DuckDB는 한 프로세스만 쓰므로 작업마다 잠금 파일을 쓴다.
[사용]  from common import connect, fetch_all, alert, log_run, table_exists, job_lock
"""
import datetime as dt
import os
import time
from contextlib import contextmanager
from pathlib import Path

import duckdb
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
SEOUL = "http://openapi.seoul.go.kr:8088"
LOCK = ROOT / "data/db/.ops.lock"


def connect(path="data/db/ops.duckdb"):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p))
    con.execute("SET memory_limit = '4GB'")
    return con


@contextmanager
def job_lock(wait_s=2, tries=150):
    """다른 작업이 DB를 쓰는 동안 기다린다(잠금 파일, 2초마다 최대 5분). 오래된 잠금(30분+)은 죽은 작업으로 보고 지운다"""
    for _ in range(tries):
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime > 1800:
            LOCK.unlink()
        if not LOCK.exists():
            LOCK.write_text(str(os.getpid()))
            try:
                yield
            finally:
                LOCK.unlink(missing_ok=True)
            return
        time.sleep(wait_s)
    raise RuntimeError("DB 잠금을 얻지 못했다(다른 작업이 오래 돌고 있음)")


def fetch(service, start, end, *params, retries=4):
    """한 페이지(최대 1,000건). 돌려주는 것: (전체 건수, 행 목록). 데이터 없음(INFO-200)이면 (0, [])"""
    url = f"{SEOUL}/{os.environ['SEOUL_API_KEY']}/json/{service}/{start}/{end}/" + "/".join(str(p) for p in params)
    for i in range(retries):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            js = r.json()
            body = js.get(service) or next((v for v in js.values() if isinstance(v, dict) and "RESULT" in v), None)
            if body is None:
                code = js.get("RESULT", {}).get("CODE")
                if code == "INFO-200":
                    return 0, []
                raise RuntimeError(f"{service} {params}: {js.get('RESULT')}")
            return int(body.get("list_total_count", 0)), body.get("row", [])
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(2 ** i)


def fetch_all(service, *params):
    total, rows = fetch(service, 1, 1000, *params)
    for s in range(1001, total + 1, 1000):
        _, more = fetch(service, s, min(s + 999, total), *params)
        rows += more
        time.sleep(0.2)
    return total, rows


def alert(msg):
    """경보: 콘솔에 찍고, ALERT_WEBHOOK(디스코드·슬랙)이 있으면 보낸다"""
    hook = os.environ.get("ALERT_WEBHOOK")
    print(f"[ALERT] {dt.datetime.now():%Y-%m-%d %H:%M} {msg}", flush=True)
    if hook:
        try:
            requests.post(hook, json={"content": msg}, timeout=10)
        except Exception:
            pass


def log_run(con, job, status, detail=""):
    con.execute("CREATE TABLE IF NOT EXISTS job_runs (job VARCHAR, ts TIMESTAMP, status VARCHAR, detail VARCHAR)")
    con.execute("INSERT INTO job_runs VALUES (?, now(), ?, ?)", [job, status, detail[:2000]])


def table_exists(con, name):
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0] > 0


def upsert_rows(con, table, df, delete_where=None, params=()):
    """원자료를 문자열 그대로 넣는다(변환은 뷰에서). delete_where가 있으면 그 범위를 지우고 다시 넣는다(멱등)"""
    if table_exists(con, table) and delete_where:
        con.execute(f"DELETE FROM {table} WHERE {delete_where}", list(params))
    if df is None or df.empty:
        return 0
    con.register("df_", df)
    if not table_exists(con, table):
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM df_ LIMIT 0")
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM df_")      # 새 필드가 생기면 여기서 오류 → 스키마 변화 감지
    con.unregister("df_")
    return len(df)
