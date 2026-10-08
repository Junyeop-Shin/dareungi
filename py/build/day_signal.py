"""대여이력에서 날짜별 헛대여 수를 센다. 레짐 원인 사후 분석(04b R3·R4a)의 고장 신호.

2021-04 급증과 2025-03 감소가 실제 고장의 변화인지 신고의 변화인지 가르려면 신고와 무관한 고장 신호가
필요하다. 헛대여(빌렸다가 몇 분 안에 같은 대여소에 반납)에는 고장을 발견하고 바로 돌려놓는 경우가 많아
대리 지표로 쓴다. 마음이 바뀐 대여도 섞여 있어서 수준이 아니라 전후 변화만 해석한다.

입력: data/raw/rent_history/  (2015-09 ~ 2026-06, py/rent_history_daily.py와 같은 원본)
      대여일시, 대여 대여소번호, 반납대여소번호, 이용시간(분), 이용거리(M) 열만 읽는다
출력: data/processed/day_signal.parquet (날짜별)
      n_trips   대여 건수
      sham01    같은 대여소 반납, 이용시간 ≤ 1분
      sham2/3/5 같은 대여소 반납, 이용시간 ≤ 2/3/5분
      sham3d    같은 대여소 반납, ≤ 3분, 이용거리 ≤ 300m. 2020-03 ~ 2021-01은 거리 기록이 깨져 못 쓴다
      dist0     이용거리 0 또는 빈칸
      same_any  같은 대여소 반납(시간 조건 없음)
주의: 0~1분 대여 기록은 2026년 파일에만 있다(그 전에는 2분 미만 대여가 아예 없다). 연도 비교에는
sham3 − sham01(2~3분), sham5 − sham01(2~5분)을 쓴다.
실행: .venv/bin/python py/build/day_signal.py  (저장소 루트, 52GB를 한 번 훑어 몇 분)
"""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from rent_history_daily import ALL_MONTHS, _open_month

ROOT = Path(__file__).resolve().parents[2]


def month_signal(yymm):
    """한 달 치를 읽어 날짜별 헛대여 수를 센다"""
    agg = []
    with _open_month(yymm) as f:
        # 1 대여일시, 2 대여 대여소번호, 6 반납대여소번호, 9 이용시간(분), 10 이용거리(M). 2026년 16열도 같은 위치
        for x in pd.read_csv(f, encoding="cp949", encoding_errors="replace", dtype=str, header=0,
                             usecols=[1, 2, 6, 9, 10], names=["rent_dt", "rent_st", "rtn_st", "min_use", "dist_m"],
                             on_bad_lines="skip", chunksize=2_000_000):
            x["date"] = pd.to_datetime(x.rent_dt, errors="coerce").dt.normalize()
            same = pd.to_numeric(x.rent_st, errors="coerce") == pd.to_numeric(x.rtn_st, errors="coerce")
            mn = pd.to_numeric(x.min_use, errors="coerce")
            dm = pd.to_numeric(x.dist_m, errors="coerce")
            x = x.assign(same_any=same, sham01=same & mn.le(1), sham2=same & mn.le(2), sham3=same & mn.le(3), sham5=same & mn.le(5),
                         sham3d=same & mn.le(3) & dm.le(300), dist0=dm.fillna(0).eq(0))
            agg.append(x.dropna(subset=["date"]).groupby("date")
                        .agg(n_trips=("rent_dt", "size"), same_any=("same_any", "sum"), sham01=("sham01", "sum"), sham2=("sham2", "sum"),
                             sham3=("sham3", "sum"), sham5=("sham5", "sum"), sham3d=("sham3d", "sum"), dist0=("dist0", "sum")))
    return pd.concat(agg).groupby(level=0).sum().reset_index().assign(ym=yymm)


def build(months=ALL_MONTHS, workers=4):
    with ProcessPoolExecutor(workers) as ex:
        out = pd.concat(list(ex.map(month_signal, months)), ignore_index=True)
    out = out[out.date.dt.strftime("%y%m") == out.ym].drop(columns="ym")     # 월 파일에 섞인 다른 달 날짜는 뺀다
    out = out.groupby("date").sum().reset_index()
    out.to_parquet(ROOT / "data/processed/day_signal.parquet", index=False)
    return out


if __name__ == "__main__":
    d = build()
    y = d.groupby(d.date.dt.year).sum(numeric_only=True)
    print((y[["sham01", "sham2", "sham3", "sham5", "sham3d", "same_any"]].div(y.n_trips, axis=0) * 1000).round(2).assign(n_trips=y.n_trips).to_string())
