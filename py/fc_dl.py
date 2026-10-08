"""GRU 예측 모형. 1C에서 다른 모형과 같은 조건으로 비교한다.

입력
  past  과거 28일: 신고율(로그), 대여량(로그), 평균기온, 강수(로그), 쉬는 날, 요일(사인·코사인).
        GRU가 16차원으로 요약한다
  fut   앞으로 7일: 쉬는 날, 요일, 날씨(시나리오 값), 대여량 예측(로그). 7일치를 펼쳐 넣는다
  stat  원점 정보: 계절, (선택) 개입·환경 변수, 목표 주의 hot·freeze_thaw 일수
출력은 구조 나이브(최근 4주 신고율 × 대여량 예측) 대비 로그 배수이고, 예측 = 구조 나이브 × exp(출력)이다.

학습 원점이 약 900개뿐이라 건수를 직접 맞히게 하면 수준(레짐)을 외우는 데 용량을 다 쓴다.
수준과 이용량은 구조 나이브에 맡기고, 신경망은 날씨·요일에 따른 편차만 배우게 했다.

날씨는 실측값(시나리오 A)으로 학습하고 예측할 때 시나리오 값을 넣는다. 홀드아웃 오차가 20 epoch 연속
나아지지 않으면 멈추고, 시드 3개로 학습해 평균한다.
"""
import lightgbm  # noqa: F401  torch보다 먼저 import해야 한다. 맥에서 torch가 먼저면 LightGBM 학습 때 프로세스가 죽는다(OpenMP 충돌)
import numpy as np
import pandas as pd
import torch
from torch import nn

from fc import INTERVENTIONS

# LightGBM을 돌린 프로세스에서 torch를 여러 스레드로 쓰면 맥에서 멈춘다(OpenMP 충돌).
# 원점이 약 900개라 1스레드로도 몇 초면 끝난다.
torch.set_num_threads(1)

PAST_LEN = 28


def _day_feats(m, part):
    """날짜별 과거 특성. 0건인 날 때문에 신고율은 건수에 1을 더해 로그를 취한다"""
    return np.column_stack([
        np.log((m[part].to_numpy() + 1) / m.rentals.clip(lower=1).to_numpy() * 1e4),
        np.log(m.rentals.clip(lower=1).to_numpy()), m.tavg.to_numpy() / 10, np.log1p(m.rain_mm.to_numpy()),
        m.is_off.to_numpy(), np.sin(2 * np.pi * m.dow.to_numpy() / 7), np.cos(2 * np.pi * m.dow.to_numpy() / 7)]).astype(np.float32)


def make_arrays(m, part, f, expo_hat, extra=False):
    """원점별 입력 배열을 만든다.

    f: fc.future_frame의 미래 7일 표, expo_hat: 행별 대여량 예측.
    반환: dict(past, fut, stat, s1, oi). s1은 구조 나이브(최근 4주 신고율 × 대여량 예측)의 7일 합계
    """
    oi = f.oi.to_numpy().reshape(-1, 7)[:, 0]
    day = _day_feats(m, part)
    past = np.stack([day[i - PAST_LEN:i] for i in oi])                                   # (원점, 28, 7)
    eh = np.asarray(expo_hat, dtype=np.float64)
    fut = np.column_stack([f.is_off.to_numpy(), np.sin(2 * np.pi * f.dow.to_numpy() / 7), np.cos(2 * np.pi * f.dow.to_numpy() / 7),
                           f.tavg.to_numpy() / 10, np.log1p(f.rain_mm.to_numpy()), f.cold_wave.to_numpy(), f.winter_anom.to_numpy() / 5,
                           f.snow_any.to_numpy(), np.log(eh)]).astype(np.float32).reshape(len(oi), 7, -1)
    o = m.iloc[oi]
    stat = [o.fs1.to_numpy(), o.fc1.to_numpy(), o.fs2.to_numpy(), o.fc2.to_numpy()]
    if extra:
        g = f.groupby("oi", sort=False)
        stat += [g.hot.sum().to_numpy() / 7, g.freeze_thaw.sum().to_numpy() / 7]
        stat += [(o[c].to_numpy() / 40000 if c == "bikes_active_prev" else o[c].to_numpy()) for c in INTERVENTIONS]
    s1 = (f["lvl_" + part].to_numpy() * eh).reshape(-1, 7).sum(axis=1)
    return {"past": past, "fut": fut, "stat": np.column_stack(stat).astype(np.float32), "s1": s1, "oi": oi}


class _Net(nn.Module):
    def __init__(self, n_past, n_fut, n_stat, hidden=16):
        super().__init__()
        self.gru = nn.GRU(n_past, hidden, batch_first=True)
        self.head = nn.Sequential(nn.Linear(hidden + 7 * n_fut + n_stat, 32), nn.ReLU(), nn.Dropout(0.1), nn.Linear(32, 1))

    def forward(self, past, fut, stat):
        _, h = self.gru(past)                                   # h: 과거 28일의 요약
        return self.head(torch.cat([h[-1], fut.flatten(1), stat], dim=1)).squeeze(1)


def fit_predict_gru(tr, y_tr, hold_mask, va_pred, seeds=(0, 1, 2), max_epochs=300, patience=20):
    """GRU를 학습하고 예측한다.

    tr, y_tr  : 학습 원점의 입력 배열(시나리오 A)과 실제 7일 합계
    hold_mask : early stopping에 쓸 뒤쪽 학습 원점(bool 배열)
    va_pred   : 예측할 입력(평가할 시나리오 값)

    epoch 수는 검증 구간을 보지 않고 정한다. hold_mask가 False인 원점으로 학습하며 홀드아웃 오차가
    patience번 연속 나아지지 않으면 멈추고, 가장 좋았던 epoch 수로 학습 원점 전체를 처음부터 다시 학습한다.
    표준화용 평균·표준편차는 학습 원점에서만 구한다.
    반환: 원점별 예측 표
    """
    def norm_stats(a):
        flat = a.reshape(-1, a.shape[-1]); mu = flat.mean(0); sd = flat.std(0); sd[sd < 1e-6] = 1
        return mu, sd
    st = {k: norm_stats(tr[k]) for k in ("past", "fut", "stat")}
    def tens(d):
        return [torch.tensor((d[k] - st[k][0]) / st[k][1], dtype=torch.float32) for k in ("past", "fut", "stat")]
    Xtr, Xvp = tens(tr), tens(va_pred)
    t_tr = torch.tensor(np.log(np.clip(y_tr, 1, None) / tr["s1"]), dtype=torch.float32)     # 목표: 구조 나이브 대비 로그 배수
    hold = torch.tensor(np.asarray(hold_mask)); fit_idx = torch.where(~hold)[0]; all_idx = torch.arange(len(t_tr))

    def train(net, opt, idx, epochs, watch=None):
        """idx 원점으로 epochs번 학습. watch가 있으면 그 오차로 early stopping하고 가장 좋았던 epoch을 돌려준다"""
        best, best_ep, wait = np.inf, epochs, 0
        for ep in range(1, epochs + 1):
            net.train(); perm = idx[torch.randperm(len(idx))]
            for k in range(0, len(perm), 64):
                b = perm[k:k + 64]
                loss = nn.functional.l1_loss(net(Xtr[0][b], Xtr[1][b], Xtr[2][b]), t_tr[b])
                opt.zero_grad(); loss.backward(); opt.step()
            if watch is not None:
                net.eval()
                with torch.no_grad():
                    v = float(nn.functional.l1_loss(net(Xtr[0][watch], Xtr[1][watch], Xtr[2][watch]), t_tr[watch]))
                if v < best - 1e-4:
                    best, best_ep, wait = v, ep, 0
                else:
                    wait += 1
                    if wait >= patience:
                        break
        return best_ep

    def new_net(seed):
        torch.manual_seed(seed); np.random.seed(seed)
        net = _Net(Xtr[0].shape[2], Xtr[1].shape[2], Xtr[2].shape[1])
        return net, torch.optim.Adam(net.parameters(), lr=3e-3, weight_decay=1e-4)

    outs = []
    for seed in seeds:
        net, opt = new_net(seed)
        ep = train(net, opt, fit_idx, max_epochs, watch=torch.where(hold)[0])      # 1) epoch 수 정하기
        net, opt = new_net(seed)
        train(net, opt, all_idx, ep)                                               # 2) 전체로 다시 학습
        net.eval()
        with torch.no_grad():
            outs.append(net(*Xvp).numpy())
    return pd.DataFrame({"oi": va_pred["oi"], "pred": va_pred["s1"] * np.exp(np.mean(outs, axis=0))})
