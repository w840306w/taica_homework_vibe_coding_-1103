# 八卦懶人包 · Gossip Wiki

以遊戲大亂鬥、電競與實況圈為主題的靜態網站。直接開啟 `index.html` 即可瀏覽首頁與第一篇示範文章，無需安裝套件。文章擴充方式見 [AGENTS.md](AGENTS.md)，基本結構見 [事件樣板](pages/template.html)。

若要透過本機 HTTP 預覽，在本目錄執行：

```powershell
python -m http.server 8000 --bind 127.0.0.1
```

然後開啟 [本機網站](http://127.0.0.1:8000/)。

## 更新遊戲大亂鬥八卦版文章

更新會讀取新貼文正文，用 Codex CLI 整理事件始末、時間軸和各方說法，並附上可點開的原文圖片與原文連結。摘要先交代重點，再按事件需要補上背景、關鍵轉折與目前進展。新內頁會加入首頁的「最新懶人包」。程式依原文文章編號跳過已整理的貼文。

要讓 Codex 在遇到 HTTP 403 時接手，在此專案的 Codex 聊天輸入 **更新 gossip-wiki 八卦版**。Codex 會先跑本機更新。若本機瀏覽器無法取得文章，便嘗試用內建瀏覽器讀取公開看板及原文，把正文和原圖網址直接匯入。操作規則記錄在 [AGENTS.md](AGENTS.md)。

在本目錄執行以下指令。首次更新需安裝 Python 套件，並確認 PowerShell 裡的 `codex login status` 顯示已登入。更新程式預設使用已安裝的 Microsoft Edge，使用 Chrome 時加上 `--channel chrome`。

```powershell
python -m pip install playwright beautifulsoup4
codex login status
python scripts/update_league_funny.py
```

程式會將公開貼文的標題、正文與發表時間交給已登入的 Codex CLI 整理。附圖直接引用原文圖片網址，顯示取決於來源網站。程式會列出新增和待整理的數量。暫時取不到正文或摘要的貼文保留原文連結，並在下次更新時重試。

在 PowerShell 執行時，程式會在背景讀取失敗後自動開啟一般瀏覽器重試。若該瀏覽器仍收不到文章，請使用上方的 Codex 更新指令。若手邊已有儲存的清單與原文 HTML，也可匯入。原文檔名使用文章編號，例如 `article-432847.html`。

```powershell
python scripts/update_league_funny.py --saved-page "C:\path\to\board.html" --articles-dir "C:\path\to\articles"
```

## 日後手動部署 GitHub Pages

為網站建立專用的 GitHub 儲存庫，並將本目錄的內容放在該儲存庫的根目錄。將下列命令中的網址換成實際網站儲存庫網址，再執行：

```powershell
git init -b main
git add .
git commit -m "feat: complete gossip wiki setup with dark neon style"
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
```

推送後，在 GitHub 的 **Settings → Pages** 選擇從分支部署，設定為 `main` 與 `/ (root)`。網站與事件頁使用相對路徑，部署在儲存庫名稱的子路徑下也能運作。

若從既有 Git 專案操作，先將整個網站目錄複製到原專案以外的位置，再初始化獨立儲存庫。
