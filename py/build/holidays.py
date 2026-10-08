"""공휴일 표 받기. 특일정보 API(getRestDeInfo)로 한 해의 공휴일(대체·임시공휴일 포함)을 받아 연도별 파일로 둔다.

1A 마스터는 2016 ~ 2026년을 data/raw/calendar/holidays_2016_2026.csv로 받아 썼다(1A_Build_master 2번째 셀, 같은 API).
그 파일은 그대로 두고 그 뒤 해는 holidays_<연도>.csv로 따로 받는다. 주간 예측(fc_ops.py)은 holidays_*.csv를 모두 읽는다.
한국천문연구원이 다음 해 공휴일을 보통 그해 여름쯤 올리므로 연말 전에 한 번 돌린다. 임시공휴일이 새로 지정되면 다시 받는다.

출력: data/raw/calendar/holidays_<연도>.csv (date, name)
실행: .venv/bin/python py/build/holidays.py 2027
"""
import os
import sys
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from common import mask_keys   # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
URL = "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
load_dotenv(ROOT / ".env")


def get_holidays(year):
    out = []
    for m in range(1, 13):
        try:
            js = requests.get(URL, params=dict(serviceKey=os.environ["DATA_GO_KR_KEY"], solYear=year, solMonth=f"{m:02d}",
                                               _type="json", numOfRows=50), timeout=30).json()
        except Exception as e:
            raise RuntimeError(mask_keys(f"특일정보 {year}-{m:02d}: {e}")) from None
        items = js["response"]["body"]["items"]
        if not items:                               # 그 달에 공휴일이 없으면 빈 문자열이 온다
            continue
        it = items["item"]
        it = [it] if isinstance(it, dict) else it   # 1건이면 리스트가 아니라 dict로 온다
        out += [{"date": pd.to_datetime(str(x["locdate"])).date(), "name": x["dateName"]} for x in it if x.get("isHoliday", "Y") == "Y"]
    return pd.DataFrame(out, columns=["date", "name"])


if __name__ == "__main__":
    year = int(sys.argv[1])
    h = get_holidays(year)
    if h.empty:
        raise SystemExit(f"{year}년 공휴일이 아직 없다(천문연구원이 올리기 전)")
    out = ROOT / f"data/raw/calendar/holidays_{year}.csv"
    h.to_csv(out, index=False)
    print(f"{out.name}: {len(h)}일"); print(h.to_string(index=False))
