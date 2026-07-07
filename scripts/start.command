#!/bin/bash
# TradeManager 手動啟動（雙擊開啟終端機，可查看即時日誌）

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

clear
echo "════════════════════════════════════════════════════════"
echo "  TradeManager 手動啟動"
echo "  按 Ctrl+C 可優雅關閉所有服務"
echo "════════════════════════════════════════════════════════"
echo ""

cd "$PROJECT_DIR"

# 確保能找到 python3（.command 的 PATH 可能不同）
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

/opt/homebrew/bin/python3.11 start.py
