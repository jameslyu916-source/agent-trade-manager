#!/usr/bin/env python3
"""TradeManager Pro — 一鍵啟動腳本"""

import subprocess
import sys
import signal
import time
import webbrowser
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
processes = []


def print_banner():
    print("\033[38;5;214m")
    print("  ╔═══════════════════════════════════════╗")
    print("  ║       TradeManager Pro 啟動中...      ║")
    print("  ╚═══════════════════════════════════════╝")
    print("\033[0m")


def start_backend():
    print("  [1/3] 啟動後端 API 伺服器...", end=" ", flush=True)
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    processes.append(("後端 API", p))
    time.sleep(2)
    print("\033[32m✓\033[0m (port 8000)")


def start_telegram_bot():
    print("  [2/3] 啟動 Telegram Bot...", end=" ", flush=True)
    p = subprocess.Popen(
        [sys.executable, "-m", "bot.bot"],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    processes.append(("Telegram Bot", p))
    time.sleep(1.5)
    print("\033[32m✓\033[0m")


def start_whatsapp_bot():
    print("  [3/3] 啟動 WhatsApp Bot...", end=" ", flush=True)
    wa_dir = os.path.join(BASE_DIR, "wa_bot")
    p = subprocess.Popen(
        ["node", "wa_bot.js"],
        cwd=wa_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    processes.append(("WhatsApp Bot", p))
    time.sleep(1)
    print("\033[32m✓\033[0m")


def cleanup(signum=None, frame=None):
    print("\n\033[33m  正在關閉所有服務...\033[0m")
    for name, p in processes:
        p.terminate()
    for name, p in processes:
        try:
            p.wait(timeout=5)
            print(f"  \033[32m✓\033[0m {name} 已關閉")
        except subprocess.TimeoutExpired:
            p.kill()
            print(f"  \033[31m✗\033[0m {name} 強制關閉")

    # 清理 WhatsApp Bot 殘留的 Chromium 子程序
    try:
        subprocess.run(
            ["pkill", "-f", "chrome.*wwebjs_auth/session-wa-bot"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass
    # 確保沒有殘留的 wa_bot 程序
    try:
        subprocess.run(
            ["pkill", "-f", "node.*wa_bot\\.js"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass

    print("\033[38;5;214m  TradeManager Pro 已停止\033[0m")
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print_banner()

    try:
        start_backend()
        start_telegram_bot()
        start_whatsapp_bot()
    except Exception as e:
        print(f"\n\033[31m  啟動失敗：{e}\033[0m")
        cleanup()
        return

    print()
    print("  \033[38;5;214m所有服務已啟動 ✓\033[0m")
    print("  前端頁面：\033[36mhttp://localhost:8000\033[0m")
    print("  按 \033[33mCtrl+C\033[0m 停止所有服務")
    print()

    webbrowser.open("http://localhost:8000/frontend/index.html")

    # 等待任意子程序結束或 Ctrl+C
    signal.pause()


if __name__ == "__main__":
    main()
