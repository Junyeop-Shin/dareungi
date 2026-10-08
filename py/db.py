"""DuckDB 연결. 단계 2(대여이력 적재, 자전거 패널) 스크립트는 모두 이 함수로 같은 DB 파일을 연다.

data/db/dareungi.duckdb를 열고, 16GB 맥북에서 1억 건을 돌릴 수 있게 메모리 한도와 임시 폴더를 잡는다.

사용: from db import connect; con = connect()
      표를 받을 때는 con.sql("SELECT ...").df(), 테이블을 만들 때는 con.execute(...)
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent


def connect(path="data/db/dareungi.duckdb", memory="8GB", threads=6):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "data/interim/duckdb_tmp").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p))
    con.execute(f"SET memory_limit = '{memory}'")                                   # 넘치면 temp_directory로 내린다
    con.execute(f"SET threads = {threads}")
    con.execute(f"SET temp_directory = '{(ROOT / 'data/interim/duckdb_tmp').as_posix()}'")
    con.execute("SET preserve_insertion_order = false")                             # 큰 COPY에서 메모리 절약
    return con
