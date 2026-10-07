"""DuckDB 연결 — 단계 2(대여이력 적재·자전거 패널)의 모든 스크립트가 이 함수로 같은 DB 파일을 연다

[무엇을] data/db/dareungi.duckdb를 열고 맥북(16GB 기준)에서 1억 건을 다룰 수 있게 메모리·임시 폴더를 설정한다.
[실행]   from db import connect; con = connect()
         노트북에서는 con.sql("SELECT ...").df()로 표를 받고, 테이블을 만드는 SQL은 con.execute(...)로.
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent


def connect(path="data/db/dareungi.duckdb", memory="8GB", threads=6):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "data/interim/duckdb_tmp").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p))
    con.execute(f"SET memory_limit = '{memory}'")                                   # 넘치는 부분은 디스크로
    con.execute(f"SET threads = {threads}")
    con.execute(f"SET temp_directory = '{(ROOT / 'data/interim/duckdb_tmp').as_posix()}'")
    con.execute("SET preserve_insertion_order = false")                             # 큰 COPY에서 메모리 절약
    return con
