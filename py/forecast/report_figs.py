"""단계 1 보고서 그림: 부품별·모형별 skill 막대(검증·테스트).

skill은 나이브 B1 대비 오차 감소율이다. 검증과 테스트를 나란히 그려 검증 > 테스트인 낙관 편향이 보이게 한다.

입력: output/tables/fc_valid.csv, fc_test.csv  (notebooks/1C_forecast.ipynb, 1C_test.ipynb가 저장)
출력: output/figures/fc_skill_bars.png
실행: .venv/bin/python py/forecast/report_figs.py  (저장소 루트)
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False
ROOT = Path(__file__).resolve().parents[2]
PARTS = {"fail_tire": "타이어", "fail_chain": "체인", "fail_saddle": "안장", "fail_pedal": "페달", "total_faults": "전체"}
MODELS = [("selected", "선택한 음이항"), ("S1_B", "구조 나이브 S1"), ("GRU_B", "GRU"), ("GLM_D", "음이항(날씨 없음)")]


def skill_table(name):
    t = pd.read_csv(ROOT / "output/tables" / name)
    t["부품"] = t.part.map(PARTS)
    s = t.pivot(index="model", columns="부품", values="skill")[list(PARTS.values())]
    if "selected" not in s.index:       # 검증 표에는 selected 행이 없어 selection.csv의 부품별 모형으로 채운다
        sel = pd.read_csv(ROOT / "output/models/selection.csv")
        s.loc["selected"] = [s.loc[sel.set_index("부품").loc[p, "선택"], p] for p in PARTS.values()]
    return s


def main():
    va, te = skill_table("fc_valid.csv"), skill_table("fc_test.csv")
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)
    x = np.arange(len(PARTS))
    w = 0.8 / len(MODELS)
    for ax, s, title in [(axes[0], va, "검증 구간(2024) — 모형을 고른 곳"), (axes[1], te, "테스트 구간(2025-01 ~ 2026-06) — 한 번만 본 곳")]:
        for i, (k, label) in enumerate(MODELS):
            if k not in s.index:
                continue
            ax.bar(x + (i - len(MODELS) / 2 + 0.5) * w, s.loc[k], w, label=label)
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xticks(x, list(PARTS.values()))
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.3)
    axes[0].set_ylabel("skill = 1 - MAE / MAE(나이브 B1)")
    axes[0].legend(loc="lower left", fontsize=9)
    fig.suptitle("부품별 나이브 대비 오차 감소율 — 검증에서 고른 모형이 테스트에서는 구조 나이브에 뒤진다", y=1.02)
    fig.tight_layout()
    out = ROOT / "output/figures/fc_skill_bars.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print("wrote", out)


if __name__ == "__main__":
    main()
