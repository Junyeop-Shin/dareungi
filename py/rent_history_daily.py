"""대여이력(건별 원본)을 날짜별 합계로 줄인다 — 이용정보(일별)의 구멍을 메우고 거리 기록 품질을 재는 용도

[배경]
노출(고장신고율의 분모) 후보인 이동거리·이용시간은 '이용정보(일별)' 파일에서 가져온다(py/use_daily.py).
그런데 그 파일에는 구멍이 있다:
  - 2016년 파일이 없다(2017년부터 제공), 2022년 5월 파일이 없다
  - 2021-04, 2021-05, 2021-06, 2025-02 파일은 엑셀 최대 줄 수(1,048,576)에서 잘려 뒤쪽 날짜가 모자라다
  - 2021-01 ~ 05 파일은 이용건수가 일별 대여건수보다 2~9% 적다
  - 하루씩 빠진 날이 있다(2018-04-11, 2018-05-10, 2019-02-22)
'대여이력'(OA-15182)은 대여 한 건이 한 줄인 원본이라, 날짜별로 더하면 같은 숫자를 직접 만들 수 있다.
두 출처가 모두 멀쩡한 날에는 거리·시간 합계가 소수 넷째 자리까지 같다(2026-10-05 확인).

[이 스크립트가 추가로 알려 주는 것 — 거리 기록 품질]
대여 한 건마다 이용거리가 0(또는 빈칸)인지 센다(dist_zero). 평소에는 2~8%인데
2020년 3월부터 늘어 2020년 8~12월에는 약 90%가 0이고, 2021-01-05에 갑자기 정상으로 돌아온다.
즉 그 구간의 '거리 합계'는 원본 자체가 믿을 수 없고, 어떤 출처로도 복구되지 않는다.

[입력]  data/raw/rent_history/서울특별시 공공자전거 대여이력 정보_<연도>.zip   (2015~2025, 안에 월별 CSV, cp949)
        data/raw/rent_history/서울특별시 공공자전거 대여이력 정보_26MM.csv     (2026년은 월별 CSV)
[출력]  data/interim/rent_hist_daily/rent_hist_daily.parquet
        열: date(대여일), n_use(대여 건수), dist_m(이용거리 합, 미터), min_use(이용시간 합, 분),
            dist_zero(이용거리가 0이거나 비어 있는 건수), ym(어느 달 파일에서 왔는지, YYMM)

[실행]  터미널(프로젝트 폴더):  python py/rent_history_daily.py      (전체 약 52GB, 몇 분)
"""
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/raw/rent_history"

# 처리할 달(YYMM): 2015-09(첫 파일) ~ 2026-06
ALL_MONTHS = [p.strftime("%y%m") for p in pd.period_range("2015-09", "2026-06", freq="M")]


def _zip_name(info):
    """ZIP 안의 한글 파일 이름을 바르게 읽는다(옛 ZIP은 이름이 cp949인데 파이썬은 cp437로 읽는다)"""
    if info.flag_bits & 0x800:                    # 이름이 UTF-8로 저장된 ZIP이면 그대로
        return info.filename
    return info.filename.encode("cp437").decode("cp949", "replace")


def _open_month(yymm):
    """그 달의 CSV를 읽을 수 있게 연다. 2025년까지는 연도 ZIP 안에서 바로, 2026년은 낱개 CSV에서"""
    if yymm >= "2601":
        return open(RAW / f"서울특별시 공공자전거 대여이력 정보_{yymm}.csv", "rb")
    z = zipfile.ZipFile(RAW / f"서울특별시 공공자전거 대여이력 정보_20{yymm[:2]}.zip")
    info = next(i for i in z.infolist() if f"_{yymm}." in _zip_name(i))
    return z.open(info)


def month_daily(yymm):
    """한 달 치 대여이력(예: "2205")을 읽어 날짜별 합계로 돌려준다(ZIP을 디스크에 풀지 않는다)"""
    agg = []
    with _open_month(yymm) as f:
        # 17열(2026년은 16열) 중 3개만 읽는다: 1번째 대여일시, 9번째 이용시간(분), 10번째 이용거리(M)
        # 글자가 깨진 줄·칸 수가 안 맞는 줄은 건너뛴다(대여소 이름 문제이고 수는 매우 적다)
        for x in pd.read_csv(f, encoding="cp949", encoding_errors="replace", dtype=str, header=0,
                             usecols=[1, 9, 10], names=["rent_dt", "min_use", "dist_m"],
                             on_bad_lines="skip", chunksize=2_000_000):
            x["date"] = pd.to_datetime(x.rent_dt, errors="coerce").dt.normalize()   # 대여일(시각은 버림)
            x["min_use"] = pd.to_numeric(x.min_use, errors="coerce")
            x["dist_m"] = pd.to_numeric(x.dist_m, errors="coerce")
            x["dist_zero"] = x.dist_m.fillna(0).eq(0)
            agg.append(x.dropna(subset=["date"]).groupby("date")
                        .agg(n_use=("rent_dt", "size"), dist_m=("dist_m", "sum"),
                             min_use=("min_use", "sum"), dist_zero=("dist_zero", "sum")))
    return pd.concat(agg).groupby(level=0).sum().reset_index().assign(ym=yymm)


def build(months=ALL_MONTHS, workers=4):
    """여러 달을 동시에 처리해 한 표로 합치고 저장한다"""
    with ProcessPoolExecutor(workers) as ex:
        out = pd.concat(list(ex.map(month_daily, months)), ignore_index=True)
    # 월 파일에 다른 달 날짜가 조금 섞여 있을 수 있으므로, 그 파일이 맡은 달의 날짜만 남긴다
    out = out[out.date.dt.strftime("%y%m") == out.ym].sort_values("date").reset_index(drop=True)
    dst = ROOT / "data/interim/rent_hist_daily"
    dst.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dst / "rent_hist_daily.parquet", index=False)
    return out


if __name__ == "__main__":
    # 터미널에서 직접 실행했을 때: 전체를 만들고 달별 요약을 보여 준다
    d = build()
    s = d.groupby("ym").agg(days=("date", "size"), n_use=("n_use", "sum"), dist_km=("dist_m", lambda v: v.sum() / 1000),
                            hours=("min_use", lambda v: v.sum() / 60), dist_zero=("dist_zero", "sum"))
    s["dist_zero_share"] = (s.dist_zero / s.n_use).round(3)
    pd.set_option("display.max_rows", 200)
    print(s.round(3).to_string())
