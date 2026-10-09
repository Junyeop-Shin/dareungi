# 배포 B — 서버 1대 (우분투 22.04/24.04 기준)

수집기(cron 대신 systemd 타이머) + 운영 DB + 스냅샷 + Streamlit + Caddy(HTTPS)를 한 VM에 올린다. 결정 로그 2026-10-08.

## 서버 사양·준비
- 2 vCPU, 4GB RAM, 디스크 30GB 이상(운영 DB는 최근 120일 기준 수 GB). 시간대 `Asia/Seoul`.
- 열어야 할 포트: 80, 443(Caddy). 8501은 밖에 열지 않는다(Caddy가 내부로 넘김).
- 서울 열린데이터 API는 **8088 포트 평문 HTTP**다. 바깥 연결(egress)이 막혀 있지 않은지 확인.
- 도메인 하나(없으면 DuckDNS 등 무료 서브도메인)를 서버 IP로 연결. Caddy가 인증서를 자동 발급한다.

## 설치 순서 (서버에서)
```bash
sudo timedatectl set-timezone Asia/Seoul
git clone git@github.com:Junyeop-Shin/dareungi.git ~/dareungi      # 비공개 저장소: 배포 키 등록
cd ~/dareungi && bash deploy/setup.sh                                 # 파이썬·venv·requirements·Caddy·systemd
nano .env                                                             # SEOUL_API_KEY=..., DATA_GO_KR_KEY=..., (선택) ALERT_WEBHOOK=...
```
맥북에서 한 번 옮길 것(깃에 없음): `data/interim/station_master.csv`, `data/interim/station_cluster.csv`,
그리고 운영 DB 초기 표(`init_ops.py`가 만든 `bw_usage`·`bw_fail`·`bike_info`·`station_base`·`wx_w`·`station_cluster`) — 맥북의 `data/db/ops.duckdb`를 그대로 복사하는 것이 가장 쉽다(분석 DB 19GB는 옮기지 않는다).
```bash
scp data/db/ops.duckdb data/interim/station_master.csv data/interim/station_cluster.csv <서버>:~/dareungi/...   # 경로 맞춰서
```
모형: `output/models/risk_spec.json`, `risk_*_final.txt`는 깃에 있다.

## 서비스
- `dareungi-app-admin.service`(8501): Streamlit 관리자 앱 상시 실행(로컬만). Caddy가 HTTPS 주소로 연결하고 basic_auth를 건다. 시민 조회 앱은 2026-10-10에 없앴다(자전거 조회는 관리자 앱의 한 화면).
- `dareungi-<job>.timer/.service`: cron 대신 systemd 타이머(맥북 cron과 같은 주기). `systemctl list-timers`로 확인.
- Caddy: `deploy/Caddyfile`의 두 도메인과 관리자 비밀번호 해시(`caddy hash-password`)를 바꾼 뒤 `/etc/caddy/Caddyfile`로. 하위 경로보다 하위 도메인 두 개를 권장(Streamlit은 하위 경로 설정이 번거롭다).

## 운영 전 점검표(런북 07·08)
- [ ] 같은 시간대 두 번 수집해도 결과 같음(멱등)
- [ ] 서버를 1시간 멈췄다 켜도 빠진 시간대가 채워짐(따라잡기)
- [ ] 잘못된 키로 실행 → 경보
- [ ] 앱이 `serving/*.parquet`만 읽음(DB 잠금 없음)
- [ ] `git log -p | grep <키 앞 6자>` 결과 없음, 로그·스냅샷에 키 없음
- [ ] HTTPS 주소로 접속, 스냅샷 48시간 넘으면 경고 뜸
- [ ] 2주 연속 무중단 수집 로그
