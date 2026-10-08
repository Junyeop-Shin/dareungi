"""자전거별·월별 대여 건수를 센다.

신고율이 내려간 것이 새 자전거가 많이 들어와서인지(구성 효과), 같은 자전거의 신고율이 바뀌어서인지
가르려면 도입 시기 묶음별로 신고율을 봐야 하고, 그 분모로 자전거별 대여 건수가 필요하다.
daily_master에는 자전거 구분이 없어서 대여이력에서 자전거번호별로 다시 센다.

실제 도입일 자료는 없어서 대여이력에 번호가 처음 나타난 달을 도입 시기로 본다.
주의: 잠금장치를 바꾸며 번호만 새로 붙인 기존 자전거도 새 자전거처럼 보이고, 이 자료로는 구분할 수 없다.

입력: data/raw/rent_history/  (py/rent_history_daily.py와 같은 원본)
출력: data/interim/bike_month/bike_month.parquet
      열: bike(자전거번호의 숫자 부분), ym(YYMM), n(그 달 대여 건수)
실행: python py/build/bike_month.py  (전체 약 52GB, 2~3분)
"""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from rent_history_daily import ALL_MONTHS, _open_month

ROOT = Path(__file__).resolve().parents[2]


def one_month(yymm):
    """한 달 치 대여이력에서 자전거번호별 대여 건수를 센다"""
    parts = []
    with _open_month(yymm) as f:
        # 0번 열(자전거번호, 예: SPB-22041)만 읽는다
        for x in pd.read_csv(f, encoding="cp949", encoding_errors="replace", dtype=str, header=0,
                             usecols=[0], names=["bike_no"], on_bad_lines="skip", chunksize=2_000_000):
            parts.append(x.bike_no.str.extract(r"(\d+)", expand=False).value_counts())    # 고장신고 쪽과 맞추려고 숫자 부분만
    s = pd.concat(parts).groupby(level=0).sum()
    return pd.DataFrame({"bike": s.index.astype("int64"), "ym": yymm, "n": s.values})


if __name__ == "__main__":
    with ProcessPoolExecutor(4) as ex:
        out = pd.concat(list(ex.map(one_month, ALL_MONTHS)), ignore_index=True)
    dst = ROOT / "data/interim/bike_month"
    dst.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dst / "bike_month.parquet", index=False)
    print(out.shape, "자전거 수:", out.bike.nunique())
