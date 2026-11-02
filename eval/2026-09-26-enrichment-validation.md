# LLM 候選驗證與 reconciliation 驗收

日期：2026-09-26。分支：`feat/linguistic-workbench-mvp`。承接 [parser-only 穩定性切片](2026-09-26-parser-only-stability.md)。

本次完成的是候選資料合約與失敗隔離，不是 LLM 語言品質或完整 MVP 驗收。沒有使用真實 API key、呼叫付費模型或推送遠端。

## 修正與政策

- 候選只允許 lemma／UPOS／morphology／dependency；UPOS 為既有 17 個 enum，lemma 不空白，morphology 為有明列 feature/value 的子集；dependency 為 UD basic relation 與法／日 subtype allowlist。allowlist 在 `backend/src/workbench/candidates.py`，不改寫 parser 的既有輸出，不宣稱涵蓋全部 UD 特徵。
- 拒絕未知 ID、跨句 scope/head、自指、錯誤 root/null、重複 proposals、重複／空白 annotations 與超過三個 LLM constructions。`value_json` 拒絕錯誤型別、重複 property、NaN 等非 JSON 值。
- 依存候選以整批暫時圖驗證，可協調改 root/head，但不可成環或有多個 root。只驗證結構，不判斷這個句法解讀是否正確；正式 parser 圖不變。
- 相同值不產生分歧、不提升 certainty；morphology 陣列換序不算差異。不同值保留 parser，selected/alternative 標 tentative；原欄位無值時維持無值與 unresolved，不自動補入。
- 同 kind／sentence／word ID 集合的 resource annotation 優先，三種 annotation 都去重；來源 ID 與 annotation／disagreement ID 由 core 配置避免碰撞。unknown placeholder 不算 coverage，可被有效候選取代。
- 合併採深拷貝，Pydantic 與跨欄位驗證全部通過才交付；整批失敗不留下來源、annotation 或 tentative 值。最多一次修正重試；provider HTTP 錯誤、timeout、refusal、incomplete 不重試。HTTP 路徑仍受既有 enrichment 20 秒／總 30 秒監督。
- 缺翻譯／非標點 gloss 為 partial + ENRICHMENT_INCOMPLETE，不以空陣列假裝完成。壞候選最終失敗回有效 parser/resource + ENRICHMENT_FAILED，不回 500 或 LLM-only 結果。
- provider JSON schema 改由 Pydantic draft 直接產生，prompt 帶可接受詞彙及既有 annotations；記錄 `prompt_version=v0.1.1`、`candidate_policy_version=0.1.1`。API Analysis schema_version 仍為 0.1.0。
- 分歧 UI 分開顯示 selected、alternative、各自來源與 rationale；未判定值不假裝已由模型修好。候選文字以純文字顯示。

## 測試證據

先新增 36 個回歸情境，舊實作有 22 項未通過，再修正並擴充覆蓋範圍。

| 驗證 | 結果 | 範圍 |
| --- | --- | --- |
| `WORKBENCH_TEST_MODELS=1 python -m pytest backend/tests -q` | 126 passed | 既有 40 項 + 新增 86 項，含真實法日 parser 與 spawn 故障注入 |
| `WORKBENCH_TEST_UI=1 python -m pytest frontend/tests -q` | 10 passed | 原有 8 項 + 已知／未知 parser 值的分歧卡片與來源、純文字渲染 |
| `node --check frontend/app.js`、`git diff --check` | 通過 | 語法與 patch 檢查 |

環境為 Linux／WSL、Python 3.12、Chromium headless；沿用前次已 provisioning 的 Stanza combined 與 GiNZA。後端仍有一則既有 Starlette/httpx 棄用警告。

LLM 回應全部由 mock provider／`httpx.MockTransport` 提供；涵蓋合法／非法回應、401／429／500、refusal、incomplete、修正重試、原子回復、法日候選 JSON round-trip。瀏覽器使用真實 parser 結果加通過後端 validator 的模擬補充，並攔截分析回應；這不是連到真實 provider 的端到端測試。

重跑完整流程見 [README](../README.md)。單跑候選測試不需模型或 API key：`python -m pytest backend/tests/test_enrichment.py -q`。

## 依據與限制

依 OpenAI Docs 技能核對 [Structured Outputs 官方文件](https://developers.openai.com/api/docs/guides/structured-outputs)：schema 約束不能保證內容正確，拒答也須另外處理。因此保留本地引用／圖驗證與失敗降級，並避免手寫 provider schema 與本地 model 分歧。

候選詞彙的設計參考 [UD features](https://universaldependencies.org/u/feat/index.html)、[UD relations](https://universaldependencies.org/u/dep/index.html) 及 [French UD](https://universaldependencies.org/fr/index.html)，本專案採保守子集，不把白名單視為詞彙適用於特定句子的證明。擴充 subtype／feature 需同步修改程式、測試與政策版本。

下一步需要真實 provider 的小批次品質驗收：在本機設定 key／model 後，以人工 review 的法日例句檢查翻譯、gloss、語境保留與過度拒收率。尚未保證自由文字中的省略主語、不確定語境或語言選擇都能由 validator 自動辨識。Windows 原生、人工 gold／held-out 與資源條目審查也仍待完成。
