"""운영 파이프라인 공통 함수(런북 07).

py/jobs/*.py 수집·갱신 작업이 같이 쓰는 것들: 운영 DB 연결, 서울 열린데이터 API 호출(재시도·페이지 넘기기),
경보, 실행 기록, 잠금.

운영 DB(data/db/ops.duckdb)는 분석 DB(dareungi.duckdb, 19GB)와 따로 둔다. DuckDB는 한 프로세스만 쓸 수 있어서
작업마다 잠금 파일을 잡고 들어간다.
주의: 인증키는 .env에서만 읽는다(SEOUL_API_KEY, DATA_GO_KR_KEY, 선택 ALERT_WEBHOOK). 코드·로그·스냅샷에 남기지 않는다.
      requests 오류 메시지에는 키가 든 URL이 그대로 찍히므로, 밖으로 나가는 메시지는 mask_keys()를 거친다.

사용: from common import connect, fetch_all, alert, log_run, table_exists, job_lock
"""
import datetime as dt
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, quote_plus

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


def _lock_is_stale():
    """잠금 파일을 만든 프로세스가 이미 끝났으면 True. 프로세스 번호를 못 읽으면 30분 넘은 잠금만 죽은 것으로 본다."""
    try:
        pid = int(LOCK.read_text().strip())
    except (OSError, ValueError):
        return LOCK.exists() and time.time() - LOCK.stat().st_mtime > 1800
    try:
        os.kill(pid, 0)                     # 신호를 보내지 않고 살아 있는지만 본다
        return False
    except ProcessLookupError:
        return True
    except PermissionError:                 # 다른 사용자의 살아 있는 프로세스
        return False


@contextmanager
def job_lock(wait_s=2, tries=150):
    """잠금 파일로 DB 사용 순서를 맞춘다. 2초 간격으로 최대 5분 기다린다.

    파일은 open(..., "x")로 만들어서, 두 작업이 같은 순간에 시작해도 한쪽만 잡는다(있는지 보고 쓰는 두 단계로 하면 둘 다 잡을 수 있다).
    잡은 프로세스가 죽어 남은 잠금은 지운다. 살아 있는 작업의 잠금은 오래 걸려도 건드리지 않는다.
    """
    for _ in range(tries):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _lock_is_stale():
                LOCK.unlink(missing_ok=True)
                continue
            time.sleep(wait_s)
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        try:
            yield
        finally:
            LOCK.unlink(missing_ok=True)
        return
    raise RuntimeError("DB 잠금을 얻지 못했다(다른 작업이 오래 돌고 있음)")


def fetch(service, start, end, *params, retries=4):
    """한 페이지(최대 1,000건)를 받아 (전체 건수, 행 목록)을 돌려준다. 데이터 없음(INFO-200)이면 (0, [])."""
    url = f"{SEOUL}/{os.environ['SEOUL_API_KEY']}/json/{service}/{start}/{end}/" + "/".join(str(p) for p in params)
    for i in range(retries):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            if "xml" in r.headers.get("content-type", ""):          # 키 오류 등은 json을 요청해도 XML로 온다
                text = r.text.replace("<![CDATA[", "").replace("]]>", "")
                raise RuntimeError(" ".join(re.sub(r"<[^>]+>", " ", text).split()))
            js = r.json()
            body = js.get(service) or next((v for v in js.values() if isinstance(v, dict) and "RESULT" in v), None)
            if body is None:
                code = js.get("CODE") or js.get("RESULT", {}).get("CODE")   # 데이터 없음은 {"CODE": "INFO-200", ...}로 온다
                if code == "INFO-200":
                    return 0, []
                raise RuntimeError(js.get("RESULT") or js)
            return int(body.get("list_total_count", 0)), body.get("row", [])
        except Exception as e:
            if i == retries - 1:
                raise RuntimeError(mask_keys(f"{service} {params}: {e}")) from None   # 원래 예외에는 키가 든 URL이 있다
            time.sleep(2 ** i)


def fetch_all(service, *params):
    total, rows = fetch(service, 1, 1000, *params)
    for s in range(1001, total + 1, 1000):
        _, more = fetch(service, s, min(s + 999, total), *params)
        rows += more
        time.sleep(0.2)
    return total, rows


def mask_keys(text):
    """메시지에 섞인 인증키를 ***로 가린다. URL 인코딩된 형태(공공데이터포털 키의 +, /, =)도 같이 가린다."""
    text = str(text)
    for name in ("SEOUL_API_KEY", "DATA_GO_KR_KEY", "ALERT_WEBHOOK"):
        v = os.environ.get(name)
        if v:
            for form in {v, quote(v, safe=""), quote_plus(v)}:
                text = text.replace(form, "***")
    return text


def alert(msg):
    """콘솔에 찍고, ALERT_WEBHOOK(디스코드·슬랙)이 설정돼 있으면 거기에도 보낸다."""
    msg = mask_keys(msg)
    hook = os.environ.get("ALERT_WEBHOOK")
    print(f"[ALERT] {dt.datetime.now():%Y-%m-%d %H:%M} {msg}", flush=True)
    if hook:
        try:
            requests.post(hook, json={"content": msg}, timeout=10)
        except Exception:
            pass


def log_run(con, job, status, detail=""):
    con.execute("CREATE TABLE IF NOT EXISTS job_runs (job VARCHAR, ts TIMESTAMP, status VARCHAR, detail VARCHAR)")
    con.execute("INSERT INTO job_runs VALUES (?, now(), ?, ?)", [job, status, mask_keys(detail)[:2000]])


def table_exists(con, name):
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0] > 0


def upsert_rows(con, table, df, delete_where=None, params=()):
    """원자료를 문자열 그대로 넣는다(형 변환은 뷰에서 한다). delete_where가 있으면 그 범위를 먼저 지워서 다시 돌려도 결과가 같다.

    빈 df면 아무것도 지우지 않는다. API가 잠깐 빈 응답을 줘도 이미 받은 행이 남게 하려는 것이다.
    지우기와 넣기는 한 트랜잭션이라, 넣다가 실패하면(API 필드 변경 등) 지운 것도 되돌린다.
    """
    if df is None or df.empty:
        return 0
    con.register("df_", df)
    con.begin()
    try:
        if table_exists(con, table) and delete_where:
            con.execute(f"DELETE FROM {table} WHERE {delete_where}", list(params))
        if not table_exists(con, table):
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM df_ LIMIT 0")
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM df_")  # API에 새 필드가 생기면 여기서 오류가 나서 스키마 변화를 알 수 있다
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.unregister("df_")
    return len(df)
