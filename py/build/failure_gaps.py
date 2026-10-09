"""고장신고 파일에서 통째로 빠진 날을 Open API로 받아 채운다.

고장신고 파일은 몇 달 단위로 나뉘어 있는데 일부 파일은 마지막 날이 통째로 빠져 있다.
2020-10-31, 2021-01-31, 2021-06-30, 2021-12-31, 2022-06-30, 2022-12-31이 0건이고 앞뒤 날은
수백 건이라, 신고가 없던 게 아니라 파일을 만들 때 잘린 것이다. Open API(tbCycleFailureReport)에는
이 날들이 있어서(예: 2021-06-30 690행) 보간하지 않고 실제 값으로 채운다.

API에 날짜를 주면 그날부터 약 30일치가 시간순으로 와서, 앞쪽 페이지만 받다가 멈춘다.
다음 날까지 받아 두고 파일 건수와 맞춰 본다.

출력: data/raw/failure_api/failure_gap_days.csv
      열: bike_no, reg_dt(등록일시), kind(고장구분), gap_day(빠졌던 날짜),
          is_gap(True면 빠졌던 날의 행, False면 검증용 다음 날 행)
실행: python py/build/failure_gaps.py  (.env에 SEOUL_API_KEY 필요)
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from seoul_api import OK_CODES, seoul_get   # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/raw/failure_api/failure_gap_days.csv"

# 2019년 이후 신고가 0건인 날 전부
GAP_DAYS = ["2020-10-31", "2021-01-31", "2021-06-30", "2021-12-31", "2022-06-30", "2022-12-31"]


def fetch_day(day):
    """day(예: "2021-06-30")와 다음 날의 고장신고 행을 API에서 받는다"""
    d0 = pd.Timestamp(day)
    d_end = d0 + pd.Timedelta(days=2)                 # 다음 날 끝까지
    rows, start = [], 1
    while True:
        r = seoul_get("tbCycleFailureReport", start, start + 999, d0.strftime("%Y%m%d"))
        if r["code"] not in OK_CODES:                    # 오류 페이지를 '더 없음'으로 읽지 않는다
            raise RuntimeError(f"tbCycleFailureReport {day} {start}행부터: {r['code']} {r['msg']}")
        page = r["rows"]
        if not page:
            break
        rows += page
        if pd.to_datetime(page[-1]["regDttm"]) >= d_end:   # 시간순이라 다음다음 날이 나오면 멈춘다
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
