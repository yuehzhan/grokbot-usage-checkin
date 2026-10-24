# Grok Bot Usage & Check-in（用量・打卡・搜尋）

在自己電腦上記錄 Grok 每週用量 %、誰在做什麼（打卡），並能搜尋「哪幾天做了哪些事、花了多少用量」。
資料只存在本機資料夾，網頁只在 `127.0.0.1` 開，不會上傳到任何地方。完整說明（英文）見 [README.md](./README.md)。

## 建議：安裝在 Grok Bot 的電腦上（Linux 版）

建議使用 **Linux 版，安裝在你的 Grok Bot 自己的電腦上**。這是平常實際在用的版本：Grok Bot 每小時在自己的電腦上用
Grok Build 讀 `/usage`，不會呼叫模型，也不扣用量。可以直接請你的 Grok Bot 幫你安裝：

```bash
git clone https://github.com/yuehzhan/grokbot-usage-checkin.git
cd grokbot-usage-checkin
python3 -m pip install -r requirements.txt
sudo apt install -y tmux        # Linux 自動讀取 /usage 需要 tmux
python3 service.py start        # 背景啟動網頁與每小時取樣；停止：python3 service.py stop
```

那台電腦需要先安裝 Grok Build 並登入過一次（見英文 README 的 *Recommended Setup*）。

> **Windows 版尚未經過充分測試。** 下面的步驟提供給想試用的人；在 Windows 上，手動輸入每週 % 是可靠的方式，自動讀取仍屬實驗性。

## Windows 快速開始（尚未充分測試）

需要 Windows 10（1809 以上）或 11，以及 [Python 3.10 以上](https://www.python.org/downloads/windows/)
（安裝時勾選「Add python.exe to PATH」）。不需要系統管理員權限。

1. 還沒裝 Python 和 Git 的話先安裝（已有的跳過），裝完關掉 PowerShell 再重開：
   ```powershell
   winget install -e --id Python.Python.3.12
   winget install -e --id Git.Git
   ```
2. 下載並啟動：
   ```powershell
   git clone https://github.com/yuehzhan/grokbot-usage-checkin.git
   cd grokbot-usage-checkin
   powershell -ExecutionPolicy Bypass -File .\install.ps1    # 想一律手動輸入 % 就加 -Manual
   powershell -ExecutionPolicy Bypass -File .\start.ps1      # 背景啟動並打開網頁
   ```
   沒有 Git 的話，也可以按 **Code → Download ZIP** 解壓縮，在該資料夾先執行 `Get-ChildItem *.ps1 | Unblock-File`
   （從網路下載的 ZIP，Windows 會封鎖腳本）。
3. 停止：`powershell -ExecutionPolicy Bypass -File .\stop.ps1`
4. （選用）登入 Windows 時自動啟動：`.\register-task.ps1`；取消：`.\register-task.ps1 -Uninstall`

## Windows 上哪些可用

| 功能 | Windows |
| :--- | :--- |
| 網頁、圖表、打卡、搜尋、用量帳本、提醒 | 可用（和 Linux/macOS 同一套程式）。 |
| 自己輸入每週 %（網頁的「手動輸入」按鈕，或 `usage_history.py sample --pct 42 --reset "Sat 09:00"`） | 可用，這是最可靠的方式。 |
| 每小時自動讀取 `/usage` 畫面 | **實驗性，尚未在真正的 Windows 電腦上測過。** 需要先安裝 Grok Build、登入過一次，且 `grok` 在 `PATH` 上（或設定 `GROK_BIN`）。程式會在看不見的虛擬主控台（ConPTY／`pywinpty`）開 `grok`、輸入 `/usage`、讀畫面後關閉。遇到更新提示、登入提示或畫面改版時，會記成「無法讀取」（絕不記成 0），可改用手動輸入。 |
| 桌面通知 | 用 PowerShell 顯示 Windows 通知，不需另外安裝。 |
| 讀取 Grok Build 的設定、登入資料、cookie 或 token | **絕不會。** 程式裡沒有這種功能；用量只來自 `/usage` 畫面上顯示的內容，或你自己輸入的數字。 |

Grok Build 沒有「不開畫面就印出用量」的指令；`grok -p "/usage"` 會被當成問題送給模型（會扣用量，也不會顯示面板），所以本工具不使用。

自動讀取一直失敗時，把 `usage_config.json` 的 `"pct_source"` 改成 `"manual"`（找不到 `grok` 時 install.ps1 會自動這樣設）：
取樣器就不再啟動 Grok Build，網頁會請你輸入數值。

記錄檔：`usage-data\service.log`、`usage-data\server.log`、`usage-data\sampler.log`。回報問題時附上這些檔案即可（裡面不含帳號資料）。
