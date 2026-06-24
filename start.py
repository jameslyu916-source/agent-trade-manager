#!/usr/bin/env python3
"""TradeManager — 一鍵啟動腳本"""

import subprocess
import sys
import signal
import time
import webbrowser
import threading
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
processes = []


def print_banner():
    print("\033[38;5;214m")
    print("  ╔══════════════════════════════════════════╗")
    print("  ║        TradeManager 啟動中 ...           ║")
    print("  ╚══════════════════════════════════════════╝")
    print("\033[0m")


def start_backend():
    print("  [1/3] 啟動後端 API 伺服器...", end=" ", flush=True)
    log_file = open(os.path.join(LOG_DIR, "backend.log"), "w")
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=BASE_DIR,
        stdout=log_file,
        stderr=log_file,
    )
    processes.append(("後端 API", p, log_file))
    time.sleep(2)
    print("\033[32m✓\033[0m (port 8000)")


def start_telegram_bot():
    print("  [2/3] 啟動 Telegram Bot...", end=" ", flush=True)
    def run_bot():
        log_file = open(os.path.join(LOG_DIR, "telegram.log"), "w")
        p = subprocess.Popen(
            [sys.executable, "-m", "bot.bot"],
            cwd=BASE_DIR,
            stdout=log_file,
            stderr=log_file,
        )
        return p, log_file

    p, log_file = run_bot()
    processes.append(("Telegram Bot", p, log_file))
    time.sleep(1.5)
    print("\033[32m✓\033[0m")

    # 後台監控 Telegram Bot，崩潰後自動重啟（最多 5 次，間隔 10 秒）
    bot_ref = [p, log_file]  # 可變引用，供監控線程更新
    def monitor_telegram():
        restart_count = 0
        while True:
            ret = bot_ref[0].wait()
            restart_count += 1
            if restart_count > 5:
                print(f"\n  \033[31mTelegram Bot 已崩潰 {restart_count} 次，停止重啟\033[0m")
                break
            print(f"\n  \033[33mTelegram Bot 異常退出（第 {restart_count} 次），10 秒後重啟...\033[0m")
            time.sleep(10)
            try: bot_ref[1].close()
            except: pass
            new_p, new_log = run_bot()
            bot_ref[0] = new_p
            bot_ref[1] = new_log
            for i, (name, _, _) in enumerate(processes):
                if name == "Telegram Bot":
                    processes[i] = ("Telegram Bot", new_p, new_log)
                    break
    threading.Thread(target=monitor_telegram, daemon=True).start()


def start_whatsapp_bot():
    print("  [3/3] 啟動 WhatsApp Bot...", end=" ", flush=True)
    log_file = open(os.path.join(LOG_DIR, "whatsapp.log"), "w")
    wa_dir = os.path.join(BASE_DIR, "wa_bot")
    p = subprocess.Popen(
        ["node", "wa_bot.js"],
        cwd=wa_dir,
        stdout=log_file,
        stderr=log_file,
    )
    processes.append(("WhatsApp Bot", p, log_file))
    time.sleep(1)
    print("\033[32m✓\033[0m")


def cleanup(signum=None, frame=None):
    print("\n\033[33m  正在關閉所有服務...\033[0m")
    for item in processes:
        name = item[0]
        p = item[1]
        log_file = item[2] if len(item) > 2 else None
        p.terminate()
        if log_file:
            log_file.close()
    for item in processes:
        name = item[0]
        p = item[1]
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
    # 清理後端 API 殘留程序
    try:
        subprocess.run(
            ["pkill", "-f", "uvicorn backend.main:app"],
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

    print("\033[38;5;214m  TradeManager 已停止\033[0m")
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
    print(f"  日誌目錄：\033[36m{LOG_DIR}\033[0m")
    print("  按 \033[33mCtrl+C\033[0m 停止所有服務")
    print()

    webbrowser.open("http://localhost:8000/frontend/index.html")

    # 用 while 循環代替 signal.pause()，避免子進程 SIGCHLD 導致提前退出
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
