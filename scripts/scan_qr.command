#!/bin/bash
# TradeManager 掃碼工具 — 在終端機中顯示 WhatsApp QR 碼，掃完關閉即可

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# 先清理上一次可能殘留的 Chrome 程序（避免 "browser already running"）
pkill -f "Google Chrome for Testing" 2>/dev/null
sleep 2

clear
echo "════════════════════════════════════════════════════════"
echo "  TradeManager QR 碼掃描"
echo "  請用手機 WhatsApp 掃描下方終端機中的二維碼"
echo "  掃碼完成後按 Ctrl+C 關閉（不要直接關視窗）"
echo "════════════════════════════════════════════════════════"
echo ""

cd "$PROJECT_DIR/wa_bot"
/opt/homebrew/bin/node wa_bot.js
