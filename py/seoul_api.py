"""서울 열린데이터광장 Open API 호출 도우미 (01 문서 0-1)

노트북에서:  import sys; sys.path.append("../py");  from seoul_api import seoul_get, seoul_all
인증키는 프로젝트 폴더의 .env (SEOUL_API_KEY=...)에서 읽는다. 코드에 키를 쓰지 않는다.
"""
import os
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

# 이 파일(py/seoul_api.py) 기준 한 단계 위 = 프로젝트 폴더의 .env
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
SEOUL = "http://openapi.seoul.go.kr:8088"


def _body(js, service):
    """응답에서 데이터 본문을 찾는다.
    서비스 이름과 응답 키가 다른 API가 있다(예: bikeList → rentBikeStatus)"""
    if service in js:
        return js[service]
    return next((v for v in js.values() if isinstance(v, dict) and "RESULT" in v), None)


def seoul_get(service, start=1, end=1000, *params):
    """한 페이지(최대 1,000건) 호출. 반환: total, code, msg, rows"""
    url = f"{SEOUL}/{os.environ['SEOUL_API_KEY']}/json/{service}/{start}/{end}/" + "/".join(params)
    js = requests.get(url, timeout=30).json()
    body = _body(js, service)
    if body is None:                            # 데이터 없음·오류면 본문 없이 RESULT만 온다
        r = js.get("RESULT", js)
        return {"total": 0, "code": r.get("CODE"), "msg": r.get("MESSAGE"), "rows": [], "raw": js}
    res = body.get("RESULT", {})
    return {"total": int(body.get("list_total_count", 0)), "code": res.get("CODE"),
            "msg": res.get("MESSAGE"), "rows": body.get("row", [])}


def seoul_all(service, *params):
    """전체 페이지를 모아 DataFrame으로.
    주의: tbCycleFailureReport의 날짜 파라미터는 '그날부터 약 30일치'를 돌려준다(단계 0 확인)"""
    first = seoul_get(service, 1, 1000, *params)
    rows = list(first["rows"])
    for s in range(1001, first["total"] + 1, 1000):
        rows += seoul_get(service, s, min(s + 999, first["total"]), *params)["rows"]
        time.sleep(0.2)
    return pd.DataFrame(rows)
