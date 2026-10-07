"""대여량(노출) 예측 모형의 후보 비교와 최종 모형 — 단계 1C

[왜 따로 다루나]
신고 건수 = 대여량 × 신고율. 검증 구간에서 실제 대여량을 넣으면 나이브 대비 개선율이 21~26%에서 55~60%로 뛴다.
즉 남은 오차의 가장 큰 부분이 대여량 예측 오차다. 그래서 대여량 모형을 먼저 다듬는다.

[후보] (compare_expo가 한 표로 비교한다)
  실측 날씨로 학습
    E0 음이항(처음 쓰던 것)   E2 음이항 + 기온 편차·계절 상호작용   E5 LightGBM
  예보 오차가 섞인 날씨로 학습 ("N" = noisy)
    N2 음이항 E2              N5 LightGBM                           N7 N2와 N5의 기하평균
  기상청 과거 예보로 학습 ("K" = KMA, 시나리오 B)
    K2 음이항 E2              K5 LightGBM

[예보 오차가 섞인 날씨로 학습한다는 것]
모형을 쓸 때는 미래 날씨를 '예보'로만 안다. 그런데 학습을 '실제 날씨'로 하면 모형은 날씨를 100% 믿도록 배운다.
그러면 예보가 틀린 날(비 온다더니 안 옴) 예측이 크게 빗나간다.
학습할 때도 예보처럼 오차가 섞인 날씨를 넣으면, 모형이 "먼 날의 예보일수록 덜 믿는" 법을 배운다.
(실제 운영에서는 과거 예보를 쌓아 두고 그것으로 학습하면 된다. 여기서는 시나리오 C의 오차 흉내를 쓴다.)

[고르는 기준] 학습 구간의 뒤쪽 원점(2023-03-01 ~ 12-25)을 맞힌 주간 오차율. 검증 구간(2024)은 확인용으로만 본다.
"""
import lightgbm as lgb      # torch보다 먼저
import numpy as np
import pandas as pd

from fc import EXPO_CAL, EXPO_WX, TRAIN_DAYS, fit_nb_fc, future_frame

# LightGBM 대여량 모형의 입력: 최근 수준(4주·1주), 달력, 날씨, 며칠 뒤인가(lead)
EXPO_LGB_FEATS = ["expo_lvl", "expo_lvl7", "dow", "is_holiday", "after_off", "bridge", "is_off", "doy",
                  "tavg", "tmax", "tmin", "tavg_anom", "rain_mm", "rain_any", "snow_any", "cold_wave", "rain_lag1", "lead"]
_LGB_PARAMS = dict(objective="poisson", learning_rate=0.03, num_leaves=15, min_data_in_leaf=40, feature_fraction=0.8,
                   bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, seed=42, verbose=-1)
E2_WX = " + tavg + I(tavg**2) + cold_wave + tavg_anom + I(tavg_anom**2) + tavg_anom:fs1 + tavg_anom:fc1 + rain_any + np.log1p(rain_mm) + snow_any"


def _with_anom(f):
    """시나리오로 기온이 바뀌었으면 기온 편차(평년 대비)도 다시 계산한다"""
    return f.assign(tavg_anom=f.tavg - f.tavg_normal)


def expo_rows(m, start, end, scn):
    """학습용 행: start ~ end의 원점마다 미래 7일(날씨는 시나리오 scn). 7일이 end를 넘지 않는 원점만, 운영 중단일은 뺀다.
    같은 날짜가 여러 원점에서(며칠 뒤인지가 다르게) 나온다."""
    last = int(np.where(m.date <= end)[0][-1])
    oi = np.where(m.date.between(start, end))[0]
    f = _with_anom(future_frame(m, oi[oi + 6 <= last], scn))
    return f[f.no_service == 0].dropna(subset=["expo_lvl", "expo_lvl7"])


def lgb_rounds(rows, feats=EXPO_LGB_FEATS, tail_days=150):
    """나무 개수 정하기: 학습 행의 마지막 tail_days일을 떼어 그 오차가 더 줄지 않는 시점을 찾고, 자료가 늘어나는 만큼 조금 늘린다"""
    cut = rows.date.max() - pd.Timedelta(days=tail_days)
    a, h = rows[rows.date <= cut], rows[rows.date > cut]
    probe = lgb.train(_LGB_PARAMS, lgb.Dataset(a[feats], a.rentals), 3000, valid_sets=[lgb.Dataset(h[feats], h.rentals)],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
    return max(int(probe.best_iteration * len(rows) / len(a)), 50)


def fit_expo_lgb(rows, rounds, feats=EXPO_LGB_FEATS):
    """LightGBM 대여량 모형을 맞춘다. 돌려주는 것: '미래 7일 표'를 받아 하루하루 대여량 예측을 내는 함수"""
    model = lgb.train(_LGB_PARAMS, lgb.Dataset(rows[feats], rows.rentals), rounds)
    return lambda f: model.predict(_with_anom(f)[feats])


def expo_oof(rows, f_pred, rounds, k=5, gap=7):
    """학습 원점에 대한 '정직한' 대여량 예측(교차 예측).

    왜 필요한가: 신고 모형(GRU 등)은 학습할 때도 대여량 예측값을 입력으로 받는다. 그런데 대여량 모형이 자기가 학습한 날을
    다시 예측하면 실제보다 훨씬 잘 맞는다. 신고 모형이 그 값으로 배우면 "대여량 예측은 거의 정확하다"고 잘못 배운다.
    그래서 기간을 k토막으로 나눠, 각 토막의 날은 '그 토막을 빼고 학습한 모형'으로 예측한다(토막 앞뒤 gap일도 학습에서 뺀다).
    rows: 학습용 행(expo_rows), f_pred: 예측할 '미래 7일 표'(학습 원점). 돌려주는 것: f_pred 각 행의 대여량 예측"""
    f_pred = _with_anom(f_pred)
    dates = np.sort(rows.date.unique())
    blocks = np.array_split(dates, k)
    out = np.full(len(f_pred), np.nan)
    for b in blocks:
        lo, hi = b[0], b[-1]
        tr = rows[(rows.date < lo - np.timedelta64(gap, "D")) | (rows.date > hi + np.timedelta64(gap, "D"))]
        model = lgb.train(_LGB_PARAMS, lgb.Dataset(tr[EXPO_LGB_FEATS], tr.rentals), rounds)
        sel = ((f_pred.date >= lo) & (f_pred.date <= hi)).to_numpy()
        out[sel] = model.predict(f_pred.loc[sel, EXPO_LGB_FEATS])
    full = lgb.train(_LGB_PARAMS, lgb.Dataset(rows[EXPO_LGB_FEATS], rows.rentals), rounds)     # 토막 밖 날짜(가장자리)는 전체 모형으로
    miss = np.isnan(out)
    if miss.any():
        out[miss] = full.predict(f_pred.loc[miss, EXPO_LGB_FEATS])
    return out


def _wk_err(f, hat):
    """주간(원점별 7일 합계) 대여량 예측의 오차율(%)"""
    w = pd.DataFrame({"h": np.asarray(hat), "a": f.rentals.to_numpy(), "oi": f.oi.to_numpy()}).groupby("oi").sum()
    return float((w.h - w.a).abs().mean() / w.a.mean() * 100)


def compare_expo(m):
    """대여량 모형 후보를 비교한다.
    열: 학습 구간 뒤쪽 원점(2023-03-01 ~ 12-25, 그 앞까지만 학습)과 검증 원점(2024, 2023년까지 학습)에서의 주간 오차율.
        날씨 A = 예측할 때 실제 날씨를 넣음, B = 기상청 예보를 넣음(실제 운영 조건), C = 예보 오차가 섞인 날씨를 넣음"""
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
