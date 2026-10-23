"""정비·점검 기록(공공데이터포털 정보공개 답변, 2026-10-22 받음)을 행 단위·작업 단위 표로 정리한다.

왜: 지금까지 이 프로젝트는 정비 이력이 없어 이용자 신고를 정답 자리에 놓았다. 이 기록으로 신고와 실제 정비를
견줄 수 있다(B: 연도별 총량 비교, A: 점검 순위를 실제 정비로 채점). 두 분석이 같은 기준을 쓰도록 정리를 한 번만 한다.

원본(data/raw, 고치지 않는다): 파일마다 시트가 점검·정비·따릉이포(2025년 파일만 점검 시트 없음).
  - 점검(INSPB_001 ~ 039)과 정비(MAB_001 ~ 039): 공단 정비 시스템 기록. 같은 번호는 같은 항목(10개 분류)이다.
    정비 작업의 98%가 같은 자전거·같은 시각의 점검 작업과 짝이 맞아, 한 번 손볼 때 둘이 함께 적힌다.
  - 따릉이포(REC_001 ~ 041): '우리동네 자전거방'(협력 민간 수리점)의 수리 기록. 부품 이름 41개. 해마다 3월 중순 ~ 12월만 있다.
  - 한 행 = 자전거 1대 × 항목 1개. 같은 자전거·같은 완료 시각의 행을 묶으면 작업 1건이다.

처리:
  - 자전거번호 'SPB-12345' → 정수 12345(프로젝트의 다른 표와 같은 번호)
  - 같은 시트에서 자전거·코드·완료 시각이 모두 같은 행은 한 번만 남긴다(따릉이포에만 몇십 건 있다)
  - 작업 표: (구분, 자전거, 완료 시각)마다 1행. 항목 수와 내역 목록을 둔다

입력: data/raw/정비,점검 건수(2023).xlsx, 정비,점검 건수(2024).xlsx, 정비 건수(2025).xlsx, 정비,점검 건수(2026.1.1~6.30.).xlsx
출력: data/interim/maint/maint_rows.parquet  행 단위(kind, bike_id, code, item, done, src)
      data/interim/maint/maint_jobs.parquet  작업 단위(kind, bike_id, done, n_items, items)
      output/tables/maint_prep_checks.csv    파일·시트별 행 수, 지운 중복, 작업 수, 자전거 수, 기간
실행: .venv/bin/python py/maint/maint_prep.py   (엑셀을 읽느라 2 ~ 3분)
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FILES = ["정비,점검 건수(2023).xlsx", "정비,점검 건수(2024).xlsx", "정비 건수(2025).xlsx", "정비,점검 건수(2026.1.1~6.30.).xlsx"]
KIND = {"점검": "점검", "정비": "정비", "따릉이포": "수리점"}      # 시트 이름 → 구분(수리점 = 우리동네 자전거방)
PREFIX = {"점검": "INSPB_", "정비": "MAB_", "수리점": "REC_"}       # 구분마다 코드 머리글자가 정해져 있다(확인용)


def read_all():
    """네 파일의 모든 시트를 열 이름을 맞춰 한 표로 읽는다"""
    out = []
    for name in FILES:
        path = ROOT / "data/raw" / name
        for sheet in pd.ExcelFile(path).sheet_names:
            d = pd.read_excel(path, sheet_name=sheet, dtype=str)
            d.columns = ["bike", "code", "item", "done"]          # 열 순서는 모든 시트가 같다: 번호, 코드, 내역, 완료일자
            d["kind"], d["src"] = KIND[sheet], f"{Path(name).stem}/{sheet}"
            out.append(d)
    return pd.concat(out, ignore_index=True)


def main():
    a = read_all()
    assert a.bike.str.fullmatch(r"SPB-\d+").all(), "자전거번호 형식이 SPB-숫자가 아닌 행이 있다"
    a["done"] = pd.to_datetime(a.done, errors="raise").astype("datetime64[ns]")
    for k, p in PREFIX.items():
        assert a.loc[a.kind == k, "code"].str.startswith(p).all(), f"{k}의 코드 머리글자가 {p}가 아니다"
    a["bike_id"] = a.bike.str[4:].astype("int64")

    dup = a.duplicated(["kind", "bike_id", "code", "done"])
    checks = a.assign(dup=dup).groupby("src").agg(rows=("code", "size"), dup_removed=("dup", "sum"),
                                                  first=("done", "min"), last=("done", "max"), bikes=("bike_id", "nunique"))
    rows = a[~dup][["kind", "bike_id", "code", "item", "done", "src"]].reset_index(drop=True)

    jobs = (rows.groupby(["kind", "bike_id", "done"])
            .agg(n_items=("code", "size"), items=("item", lambda s: "|".join(sorted(set(s))))).reset_index())
    checks["jobs"] = rows.groupby("src").apply(lambda d: d[["bike_id", "done"]].drop_duplicates().shape[0], include_groups=False)

    # 확인: 같은 번호의 점검·정비 코드는 같은 항목이어야 한다(한 점검표를 두 구분이 같이 쓴다)
    pm = rows[rows.kind != "수리점"].assign(n=lambda d: d.code.str.split("_").str[1]).groupby(["n", "kind"]).item.first().unstack()
    assert (pm["점검"] == pm["정비"]).all(), "같은 번호인데 점검과 정비의 항목이 다르다"

    out = ROOT / "data/interim/maint"
    out.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(out / "maint_rows.parquet", index=False)
    jobs.to_parquet(out / "maint_jobs.parquet", index=False)
    checks.reset_index().to_csv(ROOT / "output/tables/maint_prep_checks.csv", index=False)
    print(checks.to_string())
    print(f"행 {len(rows):,}(중복 {int(dup.sum())}행 뺌), 작업 {len(jobs):,}건:", jobs.kind.value_counts().to_dict())


if __name__ == "__main__":
    main()
