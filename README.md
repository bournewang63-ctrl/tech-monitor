# AI・資安・量子・物聯網　新技術發布監測儀表板

每小時自動收集 27 個來源的新論文、新模型、新開源專案、官方發布、資安新聞與已遭利用漏洞，
用關鍵字分成 11 個領域，產生一頁式儀表板，放在 GitHub Pages 上（不用開電腦、免費）。

## 一、部署到 GitHub（約 10 分鐘，只需做一次）

1. **建立倉庫**：登入 GitHub → 右上角「+」→ New repository
   - 名稱例如 `tech-monitor`，選 **Public**（免費帳號的 GitHub Pages 需要公開倉庫）
   - 不要勾選任何初始化選項 → Create repository
2. **上傳檔案**：在新倉庫頁面點「uploading an existing file」，
   把這個資料夾**裡面的所有檔案和資料夾**一起拖進去 → Commit changes。
3. **加入排程設定檔**（GitHub 只認得放在 `.github/workflows/` 裡的設定）：
   倉庫首頁 → Add file → Create new file → 檔名欄輸入 `.github/workflows/update.yml`
   → 把 `排程設定_update.yml` 的內容整個貼上 → Commit changes。
   （如果你用的是 aimonitor.zip 解壓後的資料夾，裡面已經有 `.github`，一起拖曳上傳即可跳過這步。）
4. **開啟寫入權限**：倉庫 Settings → Actions → General → 最下方 Workflow permissions
   → 選 **Read and write permissions** → Save。
5. **開啟 Pages**：Settings → Pages → Build and deployment → Source 選 **GitHub Actions**。
6. **第一次執行**：上方 Actions 分頁 → 左側「每小時更新監測儀表板」→ 右側 Run workflow。
   約 2–3 分鐘跑完，網址會顯示在執行結果裡：`https://<你的帳號>.github.io/tech-monitor/`

之後每小時第 17 分（台灣時間）自動更新，網頁每 30 分鐘自動重新整理。

## 二、日常調整（直接在 GitHub 網頁上編輯即可，存檔後會自動重建）

| 想做的事 | 改哪個檔 |
|---|---|
| 新增／修改關鍵字、領域 | `config/topics.yaml` |
| 新增／關閉資料來源 | `config/sources.yaml`（加 `enabled: false` 可暫停） |
| 加台灣的研討會、活動 | `config/events.yaml` |
| 調整機構清單 | `config/orgs.yaml` |

關鍵字規則：英文不分大小寫、以單字比對（`robot` 也會比對到 `robots`）；
前面加 `=` 表示區分大小寫（`"=AI"`）；中文直接比對字串。修改後舊資料也會用新規則重新分類。

## 三、在自己電腦預覽（選用）

雙擊 `run_local.bat`（需 Python 3.10+）：會抓一次資料並開啟 `site/index.html`。
只抓一個來源除錯：`python collect.py ithome`

## 四、面板說明

| # | 面板 | 算法 |
|---|---|---|
| 01 | 每小時新項目 | 近 72 小時各領域項目數（一則可屬多個領域） |
| 02 | 焦點判讀 | 熱度指數＝近 24h 項目數 ÷ 前 7 日日均；≥1.5 HOT、≥1.15 RISING、≥0.85 STEADY |
| 03 | 領域熱度雷達 | 各領域 24h 數量與 7 日日均比較 |
| 04 | 領域 × 日期熱圖 | 14 天每日數量 |
| 05 | 來源類型分布 | 論文／模型／專案／討論／新聞／官方／漏洞 |
| 06 | 成長動能 | 24h 數量相對 7 日日均的百分比 |
| 07 | 爆量關鍵字 | (24h 次數 − 前7日日均) ÷ √(日均+1)，含英文標題專有名詞 |
| 08 | 機構提及 | 標題與摘要中提到的公司／機構 |
| 09 | 資安威脅指標 | CISA KEV（已遭實際利用的漏洞）、資安新聞、CVE／零時差提及 |
| 10–11 | 密碼學・量子／物聯網 | 7 天內子題出現次數 |
| 12 | 重大發布警示 | 大廠官方 AI 發布、HN ≥300 分、GitHub ≥1000 星、HF 論文 ≥100 讚、KEV 48h 內新增、PQC 標準新聞 |
| 13 | 熱門排行 | 依互動數（HN 分數、GitHub 星數、HF 讚數）對數正規化＋新鮮度 |
| 14 | 資安會議與活動 | sec-deadlines 國際資安會議＋自訂活動、90 天內投稿截止 |
| 15 | 來源健康 | 每個來源本次是否成功與新增數 |

## 五、限制

- 分類是關鍵字比對，會有誤判與漏判，不是 AI 判讀。
- arXiv 每天只公布一批（約台灣時間早上 8 點），論文數會在那時跳升。
- X（Twitter）、Reddit 需付費 API，未納入。
- 前 7 天歷史資料不足，成長率與爆量關鍵字會偏高，累積一週後才穩定。
- 公開倉庫若 60 天沒有任何活動，GitHub 可能暫停排程（會寄信通知，到 Actions 頁面按啟用即可）。
- 資料存在 `data/items/`（每月一個 jsonl 檔），可自行下載分析。
