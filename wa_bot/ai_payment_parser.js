// wa_bot/ai_payment_parser.js — AI 支付信息提取（DeepSeek 優先，OpenAI 備援）
const axios = require("axios");

const DEEPSEEK_API_KEY = process.env.DEEPSEEK_API_KEY || "";
const OPENAI_API_KEY = process.env.OPENAI_API_KEY || "";

const DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions";
const OPENAI_URL = "https://api.openai.com/v1/chat/completions";
const TIMEOUT_MS = 8000;

// ── 簡易內存緩存 ──
const _CACHE = new Map();
const CACHE_TTL = 5 * 60 * 1000;

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
  if (entry && Date.now() - entry.ts < CACHE_TTL) return entry.result;
  _CACHE.delete(key);
  return null;
}

function _setCache(text, result) {
  const key = _simpleHash(text);
  _CACHE.set(key, { result, ts: Date.now() });
}

function _cleanJsonResponse(content) {
  let cleaned = content.trim();
  cleaned = cleaned.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "");
  return cleaned.trim();
}

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

async function extractPaymentInfo(messageText) {
  if (!messageText || !messageText.trim()) return null;

  const cached = _getCached(messageText);
  if (cached !== null) {
    console.log("   🤖 AI 支付解析（緩存命中）");
    return cached;
  }

  const prompt = `你是一個付款信息提取助手。從以下 WhatsApp 消息中提取銀行轉帳付款信息。

## 輸出 JSON 字段（標記重要性）

**必須提取（缺一不可）：**
- customer_name: 收款帳戶持有人姓名（如「LI PENG」「陳大明」）
- amount: 交易金額數字（需將中文單位轉換：2千万=20000000，100w=1000000，2,839,668=2839668）
- currency: 貨幣代碼 USD/HKD/CNY（無標示時默認 USD）
- account_number: 收款銀行帳戶號碼

**重要字段（有就提取）：**
- swift: SWIFT/BIC 代碼（8 或 11 位字母數字，如 SCBLHKHHXXX）
- bank_name: 收款銀行名稱
- bank_code: 銀行編號（通常為 3 位數字，如 003）
- bank_address: 銀行地址

**可選字段（有的話就提取，沒有就留空字串）：**
- routing_number: Routing / ABA 號碼
- remarks: 備註信息
- insured_person: 投保人名稱

## 規則
1. 金額必須轉換為純數字：去掉千分位逗號，中文單位換算（万=10000，w=10000，千万=10000000，亿=100000000）
2. 貨幣識別：美金/美元/USD → USD，港幣/港元/HKD → HKD，人民幣/人民币/CNY/RMB → CNY
3. SWIFT 代碼格式為 8 或 11 位字母+數字組合

## 何時返回 null（非常重要）
以下情況**必須返回 null**，不要嘗試提取：
- 閒聊、打招呼、詢問匯率（如「今天匯率多少」「好的謝謝」）
- 客戶訂單消息（如「李鹏 需要2千万」— 這是訂單不是付款）
- 換匯公式（如「200w / 7.01 = 285,307 USD」）
- 只有人名+金額但沒有任何銀行/帳戶/入金證據的訊息
- 中英文混合的隨意對話（如「麻煩轉這筆 大概50w左右吧」— 語氣太隨意，非正式付款指令）

**必須同時滿足以下條件才提取**：
- 有明確的收款人名稱
- 有明確的交易金額
- 至少有 2 項銀行業務證據（帳戶號碼、SWIFT代碼、銀行名稱、銀行編號、銀行地址中的任兩項）

5. 僅返回 JSON，不要任何其他文字。不確定的情況，**寧可返回 null 也不要猜測**。

如果沒有付款信息，返回：
null

如果有付款信息，返回：
{"customer_name": "...", "amount": 數字, "currency": "USD", "account_number": "...", "swift": "...", "bank_name": "...", "bank_code": "...", "bank_address": "...", "routing_number": "", "remarks": "", "insured_person": ""}

消息內容：
${messageText}`;

  let content;
  // ── Primary: DeepSeek ──
  if (DEEPSEEK_API_KEY) {
    try {
      content = await _callApi(DEEPSEEK_URL, DEEPSEEK_API_KEY, "deepseek-v4-flash", prompt);
    } catch (err) {
      console.log(`   ⚠️ DeepSeek 支付 API 失敗：${err.message}`);
    }
  }

  // ── Fallback: OpenAI ──
  if (!content && OPENAI_API_KEY) {
    try {
      content = await _callApi(OPENAI_URL, OPENAI_API_KEY, "gpt-3.5-turbo", prompt);
    } catch (err) {
      console.log(`   ⚠️ OpenAI 支付 API 失敗：${err.message}`);
    }
  }

  if (!content) return null;

  try {
    const parsed = JSON.parse(_cleanJsonResponse(content));
    if (!parsed || parsed === null || !parsed.customer_name || !parsed.amount) return null;

    // 標準化金額
    const amount = parseInt(String(parsed.amount).replace(/,/g, ""), 10);
    if (isNaN(amount) || amount <= 0) return null;

    const currency = ["USD", "HKD", "CNY"].includes(parsed.currency) ? parsed.currency : "USD";
    const customerName = String(parsed.customer_name || "").trim();

    const paymentDetails = {
      swift: String(parsed.swift || "").trim(),
      bank_name: String(parsed.bank_name || "").trim(),
      bank_address: String(parsed.bank_address || "").trim(),
      bank_code: String(parsed.bank_code || "").trim(),
      routing_number: String(parsed.routing_number || "").trim(),
      account_number: String(parsed.account_number || "").trim(),
      account_name: customerName,
      remarks: String(parsed.remarks || "").trim(),
      insured_person: String(parsed.insured_person || "").trim(),
    };

    const result = {
      customer_name: customerName,
      amount,
      currency,
      raw_message: messageText,
      source: "whatsapp",
      payment_details: JSON.stringify(paymentDetails),
      payment_details_dict: paymentDetails,
      remarks: paymentDetails.remarks,
      insured_person: paymentDetails.insured_person,
      warnings: [],
      matched_bank: null,
    };

    console.log(`   🤖 AI 支付解析成功：客戶=${customerName} ${amount.toLocaleString()} ${currency}`);
    _setCache(messageText, result);
    return result;
  } catch (e) {
    console.log(`   ⚠️ AI 支付解析 JSON 處理失敗：${e.message}`);
    return null;
  }
}

module.exports = { extractPaymentInfo };
