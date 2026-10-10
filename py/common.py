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
import fcntl
import os
import re
import sys
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
    # DuckDB가 쓸 메모리 상한. 넘치는 몫은 디스크 임시 파일로 내린다. 서버 메모리보다 크게 두면 운영체제가 작업을 죽인다
    # (4GB 서버에서 상한 4GB로 daily가 죽었다, 2026-10-10). 작은 서버는 .env에 OPS_DUCKDB_MEMORY=2GB처럼 적는다.
    con.execute(f"SET memory_limit = '{os.environ.get('OPS_DUCKDB_MEMORY', '4GB')}'")
    return con


@contextmanager
def job_lock(wait_s=2, tries=150):
    """잠금 파일에 flock을 걸어 DB 사용 순서를 맞춘다. 2초 간격으로 최대 5분 기다린다.

    잠금은 운영체제가 들고 있어서 잡은 프로세스가 죽으면 저절로 풀린다(남은 잠금을 지우는 처리가 필요 없다).
    파일 자체는 지우지 않는다. 지우면 기다리던 작업과 새 작업이 서로 다른 파일을 잡을 수 있다.
    """
    with open(LOCK, "w") as fh:
        for _ in range(tries):
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(wait_s)
        else:
            alert("DB 잠금을 5분 동안 얻지 못해 작업을 건너뛴다(다른 작업이 오래 돌고 있음)")          # 작업의 try 밖에서 나는 예외라 여기서 알린다
            raise RuntimeError("DB 잠금을 얻지 못했다(다른 작업이 오래 돌고 있음)")
        yield


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
    """콘솔에 찍고, ALERT_WEBHOOK(디스코드·슬랙)이 설정돼 있으면 거기에도 보낸다. 반환: 밖으로 나갔으면 True.

    웹훅이 없으면 로그에만 남는다(맥북 cron이면 아무도 못 본다). 보내다 실패하면 그 사실을 로그에 적는다(전에는 조용히 넘어갔다).
    본문 이름이 서비스마다 다르다: 슬랙(hooks.slack.com)은 text, 디스코드는 content. 연결 확인: .venv/bin/python py/common.py alert-test"""
    msg = mask_keys(msg)
    hook = os.environ.get("ALERT_WEBHOOK")
    print(f"[ALERT] {dt.datetime.now():%Y-%m-%d %H:%M} {msg}", flush=True)
    if not hook:
        return False
    try:
        r = requests.post(hook, json={"text": msg} if "hooks.slack.com" in hook else {"content": msg}, timeout=10)
        if r.status_code >= 300:
            print(f"[ALERT] 웹훅 전송 실패: HTTP {r.status_code}", flush=True)
        return r.status_code < 300
    except Exception as e:
        print(f"[ALERT] 웹훅 전송 실패: {type(e).__name__}", flush=True)
        return False


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


if __name__ == "__main__":
    # 경보가 실제로 사람에게 닿는지 확인한다: .venv/bin/python py/common.py alert-test
    # 주소(인증 정보)는 찍지 않고 설정 여부와 전송 결과만 알려 준다
    if sys.argv[1:] == ["alert-test"]:
        if not os.environ.get("ALERT_WEBHOOK"):
            print("ALERT_WEBHOOK이 .env에 없습니다. 지금은 경보가 로그 파일에만 남습니다.")
        else:
            print("전송 성공: 채널에 시험 알림이 왔는지 확인하세요." if alert("시험 알림입니다(py/common.py alert-test). 이 글이 보이면 연결된 것입니다.")
                  else "전송 실패: 웹훅 주소를 확인하세요.")
