"""이용정보(일별) 파일 82개를 날짜별 합계로 줄인다.

'공공자전거 이용정보(일별)'(OA-15246)은 한 행이 날짜 × 대여소 × 이용권 × 성별 × 연령대별
이용건수·이동거리·이용시간이다(월 100만~200만 행). 여기서는 서울 전체의 하루 이용건수·거리·시간만
필요해서 날짜별로 더한다. 결과는 신고율 분모(노출) 후보로 쓴다(런북 02 '노출 3종').

헤더는 파일마다 다르다. 헤더가 없거나 글자가 깨졌거나 작은따옴표로 감싼 파일이 있고, 같은 열을
'이동거리(M)'/'이용거리(M)', '이동시간(분)'/'이용시간(분)'처럼 다르게 부른다. 열 순서(11열)는
모두 같아서 위치로 읽는다: 0 대여일자, 6 이용건수, 9 이동거리(M), 10 이용시간(분).

입력: data/interim/use_daily_utf8/  (원본의 UTF-8 사본)
출력: data/interim/use_daily_agg/use_daily_by_file.parquet  파일×날짜별 합계(n_use, dist_m, min_use)
      data/interim/use_daily_agg/use_daily_files.csv        파일별 점검표(행 수, 기간, 읽기 실패 행 수)
실행: python py/build/use_daily.py  (약 1분), 노트북에서는 sys.path.append("../py/build") 뒤 from use_daily import build_use_daily
"""
from concurrent.futures import ProcessPoolExecutor
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from prep import data_files   # noqa: E402

# 열 위치 → 이름. n_use 이용건수, dist_m 이동거리(m), min_use 이용시간(분)
COLS = {0: "date_raw", 6: "n_use", 9: "dist_m", 10: "min_use"}


def read_use_file(path):
    """파일 하나를 읽어 날짜별로 더한다.

    반환: (day, info)
      day  날짜별 합계(date, n_use, dist_m, min_use, src=파일 이름)
      info 점검용 요약(행 수, 첫 날·끝 날, 날짜로 읽히지 않은 줄 수 등)
    """
    path = Path(path)
    with open(path, encoding="utf-8") as h:
        first = h.readline().lstrip("\ufeff")          # BOM 제거
    quote = "'" if first.startswith("'") else '"'          # 2017·2018 파일은 값을 작은따옴표로 감쌌다
    agg, n_rows, n_bad_date, n_bad_num, n_cols = [], 0, 0, 0, None
    # 작은따옴표 파일은 대여소 이름에 쉼표가 있으면 12칸으로 갈라진다
    # (예: "' 고속터미널역 8-1번, 8-2번 출구 사이'"). 줄을 통째로 읽어 앞 1칸·뒤 5칸만 쓴다
    kw = dict(sep="\x01", quoting=3) if quote == "'" else dict(quotechar=quote)
    # 최대 500MB라 200만 행씩 끊어 읽으며 조각마다 날짜별로 더한다. "\\N"은 원본의 결측 표시
    for x in pd.read_csv(path, header=None, dtype=str, encoding="utf-8",
                         na_values=["\\N"], keep_default_na=False, chunksize=2_000_000, **kw):
        if quote == "'":
            tail = x[0].str.rsplit(",", n=5, expand=True)          # … | 이용건수 | 운동량 | 탄소량 | 거리 | 시간
            x = pd.DataFrame({0: x[0].str[1:11], 6: tail[1], 9: tail[4], 10: tail[5]})
            n_cols = 11
        else:
            n_cols = x.shape[1]
        x = x[list(COLS)].rename(columns=COLS)
        date = pd.to_datetime(x.date_raw.str.strip().str[:10], format="%Y-%m-%d", errors="coerce")
        x = x[date.notna()].assign(date=date[date.notna()])     # 헤더 줄도 여기서 빠진다
        n_bad_date += int(date.isna().sum())               # 헤더 줄(있으면 1)과 날짜가 아닌 줄
        for c in ("n_use", "dist_m", "min_use"):
            x[c] = pd.to_numeric(x[c], errors="coerce")
        # 숫자로 안 읽힌 값이 있는 행 수. 합계에서는 0으로 들어가므로 0이 아니면 원본을 본다
        n_bad_num += int(x[["n_use", "dist_m", "min_use"]].isna().any(axis=1).sum())
        n_rows += len(x)
        agg.append(x.groupby("date")[["n_use", "dist_m", "min_use"]].sum())
    # 조각별 합계를 다시 날짜별로 합친다
    day = pd.concat(agg).groupby(level=0).sum().reset_index().assign(src=path.name)
    info = {"file": path.name, "n_cols": n_cols, "rows": n_rows, "non_date_rows": n_bad_date,
            "bad_num_rows": n_bad_num, "first": day.date.min(), "last": day.date.max(), "n_dates": len(day)}
    return day, info


def build_use_daily(src_dir, out_dir, workers=4):
    """폴더의 모든 파일을 병렬로 읽어(workers개) out_dir에 저장한다.

    반환: by_file(파일×날짜별 합계), info(파일별 점검표, 시작 날짜순).
    같은 날짜가 두 파일에 겹치는지는 by_file.date.duplicated()로 본다.
    """
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(read_use_file, data_files(src_dir)))
    by_file = pd.concat([d for d, _ in res], ignore_index=True)
    info = pd.DataFrame([i for _, i in res]).sort_values(["first", "file"]).reset_index(drop=True)
    by_file.to_parquet(out_dir / "use_daily_by_file.parquet", index=False)
    info.to_csv(out_dir / "use_daily_files.csv", index=False)
    return by_file, info


if __name__ == "__main__":
    # 전체 폴더를 처리하고 파일별 점검표를 출력한다
    root = Path(__file__).resolve().parents[2]
    by_file, info = build_use_daily(root / "data/interim/use_daily_utf8", root / "data/interim/use_daily_agg")
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    print(info.assign(file=info.file.str[-26:]).to_string())
