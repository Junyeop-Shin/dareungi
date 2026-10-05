"""이용정보(일별) 파일 82개를 '하루에 한 줄'짜리 합계표로 줄이는 스크립트

[무엇을 하나]
서울 열린데이터광장의 '공공자전거 이용정보(일별)'(OA-15246)은 한 행이
"날짜 × 대여소 × 이용권 종류 × 성별 × 연령대"별 이용건수·이동거리·이용시간이다(월 100만~200만 행).
이 프로젝트에서는 '서울 전체가 하루에 몇 건, 몇 km, 몇 분 탔는가'만 필요하므로 날짜별로 더한다.
결과는 고장신고율의 분모(노출) 후보로 쓴다. 절차 설명: docs/runbook/02 '노출 3종'.

[왜 열 이름이 아니라 열 위치로 읽나]
파일마다 첫 줄(헤더)이 다르다. 헤더가 아예 없는 파일, 헤더 글자가 깨진 파일, 작은따옴표로 감싼 파일이 있고
같은 열을 '이동거리(M)'/'이용거리(M)', '이동시간(분)'/'이용시간(분)'으로 다르게 부른다.
반면 열 순서는 82개 파일 모두 같아서(11열) 위치로 읽는 쪽이 안전하다:
    0번째 대여일자 | 6번째 이용건수 | 9번째 이동거리(M) | 10번째 이용시간(분)

[입력]  data/interim/use_daily_utf8/   (원본을 UTF-8로 바꾼 사본)
[출력]  data/interim/use_daily_agg/use_daily_by_file.parquet   파일×날짜별 합계 (n_use, dist_m, min_use)
        data/interim/use_daily_agg/use_daily_files.csv         파일별 점검표 (행 수, 기간, 읽기 실패 행 수)

[실행]  터미널(프로젝트 폴더):  python py/use_daily.py        (약 1분)
        노트북:                 from use_daily import build_use_daily
"""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from prep import data_files

# 읽을 열의 위치 → 이 프로젝트에서 쓰는 이름
# n_use = 이용건수, dist_m = 이동거리(미터), min_use = 이용시간(분)
COLS = {0: "date_raw", 6: "n_use", 9: "dist_m", 10: "min_use"}


def read_use_file(path):
    """파일 하나를 읽어 날짜별로 더한다.

    돌려주는 것 두 가지:
      day  : 날짜별 합계 표 (date, n_use, dist_m, min_use, src=파일 이름)
      info : 이 파일이 제대로 읽혔는지 보는 점검용 요약 (행 수, 첫 날·끝 날, 날짜로 읽히지 않은 줄 수 등)
    """
    path = Path(path)
    with open(path, encoding="utf-8") as h:
        first = h.readline().lstrip("\ufeff")          # 파일 맨 앞에 붙는 보이지 않는 표시(BOM) 제거
    quote = "'" if first.startswith("'") else '"'          # 2017·2018 파일은 값을 작은따옴표로 감쌌다
    agg, n_rows, n_bad_date, n_bad_num, n_cols = [], 0, 0, 0, None
    # 작은따옴표 파일(2017·2018)은 대여소 이름에 쉼표가 있는 줄이 12칸으로 갈라진다
    # (예: "' 고속터미널역 8-1번, 8-2번 출구 사이'") → 줄을 통째로 읽어 앞 1칸·뒤 5칸만 쓴다
    kw = dict(sep="\x01", quoting=3) if quote == "'" else dict(quotechar=quote)
    # 파일이 크므로(최대 500MB) 200만 행씩 끊어 읽고, 조각마다 날짜별로 더해 둔다
    # "\\N"은 원본에서 '값 없음'을 뜻하는 표시다
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
        x = x[date.notna()].assign(date=date[date.notna()])     # 날짜로 읽히는 줄만 남긴다(헤더 줄은 여기서 빠진다)
        n_bad_date += int(date.isna().sum())               # 헤더 줄(있으면 1)과 날짜가 아닌 줄
        for c in ("n_use", "dist_m", "min_use"):
            x[c] = pd.to_numeric(x[c], errors="coerce")
        # 숫자로 읽히지 않은 값이 있는 행 수(합계에서는 0으로 취급된다). 0이 아니면 원본을 확인한다
        n_bad_num += int(x[["n_use", "dist_m", "min_use"]].isna().any(axis=1).sum())
        n_rows += len(x)
        agg.append(x.groupby("date")[["n_use", "dist_m", "min_use"]].sum())
    # 끊어 읽은 조각들을 다시 날짜별로 더해 파일 하나의 최종 합계를 만든다
    day = pd.concat(agg).groupby(level=0).sum().reset_index().assign(src=path.name)
    info = {"file": path.name, "n_cols": n_cols, "rows": n_rows, "non_date_rows": n_bad_date,
            "bad_num_rows": n_bad_num, "first": day.date.min(), "last": day.date.max(), "n_dates": len(day)}
    return day, info


def build_use_daily(src_dir, out_dir, workers=4):
    """폴더 안의 모든 파일을 read_use_file로 읽어 결과를 out_dir에 저장한다.

    파일 여러 개를 동시에 읽는다(workers = 동시에 돌릴 개수).
    돌려주는 것: by_file(파일×날짜별 합계), info(파일별 점검표, 시작 날짜순)
    같은 날짜가 두 파일에 들어 있는지는 by_file.date.duplicated()로 확인할 수 있다.
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
    # 터미널에서 직접 실행했을 때만 돈다: 전체 폴더를 처리하고 파일별 점검표를 화면에 보여 준다
    root = Path(__file__).resolve().parent.parent
    by_file, info = build_use_daily(root / "data/interim/use_daily_utf8", root / "data/interim/use_daily_agg")
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    print(info.assign(file=info.file.str[-26:]).to_string())
