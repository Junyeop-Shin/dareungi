"""화면 캡처: 띄워 둔 대시보드(관리자 8501, 시민 8502)를 전체 페이지로 찍어 output/screens/에 저장한다.

화면을 고친 뒤 눈으로 확인할 때 쓴다. 시스템 Chrome과 playwright가 필요하다. 지도가 있는 화면은 네트워크가
계속 바빠서 'networkidle'을 기다리지 않고 몇 초 기다린 뒤 찍는다. Streamlit은 페이지가 아니라 안쪽 상자가
스크롤되어 full_page로는 첫 화면만 찍힌다. 그래서 내용 높이만큼 창을 키운 뒤 찍는다(grow).

실행: (서버 먼저) .venv/bin/streamlit run app/admin/Home.py --server.port 8501 / app/citizen/app.py --server.port 8502
      .venv/bin/python tests/screens.py
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "output/screens"
PAGES = [("admin_home", "http://localhost:8501/", 1400), ("admin_parts", "http://localhost:8501/parts_forecast", 1400),
         ("admin_inspection", "http://localhost:8501/inspection", 1400), ("admin_home_gangbuk", "http://localhost:8501/?scope=강북", 1400),
         ("citizen", "http://localhost:8502/", 390)]


def grow(pg):
    """창 높이를 내용 높이로 키운다(최대 9,000px). 키운 뒤 그래프가 다시 그려질 시간을 조금 준다."""
    h = pg.evaluate("Math.max(...[...document.querySelectorAll('[data-testid=stMain],body')].map(e => e.scrollHeight))")
    pg.set_viewport_size({"width": pg.viewport_size["width"], "height": min(int(h) + 40, 9000)})
    pg.wait_for_timeout(2500)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        for name, url, width in PAGES:
            pg = b.new_page(viewport={"width": width, "height": 1600})
            pg.goto(url, wait_until="domcontentloaded"); pg.wait_for_timeout(10000); grow(pg)
            pg.screenshot(path=str(OUT / f"{name}.png"))
            pg.close()
        pg = b.new_page(viewport={"width": 390, "height": 1600})          # 시민 조회 결과 상태
        pg.goto("http://localhost:8502/", wait_until="domcontentloaded"); pg.wait_for_timeout(6000)
        pg.get_by_placeholder("예: 74216").fill("74216"); pg.get_by_role("button", name="조회").click(); pg.wait_for_timeout(4000); grow(pg)
        pg.screenshot(path=str(OUT / "citizen_result.png"))
        b.close()
    print("저장:", OUT)
