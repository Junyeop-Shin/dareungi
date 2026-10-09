"""도입 효과 시나리오: 점검 권장 상위 N대를 미리 점검하면 다음 주 신고가 몇 건 줄 수 있는지를 여러 가정으로 나란히 본다.

왜: '모형이 맞으면 무엇이 달라지나'에 숫자로 답하려고. 새로 계산하는 것은 없고, 이미 저장된 테스트 결과
    output/tables/risk_eval_test.csv(2026-10-07에 한 번 연 테스트. 다시 돌리지 않는다)의 적중률을 건수로 바꿀 뿐이다.

읽는 법
  - 적중 대수 = 상위 N대 가운데 다음 주에 실제로 신고된 자전거 수(주 평균) = prec@N × N
  - 순위 방식 셋을 나란히 둔다: 모형(화면의 순위와 같은 '기대 신고 수'), 주행량만 본 순(B1), 무작위(B0)
    → '모형이 주행량 순보다 더 잡는 대수'가 모형의 순수한 몫이다
  - 줄일 수 있는 신고 = 적중 대수 × 예방 비율. 점검하면 그 신고를 얼마나 막는지는 자료가 없어 25% · 50% · 100% 세 경우를 모두 적는다
한계: 정답이 '신고'라서 실제 고장이 아니다. 테스트 구간(2026-01 ~ 06) 평균이고, 점검하는 데 드는 인력·시간은 넣지 않았다.

입력: output/tables/risk_eval_test.csv   출력: output/tables/impact_scenario.csv
실행: .venv/bin/python py/risk/impact_scenario.py
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RANKS = {"LGB all 기대 신고 수": "모형", "B1 노출만(km_4w)": "주행량 순", "B0 무작위": "무작위"}
PREVENT = (0.25, 0.5, 1.0)          # 점검했을 때 막는 신고의 비율(가정. 사람이 정하지 않아 세 경우를 나란히)

if __name__ == "__main__":
    ev = pd.read_csv(ROOT / "output/tables/risk_eval_test.csv", index_col=0)
    rows = []
    for n in (500, 1000, 2000):
        hit = {lab: ev.loc[k, f"prec@{n}"] * n for k, lab in RANKS.items()}
        row = {"점검 대수(주)": n, **{f"적중 대수 · {lab}": round(v, 1) for lab, v in hit.items()},
               "모형이 주행량 순보다 더 잡는 대수": round(hit["모형"] - hit["주행량 순"], 1),
               "다음 주 신고 자전거 중 비중(%) · 모형": round(ev.loc["LGB all 기대 신고 수", f"recall@{n}"] * 100, 1)}
        row |= {f"줄일 수 있는 신고 · 예방 {int(p * 100)}%": round(hit["모형"] * p, 1) for p in PREVENT}
        rows.append(row)
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "output/tables/impact_scenario.csv", index=False)
    print(out.T.to_string(header=False))
