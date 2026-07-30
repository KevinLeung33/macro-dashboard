#!/bin/bash
# ============================================
# 宏观看板 服务器部署脚本
# 用法: bash deploy.sh
# ============================================

set -e

APP_DIR="/opt/macro-dashboard"
PYTHON=$(which python3 || which python)

echo "=== 宏观看板部署 ==="
echo "目标目录: $APP_DIR"
echo ""

# 1. 创建目录
mkdir -p $APP_DIR
cp -r ./* $APP_DIR/

# 2. 安装依赖
cd $APP_DIR
echo "安装Python依赖..."
$PYTHON -m pip install -r requirements.txt -q

# 3. 初始化数据库
echo "初始化数据库 + 首次拉取数据..."
$PYTHON -c "from db.schema import init_db; init_db(); from data.pipeline import fetch_all; fetch_all(include_global=False, incremental=False)"
echo "首次数据拉取完成"

mkdir -p logs backups

# 4. 创建 systemd 服务
echo "创建systemd服务..."
cat > /etc/systemd/system/macro-dashboard.service << 'EOF'
[Unit]
Description=Macro Dashboard Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/macro-dashboard
ExecStart=/usr/bin/python3 server.py --with-api
Restart=on-failure
RestartSec=30
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=-/opt/macro-dashboard/.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable macro-dashboard
systemctl restart macro-dashboard

# 5. 可选：Streamlit 看板
read -p "是否启动 Streamlit 看板 (端口8501)? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    cat > /etc/systemd/system/macro-streamlit.service << 'EOF'
[Unit]
Description=Macro Dashboard Streamlit
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/macro-dashboard
ExecStart=/usr/bin/python3 -m streamlit run app.py --server.port 8501 --server.headless true
Restart=on-failure
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=-/opt/macro-dashboard/.env

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable macro-streamlit
    systemctl restart macro-streamlit
    echo "Streamlit 看板: http://$(hostname -I | awk '{print $1}'):8501"
fi

echo ""
echo "=== 部署完成 ==="
echo "定时任务服务: systemctl status macro-dashboard"
echo "日志: journalctl -u macro-dashboard -f"
echo ""
echo "配置 .env 文件 ($APP_DIR/.env):"
echo "  LARK_WEBHOOK_URL=xxx       # 飞书自定义机器人 Webhook"
echo "  LARK_WEBHOOK_SECRET=xxx    # 飞书签名校验密钥（可选）"
echo "  OPENAI_API_KEY=xxx         # AI分析(可选，也可用Ollama)"
echo "  NOTIFY_CHANNELS=lark       # 推送渠道：telegram,lark,email,webhook；逗号两侧可有空格"
echo "  API_PORT=8080              # API端口"
echo "  LOG_DIR=logs               # 日志目录"
echo "  BACKUP_DIR=backups         # 数据库备份目录"
echo ""
echo "手动触发:"
echo "  日报: curl http://localhost:8080/api/report/daily"
echo "  健康检查: curl http://localhost:8080/api/health"
echo "  运行状态: curl http://localhost:8080/api/status"
echo "  数据库备份: curl -X POST http://localhost:8080/api/maintenance/backup"
