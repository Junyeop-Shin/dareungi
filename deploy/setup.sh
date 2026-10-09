#!/usr/bin/env bash
# 서버 1대 배포 설치 스크립트 (우분투). 저장소 루트에서: bash deploy/setup.sh
# 하는 일: 파이썬 venv + requirements, Caddy 설치, systemd 서비스·타이머 등록. .env는 손으로 만든다(키는 깃에 없다).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
USER_NAME=$(whoami)

sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git curl debian-keyring debian-archive-keyring apt-transport-https
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p data/db data/interim serving logs

# Caddy (HTTPS 자동)
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y && sudo apt-get install -y caddy
fi
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl restart caddy

# Streamlit 상시 실행: 관리자 앱(8501) 하나. 시민 조회 앱은 2026-10-10에 없앴다(자전거 조회는 관리자 앱의 한 화면)
for app in admin:app/admin/Home.py:8501; do
  IFS=':' read -r name path port <<<"$app"
  sudo tee /etc/systemd/system/dareungi-app-$name.service >/dev/null <<EOF
[Unit]
Description=dareungi $name app (Streamlit)
After=network.target
[Service]
User=$USER_NAME
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/streamlit run $path --server.headless true --server.port $port --server.address 127.0.0.1
Restart=always
[Install]
WantedBy=multi-user.target
EOF
done

# 수집·갱신 작업: systemd 타이머 (cron과 같은 주기)
declare -A JOBS=(
  [collect_rent]="*-*-* *:15:00|py/jobs/collect_rent.py"
  [detect_sham]="*-*-* *:20:00|py/jobs/detect_sham.py"
  [hourly]="*-*-* *:25:00|py/jobs/hourly.py"
  [collect_wx_fcst]="*-*-* 05,17:20:00|py/jobs/collect_wx.py fcst"
  [collect_fail]="*-*-* 06:10:00|py/jobs/collect_fail.py"
  [collect_wx_obs]="*-*-* 06:30:00|py/jobs/collect_wx.py obs"
  [daily]="*-*-* 07:00:00|py/jobs/daily.py"
  [weekly]="Mon *-*-* 07:30:00|py/jobs/weekly.py"
  [collect_station]="Mon *-*-* 05:00:00|py/jobs/collect_station.py"
)
for name in "${!JOBS[@]}"; do
  IFS='|' read -r cal cmd <<<"${JOBS[$name]}"
  sudo tee /etc/systemd/system/dareungi-$name.service >/dev/null <<EOF
[Unit]
Description=dareungi job $name
[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=$ROOT/.venv/bin/python $cmd
StandardOutput=append:$ROOT/logs/$name.log
StandardError=append:$ROOT/logs/$name.log
EOF
  sudo tee /etc/systemd/system/dareungi-$name.timer >/dev/null <<EOF
[Unit]
Description=dareungi timer $name
[Timer]
OnCalendar=$cal
Persistent=true
[Install]
WantedBy=timers.target
EOF
done
sudo systemctl daemon-reload
sudo systemctl enable --now dareungi-app-admin.service
for name in "${!JOBS[@]}"; do sudo systemctl enable --now dareungi-$name.timer; done
echo "설치 끝. .env를 만들고 'systemctl list-timers | grep dareungi'로 확인하세요."
