"""대시보드 화면 시험: 관리자 앱의 네 화면을 Streamlit AppTest로 한 번씩 그려 예외가 없는지 본다(자전거 조회는 번호를 넣어 조회까지).

serving/*.parquet 스냅샷이 있어야 한다(운영 작업이 만든 것). 서버를 띄우지 않고 메모리에서 그린다.
화면 문구·배치를 고친 뒤 가장 먼저 돌린다.

실행: .venv/bin/python tests/smoke_app.py
"""
import warnings
from pathlib import Path

import pandas as pd

from streamlit.testing.v1 import AppTest

warnings.filterwarnings("ignore")
APP = Path(__file__).resolve().parents[1] / "app"


def show(name, at):
    errs = [e.value[:200] for e in at.exception]
    print(f"[{name}] 예외 {len(errs)}, 경고 {len(at.warning)}, 표 {len(at.dataframe)}", *errs)
    return len(errs)


if __name__ == "__main__":
    n = 0
    at = AppTest.from_file(str(APP / "admin/Home.py"), default_timeout=120).run()
    n += show("홈", at)
    for page, name in (("views/parts_forecast.py", "유형별 예측"), ("views/inspection.py", "점검 대상")):
        at.switch_page(page).run()
        n += show(name, at)
    at.switch_page("views/lookup.py").run()
    n += show("자전거 조회(빈 화면)", at)
    risk = pd.read_parquet(APP.parent / "serving/bike_risk_latest.parquet")
    for label, bid in (("점검 권장 1위", int(risk.sort_values("risk_ratio", ascending=False).bike_id.iloc[0])), ("없는 번호", 99999999)):
        at.text_input[0].set_value(str(bid)); at.button[0].click().run()
        n += show(f"자전거 조회({label})", at)
    raise SystemExit(1 if n else 0)
