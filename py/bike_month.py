"""자전거별·월별 대여 건수를 만든다 — '신고율 변화가 자전거 교체 때문인가'를 확인하는 용도

[왜 필요한가]
전체 신고율(신고 ÷ 대여)이 내려갔을 때 원인은 두 가지일 수 있다.
  (1) 구성 효과: 고장이 덜 나는(또는 신고가 덜 잡히는) 새 자전거가 대량으로 들어와 평균이 내려갔다
  (2) 같은 자전거의 신고율 자체가 달라졌다(신고 방식 변화, 정비 강화 등)
둘을 가르려면 '같은 시기에 들어온 자전거 묶음(도입 연도 묶음)'별로 신고율을 따로 봐야 하고,
그러려면 분모인 대여 건수를 자전거별로 알아야 한다. 일별 합계표(daily_master)에는 자전거 구분이 없으므로
대여이력(건별 원본)에서 자전거번호별로 다시 센다.

[도입 시기를 아는 방법]
자전거의 실제 도입일 자료는 없다. 대신 '대여이력에 그 자전거번호가 처음 나타난 달'을 도입 시기로 본다.
주의: 기존 자전거의 잠금장치를 바꾸면서 번호를 새로 붙였다면(실물은 그대로) 새 자전거처럼 보인다. 이 자료로는 구분할 수 없다.

[입력]  data/raw/rent_history/ (py/rent_history_daily.py와 같은 원본)
[출력]  data/interim/bike_month/bike_month.parquet
        열: bike(자전거번호의 숫자 부분), ym(YYMM), n(그 달 대여 건수)

[실행]  터미널(프로젝트 폴더):  python py/bike_month.py      (전체 약 52GB, 2~3분)
"""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from rent_history_daily import ALL_MONTHS, _open_month

ROOT = Path(__file__).resolve().parent.parent


def one_month(yymm):
    """한 달 치 대여이력에서 자전거번호별 대여 건수를 센다"""
    parts = []
    with _open_month(yymm) as f:
        # 0번째 열(자전거번호, 예: SPB-22041)만 읽는다
        for x in pd.read_csv(f, encoding="cp949", encoding_errors="replace", dtype=str, header=0,
                             usecols=[0], names=["bike_no"], on_bad_lines="skip", chunksize=2_000_000):
            parts.append(x.bike_no.str.extract(r"(\d+)", expand=False).value_counts())    # 숫자 부분만(고장신고 쪽과 같은 방식)
    s = pd.concat(parts).groupby(level=0).sum()
    return pd.DataFrame({"bike": s.index.astype("int64"), "ym": yymm, "n": s.values})


if __name__ == "__main__":
    with ProcessPoolExecutor(4) as ex:
        out = pd.concat(list(ex.map(one_month, ALL_MONTHS)), ignore_index=True)
    dst = ROOT / "data/interim/bike_month"
    dst.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dst / "bike_month.parquet", index=False)
    print(out.shape, "자전거 수:", out.bike.nunique())
