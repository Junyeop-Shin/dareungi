"""대여량(노출) 예측 모형 후보 비교와 최종 모형(1C).

신고 건수 = 대여량 × 신고율이다. 검증 구간에서 실제 대여량을 넣으면 나이브 대비 skill이 21~26%에서
55~60%로 뛰어서, 남은 오차의 대부분이 대여량 예측 오차다. 그래서 대여량 모형을 따로 다듬는다.

후보(compare_expo가 한 표로 비교):
  실측 날씨로 학습      E0 음이항(초기 모형), E2 음이항 + 기온 편차·계절 상호작용, E5 LightGBM
  오차 섞인 날씨로 학습 N2 음이항 E2, N5 LightGBM, N7 N2·N5 기하평균  (N = noisy, 시나리오 C)
  기상청 과거 예보로 학습 K2 음이항 E2, K5 LightGBM  (K = KMA, 시나리오 B)

실측 날씨로 학습하면 모형이 날씨를 그대로 믿어서, 예보가 빗나간 날 예측도 크게 빗나간다.
예보처럼 오차가 섞인 날씨로 학습하면 먼 날의 예보일수록 덜 믿는 쪽으로 맞춰진다.

모형 선택은 학습 구간 뒤쪽 원점(2023-03-01 ~ 12-25)의 주간 오차율로 하고, 검증 구간(2024)은 확인용이다.
"""
import lightgbm as lgb      # torch보다 먼저
import numpy as np
import pandas as pd

from fc import EXPO_CAL, EXPO_WX, TRAIN_DAYS, fit_nb_fc, future_frame

# LightGBM 대여량 모형 입력: 최근 수준(4주·1주), 달력, 날씨, lead
EXPO_LGB_FEATS = ["expo_lvl", "expo_lvl7", "dow", "is_holiday", "after_off", "bridge", "is_off", "doy",
                  "tavg", "tmax", "tmin", "tavg_anom", "rain_mm", "rain_any", "snow_any", "cold_wave", "rain_lag1", "lead"]
_LGB_PARAMS = dict(objective="poisson", learning_rate=0.03, num_leaves=15, min_data_in_leaf=40, feature_fraction=0.8,
                   bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, seed=42, verbose=-1)
E2_WX = " + tavg + I(tavg**2) + cold_wave + tavg_anom + I(tavg_anom**2) + tavg_anom:fs1 + tavg_anom:fc1 + rain_any + np.log1p(rain_mm) + snow_any"


def _with_anom(f):
    """시나리오 기온에 맞춰 평년 대비 편차를 다시 계산한다"""
    return f.assign(tavg_anom=f.tavg - f.tavg_normal)


def expo_rows(m, start, end, scn):
    """start ~ end 원점마다 미래 7일 학습 행을 만든다(날씨는 시나리오 scn).

    7일이 end를 넘는 원점과 운영 중단일은 뺀다. 같은 날짜가 lead만 달리해 여러 번 나온다.
    """
    last = int(np.where(m.date <= end)[0][-1])
    oi = np.where(m.date.between(start, end))[0]
    f = _with_anom(future_frame(m, oi[oi + 6 <= last], scn))
    return f[f.no_service == 0].dropna(subset=["expo_lvl", "expo_lvl7"])


def lgb_rounds(rows, feats=EXPO_LGB_FEATS, tail_days=150):
    """트리 개수. 마지막 tail_days일을 떼어 early stopping하고, 전체 행 비율만큼 늘린다"""
    cut = rows.date.max() - pd.Timedelta(days=tail_days)
    a, h = rows[rows.date <= cut], rows[rows.date > cut]
    probe = lgb.train(_LGB_PARAMS, lgb.Dataset(a[feats], a.rentals), 3000, valid_sets=[lgb.Dataset(h[feats], h.rentals)],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
    return max(int(probe.best_iteration * len(rows) / len(a)), 50)


def fit_expo_lgb(rows, rounds, feats=EXPO_LGB_FEATS):
    """LightGBM 대여량 모형을 맞춘다. 반환: 미래 7일 표를 받아 날별 대여량 예측을 내는 함수"""
    model = lgb.train(_LGB_PARAMS, lgb.Dataset(rows[feats], rows.rentals), rounds)
    return lambda f: model.predict(_with_anom(f)[feats])


def _wk_err(f, hat):
    """원점별 7일 합계 대여량 예측의 오차율(%)"""
    w = pd.DataFrame({"h": np.asarray(hat), "a": f.rentals.to_numpy(), "oi": f.oi.to_numpy()}).groupby("oi").sum()
    return float((w.h - w.a).abs().mean() / w.a.mean() * 100)


def compare_expo(m):
    """대여량 모형 후보를 비교한다.

    열은 학습 뒤쪽 원점(2023-03-01 ~ 12-25, 그 전까지로 학습)과 검증 원점(2024, 2023년까지로 학습)의
    주간 오차율이다. 날씨 A/B/C는 예측할 때 넣은 날씨(실측 / 기상청 예보 / 오차 섞인 실측).
    """
    spans = {"학습 뒤쪽": (TRAIN_DAYS[0], "2023-02-28", "2023-03-01", "2023-12-25"),
             "검증": (TRAIN_DAYS[0], TRAIN_DAYS[1], "2024-01-01", "2024-12-25")}
    glm = lambda rhs: (lambda rows: (lambda r: (lambda f: r.predict(_with_anom(f))))(fit_nb_fc("rentals ~ " + rhs, rows)[0]))
    def lgbm(rows):
        return fit_expo_lgb(rows, lgb_rounds(rows))
    def ens(rows):
        g1, g2 = glm(EXPO_CAL + E2_WX)(rows), lgbm(rows)
        return lambda f: np.sqrt(np.asarray(g1(f)) * g2(f))
    cands = [("E0 음이항(처음 모형)", "A", glm(EXPO_CAL + EXPO_WX)), ("E2 음이항 + 기온 편차·계절", "A", glm(EXPO_CAL + E2_WX)), ("E5 LightGBM", "A", lgbm),
             ("N2 음이항 E2", "C", glm(EXPO_CAL + E2_WX)), ("N5 LightGBM", "C", lgbm), ("N7 N2와 N5의 기하평균", "C", ens),
             ("K2 음이항 E2", "B", glm(EXPO_CAL + E2_WX)), ("K5 LightGBM", "B", lgbm)]
    label = {"A": "실측", "B": "기상청 예보", "C": "예보 오차 섞음"}
    out = []
    for name, train_scn, maker in cands:
        row = {"모형": name, "학습에 쓴 날씨": label[train_scn]}
        for span, (a, b, o1, o2) in spans.items():
            pred = maker(expo_rows(m, a, b, train_scn))
            oi = np.where(m.date.between(o1, o2))[0]
            for scn in "ABC":
                f = future_frame(m, oi, scn)
                row[f"{span}, 날씨 {scn}"] = _wk_err(f, pred(f))
        out.append(row)
    return pd.DataFrame(out).set_index("모형")
