// wa_bot/wa_bot.js
const { Client, LocalAuth } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const axios = require("axios");
const { parsePaymentInfo, parseConversionLine } = require("./payment_parser");
require("dotenv").config();

// ==================== 配置 ====================
const API_BASE_URL = process.env.API_BASE_URL || "http://localhost:8000";
const API_USERNAME = process.env.API_USERNAME || "admin";
const API_PASSWORD = process.env.API_PASSWORD || "admin123";
const WATCH_GROUP_NAMES = (process.env.WATCH_GROUP_NAMES || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);
const WA_SEND_REPLY = (process.env.WA_SEND_REPLY || "true") === "true";

// ── 系統設置快取 ──
let settingsCache = {};

async function refreshSettings() {
  try {
    if (!authToken) return false;
    const res = await axios.get(`${API_BASE_URL}/settings`, { headers: getHeaders() });
    if (res.status === 200) {
      settingsCache = res.data;
      const tg = res.data.telegram_enabled !== false ? "啟用" : "停用";
      const wa = res.data.whatsapp_enabled !== false ? "啟用" : "停用";
      const groups = (res.data.whatsapp_group_names || []).join(", ") || "無";
      console.log(`🔄 系統設置已刷新（TG: ${tg} | WA: ${wa} | 群組: ${groups}）`);
      return true;
    }
    return false;
  } catch (err) {
    if (err.response?.status === 401) {
      console.log("🔄 設置刷新時 token 過期，重新登錄...");
      await login();
      if (authToken) {
        return refreshSettings();
      }
    }
    console.log("⚠️ 系統設置刷新失敗：", err.message);
    return false;
  }
}

async function initSettings(maxRetries = 5) {
  for (let i = 0; i < maxRetries; i++) {
    if (await refreshSettings()) {
      console.log(`✅ 系統設置載入成功（嘗試 ${i + 1} 次）`);
      return true;
    }
    console.log(`⏳ 設置載入失敗，2 秒後重試（${i + 1}/${maxRetries}）...`);
    await new Promise(r => setTimeout(r, 2000));
  }
  console.log("❌ 系統設置載入失敗，將使用預設值");
  return false;
}

function getSetting(key, defaultValue) {
  return settingsCache[key] !== undefined ? settingsCache[key] : defaultValue;
}

// ── 貨幣兌換配對（與 bot/payment_parser.py 一致）──
const EXCHANGE_OPTIONS = {
  HKD: [{ from: "CNY", label: "人民幣 → 港幣" }, { from: "USDT", label: "USDT → 港幣" }],
  USD: [{ from: "CNY", label: "人民幣 → 美金" }, { from: "USDT", label: "USDT → 美金" }],
  CNY: [{ from: "USD", label: "美金 → 人民幣" }, { from: "HKD", label: "港幣 → 人民幣" }, { from: "USDT", label: "USDT → 人民幣" }],
};

// 暫存等待代理選擇兌換方式的付款資訊: senderId -> {paymentInfo, agentName, customerName, toCurrency, chat, expireAt}
const pendingExchanges = new Map();
// 每個聊天的最新消息: chatId -> 上一條消息文本
const lastMessages = new Map();

// ── 換匯公式自動推斷輔助函數 ──
function amountsMatch(conversionResult, paymentAmount) {
  const tolerance = Math.max(1, Math.floor(paymentAmount * 0.001));
  return Math.abs(conversionResult - paymentAmount) <= tolerance;
}

function rateWithinThreshold(usedRate, dailyRate, threshold = 0.03) {
  if (!dailyRate || dailyRate <= 0) return false;
  return Math.abs(usedRate - dailyRate) / dailyRate <= threshold;
}

function getYesterdayDate() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().split("T")[0];
}

async function resolveConversion(paymentInfo, prevText, toCurrency) {
  if (!prevText) return null;

  const conv = parseConversionLine(prevText);
  if (!conv) return null;

  // 檢查數學等式: source_amount / rate ≈ result_amount
  let sourceAmount = conv.source_amount;
  let autocorrected = false;
  const expectedResult = conv.rate !== 0 ? sourceAmount / conv.rate : 0;
  if (!amountsMatch(Math.round(expectedResult), conv.result_amount)) {
    // 嘗試補全萬位
    const correctedSource = sourceAmount * 10000;
    const correctedResult = conv.rate !== 0 ? correctedSource / conv.rate : 0;
    if (amountsMatch(Math.round(correctedResult), conv.result_amount)) {
      sourceAmount = correctedSource;
      autocorrected = true;
    } else {
      return null;
    }
  }

  // 驗證 result_amount 與付款 amount 是否匹配
  if (!amountsMatch(conv.result_amount, paymentInfo.amount)) return null;

  // 獲取今日匯率
  const today = new Date().toISOString().split("T")[0];
  const rates = await getExchangeRates(today);
  const dailyRateMap = {};
  for (const r of (rates || [])) {
    dailyRateMap[`${r.from_currency}:${r.to_currency}`] = r.rate;
  }

  // 獲取昨日匯率（作為 CNY 推斷的備選）
  const yesterday = getYesterdayDate();
  const yesterdayRates = await getExchangeRates(yesterday);
  const yesterdayRateMap = {};
  for (const r of (yesterdayRates || [])) {
    yesterdayRateMap[`${r.from_currency}:${r.to_currency}`] = r.rate;
  }

  // 獲取預設匯率
  const presetRates = getSetting("preset_exchange_rates", {});
  if (typeof presetRates !== "object") presetRates = {};

  // 遍歷所有可能的 (source, target) 組合，找最接近的參考匯率
  const candidates = EXCHANGE_OPTIONS[conv.result_currency] || [];
  let bestMatch = null; // { from, referenceRate, rateSource, pctDiff }

  for (const candidate of candidates) {
    const pair = `${candidate.from}:${conv.result_currency}`;
    let referenceRate = null;
    let rateSource = null;

    if (dailyRateMap[pair] !== undefined) {
      referenceRate = dailyRateMap[pair];
      rateSource = "daily";
    } else if (yesterdayRateMap[pair] !== undefined) {
      referenceRate = yesterdayRateMap[pair];
      rateSource = "previous_day";
    } else if (presetRates[pair] !== undefined) {
      referenceRate = presetRates[pair];
      rateSource = "preset";
    }

    if (referenceRate && referenceRate > 0) {
      const pctDiff = Math.abs(conv.rate - referenceRate) / referenceRate;
      if (!bestMatch || pctDiff < bestMatch.pctDiff) {
        bestMatch = { from: candidate.from, referenceRate, rateSource, pctDiff };
      }
    }
  }

  // 最佳匹配在 3% 閾值內 → 自動推斷
  if (bestMatch && bestMatch.pctDiff <= 0.03) {
    const conversionInfo = {
      source_amount: sourceAmount,
      rate: conv.rate,
      source_currency: bestMatch.from,
      matched: true,
      daily_rate: bestMatch.referenceRate,
      rate_source: bestMatch.rateSource,
    };
    if (autocorrected) {
      conversionInfo.autocorrected = true;
    }
    const wanNote = autocorrected
      ? `（已自動補全萬位 ${conv.source_amount.toLocaleString()}→${sourceAmount.toLocaleString()}）`
      : "";
    const label = candidates.find(o => o.from === bestMatch.from)?.label || bestMatch.from;
    return {
      auto_inferred: true,
      from_currency: bestMatch.from,
      conversion: conversionInfo,
      note: `📐 從換匯公式 ${conv.result_currency} ${conv.rate} 自動推斷為 ${bestMatch.from}（${label}）${wanNote}`,
    };
  }

  // 無匹配 → 提示手動選擇
  const bestDailyStr = bestMatch
    ? `${bestMatch.referenceRate.toFixed(3)}（差 ${(bestMatch.pctDiff * 100).toFixed(1)}%）`
    : "無可用參考匯率";
  const bestPairLabel = bestMatch
    ? `${bestMatch.from}→${conv.result_currency}`
    : "無";
  const conversionInfo = {
    source_amount: sourceAmount,
    rate: conv.rate,
    source_currency: "CNY",
    matched: false,
  };
  if (autocorrected) {
    conversionInfo.autocorrected = true;
  }
  const wanNote = autocorrected
    ? `（已自動補全萬位 ${conv.source_amount.toLocaleString()}→${sourceAmount.toLocaleString()}）`
    : "";
  return {
    auto_inferred: false,
    from_currency: null,
    conversion: conversionInfo,
    note: `📐 檢測到換匯公式 ${sourceAmount.toLocaleString()} / ${conv.rate} = ${conv.result_amount.toLocaleString()} ${conv.result_currency}，最佳匹配 ${bestPairLabel} (${bestDailyStr}) 超過 3% 閾值，請手動選擇${wanNote}`,
  };
}

// ── 交易格式範本 ──
const FORMAT_EXAMPLE = `📋 交易信息格式範例（已填寫）：

收款銀行：Citibank, N.A. Hong Kong Branch
收款銀行SWIFT代號：CITIHKHXXXX
銀行地址：Champion Tower, Three Garden Road, Central, Hong Kong
收款人名字：CHAN TAI MAN
銀行代碼：006
收款人帳號：391-17721113
金額：16888 USD

--- 以下為可選項 ---
備註：G12345678
投保人：陳大文`;

const FORMAT_TEMPLATE = `📋 交易信息格式（請複製並填寫）：

收款銀行：
收款銀行SWIFT代號：
銀行地址：
收款人名字：
銀行代碼：
收款人帳號：
金額：

--- 以下為可選項 ---
備註：
投保人：`;

const FORMAT_CONVERSION_HINT = `💡 發送提示：
請先發送換匯公式（如：50w / 7.01 = 71,023 USD），
再發送上述交易信息。兩條消息請分開發送。`;

const FORMAT_FULL = FORMAT_CONVERSION_HINT + "\n\n" + FORMAT_EXAMPLE + "\n\n" + FORMAT_TEMPLATE;

// ==================== API 客戶端 ====================
let authToken = null;

async function login() {
  try {
    const params = new URLSearchParams();
    params.append("username", API_USERNAME);
    params.append("password", API_PASSWORD);

    const res = await axios.post(`${API_BASE_URL}/auth/login`, params);
    authToken = res.data.access_token;
    console.log("✅ 後端API登錄成功");
  } catch (err) {
    console.error("❌ 後端API登錄失敗：", err.message);
  }
}

function getHeaders() {
  return { Authorization: `Bearer ${authToken}` };
}

async function createTransaction(data) {
  try {
    const payload = {
      agent_name: data.agent_name,
      customer_name: data.customer_name || "",
      amount: data.amount,
      currency: data.currency || "USD",
      from_currency: data.from_currency || "",
      to_currency: data.to_currency || "",
      remarks: data.remarks || "",
      insured_person: data.insured_person || "",
      raw_message: data.raw_message || null,
      source: data.source || "whatsapp",
      payment_details: data.payment_details || null
    };
    const res = await axios.post(`${API_BASE_URL}/transactions/`, payload, {
      headers: getHeaders(),
    });
    return res.status === 200;
  } catch (err) {
    if (err.response?.status === 401) {
      await login();
      return createTransaction(data);
    }
    console.error("❌ 創建交易失敗：", err.response?.data || err.message);
    return false;
  }
}

async function isAgentAllowed(agentName) {
  try {
    const res = await axios.get(
      `${API_BASE_URL}/agents/${encodeURIComponent(agentName)}`,
      { headers: getHeaders() }
    );
    return res.data.is_active === true;
  } catch (err) {
    if (err.response?.status === 401) {
      await login();
      return isAgentAllowed(agentName);
    }
    return false;
  }
}

async function saveExchangeRate(data) {
  try {
    const payload = {
      date: data.date,
      from_currency: data.from_currency,
      to_currency: data.to_currency,
      rate: data.rate,
      source: "POBO-MSO"
    };
    const res = await axios.post(`${API_BASE_URL}/exchange-rates/`, payload, {
      headers: getHeaders(),
    });
    return res.status === 200;
  } catch (err) {
    if (err.response?.status === 401) {
      await login();
      return saveExchangeRate(data);
    }
    console.error("❌ 儲存匯率失敗：", err.response?.data || err.message);
    return false;
  }
}

async function getExchangeRates(date) {
  try {
    const params = date ? `?date=${encodeURIComponent(date)}` : "";
    const res = await axios.get(`${API_BASE_URL}/exchange-rates/${params}`, {
      headers: getHeaders(),
    });
    return res.data || [];
  } catch (err) {
    if (err.response?.status === 401) {
      await login();
      return getExchangeRates(date);
    }
    console.error("❌ 獲取匯率失敗：", err.message);
    return [];
  }
}

// ── 匯率訊息解析 ──
const RE_DATE = /^(\d{1,2})\/(\d{1,2})\s*\/?\s*(\d{4})$/;
const RE_RATE_LINE = /人[兌兑](美|港)\s*\(POBO-MSO\)\s*:\s*([\d.]+)/;
const CURRENCY_MAP = { "美": "USD", "港": "HKD" };

function parseExchangeRates(text) {
  if (!text || !text.trim()) return null;

  const lines = text.split(/\r?\n/);
  // 跳過開頭空白行
  let i = 0;
  while (i < lines.length && lines[i].trim() === "") i++;
  if (i >= lines.length) return null;

  // 第一行非空行必須是日期
  const dateMatch = lines[i].trim().match(RE_DATE);
  if (!dateMatch) return null;
  const day = dateMatch[1].padStart(2, "0");
  const month = dateMatch[2].padStart(2, "0");
  const year = dateMatch[3];
  const dateStr = `${year}-${month}-${day}`;

  // 掃描後續行，收集 POBO-MSO 匯率
  const rates = [];
  for (let j = i + 1; j < lines.length; j++) {
    const m = lines[j].match(RE_RATE_LINE);
    if (m) {
      rates.push({
        to_currency: CURRENCY_MAP[m[1]],
        rate: parseFloat(m[2])
      });
    }
  }

  if (rates.length === 0) return null;
  return { date: dateStr, rates };
}

// ==================== 消息解析（與 parser.py 邏輯一致）====================

// 常見干擾詞
const NOISE_WORDS = [
  "剛剛", "刚刚", "刚", "剛", "客戶", "客户", "通過", "通过", "一單", "一单",
  "已完成", "已完成交易", "完成一筆", "已完成一筆", "交易完成",
  "恭喜", "祝賀",
];

function tryParseAmount(str) {
  const s = str.replace(/,/g, "").trim();
  const n = parseFloat(s);
  return Number.isInteger(n) && n > 0 ? n : null;
}

function normalizeText(text) {
  let t = text.trim();
  t = t.replace(/，/g, " ").replace(/,/g, "");
  t = t.replace(/：/g, ":").replace(/＝/g, "=");
  t = t.replace(/（/g, "(").replace(/）/g, ")");
  t = t.replace(/【/g, "[").replace(/】/g, "]");
  t = t.replace(/HKD/gi, "").replace(/元/g, "").replace(/塊/g, "");
  t = t.replace(/@\S+/g, "");  // 移除 WhatsApp @提及（含電話號碼）
  t = t.replace(/\s+/g, " ");
  // 移除干擾詞
  for (const w of NOISE_WORDS) {
    t = t.replace(w, "");
  }
  // 移除 emoji
  t = t.replace(/[\u{1F600}-\u{1FAFF}\u{2600}-\u{27BF}\u{2300}-\u{23FF}\u{FE00}-\u{FEFF}]/gu, "");
  return t.replace(/\s+/g, " ").trim();
}

function parseTransaction(messageText) {
  if (!messageText || !messageText.trim()) return null;

  const raw = messageText.trim();
  const text = normalizeText(raw);

  // ── 階段 1：代理名在前、金額在後的明確模式 ──
  const agentFirstPatterns = [
    // 【成交】代理A 交易 1000
    /[\[【]交易[\]】]\s*(.+?)\s+交易\s+([\d,]+(?:\.\d+)?)/,
    // 成交：代理A 1000
    /交易[:：]\s*(.+?)\s+([\d,]+(?:\.\d+)?)/,
    // 代理A 今日交易 1000
    /(.+?)\s+今日交易\s+([\d,]+(?:\.\d+)?)/,
    // 代理A 成交/交易 1000
    /(.+?)\s+(?:成交|交易)\s+([\d,]+(?:\.\d+)?)/,
    // 代理A成交1000（無空格）
    /(.+?)(?:成交|交易)([\d,]+(?:\.\d+)?)/,
    // 代理A 入金/出金/盈利/收益 1000
    /(.+?)\s+(?:入金|出金|盈利|盈餘|收益)\s*([\d,]+(?:\.\d+)?)/,
    // 代理A 完成/做了/處理 1000
    /(.+?)\s+(?:完成|做了|處理)\s+([\d,]+(?:\.\d+)?)/,

    // --- 英文 ---
    // AgentA closed/finished/made/done/completed 1000
    /(.+?)\s+(?:closed|finished|made|done|completed)\s+(?:a\s+)?(?:deal\s+)?(?:of\s+)?(?:for\s+)?([\d,]+(?:\.\d+)?)/i,
    // Transaction: AgentA 1000
    /[Tt]ransaction[:：]\s*(.+?)\s+([\d,]+(?:\.\d+)?)/,
    // AgentA transaction 1000
    /(.+?)\s+transaction\s+([\d,]+(?:\.\d+)?)/i,

    // --- 分隔符 ---
    // 代理A：1000 / 代理A=1000
    /(.+?)\s*[:：=]\s*([\d,]+(?:\.\d+)?)/,
    // 代理A +1000
    /(.+?)\s*[＋+]\s*([\d,]+(?:\.\d+)?)/,
    // 代理A → 1000
    /(.+?)\s*[→>]\s*([\d,]+(?:\.\d+)?)/,
  ];

  for (const pattern of agentFirstPatterns) {
    const match = text.match(pattern);
    if (match) {
      const agent = match[1].trim();
      const amount = tryParseAmount(match[2]);
      if (agent && amount && agent.length >= 1 && agent.length <= 50) {
        return { customer_name: agent, amount, raw_message: raw, source: "whatsapp" };
      }
    }
  }

  // ── 階段 2：金額在前的句式 ──
  const amountFirstPatterns = [
    // 1000 代理A
    /([\d,]+(?:\.\d+)?)\s*(?:元|塊)?\s+(.+?)$/,
    // 1000 from/by/via AgentA
    /([\d,]+(?:\.\d+)?)\s+(?:from|by|via)\s+(.+?)$/i,
    // 金額/成交金额 1000 代理A
    /(?:金額|成交金額)\s*([\d,]+(?:\.\d+)?)\s+(.+?)$/,
    // Amount 1000 AgentA
    /[Aa]mount\s*([\d,]+(?:\.\d+)?)\s+(.+?)$/,
  ];

  for (const pattern of amountFirstPatterns) {
    const match = text.match(pattern);
    if (match) {
      const amount = tryParseAmount(match[1]);
      const agent = match[2].trim();
      // 避免代理名位置抓到純數字
      if (agent && amount && !/^[\d,.]+$/.test(agent) && agent.length >= 1 && agent.length <= 50) {
        return { customer_name: agent, amount, raw_message: raw, source: "whatsapp" };
      }
    }
  }

  // ── 階段 3：寬鬆匹配（最後防線）──
  const looseMatch = text.match(/(.{2,30}?)\s+([\d,]{2,}(?:\.\d+)?)\s*$/);
  if (looseMatch) {
    let agent = looseMatch[1].trim().replace(/[:：=+\->]+$/, "");
    const amount = tryParseAmount(looseMatch[2]);
    const noise = ["今日", "昨天", "本週", "本月", "月", "日", "號", "today", "daily", "total"];
    if (agent && amount && !noise.includes(agent.toLowerCase()) && agent.length >= 1 && agent.length <= 50) {
      return { customer_name: agent, amount, raw_message: raw, source: "whatsapp" };
    }
  }

  return null;
}

// ==================== 取消指令解析 ====================

const CANCEL_KEYWORDS = [
  "取消", "撤銷", "撤销", "刪除", "删除", "移除",
  "undo", "cancel", "remove", "delete", "revert",
];

function parseCancellation(messageText) {
  if (!messageText || !messageText.trim()) return null;

  const text = messageText.trim().toLowerCase();
  let matchedKw = null;
  for (const kw of CANCEL_KEYWORDS) {
    if (text.startsWith(kw.toLowerCase())) {
      matchedKw = kw;
      break;
    }
  }
  if (!matchedKw) return null;

  const remainder = text.slice(matchedKw.length).trim();

  // "取消" 單獨使用 → 取消上一筆
  if (!remainder || ["上一筆", "上一笔", "上一单", "上一單", "last", "上一條", "上一"].includes(remainder)) {
    return { action: "cancel", target: "last" };
  }

  // 嘗試提取代理名 + 可選金額
  const parsed = parseTransaction("【交易】" + remainder + " 交易 0");
  // 從剩餘文字提取代理名
  const rawRemainder = messageText.trim().slice(matchedKw.length).trim();

  // 嘗試從剩餘文字提取金額資訊
  let agent = null;
  let amount = null;

  // 如果 remainder 裡有數字，嘗試用 parseTransaction 提取
  const tempParsed = parseTransaction(remainder + " 交易 1");
  if (tempParsed) {
    agent = tempParsed.customer_name || tempParsed.agent_name;
    // 再嘗試提取金額
    const amountMatch = remainder.match(/([\d,]+(?:\.\d+)?)/);
    if (amountMatch) {
      amount = tryParseAmount(amountMatch[1]);
    }
  } else if (rawRemainder && !/[\d]/.test(rawRemainder) && rawRemainder.length <= 50) {
    agent = rawRemainder;
  }

  if (agent && amount) {
    return { action: "cancel", target: "specific", agent_name: agent, amount };
  }
  if (agent) {
    return { action: "cancel", target: "agent", agent_name: agent };
  }

  return null;
}

// ==================== API 輔助：交易刪除 ====================

async function getLastTransaction(agentName) {
  try {
    const params = new URLSearchParams();
    params.set("source", "whatsapp");
    if (agentName) params.set("agent_name", agentName);
    const res = await axios.get(`${API_BASE_URL}/transactions/last?${params.toString()}`, { headers: getHeaders() });
    return res.data;
  } catch (err) {
    if (err.response?.status === 401) { await login(); return getLastTransaction(agentName); }
    return null;
  }
}

async function deleteTransactionById(txId) {
  try {
    await axios.delete(`${API_BASE_URL}/transactions/${txId}`, { headers: getHeaders() });
    return true;
  } catch (err) {
    if (err.response?.status === 401) { await login(); return deleteTransactionById(txId); }
    return false;
  }
}

// ── 客戶訂單 API ──
async function createCustomerOrder(data) {
  try {
    const res = await axios.post(`${API_BASE_URL}/orders/`, data, { headers: getHeaders() });
    return res.data;
  } catch (err) {
    if (err.response?.status === 401) { await login(); return createCustomerOrder(data); }
    console.error("❌ 創建客戶訂單失敗：", err.response?.data || err.message);
    return null;
  }
}

async function getDailyOrders(date) {
  try {
    const res = await axios.get(`${API_BASE_URL}/orders/daily?date=${encodeURIComponent(date)}`, { headers: getHeaders() });
    return res.data || [];
  } catch (err) {
    if (err.response?.status === 401) { await login(); return getDailyOrders(date); }
    console.error("❌ 獲取當日訂單失敗：", err.response?.data || err.message);
    return [];
  }
}

async function getUnmatchedOrders() {
  try {
    const res = await axios.get(`${API_BASE_URL}/orders/unmatched`, { headers: getHeaders() });
    return res.data;
  } catch (err) {
    if (err.response?.status === 401) { await login(); return getUnmatchedOrders(); }
    console.error("❌ 獲取未匹配訂單失敗：", err.response?.data || err.message);
    return [];
  }
}

async function getOrderByReminderMessage(messageId) {
  try {
    const res = await axios.get(`${API_BASE_URL}/orders/by-reminder/${encodeURIComponent(messageId)}`, { headers: getHeaders() });
    return res.data;
  } catch (err) {
    if (err.response?.status === 404) return null;
    if (err.response?.status === 401) { await login(); return getOrderByReminderMessage(messageId); }
    return null;
  }
}

async function updateOrderStatus(orderId, status) {
  try {
    await axios.put(`${API_BASE_URL}/orders/${orderId}/status`, { status }, { headers: getHeaders() });
    return true;
  } catch (err) {
    if (err.response?.status === 401) { await login(); return updateOrderStatus(orderId, status); }
    return false;
  }
}

async function updateOrderReminderSent(orderId, reminderMessageId) {
  try {
    await axios.put(`${API_BASE_URL}/orders/${orderId}/reminder-sent`, { reminder_message_id: reminderMessageId }, { headers: getHeaders() });
    return true;
  } catch (err) {
    if (err.response?.status === 401) { await login(); return updateOrderReminderSent(orderId, reminderMessageId); }
    return false;
  }
}

// ── 漏單提醒排程 ──
let lastReminderDate = null;

function parseHKTime(timeObj) {
  // timeObj: {hour: 17, minute: 30} 為 HKT
  return { hour: timeObj.hour || 17, minute: timeObj.minute || 30 };
}

async function sendReminderIfTime() {
  try {
    const reminderTime = getSetting("reminder_time", { hour: 17, minute: 30 });
    const { hour, minute } = parseHKTime(reminderTime);

    const now = new Date();
    const hkNow = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Hong_Kong" }));
    const todayStr = `${hkNow.getFullYear()}-${String(hkNow.getMonth() + 1).padStart(2, "0")}-${String(hkNow.getDate()).padStart(2, "0")}`;

    if (hkNow.getHours() !== hour || hkNow.getMinutes() !== minute) return;
    if (lastReminderDate === todayStr) return;

    console.log(`⏰ 觸發漏單提醒（${todayStr} ${hour}:${String(minute).padStart(2, "0")} HKT）`);
    lastReminderDate = todayStr;

    const reminderGroupName = getSetting("reminder_group_name", null);
    if (!reminderGroupName) {
      console.log("   ⚠️ 未設定提醒群組名稱");
      return;
    }

    const chats = await client.getChats();
    const reminderChat = chats.find(c => c.name === reminderGroupName && c.isGroup);
    if (!reminderChat) {
      console.log(`   ⚠️ 找不到提醒群組「${reminderGroupName}」`);
      return;
    }

    const orders = await getUnmatchedOrders();
    if (!orders || orders.length === 0) {
      console.log("   ✅ 今日無漏單");
      await reminderChat.sendMessage("✅ 今日無漏單 🎉（定時消息）");
      return;
    }

    console.log(`   📋 發送 ${orders.length} 筆漏單提醒到「${reminderGroupName}」`);

    for (const order of orders) {
      try {
        const orderDate = order.created_at
          ? new Date(order.created_at).toLocaleDateString("zh-HK", { timeZone: "Asia/Hong_Kong" })
          : "未知日期";
        const msg = await reminderChat.sendMessage(
          `📋 漏單提醒：${order.customer_name} ¥${order.amount.toLocaleString()}（${orderDate}）\n` +
          `請回覆處理狀態（直接回覆此消息）：\n` +
          `1=已處理  2=未處理  3=忽略`
        );
        await updateOrderReminderSent(order.id, msg.id._serialized);
        console.log(`      ✅ 已發送：${order.customer_name}（order #${order.id}, msg ${msg.id._serialized}）`);
        // 短暫延遲避免發送過快
        await new Promise(r => setTimeout(r, 1500));
      } catch (e) {
        console.error(`      ❌ 發送失敗：${order.customer_name}`, e.message);
      }
    }
  } catch (err) {
    console.error("❌ 漏單提醒檢查失敗：", err.message);
  }
}

// ==================== WhatsApp 客戶端 ====================
const fs = require("fs");
const path = require("path");
const PID_FILE = path.join(__dirname, "wa_bot.pid");

// 寫入 PID 檔案
fs.writeFileSync(PID_FILE, String(process.pid));

// 清理 PID 檔案
function cleanupPidFile() {
  try { if (fs.existsSync(PID_FILE)) fs.unlinkSync(PID_FILE); } catch (_) {}
}

process.on("exit", cleanupPidFile);
process.on("SIGTERM", () => { cleanupPidFile(); process.exit(); });
process.on("SIGINT", () => { cleanupPidFile(); process.exit(); });

const client = new Client({
  // LocalAuth 會將登錄狀態保存到本地，重啟後不需要重新掃碼
  authStrategy: new LocalAuth({ clientId: "wa-bot" }),
  puppeteer: {
    // Mac 上 Chromium 路徑，whatsapp-web.js 會自動處理
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  },
});

// 顯示二維碼（首次登錄需要掃碼）
client.on("qr", (qr) => {
  console.log("\n📱 請用手機 WhatsApp 掃描以下二維碼登錄：\n");
  qrcode.generate(qr, { small: true });
});

// 登錄成功
client.on("ready", async () => {
  console.log("✅ WhatsApp Bot 已就緒！");
  console.log(`📌 監控群組：${WATCH_GROUP_NAMES.join(", ") || "（未設置）"}`);
  await login(); // 登錄後端API
  await initSettings(); // 載入系統設置（含重試）
  // 每 60 秒刷新設置
  setInterval(refreshSettings, 60 * 1000);
  // 每 60 秒檢查漏單提醒
  setInterval(sendReminderIfTime, 60 * 1000);
});

// 監聽所有消息
client.on("message", async (msg) => {
  // 第一關：確認事件有觸發（任何消息都會打印）
  console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  console.log("📩 收到消息");
  console.log("   from:", msg.from);
  console.log("   body:", msg.body);
  console.log("   是否群組消息:", msg.from.endsWith("@g.us"));

  // 第二關：確認群組名稱
  if (msg.from.endsWith("@g.us")) {
    const chat = await msg.getChat();
    console.log("   群組名稱:", `「${chat.name}」`);
    console.log("   監控列表:", WATCH_GROUP_NAMES);
    console.log(
      "   名稱是否匹配:",
      WATCH_GROUP_NAMES.length === 0 || WATCH_GROUP_NAMES.includes(chat.name)
    );
  }

  // 以下保持原有邏輯不變
  // ── 格式範例請求（私聊和群組都可用）──
  const msgText = msg.body.trim();
  if (msgText === "/format" || msgText === "/Format") {
    if (WA_SEND_REPLY) { await msg.reply(FORMAT_FULL); }
    return;
  }

  // ── @mention 客戶訂單檢測（群組消息）──
  // WhatsApp body 中 @mention 顯示為 @phone_number，需用 mentionedIds 判斷
  if (msg.from.endsWith("@g.us") && msg.mentionedIds && msg.mentionedIds.length > 0) {
    // 移除所有 @mention（格式為 @phone_number），再逐行匹配訂單
    const afterMention = msgText.replace(/@\S+/g, "").trim();
    const lines = afterMention.split(/\r?\n/);
    const ORDER_LINE_RE = /^(.+?)\s+(?:需要(?:戶口|户口)?|需|要)?\s*(?:[￥¥$€£])?(\d[\d,]*(?:w|万|萬)?)/i;
    const parsedOrders = [];

    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line) continue;
      const m = line.match(ORDER_LINE_RE);
      if (!m) continue;
      const customerName = m[1].trim();
      const amountStr = m[2];
      let amount = parseInt(amountStr.replace(/,/g, ""), 10);
      if (/[w万萬]$/i.test(amountStr)) {
        amount = amount * 10000;
      }
      if (amount > 0 && customerName.length >= 1) {
        parsedOrders.push({ customerName, amount });
      }
    }

    if (parsedOrders.length > 0) {
      // 查詢當天已有訂單，建立去重 set
      const today = new Date().toISOString().split("T")[0];
      const todayOrders = await getDailyOrders(today);
      const existingSet = new Set(todayOrders.map(o => `${o.customer_name}:${o.amount}`));

      console.log(`📋 檢測到 ${parsedOrders.length} 筆客戶訂單`);
      const results = [];
      let skipped = 0;
      for (const o of parsedOrders) {
        const key = `${o.customerName}:${o.amount}`;
        if (existingSet.has(key)) {
          console.log(`   ⏭️ 跳過重複：${o.customerName} ¥${o.amount.toLocaleString()}`);
          skipped++;
          continue;
        }
        try {
          const order = await createCustomerOrder({
            customer_name: o.customerName,
            amount: o.amount,
            currency: "CNY",
            group_id: msg.from,
            message_timestamp: new Date().toISOString(),
            raw_message: msgText
          });
          if (order) {
            results.push(`• ${o.customerName} ¥${o.amount.toLocaleString()}`);
            console.log(`   ✅ ${o.customerName} ¥${o.amount.toLocaleString()}`);
          }
        } catch (e) {
          console.error(`   ❌ ${o.customerName} 記錄失敗：`, e.message);
        }
      }
      if (results.length > 0 && WA_SEND_REPLY) {
        const prefix = results.length === 1 ? "✅ 已記錄客戶訂單：" : `✅ 已記錄 ${results.length} 筆客戶訂單：`;
        const suffix = skipped > 0 ? `\n（跳過 ${skipped} 筆重複）` : "";
        await msg.reply(prefix + "\n" + results.join("\n") + suffix);
      } else if (results.length === 0 && skipped > 0 && WA_SEND_REPLY) {
        await msg.reply(`⚠️ ${skipped} 筆訂單皆為當天重複，未新增記錄`);
      }
      return;
    }
  }

  // ── 匯率訊息檢測（需 Bot 啟用，不限群組）──
  if (msg.from.endsWith("@g.us") && getSetting("whatsapp_enabled", true) !== false) {
    const exchangeRates = parseExchangeRates(msgText);
    if (exchangeRates) {
      console.log(`💱 檢測到匯率訊息：${exchangeRates.date}，${exchangeRates.rates.length} 組匯率`);
      let savedCount = 0;
      for (const r of exchangeRates.rates) {
        const ok = await saveExchangeRate({
          date: exchangeRates.date,
          from_currency: "CNY",
          to_currency: r.to_currency,
          rate: r.rate
        });
        if (ok) savedCount++;
        console.log(`   ${ok ? "✅" : "❌"} CNY → ${r.to_currency}: ${r.rate}`);
      }
      if (savedCount > 0 && WA_SEND_REPLY) {
        let confirmMsg = `✅ 已記錄今日匯率 (${exchangeRates.date})：`;
        for (const r of exchangeRates.rates) {
          confirmMsg += `\nCNY → ${r.to_currency}: ${r.rate}`;
        }
        await msg.reply(confirmMsg);
      }
      return;
    }
  }

  if (!msg.from.endsWith("@g.us")) return;

  // ── 檢查 WhatsApp Bot 是否啟用 ──
  if (getSetting("whatsapp_enabled", true) === false) return;

  const chat = await msg.getChat();
  const groupName = chat.name;

  // 優先使用系統設置中的群組列表，若無則回退至 .env
  const groupNames = getSetting("whatsapp_group_names", null) || WATCH_GROUP_NAMES;
  if (
    groupNames.length > 0 &&
    !groupNames.includes(groupName)
  ) {
    console.log(`   ⚠️ 群組「${groupName}」不在監控列表，已跳過`);
    return;
  }

  const text = msg.body;

  const senderId = msg.author;
  const senderDisplayName = (msg._data && msg._data.notifyName) || senderId;

  // ── 讀取並更新聊天消息歷史 ──
  const prevText = lastMessages.get(msg.from) || null;
  lastMessages.set(msg.from, text);

  // ── 換匯公式後發匹配：若當前消息是換匯公式，檢查是否有待處理的兌換 ──
  const trailingConv = parseConversionLine(text);
  if (trailingConv) {
    const pendingByFormula = pendingExchanges.get(senderId);
    if (pendingByFormula && amountsMatch(trailingConv.result_amount, pendingByFormula.paymentInfo.amount)) {
      const conversionResult = await resolveConversion(pendingByFormula.paymentInfo, text, pendingByFormula.toCurrency);
      if (conversionResult && conversionResult.auto_inferred) {
        pendingExchanges.delete(senderId);
        const pd = pendingByFormula.paymentInfo.payment_details_dict || {};
        if (conversionResult.conversion) {
          pd.conversion = conversionResult.conversion;
          pendingByFormula.paymentInfo.payment_details = JSON.stringify(pd);
        }
        let replyMsg = `✅ 已檢測付款：${pendingByFormula.customerName}\n金額：${pendingByFormula.paymentInfo.amount.toLocaleString()} ${pendingByFormula.toCurrency}`;
        if (pd.bank_name) replyMsg += `\n銀行：${pd.bank_name}`;
        if (pd.account_number) replyMsg += `\n戶口：${pd.account_number}`;
        replyMsg += `\n${conversionResult.note}`;
        if (WA_SEND_REPLY) { await msg.reply(replyMsg); }
        await createTransaction({
          agent_name: pendingByFormula.agentName, customer_name: pendingByFormula.customerName,
          amount: pendingByFormula.paymentInfo.amount, currency: pendingByFormula.paymentInfo.currency,
          raw_message: pendingByFormula.paymentInfo.raw_message, source: "whatsapp",
          payment_details: pendingByFormula.paymentInfo.payment_details,
          from_currency: "CNY",
          to_currency: pendingByFormula.toCurrency,
          remarks: pendingByFormula.paymentInfo.remarks || "",
          insured_person: pendingByFormula.paymentInfo.insured_person || ""
        });
        console.log(`💾 付款資訊已記錄（公式後發自動推斷 CNY→${pendingByFormula.toCurrency}，代理: ${pendingByFormula.agentName}, 客戶: ${pendingByFormula.customerName}）`);
        return;
      }
    }
  }

  // ── 漏單提醒回覆處理 ──
  if (msg.from.endsWith("@g.us") && msg.hasQuotedMsg) {
    try {
      const quotedMsg = await msg.getQuotedMessage();
      if (quotedMsg && quotedMsg.id && quotedMsg.id._serialized) {
        const order = await getOrderByReminderMessage(quotedMsg.id._serialized);
        if (order) {
          const num = parseInt(text.trim());
          const statusMap = { 1: "processed", 2: "unprocessed", 3: "ignored" };
          const status = statusMap[num];
          if (status) {
            const ok = await updateOrderStatus(order.id, status);
            if (ok) {
              const statusText = { processed: "已處理", unprocessed: "未處理", ignored: "忽略" };
              console.log(`📋 漏單回覆：${order.customer_name} → ${statusText[status]}`);
              if (WA_SEND_REPLY) {
                await msg.reply(`✅ 已更新：${order.customer_name} - ${statusText[status]}`);
              }
            }
          }
          return;
        }
      }
    } catch (e) {
      // hasQuotedMsg 但無法獲取引用消息，忽略
    }
  }

  // ── 檢查是否有待處理的兌換方式選擇 ──
  const pending = pendingExchanges.get(senderId);
  if (pending) {
    const num = parseInt(text.trim());
    const options = EXCHANGE_OPTIONS[pending.toCurrency] || [];
    pendingExchanges.delete(senderId);

    if (isNaN(num) || num < 1 || num > options.length) {
      // 取消或無效輸入
      if (WA_SEND_REPLY) {
        await msg.reply(`❌ 已取消記錄：${pending.customerName} ${pending.paymentInfo.amount.toLocaleString()} ${pending.toCurrency}`);
      }
      return;
    }

    const chosen = options[num - 1];

    // 注入換匯信息到 payment_details
    if (pending.conversionInfo) {
      const pd = pending.paymentInfo.payment_details_dict || {};
      pd.conversion = pending.conversionInfo;
      pending.paymentInfo.payment_details = JSON.stringify(pd);
    }

    const success = await createTransaction({
      agent_name: pending.agentName,
      customer_name: pending.customerName,
      amount: pending.paymentInfo.amount,
      currency: pending.paymentInfo.currency,
      raw_message: pending.paymentInfo.raw_message,
      source: "whatsapp",
      payment_details: pending.paymentInfo.payment_details,
      from_currency: chosen.from,
      to_currency: pending.toCurrency,
      remarks: pending.paymentInfo.remarks || "",
      insured_person: pending.paymentInfo.insured_person || ""
    });

    if (success) {
      console.log(`💾 付款資訊已記錄（${pending.agentName}, ${pending.customerName}, ${chosen.from}→${pending.toCurrency}）`);
      if (WA_SEND_REPLY) {
        let replyMsg = `✅ 已紀錄收款：${pending.customerName}\n兌換：${chosen.from} → ${pending.toCurrency}\n金額：${pending.paymentInfo.amount.toLocaleString()} ${pending.toCurrency}`;
        if (pending.paymentInfo.remarks) replyMsg += `\n備註：${pending.paymentInfo.remarks}`;
        if (pending.paymentInfo.insured_person) replyMsg += `\n投保人：${pending.paymentInfo.insured_person}`;
        await msg.reply(replyMsg);
      }
    }
    return;
  }

  // ── 優先檢查是否為結構化付款資訊 ──
  const paymentInfo = parsePaymentInfo(text);
  if (paymentInfo) {
    const customerName = paymentInfo.customer_name || "Unknown";
    console.log(`🏦 檢測到付款資訊: 客戶=${customerName} ${paymentInfo.amount} ${paymentInfo.currency}`);
    const warnings = paymentInfo.warnings || [];
    const hasErrors = warnings.some(w => w.startsWith("❌"));
    const hasWarnings = warnings.some(w => w.startsWith("⚠️"));

    // ── 有嚴重錯誤（缺必填欄位）→ 阻擋記錄，只回報錯誤 ──
    if (hasErrors) {
      if (WA_SEND_REPLY) {
        await msg.reply("❌ 付款資訊不完整，請修正後重新發送：\n\n" + warnings.join("\n") + "\n\n輸入 /format 獲取格式範例");
      }
      return;
    }

    if (paymentInfo.amount > 0 && customerName !== "Unknown") {
      const toCurrency = (paymentInfo.currency || "HKD").toUpperCase();
      const options = EXCHANGE_OPTIONS[toCurrency];

      const pd = paymentInfo.payment_details_dict || {};
      let replyMsg = `✅ 已檢測付款：${customerName}\n金額：${paymentInfo.amount.toLocaleString()} ${toCurrency}`;
      if (pd.bank_name) replyMsg += `\n銀行：${pd.bank_name}`;
      if (pd.account_number) replyMsg += `\n戶口：${pd.account_number}`;

      // ── 嘗試從前一條消息自動推斷換匯來源 ──
      const conversionResult = await resolveConversion(paymentInfo, prevText, toCurrency);

      if (conversionResult && conversionResult.auto_inferred) {
        // 自動推斷成功，跳過兌換選單直接記錄
        if (conversionResult.conversion) {
          pd.conversion = conversionResult.conversion;
          paymentInfo.payment_details = JSON.stringify(pd);
        }
        replyMsg += `\n${conversionResult.note}`;
        if (hasWarnings) replyMsg += "\n\n⚠️ 請注意：\n" + warnings.join("\n");
        if (WA_SEND_REPLY) { await msg.reply(replyMsg); }
        await createTransaction({
          agent_name: senderDisplayName, customer_name: customerName,
          amount: paymentInfo.amount, currency: paymentInfo.currency,
          raw_message: paymentInfo.raw_message, source: "whatsapp",
          payment_details: paymentInfo.payment_details,
          from_currency: "CNY",
          to_currency: toCurrency,
          remarks: paymentInfo.remarks || "",
          insured_person: paymentInfo.insured_person || ""
        });
        console.log(`💾 付款資訊已記錄（自動推斷 CNY→${toCurrency}，代理: ${senderDisplayName}, 客戶: ${customerName}）`);
      } else if (options && options.length > 0) {
        // 有兌換選項，發送文字選單讓代理回覆數字
        if (conversionResult && conversionResult.note) {
          replyMsg += `\n${conversionResult.note}`;
        }
        replyMsg += "\n\n請回覆數字選擇兌換方式：";
        options.forEach((opt, i) => {
          replyMsg += `\n${i + 1}. ${opt.label}`;
        });
        replyMsg += `\n${options.length + 1}. 取消`;
        if (hasWarnings) replyMsg += "\n\n⚠️ 請注意：\n" + warnings.join("\n");

        if (WA_SEND_REPLY) { await msg.reply(replyMsg); }
        // 暫存，等待代理回覆數字
        pendingExchanges.set(senderId, {
          paymentInfo, agentName: senderDisplayName,
          customerName, toCurrency,
          conversionInfo: conversionResult ? conversionResult.conversion : null,
          expireAt: Date.now() + 5 * 60 * 1000  // 5 分鐘過期
        });
      } else {
        // 未知目標貨幣，直接記錄
        if (conversionResult && conversionResult.note) {
          replyMsg += `\n${conversionResult.note}`;
        }
        replyMsg += `\n⚠️ 未知目標貨幣「${toCurrency}」，將直接記錄`;
        if (hasWarnings) replyMsg += "\n\n⚠️ 請注意：\n" + warnings.join("\n");
        if (WA_SEND_REPLY) { await msg.reply(replyMsg); }
        if (conversionResult && conversionResult.conversion) {
          pd.conversion = conversionResult.conversion;
          paymentInfo.payment_details = JSON.stringify(pd);
        }
        await createTransaction({
          agent_name: senderDisplayName, customer_name: customerName,
          amount: paymentInfo.amount, currency: paymentInfo.currency,
          raw_message: paymentInfo.raw_message, source: "whatsapp",
          payment_details: paymentInfo.payment_details,
          from_currency: conversionResult ? conversionResult.from_currency || "" : "",
          to_currency: toCurrency,
          remarks: paymentInfo.remarks || "",
          insured_person: paymentInfo.insured_person || ""
        });
        console.log(`   💾 付款資訊已記錄（代理: ${senderDisplayName}, 客戶: ${customerName}）`);
      }
    } else if (paymentInfo.amount <= 0 && WA_SEND_REPLY) {
      await msg.reply("❌ 無法解析付款金額，請檢查 Mso-Pobo 格式");
    }
    return;
  }

  // ── 檢查是否為取消指令 ──
  const cancellation = parseCancellation(text);
  if (cancellation) {
    console.log(`   🔙 檢測到取消指令:`, JSON.stringify(cancellation));
    try {
      if (cancellation.target === "last") {
        const lastTx = await getLastTransaction();
        if (lastTx) {
          await deleteTransactionById(lastTx.id);
          const cur = lastTx.currency || "USD";
          const src = lastTx.source === "telegram" ? "TG" : "WA";
          const cust = lastTx.customer_name ? `（${lastTx.customer_name}）` : "";
          console.log(`   ✅ 已取消上一筆 [${src}]：${lastTx.agent_name} ${cust} ${lastTx.amount} ${cur}`);
          if (WA_SEND_REPLY) {
            await msg.reply(`✅ 已取消上一筆 WhatsApp 交易：${lastTx.agent_name}${cust} ${lastTx.amount.toLocaleString()} ${cur}`);
          }
        } else {
          if (WA_SEND_REPLY) await msg.reply("⚠️ 沒有找到可取消的 WhatsApp 交易記錄");
        }
      } else if (cancellation.target === "agent") {
        const lastTx = await getLastTransaction(cancellation.agent_name);
        if (lastTx) {
          await deleteTransactionById(lastTx.id);
          const cur = lastTx.currency || "USD";
          const cust2 = lastTx.customer_name ? `（${lastTx.customer_name}）` : "";
          console.log(`   ✅ 已取消 ${cancellation.agent_name} 的交易`);
          if (WA_SEND_REPLY) {
            await msg.reply(`✅ 已取消 ${cancellation.agent_name}${cust2} 的最近一筆 WhatsApp 交易：${lastTx.amount.toLocaleString()} ${cur}`);
          }
        } else {
          if (WA_SEND_REPLY) await msg.reply(`⚠️ 沒有找到 ${cancellation.agent_name} 的 WhatsApp 交易記錄`);
        }
      }
    } catch (err) {
      console.error("取消交易失敗：", err.message);
    }
    return;
  }

  // ── 簡易交易解析已停用，僅接受結構化付款資訊 ──
  console.log("   ⚪ 訊息非結構化付款格式，已略過");
});

// 處理斷線重連
client.on("disconnected", (reason) => {
  console.warn("⚠️ WhatsApp 已斷線：", reason);
  console.log("🔄 5秒後嘗試重連...");
  setTimeout(() => client.initialize(), 5000);
});

// 啟動
client.initialize();