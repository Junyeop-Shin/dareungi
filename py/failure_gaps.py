"""고장신고 파일에서 통째로 빠진 날을 Open API로 받아 채운다

[배경]
서울 열린데이터광장의 고장신고 파일은 반기(또는 몇 달) 단위로 나뉘어 있는데,
일부 파일은 '마지막 날'이 통째로 빠져 있다. 일별로 세어 보면 그 날만 신고가 0건이다(2026-10-05 확인):
    2020-10-31, 2021-01-31, 2021-06-30, 2021-12-31, 2022-06-30, 2022-12-31
앞뒤 날은 수백 건이므로 실제로 신고가 없었던 것이 아니라 파일을 만들 때 잘린 것이다.
같은 데이터를 주는 Open API(tbCycleFailureReport)에는 이 날들이 들어 있어(예: 2021-06-30 = 690행),
추정값으로 메우지(보간하지) 않고 실제 값을 받아서 채운다.

[API 특징] 날짜를 하나 주면 '그날부터 약 30일치'를 시간순으로 돌려준다(단계 0에서 확인).
           그래서 앞쪽 페이지만 받다가 그날이 끝나면 멈춘다.
           검증용으로 '다음 날'까지 받아 둔다(다음 날은 파일에도 있으므로 건수를 서로 비교할 수 있다).

[출력]  data/raw/failure_api/failure_gap_days.csv
        열: bike_no(자전거번호), reg_dt(등록일시), kind(고장구분), gap_day(빠졌던 날짜),
            is_gap(True = 빠졌던 그날의 행, False = 검증용 다음 날 행)

[실행]  터미널(프로젝트 폴더):  python py/failure_gaps.py     (인증키 SEOUL_API_KEY가 .env에 있어야 한다)
"""
from pathlib import Path

import pandas as pd

from seoul_api import seoul_get

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/raw/failure_api/failure_gap_days.csv"

# 파일에서 통째로 빠진 날(2019년 이후 일별 신고가 0건인 날 전부)
GAP_DAYS = ["2020-10-31", "2021-01-31", "2021-06-30", "2021-12-31", "2022-06-30", "2022-12-31"]


def fetch_day(day):
    """day(예: "2021-06-30")와 그 다음 날의 고장신고 행을 API에서 받아 표로 돌려준다"""
    d0 = pd.Timestamp(day)
    d_end = d0 + pd.Timedelta(days=2)                 # 다음 날 자정까지(= 다음다음 날 0시 전까지) 받는다
    rows, start = [], 1
    while True:
        page = seoul_get("tbCycleFailureReport", start, start + 999, d0.strftime("%Y%m%d"))["rows"]
        if not page:
            break
        rows += page
        if pd.to_datetime(page[-1]["regDttm"]) >= d_end:   # 시간순이므로 다음다음 날이 나오면 그만 받는다
            break
        start += 1000
    x = pd.DataFrame(rows).rename(columns={"bikeNo": "bike_no", "regDttm": "reg_dt", "mlangComCdName": "kind"})
    t = pd.to_datetime(x.reg_dt)
    x = x[(t >= d0) & (t < d_end)][["bike_no", "reg_dt", "kind"]]
    return x.assign(gap_day=day, is_gap=pd.to_datetime(x.reg_dt).dt.normalize() == d0)


if __name__ == "__main__":
    out = pd.concat([fetch_day(d) for d in GAP_DAYS], ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(out.groupby(["gap_day", "is_gap"]).size().unstack().rename(columns={True: "빠졌던 날 행 수", False: "다음 날 행 수(검증용)"}))
