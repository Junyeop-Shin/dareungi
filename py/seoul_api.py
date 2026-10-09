"""서울 열린데이터광장 Open API 호출 도우미(01 문서 0-1).

인증키는 프로젝트 폴더의 .env에 있는 SEOUL_API_KEY를 쓴다.

주의: 요청이 실패하면 오류 메시지의 키를 가린 뒤(common.mask_keys) 예외를 낸다. requests 오류에는 키가 든 URL이 찍혀서
      노트북 출력에 남을 수 있다. seoul_all은 받은 행 수가 list_total_count와 다르면 멈춘다(중간 페이지 하나가 실패해도
      짧은 표를 그대로 돌려주지 않게).

사용: import sys; sys.path.append("../py"); from seoul_api import seoul_get, seoul_all
"""
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

# 프로젝트 폴더(py/의 상위)의 .env
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.append(str(Path(__file__).resolve().parent))
from common import mask_keys   # noqa: E402

SEOUL = "http://openapi.seoul.go.kr:8088"
OK_CODES = ("INFO-000", "INFO-200")      # 정상, 데이터 없음


def _body(js, service):
    """응답에서 데이터 본문을 찾는다.

    서비스 이름과 응답 키가 다른 API가 있어서(bikeList → rentBikeStatus) RESULT가 든 dict를 찾는다.
    """
    if service in js:
        return js[service]
    return next((v for v in js.values() if isinstance(v, dict) and "RESULT" in v), None)


def seoul_get(service, start=1, end=1000, *params):
    """한 페이지(최대 1,000건) 호출. 반환: total, code, msg, rows"""
    url = f"{SEOUL}/{os.environ['SEOUL_API_KEY']}/json/{service}/{start}/{end}/" + "/".join(params)
    try:
        r = requests.get(url, timeout=30)
        if "xml" in r.headers.get("content-type", ""):     # 키 오류 등은 json을 요청해도 XML로 온다
            text = r.text.replace("<![CDATA[", "").replace("]]>", "")
            raise RuntimeError(" ".join(re.sub(r"<[^>]+>", " ", text).split()))
        js = r.json()
    except Exception as e:
        raise RuntimeError(mask_keys(f"{service} {params}: {e}")) from None
    body = _body(js, service)
    if body is None:                            # 데이터 없음·오류면 본문 없이 RESULT만 온다
        r = js.get("RESULT", js)
        return {"total": 0, "code": r.get("CODE"), "msg": r.get("MESSAGE"), "rows": []}
    res = body.get("RESULT", {})
    return {"total": int(body.get("list_total_count", 0)), "code": res.get("CODE"),
            "msg": res.get("MESSAGE"), "rows": body.get("row", [])}


def seoul_all(service, *params):
    """전체 페이지를 모아 DataFrame으로 돌려준다.

    주의: tbCycleFailureReport에 날짜를 넘기면 그날부터 약 30일치가 온다.
    """
    first = seoul_get(service, 1, 1000, *params)
    if first["code"] not in OK_CODES:
        raise RuntimeError(f"{service} {params}: {first['code']} {first['msg']}")
    rows = list(first["rows"])
    for s in range(1001, first["total"] + 1, 1000):
        page = seoul_get(service, s, min(s + 999, first["total"]), *params)
        if page["code"] not in OK_CODES:
            raise RuntimeError(f"{service} {params} {s}행부터: {page['code']} {page['msg']}")
        rows += page["rows"]
        time.sleep(0.2)
    if len(rows) != first["total"]:
        raise RuntimeError(f"{service} {params}: {first['total']:,}행이어야 하는데 {len(rows):,}행만 받았다")
    return pd.DataFrame(rows)
