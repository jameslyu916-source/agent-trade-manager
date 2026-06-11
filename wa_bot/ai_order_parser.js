// wa_bot/ai_order_parser.js — AI 訂單提取（DeepSeek 優先，OpenAI 備援）
const axios = require("axios");

const DEEPSEEK_API_KEY = process.env.DEEPSEEK_API_KEY || "";
const OPENAI_API_KEY = process.env.OPENAI_API_KEY || "";

const DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions";
const OPENAI_URL = "https://api.openai.com/v1/chat/completions";
const TIMEOUT_MS = 8000;

// ── 簡易內存緩存（避免重連時重複調用）──
const _CACHE = new Map();
const CACHE_TTL = 5 * 60 * 1000; // 5 分鐘

function _simpleHash(text) {
  let hash = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i);
    hash = ((hash << 5) - hash) + ch;
    hash |= 0;
  }
  return String(hash);
}

function _getCached(text) {
  const key = _simpleHash(text);
  const entry = _CACHE.get(key);
  if (entry && Date.now() - entry.ts < CACHE_TTL) {
    return entry.result;
  }
  _CACHE.delete(key);
  return null;
}

function _setCache(text, result) {
  const key = _simpleHash(text);
  _CACHE.set(key, { result, ts: Date.now() });
}

// ── JSON 清理 ──
function _cleanJsonResponse(content) {
  let cleaned = content.trim();
  // 去掉 markdown 代碼塊
  cleaned = cleaned.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "");
  return cleaned.trim();
}

// ── 單次 API 調用 ──
async function _callApi(url, apiKey, model, prompt) {
  const response = await axios.post(url, {
    model,
    messages: [{ role: "user", content: prompt }],
    temperature: 0,
  }, {
    headers: {
      "Authorization": `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    timeout: TIMEOUT_MS,
  });
  return response.data.choices[0].message.content;
}

// ── 主入口：從消息文本中提取訂單 ──
async function extractOrders(messageText) {
  if (!messageText || !messageText.trim()) return null;

  // 檢查緩存
  const cached = _getCached(messageText);
  if (cached !== null) {
    console.log("   🤖 AI 訂單提取（緩存命中）");
    return cached;
  }

  const prompt = `你是一個訂單信息提取助手。從以下 WhatsApp 消息中提取客戶訂單信息。

每條訂單包含：
- customer_name: 客戶名稱（英文或中文名）
- amount: 金額數字（需將中文單位轉換為實際數字，如「2千万」=20000000，「100w」=1000000，「10万」=100000，「704,000」=704000）
- currency: 貨幣代碼（USD/HKD/CNY，無明確標示時默認 CNY）

## 必須遵守的規則

1. **只有包含明確交易意圖的消息才提取訂單**。必須出現「需要」「要」「安排」「訂」「落單」「做」「轉」「換」「兌換」等動詞，或者有明確的「客戶名 + 金額」結構。純粹的閒聊、詢問、陳述句中提到的數字不是訂單。

2. **@ 後面跟的數字串是 WhatsApp 用戶 ID / 手機號，絕對不是客戶名稱**。消息中的「@223613999395021」等形式是標記用戶，不要提取其中的數字作為客戶名。

3. **英文填充詞 + 數字不是訂單**。例如：
   - 「only. 1000W」→ 不是訂單，"only" 不是客戶名
   - 「just 50w」→ 不是訂單
   - 「about 100k」→ 不是訂單

4. **換匯公式的處理**：
   - 純計算公式（如「6.92 / 1.002 x 1.004 = 6.934」）→ 忽略，不是訂單
   - 訂單消息中帶有公式（如「200w / 7.01 = 285,307 USD」）→「/」或「*」前面的金額**就是訂單金額**

5. customer_name 不要包含敬語或前綴（@、先生、小姐等），只保留姓名本身。

6. 如果消息中完全沒有客戶訂單，返回空數組 {"orders": []}。**寧可不提取，也不要錯提取。**

7. **金額優先級**：如果消息中同時出現「單筆」金額和換匯公式，以公式中「/」或「*」前面的金額為準（那是實際總金額）。單筆金額通常是總金額的拆分說明，忽略它。

## 正例（應提取）
- 「li heyi 安排704,000」→ customer_name="li heyi", amount=704000
- 「李鹏 需要2千万」→ customer_name="李鹏", amount=20000000
- 「Chu duo 需要戶口 單筆100w 200w / 7.01 = 285,307 USD」→ customer_name="Chu duo", amount=2000000（取公式前的 200w，非單筆 100w）

## 反例（不應提取）
- 「@~信@ only. 1000W.」→ 無訂單（"only" 不是人名，無交易動詞）
- 「@某人 今天匯率多少」→ 無訂單（詢問，無數字金額）
- 「還需要一筆1千w的」→ 無訂單（條件式閒聊，無明確客戶名）
- 「6.92 / 1.002 x 1.004 = 6.934」→ 無訂單（換匯公式）

僅返回 JSON，不要任何其他文字：
{"orders": [{"customer_name": "...", "amount": 數字, "currency": "CNY"}]}

消息內容：
${messageText}`;

  // ── Primary: DeepSeek ──
  if (DEEPSEEK_API_KEY) {
    try {
      const content = await _callApi(DEEPSEEK_URL, DEEPSEEK_API_KEY, "deepseek-v4-flash", prompt);
      const result = JSON.parse(_cleanJsonResponse(content));
      if (result && Array.isArray(result.orders)) {
        console.log(`   🤖 AI 訂單提取（DeepSeek）：${result.orders.length} 筆`);
        _setCache(messageText, result);
        return result;
      }
    } catch (err) {
      console.log(`   ⚠️ DeepSeek API 呼叫失敗：${err.message}`);
    }
  }

  // ── Fallback: OpenAI ──
  if (OPENAI_API_KEY) {
    try {
      const content = await _callApi(OPENAI_URL, OPENAI_API_KEY, "gpt-3.5-turbo", prompt);
      const result = JSON.parse(_cleanJsonResponse(content));
      if (result && Array.isArray(result.orders)) {
        console.log(`   🤖 AI 訂單提取（OpenAI）：${result.orders.length} 筆`);
        _setCache(messageText, result);
        return result;
      }
    } catch (err) {
      console.log(`   ⚠️ OpenAI API 呼叫失敗：${err.message}`);
    }
  }

  // 兩個 API 都失敗了
  if (!DEEPSEEK_API_KEY && !OPENAI_API_KEY) {
    console.log("   ⚠️ 未設定 AI API Key，將使用正則解析");
  }

  return null;
}

module.exports = { extractOrders };
