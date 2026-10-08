"""스냅샷 읽기, 데이터 신선도 확인, 자치구 필터 상태(08a 설계서 2절).

대시보드는 serving/*.parquet만 읽는다. DuckDB는 한 프로세스만 쓸 수 있어서 DB를 열지 않고, 인증키도 쓰지 않는다.
읽은 표는 10분 동안 캐시한다.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
SERVING = ROOT / "serving"
STALE_H = 48


@st.cache_data(ttl=600)
def load(name):
    p = SERVING / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else None


def status():
    """data_status를 {데이터 이름: Timestamp}로 돌려준다. 스냅샷이 없으면 빈 dict."""
    s = load("data_status")
    if s is None:
        return {}
    out = {}
    for k, v in zip(s.data, s.latest):
        try:
            out[k] = pd.to_datetime(v).tz_localize(None) if pd.to_datetime(v).tzinfo else pd.to_datetime(v)
        except Exception:
            pass
    return out


def stale_items(st_):
    """STALE_H(48시간)보다 오래된 데이터를 [(이름, 며칠 전)]으로 돌려준다."""
    now = pd.Timestamp.now()
    out = []
    for k in ("대여이력", "고장신고", "날씨 관측", "발행 시각"):
        if k in st_:
            age_h = (now.value - pd.Timestamp(st_[k]).value) / 3.6e12          # ns → 시간. Timedelta를 만들면 경고가 나서 직접 나눈다
            if age_h > STALE_H:
                out.append((k, int(age_h // 24)))
    return out


def get_scope():
    """보는 권역: '서울 전체', '강북', '강남'. 처음엔 주소(?scope=)에서 읽는다."""
    if "gu_scope_v" not in st.session_state:
        q = st.query_params.get("scope", "서울 전체")
        st.session_state["gu_scope_v"] = q if q in ("서울 전체", "강북", "강남") else "서울 전체"
    return st.session_state["gu_scope_v"]


def get_selected():
    """권역 안에서 고른 자치구(빈 목록 = 아무것도 안 고름). 처음엔 주소(?gu=)에서 읽는다."""
    if "gu" not in st.session_state:
        from common.seoul_tiles import SECTORS
        q = st.query_params.get_all("gu") if hasattr(st.query_params, "get_all") else []
        scope = get_scope()
        # 주소에 권역만 있으면 버튼으로 권역을 눌렀을 때처럼 그 권역의 구를 모두 고른 상태로 시작한다
        st.session_state["gu"] = [g for g in q if g] or ([] if scope == "서울 전체" else list(SECTORS[scope]))
    return st.session_state["gu"]


def get_gu():
    """실제로 거르는 자치구 목록. 빈 목록이면 서울 전체.
    강북·강남에서 구를 다 비우면 그 권역 전체로 본다(다 빼고 다시 고르는 중에도 화면이 비지 않게)."""
    from common.seoul_tiles import SECTORS
    scope, sel = get_scope(), get_selected()
    return sel if sel or scope == "서울 전체" else list(SECTORS[scope])


def set_region(scope, gus):
    st.session_state["gu_scope_v"] = scope
    st.session_state["gu"] = list(gus)
    if scope != "서울 전체":
        st.query_params["scope"] = scope
    elif "scope" in st.query_params:
        del st.query_params["scope"]
    if gus:
        st.query_params["gu"] = list(gus)
    elif "gu" in st.query_params:
        del st.query_params["gu"]


def apply_gu(df, col="district"):
    gus = get_gu()
    return df if (df is None or not gus or col not in df) else df[df[col].isin(gus)]


def since(hours):
    """hours시간 전 시각. 스냅샷 열(datetime64[us])과 단위를 맞춘다.
    지금 pandas 버전에서 pd.Timedelta 연산이 numpy 경고를 내서 numpy로 계산한다."""
    return np.datetime64(pd.Timestamp.now().to_datetime64(), "us") - np.timedelta64(int(hours), "h")


def shift_days(x, n):
    """날짜(값 하나 또는 열)에 n일을 더한다. since()와 같은 이유로 numpy로 계산한다."""
    if isinstance(x, (pd.Series, pd.Index)):
        return pd.to_datetime(x.to_numpy().astype("datetime64[us]") + np.timedelta64(int(n), "D"))
    return pd.Timestamp(np.datetime64(pd.Timestamp(x).to_datetime64(), "us") + np.timedelta64(int(n), "D"))
