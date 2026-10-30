"""
usage_breakdown_ui.py - the "Built-in" tab: Grok Bot's built-in usage report (HTML + CSS + JS + page strings).

Data comes from usage_breakdown.py (snapshots saved by the user's Grok Bot). Like the check-ins tab, the page gets one
JSON blob (see `page_data`) and a small script draws it with textContent only, so bot and routine names can never
inject markup. Privacy mode swaps every bot / routine name for #1, #2 ... and every label for a symbol.
"""
import html
import json
from datetime import datetime

import usage_breakdown as ub
import usage_disk
import usage_dates

STALE_HOURS = 36   # a daily routine that has not run for a day and a half: the age turns into a notice

# Prompt a user can paste into a Grok Bot routine (also in README.md / README.zh-TW.md; a test keeps them equal).
PROMPT_EN = (
    "Every day at 09:00, look at my usage report for this week (the plan, the weekly limit used, how much of it is "
    "Grok Bot's, whether the limit is shared with other apps, when it resets, and Grok Bot's usage by bot and by "
    "activity: chats, each routine with its share and about how many runs a week, bot-to-bot, group chats). "
    "Save it as ONE new JSON file named usage-data/breakdown/<YYYY-MM-DDTHH-MM>.json inside the "
    "grokbot-usage-checkin folder (create the folder if it is missing; never overwrite an older file). "
    "Use this shape and leave out anything the report does not show: "
    '{"captured_at":"<now, ISO 8601 with my UTC offset>","period":"this_week","plan":"...","weekly_used_pct":0,'
    '"grokbot_used_pct":0,"shared_with_other_apps":true,"resets_at":"<ISO 8601>","extra_usage_source":"...",'
    '"by_bot":[{"name":"...","share":0}],"by_activity":[{"name":"Chats with the user","share":0},'
    '{"name":"Routines","share":0,"routines":[{"name":"...","share":0,"runs_per_week":0}]},'
    '{"name":"Bot-to-Bot","share":0}],"disk_free_gb":0,"disk_total_gb":0,"notes":""}. '
    "Also record the free and total disk space of the computer you run on, in GB (for example from df -h ~), as "
    "disk_free_gb and disk_total_gb (plain numbers). "
    "Percentages are plain numbers (or the text \"<1%\"). Do not send the file anywhere. Reply only \"saved\", "
    "or the reason it could not be saved."
)
PROMPT_ZH = (
    "每天早上 9:00，查看我這週的用量報告（方案、每週用量上限已用多少、其中 Grok Bot 用了多少、這個上限是否和其他應用程式共用、"
    "何時重置，以及 Grok Bot 依「機器人」和依「活動」的用量：對話、每個排程的占比和每週大約執行幾次、機器人之間、群組聊天）。"
    "把結果存成「一個新的」JSON 檔，放在 grokbot-usage-checkin 資料夾裡的 usage-data/breakdown/<YYYY-MM-DDTHH-MM>.json"
    "（資料夾不存在就建立；不要覆蓋舊檔）。格式如下，報告沒有顯示的欄位就省略："
    '{"captured_at":"<現在時間，ISO 8601，含我的 UTC 偏移>","period":"this_week","plan":"...","weekly_used_pct":0,'
    '"grokbot_used_pct":0,"shared_with_other_apps":true,"resets_at":"<ISO 8601>","extra_usage_source":"...",'
    '"by_bot":[{"name":"...","share":0}],"by_activity":[{"name":"Chats with the user","share":0},'
    '{"name":"Routines","share":0,"routines":[{"name":"...","share":0,"runs_per_week":0}]},'
    '{"name":"Bot-to-Bot","share":0}],"disk_free_gb":0,"disk_total_gb":0,"notes":""}。'
    "另外也把你所在這台電腦的硬碟剩餘和總容量（單位 GB，例如用 df -h ~ 查）記成 disk_free_gb 和 disk_total_gb（一般數字）。"
    "百分比用一般數字（或文字 \"<1%\"）。不要把檔案傳給任何人。完成後只回覆「saved」，或無法儲存的原因。"
)

EN = {
    "tab": "Built-in", "h": "Grok Bot's built-in usage report", "sub": "Saved by your Grok Bot; the newest snapshot is shown.",
    "p_this_week": "This week", "p_last_7_days": "Last 7 days", "p_last_30_days": "Last 30 days",
    "plan": "Plan", "weekly": "Weekly limit used", "gb": "Used by Grok Bot", "gb_sub": "of the weekly limit",
    "resets": "Resets", "in": "in {t}", "none": "—",
    "shared": "The weekly limit is shared with other apps on your plan; their usage is not in this report.",
    "extra": "Extra usage comes from {s}. The amount is only shown in the app settings.",
    "by_bot": "By bot", "by_bot_sub": "Share of all Grok Bot usage in this period",
    "by_act": "By activity", "by_act_sub": "Share of all Grok Bot usage in this period",
    "act_chat": "Chats with the user", "act_routine": "Routines", "act_bot_to_bot": "Bot-to-Bot", "act_group": "Group chats",
    "rt_other_routines": "Other routines", "rt_not_routine": "Not from a routine", "runs": "about {n} runs a week", "runs1": "about 1 run a week", "pt_u": " pts",
    "asof": "As of {t}", "ago_now": "just now", "ago_m": "{n} min ago", "ago_h": "{n} h ago", "ago_d": "{n} d ago",
    "stale": "Older snapshot: your Grok Bot may have stopped saving new ones.",
    "prev": "{d} pts since the previous snapshot ({t})", "notes": "Notes",
    "no_bots": "No per-bot split in this snapshot.", "no_acts": "No per-activity split in this snapshot.",
    "empty_h": "No built-in usage report saved yet",
    "empty_p": "Grok Bot can report its owner's usage when asked. Ask your Grok Bot to save that report here once a day:",
    "empty_1": "Create a routine in your Grok Bot (for example daily) and paste the prompt below into it.",
    "empty_2": "Each run saves one small JSON file in the dashboard's data folder: <data folder>/breakdown/.",
    "empty_3": "Reload this page (or press Refresh): the newest file is shown here. Older files are kept as history.",
    "empty_doc": "The file format is described in the README.",
    "disk": "Bot computer disk free: {f} GB / {t} GB", "disk_warn": "Getting low", "disk_danger": "Very low",
    "disk_help_aria": "Tips for low disk space", "disk_help_title": "Low on disk space?",
    "disk_help_0": "This is the disk space on the Bot's computer, separate from your PC. When it gets full, free up some space.",
    "disk_help_1": "When the Bot computer's disk space is low or critically low, Grok Bot may create a Disk Saver bot to help free space. It doesn't delete anything without your confirmation.",
    "disk_help_2": "Clear caches that rebuild themselves: browser temp/cache folders, pip/uv/npm download caches.",
    "disk_help_3": "Find big folders with:",
    "disk_help_4": "Don't Update or Reset the computer just to free space: Update removes installed apps and packages; Reset rebuilds from the last saved snapshot, so very recent changes may be lost.",
    "prompt": PROMPT_EN,
}
ZH_TW = dict(EN, **{
    "tab": "內建", "h": "Grok Bot 內建用量報告", "sub": "由你的 Grok Bot 儲存，顯示最新一份。",
    "p_this_week": "本週", "p_last_7_days": "近 7 天", "p_last_30_days": "近 30 天",
    "plan": "方案", "weekly": "每週用量已用", "gb": "Grok Bot 用量", "gb_sub": "（占每週上限）",
    "resets": "重置時間", "in": "{t}後",
    "shared": "每週上限和你方案中的其他應用程式共用；報告裡沒有它們的用量。",
    "extra": "額外用量來自{s}，金額只會顯示在應用程式的設定中。",
    "by_bot": "依機器人", "by_bot_sub": "占這段期間 Grok Bot 全部用量的比例",
    "by_act": "依活動", "by_act_sub": "占這段期間 Grok Bot 全部用量的比例",
    "act_chat": "和你的對話", "act_routine": "排程", "act_bot_to_bot": "機器人之間", "act_group": "群組聊天",
    "rt_other_routines": "其他排程", "rt_not_routine": "非排程", "runs": "每週約 {n} 次", "runs1": "每週約 1 次", "pt_u": " 個百分點",
    "asof": "資料時間：{t}", "ago_now": "剛剛", "ago_m": "{n} 分鐘前", "ago_h": "{n} 小時前", "ago_d": "{n} 天前",
    "stale": "這份比較舊：你的 Grok Bot 可能已經沒有再儲存新的了。",
    "prev": "較上一份（{t}）{d} 個百分點", "notes": "備註",
    "no_bots": "這份快照沒有依機器人的拆分。", "no_acts": "這份快照沒有依活動的拆分。",
    "empty_h": "還沒有儲存內建用量報告",
    "empty_p": "Grok Bot 被問到時可以回報主人的用量。請你的 Grok Bot 每天把那份報告存到這裡：",
    "empty_1": "在你的 Grok Bot 建立一個排程（例如每天一次），把下面的提示貼進去。",
    "empty_2": "每次執行會在儀表板資料夾存一個小小的 JSON 檔：<資料夾>/breakdown/。",
    "empty_3": "重新載入本頁（或按「重新整理」）就會顯示最新的一份，舊的檔案會留著當歷史紀錄。",
    "empty_doc": "檔案格式寫在 README 裡。", "prompt": PROMPT_ZH,
    "disk": "Bot 電腦硬碟剩餘 {f} GB / {t} GB", "disk_warn": "空間偏低", "disk_danger": "空間不足",
    "disk_help_aria": "硬碟空間不足時的小提示", "disk_help_title": "硬碟空間快滿了？",
    "disk_help_0": "這是 Bot 電腦的硬碟空間，跟你的電腦是分開的；快滿時請清理一下。",
    "disk_help_1": "Bot 電腦硬碟空間偏低或嚴重不足時，Grok Bot 可能會建立 Disk Saver 來幫忙清理；沒有你確認，它不會刪除任何東西。",
    "disk_help_2": "先清掉可以自動重建的快取：瀏覽器的暫存／快取資料夾，以及 pip、uv、npm 的下載快取。",
    "disk_help_3": "用這個指令找出最佔空間的資料夾：",
    "disk_help_4": "不要為了騰出空間就更新或重設電腦：更新會移除已安裝的應用程式和套件；重設會從上次儲存的快照重建，最近的變更可能會遺失。",
})
ZH_CN = dict(ZH_TW, **{
    "tab": "内置", "h": "Grok Bot 内置用量报告", "sub": "由你的 Grok Bot 保存，显示最新一份。",
    "p_this_week": "本周", "p_last_7_days": "近 7 天", "p_last_30_days": "近 30 天", "plan": "方案", "weekly": "每周用量已用",
    "gb_sub": "（占每周上限）", "resets": "重置时间", "in": "{t}后",
    "shared": "每周上限和你方案中的其他应用共用；报告里没有它们的用量。",
    "extra": "额外用量来自{s}，金额只会显示在应用的设置中。",
    "by_bot": "按机器人", "by_bot_sub": "占这段时间 Grok Bot 全部用量的比例", "by_act": "按活动", "by_act_sub": "占这段时间 Grok Bot 全部用量的比例",
    "act_chat": "和你的对话", "act_routine": "定时任务", "act_bot_to_bot": "机器人之间", "act_group": "群聊",
    "rt_other_routines": "其他定时任务", "rt_not_routine": "非定时任务", "runs": "每周约 {n} 次", "runs1": "每周约 1 次", "pt_u": " 个百分点",
    "asof": "数据时间：{t}", "ago_m": "{n} 分钟前", "ago_h": "{n} 小时前", "ago_d": "{n} 天前",
    "stale": "这份比较旧：你的 Grok Bot 可能已经没有再保存新的了。", "prev": "较上一份（{t}）{d} 个百分点", "notes": "备注",
    "no_bots": "这份快照没有按机器人的拆分。", "no_acts": "这份快照没有按活动的拆分。", "empty_h": "还没有保存内置用量报告",
    "empty_p": "Grok Bot 被问到时可以报告主人的用量。请你的 Grok Bot 每天把那份报告保存到这里：",
    "empty_1": "在你的 Grok Bot 创建一个定时任务（例如每天一次），把下面的提示粘贴进去。",
    "empty_2": "每次运行会在仪表板数据文件夹保存一个小小的 JSON 文件：<文件夹>/breakdown/。",
    "empty_3": "重新加载本页（或点「刷新」）就会显示最新的一份，旧文件会保留作为历史记录。", "empty_doc": "文件格式写在 README 里。",
    "disk": "Bot 电脑硬盘剩余 {f} GB / {t} GB", "disk_warn": "空间偏低", "disk_danger": "空间不足",
    "disk_help_aria": "硬盘空间不足时的小提示", "disk_help_title": "硬盘空间快满了？",
    "disk_help_0": "这是 Bot 电脑的硬盘空间，和你的电脑是分开的；快满时请清理一下。",
    "disk_help_1": "Bot 电脑硬盘空间偏低或严重不足时，Grok Bot 可能会创建 Disk Saver 来帮忙清理；没有你的确认，它不会删除任何东西。",
    "disk_help_2": "先清掉可以自动重建的缓存：浏览器的临时／缓存文件夹，以及 pip、uv、npm 的下载缓存。",
    "disk_help_3": "用这个命令找出最占空间的文件夹：",
    "disk_help_4": "不要为了腾出空间就更新或重置电脑：更新会移除已安装的应用和软件包；重置会从上次保存的快照重建，最近的更改可能会丢失。",
})
JA = dict(EN, **{
    "tab": "内蔵", "h": "Grok Bot 内蔵の使用状況レポート", "sub": "Grok Bot が保存した最新のスナップショットを表示します。",
    "p_this_week": "今週", "p_last_7_days": "過去7日間", "p_last_30_days": "過去30日間", "plan": "プラン", "weekly": "週間上限の使用量",
    "gb": "Grok Bot の使用量", "gb_sub": "（週間上限のうち）", "resets": "リセット", "in": "あと{t}",
    "shared": "週間上限はプラン内の他のアプリと共有されます。他のアプリの使用量はこのレポートに含まれません。",
    "extra": "追加使用量は{s}から使われます。金額はアプリの設定でのみ確認できます。",
    "by_bot": "ボット別", "by_bot_sub": "この期間の Grok Bot 全体の使用量に対する割合",
    "by_act": "アクティビティ別", "by_act_sub": "この期間の Grok Bot 全体の使用量に対する割合",
    "act_chat": "ユーザーとのチャット", "act_routine": "ルーティン", "act_bot_to_bot": "ボット間", "act_group": "グループチャット",
    "rt_other_routines": "その他のルーティン", "rt_not_routine": "ルーティン以外", "runs": "週に約{n}回", "runs1": "週に約1回", "pt_u": " ポイント",
    "asof": "{t} 時点", "ago_now": "たった今", "ago_m": "{n}分前", "ago_h": "{n}時間前", "ago_d": "{n}日前",
    "stale": "古いスナップショットです。Grok Bot が新しいものを保存していない可能性があります。",
    "prev": "前回（{t}）から {d} ポイント", "notes": "メモ", "no_bots": "このスナップショットにはボット別の内訳がありません。",
    "no_acts": "このスナップショットにはアクティビティ別の内訳がありません。", "empty_h": "内蔵の使用状況レポートはまだ保存されていません",
    "empty_p": "Grok Bot は尋ねるとオーナーの使用状況を報告できます。そのレポートを毎日ここに保存するよう Grok Bot に頼んでください:",
    "empty_1": "Grok Bot にルーティン（例: 毎日）を作り、下のプロンプトを貼り付けます。",
    "empty_2": "実行のたびに小さな JSON ファイルがダッシュボードのデータフォルダ <データフォルダ>/breakdown/ に保存されます。",
    "empty_3": "このページを再読み込み（または更新）すると最新のものが表示されます。古いファイルは履歴として残ります。",
    "empty_doc": "ファイル形式は README に書かれています。",
    "disk": "Bot コンピューターのディスク空き: {f} GB / {t} GB", "disk_warn": "残りわずか", "disk_danger": "空きが不足",
    "disk_help_aria": "ディスク容量が少ないときのヒント", "disk_help_title": "ディスクの空きが少なくなってきた？",
    "disk_help_0": "これは Bot のコンピューターのディスク容量で、あなたの PC とは別です。いっぱいになりそうなときは、少し空きを作ってください。",
    "disk_help_1": "Bot のコンピューターのディスク容量が少なくなったり、深刻に不足したりすると、Grok Bot が空きを作るために Disk Saver を作成することがあります。あなたの確認なしには何も削除しません。",
    "disk_help_2": "自動で作り直されるキャッシュを消しましょう：ブラウザの一時／キャッシュフォルダ、pip・uv・npm のダウンロードキャッシュ。",
    "disk_help_3": "大きなフォルダを探すコマンド：",
    "disk_help_4": "空きを増やすためだけにコンピューターの更新やリセットをしないでください。更新するとインストール済みのアプリとパッケージが削除され、リセットは最後に保存したスナップショットから再構築されるため、直近の変更が失われることがあります。",
})
KO = dict(EN, **{
    "tab": "내장", "h": "Grok Bot 내장 사용량 보고서", "sub": "Grok Bot이 저장한 가장 최근 스냅샷을 보여 줍니다.",
    "p_this_week": "이번 주", "p_last_7_days": "최근 7일", "p_last_30_days": "최근 30일", "plan": "요금제", "weekly": "주간 한도 사용량",
    "gb": "Grok Bot 사용량", "gb_sub": "(주간 한도 중)", "resets": "초기화", "in": "{t} 후",
    "shared": "주간 한도는 요금제의 다른 앱과 공유되며, 다른 앱의 사용량은 이 보고서에 없습니다.",
    "extra": "추가 사용량은 {s}에서 차감됩니다. 금액은 앱 설정에서만 볼 수 있습니다.",
    "by_bot": "봇별", "by_bot_sub": "이 기간 Grok Bot 전체 사용량 대비 비율", "by_act": "활동별", "by_act_sub": "이 기간 Grok Bot 전체 사용량 대비 비율",
    "act_chat": "사용자와의 채팅", "act_routine": "루틴", "act_bot_to_bot": "봇 간", "act_group": "그룹 채팅",
    "rt_other_routines": "기타 루틴", "rt_not_routine": "루틴이 아님", "runs": "주 약 {n}회", "runs1": "주 약 1회", "pt_u": "포인트",
    "asof": "{t} 기준", "ago_now": "방금", "ago_m": "{n}분 전", "ago_h": "{n}시간 전", "ago_d": "{n}일 전",
    "stale": "오래된 스냅샷입니다. Grok Bot이 새 스냅샷을 저장하지 않고 있을 수 있습니다.",
    "prev": "이전({t}) 대비 {d}포인트", "notes": "메모", "no_bots": "이 스냅샷에는 봇별 내역이 없습니다.",
    "no_acts": "이 스냅샷에는 활동별 내역이 없습니다.", "empty_h": "저장된 내장 사용량 보고서가 아직 없습니다",
    "empty_p": "Grok Bot은 물어보면 주인의 사용량을 보고할 수 있습니다. 그 보고서를 매일 여기에 저장하도록 Grok Bot에게 부탁하세요:",
    "empty_1": "Grok Bot에서 루틴(예: 매일)을 만들고 아래 프롬프트를 붙여 넣으세요.",
    "empty_2": "실행할 때마다 작은 JSON 파일이 대시보드 데이터 폴더 <데이터 폴더>/breakdown/ 에 저장됩니다.",
    "empty_3": "이 페이지를 새로고침하면 가장 최근 파일이 표시됩니다. 이전 파일은 기록으로 남습니다.",
    "empty_doc": "파일 형식은 README에 설명되어 있습니다.",
    "disk": "Bot 컴퓨터 디스크 여유 공간: {f} GB / {t} GB", "disk_warn": "공간 부족 주의", "disk_danger": "공간 매우 부족",
    "disk_help_aria": "디스크 공간 부족 시 팁", "disk_help_title": "디스크 공간이 부족한가요?",
    "disk_help_0": "이것은 Bot 컴퓨터의 디스크 공간이며, 본인 PC와는 별개입니다. 가득 차 가면 공간을 조금 비워 주세요.",
    "disk_help_1": "Bot 컴퓨터의 디스크 공간이 부족하거나 심각하게 부족해지면 Grok Bot이 공간 확보를 돕는 Disk Saver를 만들 수 있습니다. 사용자의 확인 없이는 아무것도 삭제하지 않습니다.",
    "disk_help_2": "저절로 다시 만들어지는 캐시부터 지우세요: 브라우저 임시/캐시 폴더, pip·uv·npm 다운로드 캐시.",
    "disk_help_3": "큰 폴더를 찾는 명령어:",
    "disk_help_4": "공간을 비우려고 컴퓨터를 업데이트하거나 초기화하지 마세요. 업데이트하면 설치된 앱과 패키지가 삭제되고, 초기화하면 마지막으로 저장된 스냅샷에서 다시 구성되므로 최근 변경 사항이 사라질 수 있습니다.",
})
NUMERIC = dict(EN, **{
    "tab": "▣", "h": "", "sub": "", "p_this_week": "W", "p_last_7_days": "7D", "p_last_30_days": "30D", "plan": "", "weekly": "∑",
    "gb": "●", "gb_sub": "", "resets": "↻", "in": "{t}", "none": "∅", "shared": "∥", "extra": "", "by_bot": "#", "by_bot_sub": "%",
    "by_act": "◫", "by_act_sub": "%", "act_chat": "✓", "act_routine": "↻", "act_bot_to_bot": "⇄", "act_group": "∷",
    "rt_other_routines": "…", "rt_not_routine": "−", "runs": "{n}×", "runs1": "1×", "pt_u": "", "asof": "{t}", "ago_now": "0", "ago_m": "{n}′",
    "ago_h": "{n}h", "ago_d": "{n}d", "stale": "▲", "prev": "Δ {d} ({t})", "notes": "", "no_bots": "∅", "no_acts": "∅",
    "empty_h": "∅", "empty_p": "", "empty_1": "", "empty_2": "", "empty_3": "", "empty_doc": "", "prompt": "",
    "disk": "▤ {f} / {t} GB", "disk_warn": "▲", "disk_danger": "▲▲",
})
ES = {
    "tab": "Integrado", "h": "Informe de uso integrado de Grok Bot", "sub": "Guardado por tu Grok Bot; se muestra la instantánea más reciente.",
    "p_this_week": "Esta semana", "p_last_7_days": "Últimos 7 días", "p_last_30_days": "Últimos 30 días", "plan": "Plan",
    "weekly": "Límite semanal usado", "gb": "Usado por Grok Bot", "gb_sub": "del límite semanal", "resets": "Se restablece", "in": "en {t}",
    "shared": "El límite semanal se comparte con otras apps de tu plan; su uso no aparece en este informe.",
    "extra": "El uso extra procede de {s}. El importe solo se ve en los ajustes de la app.",
    "by_bot": "Por bot", "by_bot_sub": "Parte de todo el uso de Grok Bot en este periodo", "by_act": "Por actividad",
    "by_act_sub": "Parte de todo el uso de Grok Bot en este periodo", "act_chat": "Chats con el usuario", "act_routine": "Rutinas",
    "act_bot_to_bot": "Entre bots", "act_group": "Chats de grupo", "rt_other_routines": "Otras rutinas", "rt_not_routine": "No procede de una rutina",
    "runs": "unas {n} ejecuciones por semana", "runs1": "una ejecución por semana", "pt_u": " puntos", "asof": "A fecha de {t}", "ago_now": "ahora mismo", "ago_m": "hace {n} min", "ago_h": "hace {n} h",
    "ago_d": "hace {n} d", "stale": "Instantánea antigua: puede que tu Grok Bot haya dejado de guardar nuevas.",
    "prev": "{d} puntos desde la instantánea anterior ({t})", "notes": "Notas", "no_bots": "Esta instantánea no tiene desglose por bot.",
    "no_acts": "Esta instantánea no tiene desglose por actividad.", "empty_h": "Aún no hay ningún informe de uso integrado guardado",
    "empty_p": "Grok Bot puede informar del uso de su dueño cuando se lo pides. Pídele a tu Grok Bot que guarde ese informe aquí una vez al día:",
    "empty_1": "Crea una rutina en tu Grok Bot (por ejemplo diaria) y pega dentro el prompt de abajo.",
    "empty_2": "Cada ejecución guarda un pequeño archivo JSON en la carpeta de datos del panel: <carpeta de datos>/breakdown/.",
    "empty_3": "Recarga esta página (o pulsa Actualizar): se muestra el archivo más reciente. Los anteriores se conservan como historial.",
    "empty_doc": "El formato del archivo se describe en el README.",
    "disk": "Disco libre del ordenador del Bot: {f} GB / {t} GB", "disk_warn": "Queda poco", "disk_danger": "Casi sin espacio",
    "disk_help_aria": "Consejos para cuando queda poco espacio en disco", "disk_help_title": "¿Poco espacio en disco?",
    "disk_help_0": "Este es el espacio en disco del ordenador del Bot, aparte de tu PC. Cuando se llene, libera algo de espacio.",
    "disk_help_1": "Cuando el espacio en disco del ordenador del Bot es bajo o críticamente bajo, Grok Bot puede crear un Disk Saver para ayudar a liberar espacio. No borra nada sin tu confirmación.",
    "disk_help_2": "Borra las cachés que se regeneran solas: carpetas temporales o de caché del navegador y cachés de descargas de pip, uv y npm.",
    "disk_help_3": "Encuentra las carpetas más grandes con:",
    "disk_help_4": "No actualices ni restablezcas el equipo solo para liberar espacio: actualizar elimina las apps y paquetes instalados; restablecer reconstruye desde la última instantánea guardada, así que los cambios muy recientes pueden perderse.",
}
PT = {
    "tab": "Integrado", "h": "Relatório de uso integrado do Grok Bot", "sub": "Salvo pelo seu Grok Bot; o instantâneo mais recente é mostrado.",
    "p_this_week": "Esta semana", "p_last_7_days": "Últimos 7 dias", "p_last_30_days": "Últimos 30 dias", "plan": "Plano",
    "weekly": "Limite semanal usado", "gb": "Usado pelo Grok Bot", "gb_sub": "do limite semanal", "resets": "Reinicia", "in": "em {t}",
    "shared": "O limite semanal é compartilhado com outros apps do seu plano; o uso deles não está neste relatório.",
    "extra": "O uso extra vem de {s}. O valor só aparece nas configurações do app.",
    "by_bot": "Por bot", "by_bot_sub": "Parte de todo o uso do Grok Bot neste período", "by_act": "Por atividade",
    "by_act_sub": "Parte de todo o uso do Grok Bot neste período", "act_chat": "Chats com o usuário", "act_routine": "Rotinas",
    "act_bot_to_bot": "Entre bots", "act_group": "Chats em grupo", "rt_other_routines": "Outras rotinas", "rt_not_routine": "Não vem de uma rotina",
    "runs": "cerca de {n} execuções por semana", "runs1": "cerca de 1 execução por semana", "pt_u": " pontos", "asof": "Em {t}", "ago_now": "agora", "ago_m": "há {n} min", "ago_h": "há {n} h",
    "ago_d": "há {n} d", "stale": "Instantâneo antigo: seu Grok Bot pode ter parado de salvar novos.",
    "prev": "{d} pontos desde o instantâneo anterior ({t})", "notes": "Notas", "no_bots": "Este instantâneo não tem divisão por bot.",
    "no_acts": "Este instantâneo não tem divisão por atividade.", "empty_h": "Nenhum relatório de uso integrado salvo ainda",
    "empty_p": "O Grok Bot pode informar o uso do dono quando você pede. Peça ao seu Grok Bot para salvar esse relatório aqui uma vez por dia:",
    "empty_1": "Crie uma rotina no seu Grok Bot (por exemplo diária) e cole nela o prompt abaixo.",
    "empty_2": "Cada execução salva um pequeno arquivo JSON na pasta de dados do painel: <pasta de dados>/breakdown/.",
    "empty_3": "Recarregue esta página (ou clique em Atualizar): o arquivo mais recente aparece aqui. Os antigos ficam como histórico.",
    "empty_doc": "O formato do arquivo está descrito no README.",
    "disk": "Disco livre do computador do Bot: {f} GB / {t} GB", "disk_warn": "Pouco espaço", "disk_danger": "Quase sem espaço",
    "disk_help_aria": "Dicas para pouco espaço em disco", "disk_help_title": "Pouco espaço em disco?",
    "disk_help_0": "Este é o espaço em disco do computador do Bot, separado do seu PC. Quando estiver cheio, libere um pouco de espaço.",
    "disk_help_1": "Quando o espaço em disco do computador do Bot está baixo ou criticamente baixo, o Grok Bot pode criar um Disk Saver para ajudar a liberar espaço. Ele não apaga nada sem a sua confirmação.",
    "disk_help_2": "Limpe caches que se recriam sozinhos: pastas temporárias e de cache do navegador, caches de download do pip, uv e npm.",
    "disk_help_3": "Encontre as pastas grandes com:",
    "disk_help_4": "Não atualize nem redefina o computador só para liberar espaço: atualizar remove os apps e pacotes instalados; redefinir reconstrói a partir do último snapshot salvo, então mudanças muito recentes podem ser perdidas.",
}
FR = {
    "tab": "Intégré", "h": "Rapport d'utilisation intégré de Grok Bot", "sub": "Enregistré par votre Grok Bot ; le dernier instantané est affiché.",
    "p_this_week": "Cette semaine", "p_last_7_days": "7 derniers jours", "p_last_30_days": "30 derniers jours", "plan": "Forfait",
    "weekly": "Limite hebdomadaire utilisée", "gb": "Utilisé par Grok Bot", "gb_sub": "de la limite hebdomadaire", "resets": "Réinitialisation", "in": "dans {t}",
    "shared": "La limite hebdomadaire est partagée avec d'autres applications de votre forfait ; leur utilisation n'apparaît pas dans ce rapport.",
    "extra": "L'utilisation supplémentaire provient de {s}. Le montant n'est visible que dans les réglages de l'application.",
    "by_bot": "Par bot", "by_bot_sub": "Part de toute l'utilisation de Grok Bot sur cette période", "by_act": "Par activité",
    "by_act_sub": "Part de toute l'utilisation de Grok Bot sur cette période", "act_chat": "Discussions avec l'utilisateur", "act_routine": "Routines",
    "act_bot_to_bot": "Entre bots", "act_group": "Discussions de groupe", "rt_other_routines": "Autres routines", "rt_not_routine": "Ne vient pas d'une routine",
    "runs": "environ {n} exécutions par semaine", "runs1": "environ 1 exécution par semaine", "pt_u": " points", "asof": "Au {t}", "ago_now": "à l'instant", "ago_m": "il y a {n} min", "ago_h": "il y a {n} h",
    "ago_d": "il y a {n} j", "stale": "Instantané ancien : votre Grok Bot a peut-être cessé d'en enregistrer de nouveaux.",
    "prev": "{d} points depuis l'instantané précédent ({t})", "notes": "Notes", "no_bots": "Cet instantané n'a pas de répartition par bot.",
    "no_acts": "Cet instantané n'a pas de répartition par activité.", "empty_h": "Aucun rapport d'utilisation intégré enregistré pour l'instant",
    "empty_p": "Grok Bot peut communiquer l'utilisation de son propriétaire sur demande. Demandez à votre Grok Bot d'enregistrer ce rapport ici une fois par jour :",
    "empty_1": "Créez une routine dans votre Grok Bot (par exemple quotidienne) et collez-y le prompt ci-dessous.",
    "empty_2": "Chaque exécution enregistre un petit fichier JSON dans le dossier de données du tableau de bord : <dossier de données>/breakdown/.",
    "empty_3": "Rechargez cette page (ou cliquez sur Actualiser) : le fichier le plus récent s'affiche. Les anciens sont conservés comme historique.",
    "empty_doc": "Le format du fichier est décrit dans le README.",
    "disk": "Disque libre de l'ordinateur du Bot : {f} Go / {t} Go", "disk_warn": "Espace faible", "disk_danger": "Espace très faible",
    "disk_help_aria": "Conseils en cas de manque d'espace disque", "disk_help_title": "Peu d'espace disque ?",
    "disk_help_0": "Voici l'espace disque de l'ordinateur du Bot, distinct de votre PC. Quand il se remplit, libérez un peu de place.",
    "disk_help_1": "Quand l'espace disque de l'ordinateur du Bot est faible ou critiquement faible, Grok Bot peut créer un Disk Saver pour aider à libérer de la place. Il ne supprime rien sans votre confirmation.",
    "disk_help_2": "Videz les caches qui se reconstruisent seuls : dossiers temporaires et de cache du navigateur, caches de téléchargement de pip, uv et npm.",
    "disk_help_3": "Repérez les gros dossiers avec :",
    "disk_help_4": "Ne mettez pas à jour et ne réinitialisez pas l'ordinateur juste pour libérer de la place : la mise à jour supprime les applications et paquets installés ; la réinitialisation reconstruit depuis le dernier instantané enregistré, donc des changements très récents peuvent être perdus.",
}
DE = {
    "tab": "Integriert", "h": "Integrierter Nutzungsbericht von Grok Bot", "sub": "Von deinem Grok Bot gespeichert; der neueste Schnappschuss wird angezeigt.",
    "p_this_week": "Diese Woche", "p_last_7_days": "Letzte 7 Tage", "p_last_30_days": "Letzte 30 Tage", "plan": "Tarif",
    "weekly": "Wochenlimit verbraucht", "gb": "Von Grok Bot verbraucht", "gb_sub": "des Wochenlimits", "resets": "Zurücksetzung", "in": "in {t}",
    "shared": "Das Wochenlimit teilst du dir mit anderen Apps deines Tarifs; deren Nutzung steht nicht in diesem Bericht.",
    "extra": "Zusätzliche Nutzung stammt aus {s}. Der Betrag ist nur in den App-Einstellungen sichtbar.",
    "by_bot": "Nach Bot", "by_bot_sub": "Anteil an der gesamten Grok-Bot-Nutzung in diesem Zeitraum", "by_act": "Nach Aktivität",
    "by_act_sub": "Anteil an der gesamten Grok-Bot-Nutzung in diesem Zeitraum", "act_chat": "Chats mit dem Nutzer", "act_routine": "Routinen",
    "act_bot_to_bot": "Bot-zu-Bot", "act_group": "Gruppenchats", "rt_other_routines": "Andere Routinen", "rt_not_routine": "Nicht aus einer Routine",
    "runs": "etwa {n} Läufe pro Woche", "runs1": "etwa 1 Lauf pro Woche", "pt_u": " Punkte", "asof": "Stand {t}", "ago_now": "gerade eben", "ago_m": "vor {n} Min.", "ago_h": "vor {n} Std.",
    "ago_d": "vor {n} T.", "stale": "Älterer Schnappschuss: Dein Grok Bot speichert möglicherweise keine neuen mehr.",
    "prev": "{d} Punkte seit dem vorigen Schnappschuss ({t})", "notes": "Notizen", "no_bots": "Dieser Schnappschuss hat keine Aufteilung nach Bot.",
    "no_acts": "Dieser Schnappschuss hat keine Aufteilung nach Aktivität.", "empty_h": "Noch kein integrierter Nutzungsbericht gespeichert",
    "empty_p": "Grok Bot kann auf Nachfrage die Nutzung seines Besitzers melden. Bitte deinen Grok Bot, diesen Bericht einmal täglich hier zu speichern:",
    "empty_1": "Lege in deinem Grok Bot eine Routine an (zum Beispiel täglich) und füge den folgenden Prompt ein.",
    "empty_2": "Jeder Lauf speichert eine kleine JSON-Datei im Datenordner des Dashboards: <Datenordner>/breakdown/.",
    "empty_3": "Lade diese Seite neu (oder klicke auf Aktualisieren): Die neueste Datei wird angezeigt. Ältere bleiben als Verlauf erhalten.",
    "empty_doc": "Das Dateiformat ist in der README beschrieben.",
    "disk": "Freier Speicher des Bot-Computers: {f} GB / {t} GB", "disk_warn": "Wird knapp", "disk_danger": "Sehr knapp",
    "disk_help_aria": "Tipps bei knappem Speicherplatz", "disk_help_title": "Wenig Speicherplatz?",
    "disk_help_0": "Das ist der Speicherplatz auf dem Computer des Bots, getrennt von deinem PC. Wenn er voll wird, schaffe etwas Platz.",
    "disk_help_1": "Wenn der Speicherplatz auf dem Computer des Bots knapp oder kritisch knapp ist, kann Grok Bot einen Disk Saver erstellen, der beim Freiräumen hilft. Ohne deine Bestätigung löscht er nichts.",
    "disk_help_2": "Leere Caches, die sich von selbst neu aufbauen: temporäre Browser-/Cache-Ordner, Download-Caches von pip, uv und npm.",
    "disk_help_3": "Große Ordner findest du mit:",
    "disk_help_4": "Aktualisiere oder setze den Computer nicht nur zurück, um Platz zu schaffen: Ein Update entfernt installierte Apps und Pakete; ein Zurücksetzen baut aus dem zuletzt gespeicherten Snapshot neu auf, daher können sehr aktuelle Änderungen verloren gehen.",
}
ID = {
    "tab": "Bawaan", "h": "Laporan pemakaian bawaan Grok Bot", "sub": "Disimpan oleh Grok Bot Anda; snapshot terbaru yang ditampilkan.",
    "p_this_week": "Minggu ini", "p_last_7_days": "7 hari terakhir", "p_last_30_days": "30 hari terakhir", "plan": "Paket",
    "weekly": "Batas mingguan terpakai", "gb": "Dipakai Grok Bot", "gb_sub": "dari batas mingguan", "resets": "Reset", "in": "dalam {t}",
    "shared": "Batas mingguan dibagi dengan aplikasi lain di paket Anda; pemakaian mereka tidak ada di laporan ini.",
    "extra": "Pemakaian tambahan berasal dari {s}. Jumlahnya hanya terlihat di pengaturan aplikasi.",
    "by_bot": "Per bot", "by_bot_sub": "Porsi dari seluruh pemakaian Grok Bot pada periode ini", "by_act": "Per aktivitas",
    "by_act_sub": "Porsi dari seluruh pemakaian Grok Bot pada periode ini", "act_chat": "Obrolan dengan pengguna", "act_routine": "Rutinitas",
    "act_bot_to_bot": "Antar bot", "act_group": "Obrolan grup", "rt_other_routines": "Rutinitas lain", "rt_not_routine": "Bukan dari rutinitas",
    "runs": "sekitar {n} kali per minggu", "runs1": "sekitar 1 kali per minggu", "pt_u": " poin", "asof": "Per {t}", "ago_now": "baru saja", "ago_m": "{n} mnt lalu", "ago_h": "{n} j lalu",
    "ago_d": "{n} h lalu", "stale": "Snapshot lama: Grok Bot Anda mungkin berhenti menyimpan yang baru.",
    "prev": "{d} poin sejak snapshot sebelumnya ({t})", "notes": "Catatan", "no_bots": "Snapshot ini tidak punya rincian per bot.",
    "no_acts": "Snapshot ini tidak punya rincian per aktivitas.", "empty_h": "Belum ada laporan pemakaian bawaan yang tersimpan",
    "empty_p": "Grok Bot dapat melaporkan pemakaian pemiliknya jika diminta. Minta Grok Bot Anda menyimpan laporan itu di sini sekali sehari:",
    "empty_1": "Buat rutinitas di Grok Bot Anda (misalnya harian) lalu tempel prompt di bawah ini.",
    "empty_2": "Setiap eksekusi menyimpan satu berkas JSON kecil di folder data dasbor: <folder data>/breakdown/.",
    "empty_3": "Muat ulang halaman ini (atau tekan Segarkan): berkas terbaru ditampilkan. Berkas lama disimpan sebagai riwayat.",
    "empty_doc": "Format berkas dijelaskan di README.",
    "disk": "Disk komputer Bot kosong: {f} GB / {t} GB", "disk_warn": "Mulai menipis", "disk_danger": "Sangat sedikit",
    "disk_help_aria": "Tips saat ruang disk menipis", "disk_help_title": "Ruang disk menipis?",
    "disk_help_0": "Ini adalah ruang disk di komputer Bot, terpisah dari PC Anda. Jika hampir penuh, kosongkan sedikit ruang.",
    "disk_help_1": "Saat ruang disk di komputer Bot menipis atau sangat menipis, Grok Bot dapat membuat Disk Saver untuk membantu mengosongkan ruang. Disk Saver tidak menghapus apa pun tanpa konfirmasi Anda.",
    "disk_help_2": "Bersihkan cache yang bisa dibuat ulang sendiri: folder sementara/cache browser, cache unduhan pip, uv, dan npm.",
    "disk_help_3": "Temukan folder besar dengan:",
    "disk_help_4": "Jangan memperbarui atau mereset komputer hanya untuk melegakan ruang: pembaruan menghapus aplikasi dan paket yang terpasang; reset membangun ulang dari snapshot terakhir yang tersimpan, sehingga perubahan yang sangat baru bisa hilang.",
}
LANGS = {"en": EN, "zh-TW": ZH_TW, "zh-CN": ZH_CN, "ja": JA, "ko": KO, "numeric": NUMERIC,
         "es": dict(EN, **ES), "pt": dict(EN, **PT), "fr": dict(EN, **FR), "de": dict(EN, **DE), "id": dict(EN, **ID)}


def for_lang(lang):
    """String table for a page language (unknown -> English; zh-* variants -> zh-TW / zh-CN; keys missing -> English)."""
    lang = str(lang or "en")
    if lang.startswith("zh") and lang not in LANGS:
        lang = "zh-CN" if lang in ("zh", "zh-Hans", "zh-SG") or "cn" in lang.lower() else "zh-TW"
    return LANGS.get(lang, EN)


def _fmt_time(iso, lang, tz):
    try:
        return usage_dates.reset_date(datetime.fromisoformat(iso), lang, tz)
    except (TypeError, ValueError):
        return ""


def _label(strings, kind, name, priv):
    if kind in ("chat", "routine", "bot_to_bot", "group"):
        return strings["act_" + kind]
    if kind in ("other_routines", "not_routine"):
        return strings["rt_" + kind]
    return "…" if priv else name


def page_data(snaps, lang, tz, privacy=False, disk=None):
    """JSON-ready dict for the tab. `disk` is usage_disk.page_data() (the free-space bar; plain numbers, so it stays in privacy
    mode, only its labels become symbols). Privacy mode: bots and routines become #1, #2 ..., the plan, the extra-usage
    source and the notes are left out, and every label comes from the symbol-only table."""
    S = for_lang("numeric" if privacy else lang)
    cur, prev = ub.latest_and_previous(snaps)
    out = {"L": {k: v for k, v in S.items() if k != "prompt"}, "priv": bool(privacy), "snap": None,
           "stale_h": STALE_HOURS, "prompt": "" if privacy else S["prompt"], "disk": disk}
    if cur is None:
        return out
    lk = "numeric" if privacy else lang
    cap = datetime.fromisoformat(cur["captured_at"])
    n_routine = [0]

    def routine(r):
        if r["kind"] in ("other_routines", "not_routine"):
            name = _label(S, r["kind"], r["name"], privacy)
        elif privacy:
            n_routine[0] += 1
            name = f"#{n_routine[0]}"
        else:
            name = r["name"]
        return {"n": name, "s": r["share"], "lt": r["lt1"], "runs": r["runs_per_week"]}
    snap = {
        "ts": int(cap.timestamp()), "asof": _fmt_time(cur["captured_at"], lk, tz),
        "period": S.get("p_" + cur["period"]) or ("" if privacy else cur["period"].replace("_", " ")),
        "plan": None if privacy else cur["plan"], "weekly": cur["weekly_used_pct"],
        "gb": cur["grokbot_used_pct"], "gb_lt": cur["grokbot_lt1"], "shared": cur["shared_with_other_apps"],
        "reset": _fmt_time(cur["resets_at"], lk, tz) if cur["resets_at"] else (None if privacy else cur["resets_text"]),
        "reset_ts": int(datetime.fromisoformat(cur["resets_at"]).timestamp()) if cur["resets_at"] else None,
        "extra": None if privacy else cur["extra_usage_source"],
        "bots": [{"n": f"#{i + 1}" if privacy else b["name"], "s": b["share"], "lt": b["lt1"]} for i, b in enumerate(cur["by_bot"])],
        "acts": [{"n": _label(S, a["kind"], a["name"], privacy), "k": a["kind"], "s": a["share"], "lt": a["lt1"],
                  "rs": [routine(r) for r in a["routines"]]} for a in cur["by_activity"]],
        "notes": "" if privacy else cur["notes"], "dw": None, "dg": None, "prev": None,
    }
    if prev is not None:
        d = lambda a, b: None if a is None or b is None else round(a - b, 1)  # noqa: E731
        snap["dw"], snap["dg"] = d(cur["weekly_used_pct"], prev["weekly_used_pct"]), d(cur["grokbot_used_pct"], prev["grokbot_used_pct"])
        snap["prev"] = _fmt_time(prev["captured_at"], lk, tz)
    out["snap"] = snap
    return out


def to_script_json(data) -> str:
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def pane_html(L):
    e = lambda k: html.escape(L.get(k, EN.get(k, "")))  # noqa: E731
    return f"<section id='p-breakdown'><div id='bdroot' aria-live='polite'><div class='sub'>{e('tab')}</div></div></section>"


CSS = """.bd-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px;margin:2px 0 4px}.bd-head h2{margin:0;font-size:18px}
.bd-badge{border:1px solid var(--accent);color:var(--accent);border-radius:999px;padding:1px 10px;font-size:12px;font-weight:700}
.bd-diskrow{position:relative;display:flex;align-items:center;gap:8px;margin:2px 0 10px;max-width:760px}
.bd-disk{flex:1 1 0;min-width:0;display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;font-size:13px}
.bd-help{flex:none;box-sizing:border-box;width:18px;height:18px;padding:0;border-radius:50%;border:1.5px solid currentColor;background:transparent;color:inherit;
opacity:.55;font:700 12px/1 inherit;cursor:pointer;display:inline-flex;align-items:center;justify-content:center}
.bd-help:hover,.bd-help[aria-expanded=true]{opacity:1;border-color:var(--accent);color:var(--accent)}.bd-help:focus-visible{opacity:1;outline:2px solid var(--accent);outline-offset:2px}
.bd-help-pop{position:absolute;z-index:30;left:0;top:calc(100% + 4px);box-sizing:border-box;width:min(420px,100%);padding:10px 14px 12px;background:var(--card);
border:1px solid var(--bd);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.45);font-size:13px;line-height:1.5;text-align:left}
.bd-help-pop h4{margin:0 0 6px;font-size:14px}.bd-help-lead{margin:0 0 6px;font-weight:700}.bd-help-pop ul{margin:0;padding-left:18px}.bd-help-pop li{margin:4px 0;overflow-wrap:anywhere}
.bd-help-pop code{display:block;margin-top:4px;padding:5px 8px;border-radius:6px;border:1px solid var(--bd);font:12px/1.4 ui-monospace,Consolas,monospace;
background:color-mix(in srgb,currentColor 7%,transparent);overflow-wrap:anywhere;user-select:all}
.bd-disk-t{font-weight:700;font-variant-numeric:tabular-nums}.bd-disk-src{font-size:12px;opacity:.7}.bd-disk-flag{font-size:12px;font-weight:700;border:1px solid currentColor;border-radius:999px;padding:0 8px}
.bd-disk-track{flex:1 1 160px;min-width:120px;height:8px;border-radius:4px;background:color-mix(in srgb,currentColor 14%,transparent);overflow:hidden}
.bd-disk-fill{height:100%;min-width:2px;border-radius:4px;background:color-mix(in srgb,currentColor 55%,transparent)}
.bd-asof{font-size:13px;opacity:.75}.bd-asof.stale{color:var(--notice);opacity:1;font-weight:700}
.bd-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin:10px 0}
.bd-card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:10px 14px}
.bd-k{font-size:12px;font-weight:700;letter-spacing:.03em;opacity:.7}.bd-v{font-size:26px;font-weight:700;margin:2px 0}.bd-v.sm{font-size:19px}
.bd-s{font-size:12px;opacity:.7}.bd-note{font-size:13px;margin:4px 0;opacity:.85}
.bd-cols{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px}@media(max-width:760px){.bd-cols{grid-template-columns:1fr}}
.bd-col{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:10px 14px}.bd-col h3{margin:0;font-size:15px}
.bd-row{display:grid;grid-template-columns:minmax(80px,32%) 1fr 4.2em;align-items:center;gap:8px;margin:6px 0;font-size:13px}
.bd-row.sub2{padding-left:18px;grid-template-columns:minmax(70px,32%) 1fr 4.2em;font-size:12.5px}
.bd-n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bd-n small{display:block;font-size:11px;opacity:.65;white-space:normal}
.bd-track{height:10px;border-radius:5px;background:color-mix(in srgb,currentColor 14%,transparent);overflow:hidden}
.bd-track.big{height:8px;margin-top:6px}.bd-fill{height:100%;min-width:2px;border-radius:5px;background:var(--accent)}
.bd-row.sub2 .bd-fill{opacity:.7}.bd-p{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
.bd-empty{background:var(--card);border:1px dashed var(--bd);border-radius:10px;padding:14px 18px}.bd-empty h3{margin:0 0 6px}
.bd-empty ol{margin:8px 0;padding-left:22px}.bd-prompt{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 ui-monospace,Consolas,monospace;
background:color-mix(in srgb,currentColor 7%,transparent);border:1px solid var(--bd);border-radius:8px;padding:10px 12px;max-height:240px;overflow:auto;user-select:all}"""

JS = r"""(function(){var root=document.getElementById('bdroot'),el0=document.getElementById('bddata');if(!root||!el0)return;
var D=JSON.parse(el0.textContent),T=D.L,S=D.snap;
function el(t,x,c){var e=document.createElement(t);if(x!=null)e.textContent=x;if(c)e.className=c;return e;}
function f(t,o){return String(t).replace(/\{(\w+)\}/g,function(_,k){return o[k]!=null?o[k]:'';});}
function num(v,lt){if(v==null)return T.none;if(lt)return '<1%';var r=Math.round(v*10)/10;return r+'%';}
function age(ts){var m=Math.max(0,Math.round((Date.now()/1000-ts)/60));if(m<2)return T.ago_now;if(m<90)return f(T.ago_m,{n:m});
 if(m<48*60)return f(T.ago_h,{n:Math.round(m/60)});return f(T.ago_d,{n:Math.round(m/1440)});}
function left(ts){var s=ts-Date.now()/1000;if(s<=0)return '';var d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
 return d>0?d+'d '+h+'h':(h>0?h+'h '+m+'m':m+'m');}
function dl(v){if(v==null)return '';var r=Math.round(v*10)/10;return (r>0?'▲ +'+r:(r<0?'▼ '+r:'± 0'));}
function card(k,v,s,sm){var c=el('div',null,'bd-card');c.appendChild(el('div',k,'bd-k'));c.appendChild(el('div',v,'bd-v'+(sm?' sm':'')));if(s)c.appendChild(el('div',s,'bd-s'));return c;}
function bar(w,c){var t=el('div',null,'bd-track '+(c||'')),b=el('div',null,'bd-fill');b.style.width=Math.max(0,Math.min(100,w||0))+'%';t.appendChild(b);return t;}
function row(n,s,lt,cls,sub){var r=el('div',null,'bd-row'+(cls?' '+cls:'')),a=el('div',n,'bd-n');a.title=n;
 if(sub)a.appendChild(el('small',sub));r.appendChild(a);r.appendChild(bar(s));r.appendChild(el('div',num(s,lt),'bd-p'));return r;}
function empty(){var b=el('div',null,'bd-empty');b.appendChild(el('h3',T.empty_h));if(T.empty_p)b.appendChild(el('div',T.empty_p));
 var ol=el('ol');[T.empty_1,T.empty_2,T.empty_3].forEach(function(x){if(x)ol.appendChild(el('li',x));});b.appendChild(ol);
 if(D.prompt)b.appendChild(el('pre',D.prompt,'bd-prompt'));if(T.empty_doc)b.appendChild(el('div',T.empty_doc,'sub'));root.appendChild(b);}
var helpOn=false;
function setHelp(on,focus){helpOn=on;var b=root.querySelector('.bd-help'),p=root.querySelector('.bd-help-pop');if(!b||!p)return;
 b.setAttribute('aria-expanded',on?'true':'false');p.hidden=!on;if(!on&&focus)b.focus();}
document.addEventListener('click',function(e){if(helpOn&&!(e.target.closest&&e.target.closest('.bd-diskrow')))setHelp(false);});
document.addEventListener('keydown',function(e){if(helpOn&&(e.key==='Escape'||e.key==='Esc'))setHelp(false,true);});
function help(row,d){if(!T.disk_help_title)return;var b=el('button','?','bd-help'),p=el('div',null,'bd-help-pop'),ul=el('ul'),i,x;
 b.type='button';b.setAttribute('aria-label',T.disk_help_aria||T.disk_help_title);b.setAttribute('aria-expanded','false');b.setAttribute('aria-controls','bd-help-pop');
 p.id='bd-help-pop';p.setAttribute('role','region');p.setAttribute('aria-label',T.disk_help_title);p.hidden=true;p.appendChild(el('h4',T.disk_help_title));
 if(T.disk_help_0)p.appendChild(el('p',T.disk_help_0,'bd-help-lead'));
 for(i=1;i<=4;i++){x=T['disk_help_'+i];if(!x)continue;var li=el('li',x);if(i===3&&d.du)li.appendChild(el('code',d.du));ul.appendChild(li);}
 p.appendChild(ul);b.onclick=function(){setHelp(!helpOn);};row.appendChild(b);row.appendChild(p);}
function disk(){var d=D.disk;if(!d||!T.disk)return;var row=el('div',null,'bd-diskrow'),w=el('div',null,'bd-disk'),c=d.level==='ok'?'':d.colors[d.level],
 t=el('span',f(T.disk,{f:d.free,t:d.total}),'bd-disk-t'),k=el('div',null,'bd-disk-track'),b=el('div',null,'bd-disk-fill');
 w.setAttribute('role','meter');w.setAttribute('aria-valuemin','0');w.setAttribute('aria-valuemax','100');w.setAttribute('aria-valuenow',d.pct);
 b.style.width=Math.max(0,Math.min(100,d.pct))+'%';if(c){w.style.color=c;b.style.background=c;}k.appendChild(b);w.appendChild(t);w.appendChild(k);
 if(c){var fl=T['disk_'+d.level];if(fl)w.appendChild(el('span',fl,'bd-disk-flag'));}w.title=d.free+' / '+d.total+' GB';
 if(d.src==='snapshot'&&d.ts){var sa=el('span','· '+age(d.ts),'bd-disk-src');w.appendChild(sa);}row.appendChild(w);help(row,d);root.appendChild(row);setHelp(helpOn);}
function draw(){root.textContent='';disk();if(!S){empty();return;}
 var h=el('div',null,'bd-head');if(T.h)h.appendChild(el('h2',T.h));if(S.period)h.appendChild(el('span',S.period,'bd-badge'));
 var stale=(Date.now()/1000-S.ts)/3600>D.stale_h,a=el('span',f(T.asof,{t:S.asof})+' · '+age(S.ts),'bd-asof'+(stale?' stale':''));h.appendChild(a);root.appendChild(h);
 if(stale&&T.stale)root.appendChild(el('div',(D.priv?'':'▲ ')+T.stale,'bd-note'));
 var cs=el('div',null,'bd-cards');
 if(S.plan)cs.appendChild(card(T.plan,S.plan,null,true));
 var wc=card(T.weekly,num(S.weekly),S.dw!=null&&S.prev?f(T.prev,{d:dl(S.dw),t:S.prev}):'');wc.appendChild(bar(S.weekly,'big'));cs.appendChild(wc);
 var gc=card(T.gb,num(S.gb,S.gb_lt),T.gb_sub+(S.dg!=null&&S.prev?' · '+dl(S.dg)+T.pt_u:''));gc.appendChild(bar(S.gb,'big'));cs.appendChild(gc);
 if(S.reset){var l=S.reset_ts?left(S.reset_ts):'';cs.appendChild(card(T.resets,S.reset,l?f(T.in,{t:l}):'',true));}
 root.appendChild(cs);
 if(S.shared&&T.shared)root.appendChild(el('div',T.shared,'bd-note'));
 if(S.extra&&T.extra)root.appendChild(el('div',f(T.extra,{s:S.extra}),'bd-note'));
 var cols=el('div',null,'bd-cols'),cb=el('div',null,'bd-col'),ca=el('div',null,'bd-col');
 cb.appendChild(el('h3',T.by_bot));if(T.by_bot_sub)cb.appendChild(el('div',T.by_bot_sub,'sub'));
 if(S.bots.length)S.bots.forEach(function(b){cb.appendChild(row(b.n,b.s,b.lt));});else cb.appendChild(el('div',T.no_bots,'sub'));
 ca.appendChild(el('h3',T.by_act));if(T.by_act_sub)ca.appendChild(el('div',T.by_act_sub,'sub'));
 if(S.acts.length)S.acts.forEach(function(x){ca.appendChild(row(x.n,x.s,x.lt));
  x.rs.forEach(function(r){ca.appendChild(row(r.n,r.s,r.lt,'sub2',r.runs!=null?(r.runs==1?T.runs1:f(T.runs,{n:r.runs})):''));});});
 else ca.appendChild(el('div',T.no_acts,'sub'));
 cols.appendChild(cb);cols.appendChild(ca);root.appendChild(cols);
 if(S.notes){root.appendChild(el('div',(T.notes?T.notes+': ':'')+S.notes,'bd-note'));}}
draw();setInterval(function(){if(S)draw();},60000);})();"""
