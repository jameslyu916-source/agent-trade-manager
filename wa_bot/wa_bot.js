// wa_bot/wa_bot.js
const { Client, LocalAuth } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const axios = require("axios");
require("dotenv").config();
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const { parsePaymentInfo, parseConversionLine, findConversionInText, parseFractionRate } = require("./payment_parser");
const { extractOrders } = require("./ai_order_parser");
const { extractPaymentInfo } = require("./ai_payment_parser");

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

// ── 統一的重新連線函數：正確關閉 Chrome 後再重啟 ──
async function reconnect(reason = "unknown") {
  console.warn(`🔄 嘗試重連（原因: ${reason}）...`);
  try { await client.destroy(); } catch (_) {}
  // 等待 Chrome 完全退出（確保 session 資料完整寫入）
  for (let i = 0; i < 10; i++) {
    await new Promise(r => setTimeout(r, 1500));
    try {
      const out = execSync("pgrep -f 'Google Chrome for Testing'", { encoding: "utf-8" }).trim();
      if (!out) break;
    } catch (_) { break; }
  }
  // 若仍未退出則強制清理
  try { execSync("pkill -f 'Google Chrome for Testing'", { encoding: "utf-8" }); } catch (_) {}
  await new Promise(r => setTimeout(r, 2000));
  await client.initialize();
  lastMessageTime = Date.now();
  console.log("✅ 重連完成");
}

let _lastSettingsHash = "";

async function refreshSettings() {
  try {
    if (!authToken) return false;
    const res = await axios.get(`${API_BASE_URL}/settings`, { headers: getHeaders() });
    if (res.status === 200) {
      const tg = res.data.telegram_enabled !== false ? "啟用" : "停用";
      const wa = res.data.whatsapp_enabled !== false ? "啟用" : "停用";
      const groups = (res.data.whatsapp_group_names || []).join(", ") || "無";
      const hash = `${tg}|${wa}|${groups}`;
      settingsCache = res.data;
      if (hash !== _lastSettingsHash) {
        _lastSettingsHash = hash;
        console.log(`🔄 系統設置已刷新（TG: ${tg} | WA: ${wa} | 群組: ${groups}）`);
      }
      return true;
    }
    return false;
  } catch (err) {
    if (err.response?.status === 401) {
      console.log("🔄 設置刷新時 token 過期，重新登錄...");
      _lastSettingsHash = "";  // 重登後強制刷新
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

// ── Agent 手機號 → 名稱映射快取 ──
const phoneToAgentName = new Map();

async function refreshAgentMapping() {
  try {
    if (!authToken) return;
    const res = await axios.get(`${API_BASE_URL}/agents/`, { headers: getHeaders() });
    if (res.status === 200) {
      phoneToAgentName.clear();
      for (const agent of res.data) {
        if (agent.phone) phoneToAgentName.set(agent.phone, agent.agent_name);
      }
    }
  } catch (_) { /* 靜默失敗，保留舊映射 */ }
}

// ── 貨幣兌換配對（與 bot/payment_parser.py 一致）──
const EXCHANGE_OPTIONS = {
  HKD: [{ from: "CNY", label: "人民幣 → 港幣" }, { from: "USDT", label: "USDT → 港幣" }],
  USD: [{ from: "CNY", label: "人民幣 → 美金" }, { from: "USDT", label: "USDT → 美金" }],
  CNY: [{ from: "USD", label: "美金 → 人民幣" }, { from: "HKD", label: "港幣 → 人民幣" }, { from: "USDT", label: "USDT → 人民幣" }],
};

// 暫存等待代理選擇兌換方式的付款資訊: senderId -> {paymentInfo, agentName, customerName, toCurrency, state, ...}
const pendingExchanges = new Map();
// 公式緩衝區：每個聊天保留最近 20 條換匯公式，用於跨消息查找
const formulaBuffer = new Map();  // chatId -> [{text, timestamp}]
const MAX_FORMULA_BUFFER = 20;

// ── 已處理消息 ID 去重（防止 WebSocket 重推）──
const processedMessageIds = new Set();
const MAX_PROCESSED_IDS = 10000;

// ── 連線健康監控 ──
let lastMessageTime = Date.now();
const MESSAGE_TIMEOUT_MS = 60 * 60 * 1000;  // 60 分鐘無消息判定靜默斷線
let reconnectAttempts = 0;
const MAX_RECONNECT_DELAY_MS = 5 * 60 * 1000;  // 最大重連延遲 5 分鐘
let isReconnecting = false;  // 防止重連重疊（disconnected / change_state 事件）
let isHealthReconnecting = false;  // 健康檢查觸發的重連
let healthCheckInterval = null;  // 健康檢查定時器

// ── 啟動訊息隊列（防止 ready 前處理訊息導致設定不完整）──
let startupComplete = false;
const pendingMessages = [];

// 從緩衝區中查找與付款金額匹配的換匯公式（從新到舊）
function findFormulaInBuffer(chatId, paymentAmount) {
  const buffer = formulaBuffer.get(chatId);
  if (!buffer || buffer.length === 0) return null;
  for (let i = buffer.length - 1; i >= 0; i--) {
    const conv = parseConversionLine(buffer[i].text) || findConversionInText(buffer[i].text);
    if (conv && amountsMatch(conv.result_amount, paymentAmount)) {
      return buffer[i].text;
    }
  }
  return null;
}

// ── 換匯公式自動推斷輔助函數 ──
function amountsMatch(conversionResult, paymentAmount) {
  const tolerance = Math.max(1, Math.floor(paymentAmount * 0.001));
  return Math.abs(conversionResult - paymentAmount) <= tolerance;
}

function rateWithinThreshold(usedRate, dailyRate, threshold = 0.03) {
  if (!dailyRate || dailyRate <= 0) return false;
  return Math.abs(usedRate - dailyRate) / dailyRate <= threshold;
}

// ── 帳戶查找消息過濾（防止客戶轉款帳號被誤判為付款信息）──
function isAccountLookupMessage(text) {
  if (!text) return false;
  const t = text.trim();
  if (/^一笔出/.test(t)) return true;
  if (/不打散/.test(t) && /不备注/.test(t)) return true;
  return false;
}

// ── 從 agent 收集的當日底價匯率快取：key = "YYYY-MM-DD:FROM:TO" ──
const collectedBaseRates = new Map();

// ── 狀態持久化 ──
const STATE_DIR = path.join(__dirname, ".state");
const STATE_FILE = path.join(STATE_DIR, "bot_state.json");

function saveState() {
  try {
    if (!fs.existsSync(STATE_DIR)) fs.mkdirSync(STATE_DIR, { recursive: true });
    const state = {
      pendingExchanges: Array.from(pendingExchanges.entries()),
      collectedBaseRates: Array.from(collectedBaseRates.entries()),
      formulaBuffer: Array.from(formulaBuffer.entries()),
      processedMessageIds: Array.from(processedMessageIds).slice(-MAX_PROCESSED_IDS),
      lastReminderDate: lastReminderDate,
      savedAt: Date.now()
    };
    fs.writeFileSync(STATE_FILE, JSON.stringify(state));
  } catch (e) {
    console.error("❌ 保存狀態失敗：", e.message);
  }
}

let _saveTimer = null;
function scheduleSaveState() {
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(saveState, 100);
}

function loadState() {
  try {
    if (!fs.existsSync(STATE_FILE)) return;
    const raw = fs.readFileSync(STATE_FILE, "utf-8");
    const state = JSON.parse(raw);
    const now = Date.now();
    const today = new Date().toISOString().split("T")[0];

    if (state.pendingExchanges) {
      for (const [key, val] of state.pendingExchanges) {
        if (val.expireAt && val.expireAt < now) continue;
        pendingExchanges.set(key, val);
      }
    }
    if (state.collectedBaseRates) {
      for (const [key, val] of state.collectedBaseRates) {
        if (key.startsWith(today)) collectedBaseRates.set(key, val);
      }
    }
    if (state.formulaBuffer) {
      const maxAge = now - 5 * 60 * 1000;
      for (const [key, entries] of state.formulaBuffer) {
        const valid = entries.filter(e => e.timestamp > maxAge);
        if (valid.length > 0) formulaBuffer.set(key, valid);
      }
    }
    if (state.processedMessageIds && Array.isArray(state.processedMessageIds)) {
      // 僅在狀態較新（≤30 分鐘）時恢復，避免跨 session 誤傷新訊息
      const maxAge = 30 * 60 * 1000;
      if (state.savedAt && (now - state.savedAt) <= maxAge) {
        const ids = state.processedMessageIds.slice(-MAX_PROCESSED_IDS);
        for (const id of ids) processedMessageIds.add(id);
      }
    }
    // 僅恢復當天的 lastReminderDate（跨天則重置，讓新一天的提醒正常觸發）
    if (state.lastReminderDate && state.lastReminderDate === today) {
      lastReminderDate = state.lastReminderDate;
    }
    console.log(`📂 已恢復狀態：${pendingExchanges.size} pending｜${collectedBaseRates.size} rates｜${formulaBuffer.size} buffers｜${processedMessageIds.size} processed ids`);
  } catch (e) {
    console.log("⚠️ 恢復狀態失敗（使用空白狀態）：", e.message);
  }
}

// ── 根據賣出匯率推斷來源幣種 ──
async function inferSourceCurrency(sellRate, toCurrency) {
  const options = EXCHANGE_OPTIONS[toCurrency] || [];
  if (options.length === 0) return null;

  const today = new Date().toISOString().split("T")[0];
  const dailyRateMap = {};
  const presetRates = getSetting("preset_exchange_rates", {});

  // 先從每日 API 匯率查
  try {
    const rates = await getExchangeRates(today);
    for (const r of (rates || [])) {
      dailyRateMap[`${r.from_currency}:${r.to_currency}`] = r.rate;
    }
  } catch (_) { /* fall through */ }

  // 收集其他來源的參考匯率（collectedBaseRates / preset 優先覆蓋）
  for (const opt of options) {
    const pair = `${opt.from}:${toCurrency}`;
    // 從 collectedBaseRates 查（優先於 API）
    const collectedKey = `${today}:${opt.from}:${toCurrency}`;
    if (collectedBaseRates.has(collectedKey)) {
      dailyRateMap[pair] = collectedBaseRates.get(collectedKey);
      continue;
    }
    // 從 preset 查（若 API 沒有的話補上）
    if (dailyRateMap[pair] === undefined && presetRates[pair] !== undefined) {
      dailyRateMap[pair] = presetRates[pair];
    }
  }

  // 找最接近的
  let best = null;
  for (const opt of options) {
    const pair = `${opt.from}:${toCurrency}`;
    const ref = dailyRateMap[pair];
    if (ref && ref > 0) {
      const pctDiff = Math.abs(sellRate - ref) / ref;
      if (pctDiff <= 0.03 && (!best || pctDiff < best.pctDiff)) {
        best = { from: opt.from, label: opt.label, pctDiff };
      }
    }
  }
  return best ? { from: best.from, label: best.label } : null;
}

// ── 獲取底價匯率（成本匯率）──
async function getBaseRate(sourceCurrency, toCurrency) {
  const today = new Date().toISOString().split("T")[0];
  // CNY→USD 和 CNY→HKD 優先從 API 獲取
  if (sourceCurrency === "CNY" && (toCurrency === "USD" || toCurrency === "HKD")) {
    try {
      const rates = await getExchangeRates(today);
      const pair = `${sourceCurrency}:${toCurrency}`;
      const r = (rates || []).find(r => r.from_currency === sourceCurrency && r.to_currency === toCurrency);
      if (r && r.rate > 0) return r.rate;
    } catch (_) { /* fall through */ }
  }
  // 從 collectedBaseRates 查
  const key = `${today}:${sourceCurrency}:${toCurrency}`;
  if (collectedBaseRates.has(key)) return collectedBaseRates.get(key);
  // 從昨天的匯率嘗試（API）
  try {
    const yesterday = getYesterdayDate();
    const yRates = await getExchangeRates(yesterday);
    const pair = `${sourceCurrency}:${toCurrency}`;
    const r = (yRates || []).find(r => r.from_currency === sourceCurrency && r.to_currency === toCurrency);
    if (r && r.rate > 0) return r.rate;
  } catch (_) { /* fall through */ }
  return null;
}

// ── 解析兌換前金額（支援多種格式）──
function parseSourceAmount(text) {
  if (!text) return null;
  let t = text.trim();
  // 去掉前導貨幣符號（￥、$ 等）
  t = t.replace(/^[￥¥$€£]\s*/, "").trim();
  // 去掉可能攜帶的幣種後綴
  t = t.replace(/\s*(CNY|USD|HKD|USDT|RMB|cny|usd|hkd|usdt|rmb)\s*$/i, "").trim();
  // 去掉千分位逗號
  t = t.replace(/,/g, "");
  // 檢查中文單位（從大到小，避免 千萬 被 萬 先匹配）
  const units = [
    { re: /^([\d.]+)\s*億$/, mul: 100000000 },
    { re: /^([\d.]+)\s*千[万萬]$/, mul: 10000000 },
    { re: /^([\d.]+)\s*百[万萬]$/, mul: 1000000 },
    { re: /^([\d.]+)\s*十[万萬]$/, mul: 100000 },
    { re: /^([\d.]+)\s*[wW万萬]$/, mul: 10000 },
    { re: /^([\d.]+)\s*千$/, mul: 1000 },
    { re: /^([\d.]+)\s*百$/, mul: 100 },
  ];
  for (const u of units) {
    const m = t.match(u.re);
    if (m) {
      const base = parseFloat(m[1]);
      if (!isNaN(base) && base > 0) return Math.round(base * u.mul);
      return null;
    }
  }
  // 純數字
  const num = parseFloat(t);
  if (!isNaN(num) && num > 0) return Math.round(num);
  return null;
}

// ── 自動推算兌換前金額 ──
function autoCalculateSourceAmount(paymentAmount, sellRate, sourceCurrency) {
  // 除法型（CNY, HKD）：source = payment × rate
  // 乘法型（USDT, USD）：source = payment ÷ rate
  const isMultiply = sourceCurrency === "USDT" || sourceCurrency === "USD";
  const source = isMultiply
    ? Math.round(paymentAmount / sellRate)
    : Math.round(paymentAmount * sellRate);
  if (isNaN(source) || !isFinite(source) || source <= 0) return null;
  return source;
}

// ── 從 pending 構建 conversion 並創建交易 ──
async function recordTransactionFromPending(pending, sourceAmount, msg) {
  pendingExchanges.delete(msg.from);

  const pd = pending.paymentInfo.payment_details_dict || {};
  const conversion = {
    source_amount: sourceAmount,
    rate: pending.sellRate,
    source_currency: pending.sourceCurrency,
    operator: (pending.sourceCurrency === "USDT" || pending.sourceCurrency === "USD") ? "*" : "/",
    matched: pending.baseRate ? true : false,
    daily_rate: pending.baseRate || null,
    rate_source: pending.baseRate ? "manual_collected" : "manual_skip",
  };
  pd.conversion = conversion;
  pending.paymentInfo.payment_details = JSON.stringify(pd);

  const success = await createTransaction({
    agent_name: pending.agentName,
    customer_name: pending.customerName,
    amount: pending.paymentInfo.amount,
    currency: pending.paymentInfo.currency,
    raw_message: pending.paymentInfo.raw_message,
    source: "whatsapp",
    group_id: msg.from,
    payment_details: pending.paymentInfo.payment_details,
    from_currency: pending.sourceCurrency,
    to_currency: pending.toCurrency,
    remarks: pending.paymentInfo.remarks || "",
    insured_person: pending.paymentInfo.insured_person || "",
    timestamp: msg.timestamp ? new Date(msg.timestamp * 1000).toISOString() : new Date().toISOString()
  }, msg);

  if (success && WA_SEND_REPLY) {
    let replyMsg = `✅ 已紀錄收款：${pending.customerName}\n兌換：${pending.sourceCurrency} → ${pending.toCurrency}\n金額：${pending.paymentInfo.amount.toLocaleString()} ${pending.toCurrency}`;
    replyMsg += `\n📐 賣出匯率：${pending.sellRate} | 來源金額：${sourceAmount.toLocaleString()} ${pending.sourceCurrency}（自動推算）`;
    if (pending.baseRate) replyMsg += ` | 底價：${pending.baseRate}`;
    await msg.reply(replyMsg);
  }
  return success;
}

// ── 解析補全欄位：從 agent 補發的文字中提取個別欄位 ──
function parseCompletionFields(text) {
  if (!text || !text.trim()) return { fields: {}, pd: {} };
  const lines = text.split("\n");
  const fields = {};     // top-level: customer_name, amount, currency
  const pd = {};         // payment_details: account_name, bank_name, swift, etc.

  // 欄位標籤映射
  const labelMap = {
    account_name: ["戶口全名", "户口全名", "收款人名稱", "收款人名称", "Account Name", "account name", "收款人", "戶名", "户名"],
    account_number: ["戶口號碼", "户口号码", "賬戶號碼", "账户号码", "Account Number", "account number", "帳號", "账号", "銀行號碼", "银行号码", "戶口", "户口"],
    bank_name: ["銀行名稱", "银行名称", "Bank Name", "bank name", "銀行", "银行", "收款銀行", "收款银行"],
    swift: ["SWIFT", "swift", "Swift Code", "SWIFT Code", "BIC", "SwiftCode", "銀行電碼", "银行电码"],
    bank_code: ["銀行代碼", "银行代码", "Bank Code", "bank code", "銀行編號", "银行编号"],
    bank_address: ["銀行地址", "银行地址", "Bank Address", "bank address"],
    amount: ["金額", "金额", "Amount", "amount", "MSO", "Mso-Pobo", "交易金額", "交易金额"],
  };

  for (const line of lines) {
    const t = line.trim();
    if (!t) continue;
    // 找分隔符
    let key = "", value = "";
    for (const sep of ["：", ":", "="]) {
      const idx = t.indexOf(sep);
      if (idx > 0) { key = t.slice(0, idx).trim(); value = t.slice(idx + 1).trim(); break; }
    }
    if (!key) {
      // 無分隔符 → 嘗試以空格分割
      const sp = t.indexOf(" ");
      if (sp > 0) { key = t.slice(0, sp).trim(); value = t.slice(sp + 1).trim(); }
      else continue;
    }

    const keyLower = key.toLowerCase();
    for (const [fieldKey, labels] of Object.entries(labelMap)) {
      if (labels.some(l => keyLower === l.toLowerCase())) {
        if (fieldKey === "amount") {
          // 嘗試解析金額+幣種
          const parsed = parsePaymentInfo(value, null);
          if (parsed && parsed.amount > 0) {
            fields.amount = parsed.amount;
            fields.currency = parsed.currency;
          } else {
            // fallback: 純數字
            const amt = parseSourceAmount(value);
            if (amt) fields.amount = amt;
          }
        } else if (["account_name"].includes(fieldKey)) {
          pd[fieldKey] = value;
          fields.customer_name = value;  // 同步更新
        } else {
          pd[fieldKey] = value;
        }
        break;
      }
    }
  }
  return { fields, pd };
}

function getYesterdayDate() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().split("T")[0];
}

async function resolveConversion(paymentInfo, prevText, toCurrency) {
  if (!prevText) return null;

  const conv = parseConversionLine(prevText) || findConversionInText(prevText);
  if (!conv) return null;

  // 若公式無貨幣標籤，用付款信息的幣種補
  const resultCurrency = conv.result_currency || toCurrency;
  if (!resultCurrency) return null;

  // 檢查數學等式（根據運算符選擇乘法或除法）
  let sourceAmount = conv.source_amount;
  let autocorrected = false;
  const isMultiply = conv.operator === "*";
  const expectedResult = isMultiply
    ? Math.round(sourceAmount * conv.rate)
    : conv.rate !== 0 ? Math.round(sourceAmount / conv.rate) : 0;
  if (!amountsMatch(expectedResult, conv.result_amount)) {
    // 嘗試補全萬位
    const correctedSource = sourceAmount * 10000;
    const correctedResult = isMultiply
      ? Math.round(correctedSource * conv.rate)
      : conv.rate !== 0 ? Math.round(correctedSource / conv.rate) : 0;
    if (amountsMatch(correctedResult, conv.result_amount)) {
      sourceAmount = correctedSource;
      autocorrected = true;
    } else {
      return null;
    }
  }

  // ── 嚴格驗證公式內部算術（僅允許個位數誤差）──
  let formulaWarning = null;
  if (!autocorrected) {
    const mathError = Math.abs(expectedResult - conv.result_amount);
    if (mathError > 5) {
      formulaWarning = `⚠️ 換匯公式驗算異常：${sourceAmount.toLocaleString()} ${isMultiply ? "×" : "/"} ${conv.rate} 應為 ${expectedResult.toLocaleString()}，但公式寫的是 ${conv.result_amount.toLocaleString()}（差 ${mathError}）\n💡 如需取消，請回覆「取消」或「取消 上一筆」`;
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
  const candidates = EXCHANGE_OPTIONS[resultCurrency] || [];
  let bestMatch = null; // { from, referenceRate, rateSource, pctDiff }

  for (const candidate of candidates) {
    const pair = `${candidate.from}:${resultCurrency}`;
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
      operator: conv.operator || "/",
    };
    if (autocorrected) {
      conversionInfo.autocorrected = true;
    }
    const wanNote = autocorrected
      ? `（已自動補全萬位 ${conv.source_amount.toLocaleString()}→${sourceAmount.toLocaleString()}）`
      : "";
    const label = candidates.find(o => o.from === bestMatch.from)?.label || bestMatch.from;
    let note = `📐 從換匯公式 ${resultCurrency} ${conv.rate} 自動推斷為 ${bestMatch.from}（${label}）${wanNote}`;
    if (formulaWarning) note += `\n${formulaWarning}`;
    return {
      auto_inferred: true,
      from_currency: bestMatch.from,
      conversion: conversionInfo,
      note,
    };
  }

  // 無匹配 → 提示手動選擇
  const bestDailyStr = bestMatch
    ? `${bestMatch.referenceRate.toFixed(3)}（差 ${(bestMatch.pctDiff * 100).toFixed(1)}%）`
    : "無可用參考匯率";
  const bestPairLabel = bestMatch
    ? `${bestMatch.from}→${resultCurrency}`
    : "無";
  const conversionInfo = {
    source_amount: sourceAmount,
    rate: conv.rate,
    source_currency: "CNY",
    matched: false,
    operator: conv.operator || "/",
  };
  if (autocorrected) {
    conversionInfo.autocorrected = true;
  }
  const wanNote = autocorrected
    ? `（已自動補全萬位 ${conv.source_amount.toLocaleString()}→${sourceAmount.toLocaleString()}）`
    : "";
  const opSymbol = conv.operator === "*" ? " × " : " / ";
  let note = `📐 檢測到換匯公式 ${sourceAmount.toLocaleString()}${opSymbol}${conv.rate} = ${conv.result_amount.toLocaleString()} ${conv.result_currency}，最佳匹配 ${bestPairLabel} (${bestDailyStr}) 超過 3% 閾值，請手動選擇${wanNote}`;
  if (formulaWarning) note += `\n${formulaWarning}`;
  return {
    auto_inferred: false,
    from_currency: null,
    conversion: conversionInfo,
    note,
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
金額（+貨幣）：

--- 以下為可選項 ---
備註：
投保人（投保專用）：`;

const FORMAT_CONVERSION_HINT = `💡 發送提示：
請先發送換匯公式（如：50w / 7.01 = 71,023 USD），
再發送下述交易信息。兩條消息請分開發送。`;

const FORMAT_MINIMAL = `📋 精簡版（僅必填項）：

戶口全名：
銀行名稱：
戶口號碼：
金額（+貨幣）：`;

const FORMAT_FULL = FORMAT_CONVERSION_HINT + "\n\n" + FORMAT_EXAMPLE + "\n\n" + FORMAT_TEMPLATE + "\n\n" + FORMAT_MINIMAL;

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

async function createTransaction(data, msg = null) {
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
      group_id: data.group_id || "",
      payment_details: data.payment_details || null,
      timestamp: data.timestamp || undefined
    };
    const res = await axios.post(`${API_BASE_URL}/transactions/`, payload, {
      headers: getHeaders(),
    });
    const result = res.data;

    // 檢查客戶帳戶警報
    if (msg && result.account_alert) {
      const a = result.account_alert;
      let alertText;
      if (a.alert_type === "account_changed") {
        alertText = `⚠️ *客戶帳戶變更提醒*\n客戶：${a.customer_name}\n新帳號：${a.account_number}\n舊帳號：${a.previous_account_number}\n請確認是否為同一客戶的新帳戶`;
      } else if (a.alert_type === "account_reused") {
        alertText = `🚨 *帳戶重複使用提醒*\n帳號：${a.account_number}\n當前客戶：${a.customer_name}\n原記錄客戶：${a.previous_customer_name}\n請確認是否打錯客戶`;
      }
      if (alertText) {
        try {
          const chat = await msg.getChat();
          await chat.sendMessage(alertText);
        } catch (e) { console.error("發送帳戶警報失敗:", e.message); }
      }
    }

    return result;
  } catch (err) {
    if (err.response?.status === 401) {
      await login();
      return createTransaction(data, msg);
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

  // 「取消」單獨使用太容易誤觸發，需搭配後綴（如「取消 上一筆」）；其他指令（撤銷等）單獨即可
  const isCancelAlone = matchedKw === "取消" && !remainder;
  if (!isCancelAlone && (!remainder || ["上一筆", "上一笔", "上一单", "上一單", "last", "上一條", "上一"].includes(remainder))) {
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

async function getLastTransaction(agentName, groupId) {
  try {
    const params = new URLSearchParams();
    params.set("source", "whatsapp");
    if (agentName) params.set("agent_name", agentName);
    if (groupId) params.set("group_id", groupId);
    const res = await axios.get(`${API_BASE_URL}/transactions/last?${params.toString()}`, { headers: getHeaders() });
    return res.data;
  } catch (err) {
    if (err.response?.status === 401) { await login(); return getLastTransaction(agentName, groupId); }
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

async function getDailyOrders(date, groupId = null) {
  try {
    let url = `${API_BASE_URL}/orders/daily?date=${encodeURIComponent(date)}`;
    if (groupId) url += `&group_id=${encodeURIComponent(groupId)}`;
    const res = await axios.get(url, { headers: getHeaders() });
    return res.data || [];
  } catch (err) {
    if (err.response?.status === 401) { await login(); return getDailyOrders(date, groupId); }
    console.error("❌ 獲取當日訂單失敗：", err.response?.data || err.message);
    return [];
  }
}

async function getUnmatchedOrders(groupId) {
  try {
    const url = groupId
      ? `${API_BASE_URL}/orders/unmatched?group_id=${encodeURIComponent(groupId)}`
      : `${API_BASE_URL}/orders/unmatched`;
    const res = await axios.get(url, { headers: getHeaders() });
    return res.data;
  } catch (err) {
    if (err.response?.status === 401) { await login(); return getUnmatchedOrders(groupId); }
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
  if (isReconnecting) return;  // 重連中，跳過（瀏覽器不可用）
  try {
    const reminderTime = getSetting("reminder_time", { hour: 17, minute: 30 });
    const { hour, minute } = parseHKTime(reminderTime);

    const now = new Date();
    const hkNow = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Hong_Kong" }));
    const todayStr = `${hkNow.getFullYear()}-${String(hkNow.getMonth() + 1).padStart(2, "0")}-${String(hkNow.getDate()).padStart(2, "0")}`;

    // 在設定時間或之後觸發（避免因斷線錯過當天提醒）
    const scheduledMin = hour * 60 + minute;
    const currentMin = hkNow.getHours() * 60 + hkNow.getMinutes();
    if (currentMin < scheduledMin) return;
    if (lastReminderDate === todayStr) return;

    console.log(`⏰ 觸發漏單提醒（${todayStr} ${hour}:${String(minute).padStart(2, "0")} HKT）`);
    lastReminderDate = todayStr;

    // 優先使用 reminder_group_names（多群組），向後兼容 reminder_group_name（單一群組）
    let reminderGroups = getSetting("reminder_group_names", null);
    if (!reminderGroups || !Array.isArray(reminderGroups) || reminderGroups.length === 0) {
      const legacyName = getSetting("reminder_group_name", null);
      if (legacyName) {
        reminderGroups = [legacyName];
      } else {
        console.log("   ⚠️ 未設定提醒群組名稱");
        return;
      }
    }

    const chats = await client.getChats();

    for (const groupName of reminderGroups) {
      try {
        const reminderChat = chats.find(c => c.name === groupName && c.isGroup);
        if (!reminderChat) {
          console.log(`   ⚠️ 找不到提醒群組「${groupName}」`);
          continue;
        }

        // 獲取該群的未匹配訂單
        const chatId = reminderChat.id._serialized;
        const orders = await getUnmatchedOrders(chatId);
        if (!orders || orders.length === 0) {
          console.log(`   ✅ 「${groupName}」今日無漏單`);
          await reminderChat.sendMessage("✅ 今日無漏單 🎉（定時消息）");
          continue;
        }

        console.log(`   📋 發送 ${orders.length} 筆漏單提醒到「${groupName}」`);

        for (const order of orders) {
          try {
            const orderDate = order.created_at
              ? new Date(order.created_at).toLocaleDateString("zh-HK", { timeZone: "Asia/Hong_Kong" })
              : "未知日期";
            const msg = await reminderChat.sendMessage(
              `📋 漏單提醒：${order.customer_name} ¥${order.amount.toLocaleString()}（${orderDate}）\n` +
              `請回覆處理狀態（引用回覆此消息）：\n` +
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
      } catch (e) {
        console.error(`   ❌ 「${groupName}」提醒處理失敗：`, e.message);
      }
    }
  } catch (err) {
    console.error("❌ 漏單提醒檢查失敗：", err.message);
  }
}

// ==================== WhatsApp 客戶端 ====================
const PID_FILE = path.join(__dirname, "wa_bot.pid");

// 寫入 PID 檔案
fs.writeFileSync(PID_FILE, String(process.pid));

// 清理 PID 檔案
function cleanupPidFile() {
  try { if (fs.existsSync(PID_FILE)) fs.unlinkSync(PID_FILE); } catch (_) {}
}

async function gracefulShutdown() {
  console.log("🛑 正在關閉 WhatsApp Bot...");
  saveState();
  cleanupPidFile();

  // 正常關閉瀏覽器並等待 Chrome 完全退出，確保 session 資料完整寫入
  try { await client.destroy(); } catch (_) {}

  // 等待 Chrome 完全退出（確保 session 資料寫入磁碟）
  for (let i = 0; i < 5; i++) {
    await new Promise(r => setTimeout(r, 1000));
    try {
      const out = execSync("pgrep -f 'Google Chrome for Testing'", { encoding: "utf-8" }).trim();
      if (!out) break;
    } catch (_) { break; }
  }

  process.exit(0);
}

process.on("unhandledRejection", (reason) => {
  console.error("⚠️ 未處理的 Promise 拒絕：", reason);
});

process.on("exit", cleanupPidFile);
process.on("SIGTERM", () => { gracefulShutdown(); });
process.on("SIGINT", () => { gracefulShutdown(); });

const client = new Client({
  // LocalAuth 會將登錄狀態保存到本地，重啟後不需要重新掃碼
  authStrategy: new LocalAuth({ clientId: "wa-bot" }),
  webVersionCache: { type: "none" },  // 禁用本地緩存，避免 WWebJS 注入失敗
  puppeteer: {
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  },
});

// 顯示二維碼（首次登錄需要掃碼）
client.on("qr", (qr) => {
  console.log("\n📱 請用手機 WhatsApp 掃描以下二維碼登錄：\n");
  qrcode.generate(qr, { small: true });
});

// ready 超時恢復：authenticated 後若 ready 遲遲不來，重試 initialize
let readyTimeout = null;

client.on("authenticated", () => {
  if (readyTimeout) clearTimeout(readyTimeout);
  readyTimeout = setTimeout(async () => {
    console.warn("⚠️ authenticated 後 60 秒仍未 ready，嘗試重連...");
    readyTimeout = null;
    try { await client.destroy(); } catch (_) {}
    try { await client.initialize(); } catch (e) {
      console.error("❌ 重連失敗：", e.message);
    }
  }, 60000);
});

// 登錄成功
let isFirstReady = true;

client.on("ready", async () => {
  if (readyTimeout) { clearTimeout(readyTimeout); readyTimeout = null; }
  lastMessageTime = Date.now();

  if (isFirstReady) {
    isFirstReady = false;
    console.log("✅ WhatsApp Bot 已就緒！");
    console.log(`📌 監控群組：${WATCH_GROUP_NAMES.join(", ") || "（未設置）"}`);
    await login();
    await initSettings();
    loadState();
    await refreshAgentMapping();
    // 每 60 秒刷新設置與 agent 映射
    setInterval(() => { refreshSettings(); refreshAgentMapping(); }, 60 * 1000);
    // 每 60 秒檢查漏單提醒
    setInterval(sendReminderIfTime, 60 * 1000);
    // 每 60 秒健康檢查
    healthCheckInterval = setInterval(async () => {
      if (isHealthReconnecting || isReconnecting) return;
      const idleMs = Date.now() - lastMessageTime;
      if (idleMs > MESSAGE_TIMEOUT_MS) {
        console.warn(`⏰ 已 ${Math.round(idleMs / 60000)} 分鐘未收到消息，可能靜默斷線...`);
        isHealthReconnecting = true;
        try { await reconnect("health_check"); } catch (e) {
          console.error("❌ 健康檢查重連失敗：", e.message);
        }
        isHealthReconnecting = false;
      }
    }, 60 * 1000);
    // ── 處理啟動期間暫存的訊息 ──
    startupComplete = true;
    if (pendingMessages.length > 0) {
      console.log(`📬 啟動完成，處理 ${pendingMessages.length} 條暫存訊息...`);
      for (const m of pendingMessages) {
        try { await processMessage(m); } catch (e) {
          console.error("處理暫存訊息失敗：", e.message);
        }
      }
      pendingMessages.length = 0;
    }
  } else {
    // 重連：只做輕量恢復，不重跑完整 init（避免 interval 重複、loadState 覆蓋記憶體狀態）
    console.log("🔁 WhatsApp Bot 已重新連接");
    startupComplete = true;
  }
});

// WhatsApp 內部狀態監聽
client.on("change_state", (state) => {
  console.log(`🔄 WhatsApp 狀態變更：${state}`);
  if (state === "CONFLICT" || state === "UNPAIRED" || state === "UNPAIRED_IDLE") {
    if (isReconnecting) return;
    console.warn("⚠️ 檢測到異常狀態，5 秒後重連...");
    isReconnecting = true;
    reconnectAttempts = 0;
    setTimeout(async () => {
      try { await reconnect(`state:${state}`); } catch (_) {}
      isReconnecting = false;
    }, 5000);
  }
});

// 認證失敗處理
client.on("auth_failure", (msg) => {
  console.error("❌ WhatsApp 認證失敗：", msg);
  console.log("🔄 請手動重啟 bot 以重新掃碼登錄");
});

// 監聽所有消息
client.on("message", async (msg) => {
  lastMessageTime = Date.now();

  // ── 消息 ID 去重：防止 WebSocket 重推已處理的消息 ──
  const msgId = msg.id && msg.id._serialized;
  if (msgId && processedMessageIds.has(msgId)) {
    console.log("⏭️ 跳過重複消息：", msgId);
    return;
  }
  if (msgId) {
    processedMessageIds.add(msgId);
    if (processedMessageIds.size > MAX_PROCESSED_IDS * 2) {
      const entries = Array.from(processedMessageIds);
      processedMessageIds.clear();
      for (const id of entries.slice(-MAX_PROCESSED_IDS)) processedMessageIds.add(id);
    }
  }

  // ── 啟動閘：ready 尚未完成初始化前先暫存訊息 ──
  if (!startupComplete) {
    pendingMessages.push(msg);
    return;
  }

  return processMessage(msg);
});

async function processMessage(msg) {
  try {
  // 第一關：確認事件有觸發（任何消息都會打印）
  console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  console.log("📩 收到消息");
  console.log("   from:", msg.from);
  console.log("   body:", msg.body);
  console.log("   是否群組消息:", msg.from.endsWith("@g.us"));

  // 第二關：確認群組名稱（使用系統設置優先，與實際過濾邏輯一致）
  if (msg.from.endsWith("@g.us")) {
    const chat = await msg.getChat();
    const effectiveGroups = getSetting("whatsapp_group_names", null) || WATCH_GROUP_NAMES;
    console.log("   群組名稱:", `「${chat.name}」`);
    console.log("   監控列表:", effectiveGroups);
    console.log(
      "   名稱是否匹配:",
      effectiveGroups.length === 0 || effectiveGroups.includes(chat.name)
    );
  }

  // 以下保持原有邏輯不變
  // ── 格式範例請求（私聊和群組都可用）──
  const msgText = msg.body.trim();
  const formatAliases = ["/format", "/Format", "上單模板", "上单模板", "上單格式", "上单格式", "上單樣板", "上单样板"];
  if (formatAliases.includes(msgText)) {
    if (WA_SEND_REPLY) { await msg.reply(FORMAT_FULL); }
    return;
  }

  // ── 共享客戶名校驗（AI 和正則結果統一過濾）──
  const NAME_STOP_WORDS = new Set([
    "only", "just", "about", "around", "approx", "approximately",
    "maybe", "probably", "almost", "nearly", "roughly",
    "ok", "okay", "yes", "no", "hi", "hello", "hey",
    "thanks", "thank", "please", "pls", "test",
    "the", "a", "an", "is", "are", "was", "were",
    "this", "that", "it", "i", "you", "he", "she", "we", "they",
    "not", "all", "some", "any", "and", "or", "but", "if", "so",
    "to", "for", "in", "on", "at", "by", "from", "with",
    "also", "too", "very", "can", "will", "would", "could",
  ]);

  function isValidCustomerName(name) {
    if (!name || name.length < 1) return false;
    // 純數字（手機號，8 位以上）
    if (/^\d{8,}$/.test(name)) return false;
    // 含 @ 殘留
    if (/@/.test(name)) return false;
    // 純標點/空白
    if (/^[\s.,\-+]+$/.test(name)) return false;
    // 英文停用詞：去標點 → 小寫 → 精確匹配（僅純 ASCII 才檢查，避免誤傷拼音名）
    const cleaned = name.replace(/[.\-_\s]/g, "").toLowerCase();
    if (/^[a-zA-Z]+$/.test(cleaned) && NAME_STOP_WORDS.has(cleaned)) return false;
    // 中文指令性開頭（避免「今日要操作」「下午3点打款」等被誤判為客戶名）
    if (/^(今日|明天|一陣|稍後|下午|早上|晚上|優先|馬上|立刻|等等)/.test(name)) return false;
    return true;
  }

  // ── @mention 客戶訂單檢測（群組消息）──
  // 優先看 mentionedIds，若沒抓到（新版 WhatsApp 格式差異）則從 body 中偵測 @數字
  const hasMention = (msg.mentionedIds && msg.mentionedIds.length > 0) || /@\d{5,}/.test(msgText);
  if (msg.from.endsWith("@g.us") && hasMention) {
    const orderChat = await msg.getChat();
    // 移除所有 @mention（格式為 @phone_number），再逐行匹配訂單
    const afterMention = msgText.replace(/@\S+/g, "").trim();
    const lines = afterMention.split(/\r?\n/);
    // ORDER_LINE_RE keyword 前用 \s+（需有空格），無空格連寫由下方預處理標準化
    const ORDER_LINE_RE = /^(.+?)\s+(?:需要(?:戶口|户口)?|需|要|單筆|今日兌換|今日兑换|兌換|兑换)?\s*(?:[￥¥$€£])?(\d[\d,]*(?:-\d+)?(?:w|万|萬|千万|千萬|百万|百萬|十万|十萬|千|百|亿|億)?)\s*(美金|美元|USD|港幣|港元|港币|HKD|人民幣|人民币|rmb|RMB|CNY)?/i;
    const CN_UNIT_MULTIPLIER = {
      "亿": 100000000, "億": 100000000,
      "千万": 10000000, "千萬": 10000000,
      "百万": 1000000, "百萬": 1000000,
      "十万": 100000, "十萬": 100000,
      "万": 10000, "萬": 10000, "w": 10000,
      "千": 1000,
      "百": 100,
    };
    // 按長度由長到短排序，避免「千万」被「万」先匹配
    const CN_UNIT_KEYS = Object.keys(CN_UNIT_MULTIPLIER).sort((a, b) => b.length - a.length);
    const CURRENCY_MAP = {
      "美金": "USD", "美元": "USD", "usd": "USD",
      "港幣": "HKD", "港元": "HKD", "港币": "HKD", "hkd": "HKD",
      "人民幣": "CNY", "人民币": "CNY", "rmb": "CNY", "cny": "CNY",
    };
    const parsedOrders = [];
    let parserSource = "📐 Regex";

    // ── 預篩條件：有數字 + 內容足夠長才調 AI ──
    const hasDigits = /\d/.test(afterMention);
    const hasMinContent = afterMention.replace(/\s/g, "").length > 3;
    if (hasDigits && hasMinContent) {
    // ── 第一關：AI 訂單提取（DeepSeek 優先，OpenAI 備援）──
    try {
      const aiResult = await extractOrders(msgText);
      if (aiResult && Array.isArray(aiResult.orders) && aiResult.orders.length > 0) {
        for (const o of aiResult.orders) {
          const name = String(o.customer_name || "").trim();
          const rawAmount = String(o.amount || "0").replace(/,/g, "");
          const amount = parseInt(rawAmount, 10);
          const currency = ["USD", "HKD", "CNY"].includes(o.currency) ? o.currency : "CNY";
          // 校驗客戶名（AI 和正則共用同一校驗邏輯）
          if (isValidCustomerName(name) && amount >= 100) {
            parsedOrders.push({ customerName: name, amount, currency });
          } else if (name && amount >= 100) {
            console.log(`   ⚠️ AI 結果被校驗拒絕：customer_name="${name}"`);
          }
        }
        if (parsedOrders.length > 0) {
          parserSource = "🤖 AI (DeepSeek)";
          console.log(`   🤖 AI 提取到 ${parsedOrders.length} 筆訂單，跳過正則`);
        }
      }
    } catch (e) {
      console.log(`   ⚠️ AI 訂單提取異常：${e.message}，降級至正則`);
    }
    } // end AI pre-filter

    // ── 第二關：正則降級（AI 無結果時執行）──
    if (parsedOrders.length === 0) {
    // ── 預處理：name+keyword 無空格連寫標準化（如「duo需要戶口」→「duo 需要戶口」）──
    const KW_NORMALIZE_RE = /(\S)((?:需要(?:戶口|户口)?|單筆))/g;
    const normalizedLines = lines.map(l => l.trim().replace(KW_NORMALIZE_RE, "$1 $2"));

    // ── 跨行合併預處理：若 line N 含關鍵詞但無數字，line N+1 含金額，則拼接 ──
    const mergedLines = [];
    for (let i = 0; i < normalizedLines.length; i++) {
      const cur = normalizedLines[i];
      if (!cur) { mergedLines.push(cur); continue; }
      const next = i + 1 < normalizedLines.length ? normalizedLines[i + 1] : "";
      // 當前行含「需要戶口/需要」但沒有數字金額，且下一行有數字
      if (/需要(?:戶口|户口)?/.test(cur) && !/\d/.test(cur) && /\d/.test(next)) {
        // 剝離下行中的「單筆」前綴再拼接
        const nextClean = next.replace(/^單筆\s*/, "");
        mergedLines.push(cur + " " + nextClean);
        i++;
      } else if (/單筆\s*\d/.test(next) && !/\d/.test(cur) && cur.length > 0) {
        // 下一行以「單筆+數字」開頭且當前行不含數字 → 拼接（剝離單筆）
        const nextClean = next.replace(/^單筆\s*/, "");
        mergedLines.push(cur + " " + nextClean);
        i++;
      } else {
        mergedLines.push(cur);
      }
    }

    for (const rawLine of mergedLines) {
      const line = rawLine.trim();
      if (!line) continue;
      // 跳過含換匯公式的行（如 "6.92 / 1.002 x 1.004 = 6.934"），避免誤判為訂單
      if (/[\/\*].*=/.test(line)) continue;
      const m = line.match(ORDER_LINE_RE);
      if (!m) continue;
      const customerName = m[1].trim();
      const amountStr = m[2];
      // 區間取第一值：70-71万 → 剝離單位後 split → 再乘回去
      let unitMultiplier = 1;
      let amountClean = amountStr;
      for (const unit of CN_UNIT_KEYS) {
        if (amountStr.endsWith(unit)) {
          unitMultiplier = CN_UNIT_MULTIPLIER[unit];
          amountClean = amountStr.slice(0, -unit.length);
          break;
        }
      }
      amountClean = amountClean.split("-")[0].replace(/,/g, "");
      const amount = parseInt(amountClean, 10) * unitMultiplier;
      // 幣種檢測
      let currency = "CNY";
      if (m[3]) {
        const c = CURRENCY_MAP[m[3].toLowerCase()];
        if (c) currency = c;
      }
      if (amount >= 100 && isValidCustomerName(customerName)) {
        parsedOrders.push({ customerName, amount, currency });
      }
    }
    } // end regex fallback

    if (parsedOrders.length > 0) {
      // 查詢當天已有訂單，建立去重 set
      const today = new Date().toISOString().split("T")[0];
      const todayOrders = await getDailyOrders(today);
      const existingSet = new Set();
      for (const o of todayOrders) {
        existingSet.add(`${o.customer_name}:${o.amount}:${o.currency || "CNY"}`);
        if (o.pinyin_name) existingSet.add(`${o.pinyin_name}:${o.amount}:${o.currency || "CNY"}`);
      }

      const CURRENCY_SYMBOL = { USD: "$", HKD: "HK$", CNY: "¥" };
      console.log(`📋 檢測到 ${parsedOrders.length} 筆客戶訂單（${parserSource}）`);
      const results = [];
      let skipped = 0;
      for (const o of parsedOrders) {
        const key = `${o.customerName}:${o.amount}:${o.currency}`;
        if (existingSet.has(key)) {
          const sym = CURRENCY_SYMBOL[o.currency] || "¥";
          console.log(`   ⏭️ 跳過重複：${o.customerName} ${sym}${o.amount.toLocaleString()}`);
          skipped++;
          continue;
        }
        try {
          const order = await createCustomerOrder({
            customer_name: o.customerName,
            amount: o.amount,
            currency: o.currency,
            group_id: msg.from,
            group_name: orderChat.name || "",
            message_timestamp: msg.timestamp ? new Date(msg.timestamp * 1000).toISOString() : new Date().toISOString(),
            raw_message: msgText
          });
          if (order) {
            const sym = CURRENCY_SYMBOL[o.currency] || "¥";
            results.push(`• ${o.customerName} ${sym}${o.amount.toLocaleString()}`);
            console.log(`   ✅ ${o.customerName} ${sym}${o.amount.toLocaleString()}`);
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

  // ── 今日訂單查詢指令（群組內直接發送關鍵詞）──
  const ORDER_QUERY_KEYWORDS = ["今日訂單", "今日订单", "查詢訂單", "查询订单", "訂單狀態", "订单状态", "/orders"];
  if (msg.from.endsWith("@g.us") && ORDER_QUERY_KEYWORDS.includes(msgText)) {
    console.log("📋 收到今日訂單查詢");
    try {
      const today = new Date().toISOString().split("T")[0];
      const dailyOrders = await getDailyOrders(today, msg.from);
      if (dailyOrders.length === 0) {
        if (WA_SEND_REPLY) await msg.reply("📋 今日尚無客戶訂單");
      } else {
        let replyText = `📋 今日客戶訂單（${dailyOrders.length} 筆）：`;
        for (const o of dailyOrders) {
          const statusText = o.matched_transaction
            ? `已匹配 → ${o.matched_transaction.customer_name || "—"}`
            : "未匹配";
          replyText += `\n  • ${o.customer_name} ¥${o.amount.toLocaleString()} — ${statusText}`;
        }
        if (WA_SEND_REPLY) await msg.reply(replyText);
      }
    } catch (e) {
      console.error("   ❌ 查詢今日訂單失敗：", e.message);
    }
    return;
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

  let text = msg.body;

  const senderId = msg.author;
  const registeredName = phoneToAgentName.get(senderId);
  const senderDisplayName = registeredName || (msg._data && msg._data.notifyName) || senderId;

  // ── Pending 管理指令 ──
  const PENDING_STATUS_CMDS = ["pending狀態", "/pending", "pending状态"];
  const CLEAR_PENDING_PREFIX = "清除pending ";
  const CLEAR_ALL_PENDING = "清除全部pending";

  if (PENDING_STATUS_CMDS.includes(msgText)) {
    if (pendingExchanges.size === 0) {
      if (WA_SEND_REPLY) await msg.reply("📋 目前沒有待處理的兌換");
    } else {
      const stateMap = { awaiting_sell_rate: "等待賣出匯率", awaiting_base_rate: "等待底價", awaiting_source_amount: "等待來源金額", awaiting_completion: "等待補全資料" };
      let reply = `📋 待處理兌換（${pendingExchanges.size} 筆）：`;
      for (const [, p] of pendingExchanges) {
        const remaining = Math.max(0, Math.round((p.expireAt - Date.now()) / 1000));
        const stateLabel = stateMap[p.state] || p.state;
        const toCur = p.toCurrency || "?";
        const info = p.paymentInfo || p.partialPaymentInfo || {};
        reply += `\n  • *${p.agentName}* — ${stateLabel}｜${info.amount?.toLocaleString?.() || "?"} ${toCur}｜${remaining}秒`;
      }
      if (WA_SEND_REPLY) await msg.reply(reply);
    }
    return;
  }

  if (msgText.startsWith(CLEAR_PENDING_PREFIX)) {
    const target = msgText.slice(CLEAR_PENDING_PREFIX.length).trim();
    if (!target) {
      if (WA_SEND_REPLY) await msg.reply("❌ 請指定 agent 名稱，例：`清除pending 張三`");
      return;
    }
    let deleted = 0;
    for (const [sid, p] of pendingExchanges) {
      if (p.agentName === target || sid === target || sid.includes(target)) {
        pendingExchanges.delete(sid);
        deleted++;
      }
    }
    if (WA_SEND_REPLY) {
      await msg.reply(deleted > 0 ? `✅ 已清除 *${target}* 的 ${deleted} 筆待處理` : `❌ 找不到 *${target}* 的待處理記錄`);
    }
    scheduleSaveState();
    return;
  }

  if (msgText === CLEAR_ALL_PENDING) {
    const count = pendingExchanges.size;
    pendingExchanges.clear();
    if (WA_SEND_REPLY) await msg.reply(`✅ 已清除全部 ${count} 筆待處理`);
    scheduleSaveState();
    return;
  }

  // ── 載入當前群的 Agent Parser 配置（若已設定） ──
  let agentParserOverrides = null;
  try {
    const groupAgentMapping = getSetting("group_agent_mapping", {});
    const agentParserConfigs = getSetting("agent_parser_configs", {});
    // group_agent_mapping key 可能是群組名稱或 chatId，兩者都嘗試匹配
    const agentName = groupAgentMapping[groupName] || groupAgentMapping[msg.from];
    if (agentName && agentParserConfigs[agentName]) {
      agentParserOverrides = agentParserConfigs[agentName];
    }
  } catch (_) { /* 解析失敗則使用默認規則 */ }

  // ── 更新公式緩衝區（保留換匯公式和分數匯率） ──
  const prevText = text;  // 向後兼容：上一條消息文本
  const parsedFormula = parseConversionLine(text) || findConversionInText(text) || parseFractionRate(text);
  if (parsedFormula) {
    if (!formulaBuffer.has(msg.from)) formulaBuffer.set(msg.from, []);
    const buffer = formulaBuffer.get(msg.from);
    buffer.push({ text, timestamp: Date.now() });
    if (buffer.length > MAX_FORMULA_BUFFER) buffer.shift();
  }

  // ── 換匯公式後發匹配：若當前消息是換匯公式，檢查是否有待處理的兌換 ──
  const trailingConv = parseConversionLine(text) || findConversionInText(text);
  if (trailingConv) {
    const pendingByFormula = pendingExchanges.get(msg.from);
    const pfPI = pendingByFormula && (pendingByFormula.paymentInfo || pendingByFormula.partialPaymentInfo);
    if (pfPI && amountsMatch(trailingConv.result_amount, pfPI.amount)) {
      const conversionResult = await resolveConversion(pfPI, text, pendingByFormula.toCurrency);
      if (conversionResult && conversionResult.auto_inferred) {
        pendingExchanges.delete(msg.from);
        const pd = pfPI.payment_details_dict || {};
        if (conversionResult.conversion) {
          pd.conversion = conversionResult.conversion;
          pfPI.payment_details = JSON.stringify(pd);
        }
        let replyMsg = `✅ 已檢測付款：${pendingByFormula.customerName}\n金額：${pfPI.amount.toLocaleString()} ${pendingByFormula.toCurrency}`;
        if (pd.bank_name) replyMsg += `\n銀行：${pd.bank_name}`;
        if (pd.account_number) replyMsg += `\n戶口：${pd.account_number}`;
        replyMsg += `\n${conversionResult.note}`;
        if (WA_SEND_REPLY) { await msg.reply(replyMsg); }
        const resolvedFrom = conversionResult.from_currency || "CNY";
        await createTransaction({
          agent_name: pendingByFormula.agentName, customer_name: pendingByFormula.customerName,
          amount: pfPI.amount, currency: pfPI.currency,
          raw_message: pfPI.raw_message, source: "whatsapp",
          group_id: msg.from,
          payment_details: pfPI.payment_details,
          from_currency: resolvedFrom,
          to_currency: pendingByFormula.toCurrency,
          remarks: pfPI.remarks || "",
          insured_person: pfPI.insured_person || "",
          timestamp: msg.timestamp ? new Date(msg.timestamp * 1000).toISOString() : new Date().toISOString()
        }, msg);
        console.log(`💾 付款資訊已記錄（公式後發自動推斷 ${resolvedFrom}→${pendingByFormula.toCurrency}，代理: ${pendingByFormula.agentName}, 客戶: ${pendingByFormula.customerName}）`);
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
      console.log("   ⚠️ 無法獲取引用消息：", e.message);
    }
  }

  // ── 第一層：帳戶查找消息過濾 ──
  if (isAccountLookupMessage(text)) {
    console.log("   🔒 檢測到帳戶查找消息，跳過付款解析");
    return;
  }

  // ── 第二層：Per-Agent skip_payment_parsing ──
  if (agentParserOverrides?.skip_payment_parsing) {
    console.log(`   🔒 agent 已設定 skip_payment_parsing，跳過付款解析`);
    return;
  }

  // ── 檢查是否有待處理的兌換方式選擇 ──
  scheduleSaveState();  // 任何 pending 操作前先排程保存
  const pending = pendingExchanges.get(msg.from);
  if (pending) {
    // 如果當前消息是新的付款信息，清除舊 pending，交給下方支付處理
    const newPaymentCheck = parsePaymentInfo(text, agentParserOverrides);
    if (newPaymentCheck && newPaymentCheck.amount > 0 && (newPaymentCheck.customer_name || "Unknown") !== "Unknown") {
      pendingExchanges.delete(msg.from);
      // fall through — 不 return，讓下方支付處理代碼執行
    } else {
    // 強制檢查過期
    if (Date.now() > pending.expireAt) {
      pendingExchanges.delete(msg.from);
      if (WA_SEND_REPLY) {
        const pInfo = pending.paymentInfo || pending.partialPaymentInfo || {};
        const amt = (pInfo.amount || 0).toLocaleString();
        await msg.reply(`⏰ 選擇已過期：${pending.customerName || "Unknown"} ${amt} ${pending.toCurrency || ""}`);
      }
      return;
    }

    // 檢查是否為顯式取消指令
    const cancelKeywords = ["取消", "撤銷", "撤销", "undo", "cancel"];
    const trimmedText = text.trim().toLowerCase();
    if (cancelKeywords.some(k => trimmedText.startsWith(k))) {
      pendingExchanges.delete(msg.from);
      if (WA_SEND_REPLY) {
        const pInfo = pending.paymentInfo || pending.partialPaymentInfo || {};
        const amt = (pInfo.amount || 0).toLocaleString();
        await msg.reply(`❌ 已取消記錄：${pending.customerName || "Unknown"} ${amt} ${pending.toCurrency || ""}`);
      }
      return;
    }

    const options = EXCHANGE_OPTIONS[pending.toCurrency] || [];

    // ── 共用：檢查是否為換匯公式（任何狀態下都可發送公式來捷徑處理）──
    const inlineFormula = parseConversionLine(text) || findConversionInText(text);
    const pendingPI = pending.paymentInfo || pending.partialPaymentInfo;
    if (inlineFormula && pendingPI && amountsMatch(inlineFormula.result_amount, pendingPI.amount)) {
      const conversionResult = await resolveConversion(pendingPI, text, pending.toCurrency);
      if (conversionResult && conversionResult.auto_inferred) {
        pendingExchanges.delete(msg.from);
        const pd = pendingPI.payment_details_dict || {};
        if (conversionResult.conversion) {
          pd.conversion = conversionResult.conversion;
          pendingPI.payment_details = JSON.stringify(pd);
        }
        let replyMsg = `✅ 已檢測付款：${pending.customerName}\n金額：${pendingPI.amount.toLocaleString()} ${pending.toCurrency}`;
        if (pd.bank_name) replyMsg += `\n銀行：${pd.bank_name}`;
        if (pd.account_number) replyMsg += `\n戶口：${pd.account_number}`;
        replyMsg += `\n${conversionResult.note}`;
        if (WA_SEND_REPLY) { await msg.reply(replyMsg); }
        const inferredFrom = conversionResult.from_currency || "CNY";
        await createTransaction({
          agent_name: pending.agentName, customer_name: pending.customerName,
          amount: pendingPI.amount, currency: pendingPI.currency,
          raw_message: pendingPI.raw_message, source: "whatsapp",
          group_id: msg.from,
          payment_details: pendingPI.payment_details,
          from_currency: inferredFrom,
          to_currency: pending.toCurrency,
          remarks: pendingPI.remarks || "",
          insured_person: pendingPI.insured_person || "",
          timestamp: msg.timestamp ? new Date(msg.timestamp * 1000).toISOString() : new Date().toISOString()
        }, msg);
        console.log(`💾 付款資訊已記錄（pending 公式捷徑 ${inferredFrom}→${pending.toCurrency}，代理: ${pending.agentName}, 客戶: ${pending.customerName}）`);
        return;
      }
    }

    // ═══════════════════════════════════════════
    let completed = false;  // awaiting_completion 成功標記

    //  State 1: awaiting_sell_rate — 等待賣出匯率
    // ═══════════════════════════════════════════
    if (pending.state === "awaiting_sell_rate") {
      // 優先檢查是否為菜單數字選擇（處理「1」「2」等同時是有效 parseFloat 的輸入）
      const num = parseInt(trimmedText);
      if (!isNaN(num) && num === options.length + 1) {
        pendingExchanges.delete(msg.from);
        if (WA_SEND_REPLY) await msg.reply(`❌ 已取消記錄：${pending.customerName} ${pending.paymentInfo.amount.toLocaleString()} ${pending.toCurrency}`);
        return;
      }
      if (!isNaN(num) && num >= 1 && num <= options.length) {
        const chosen = options[num - 1];
        pending.sourceCurrency = chosen.from;
        pending.sellRate = null;
        // 追問匯率
        if (WA_SEND_REPLY) {
          await msg.reply(`✅ 已選 ${chosen.label}\n📝 *請回覆賣出匯率*，例：7.08`);
        }
        pending.expireAt = Date.now() + 10 * 60 * 1000;
        return;
      }

      // 優先檢查是否為分數匯率（如 "0.99/0.982"）
      const fractionRate = parseFractionRate(text);
      if (fractionRate) {
        pending.sellRate = fractionRate.sell_rate;
        pending.sourceCurrency = null;  // 由 inferSourceCurrency 推斷
        const today = new Date().toISOString().split("T")[0];
        // 成本價作為底價匯率存入
        const inferred = await inferSourceCurrency(fractionRate.sell_rate, pending.toCurrency);
        if (inferred) {
          pending.sourceCurrency = inferred.from;
          pending.baseRate = fractionRate.cost_rate;
          // 存入 collectedBaseRates
          const key = `${today}:${inferred.from}:${pending.toCurrency}`;
          collectedBaseRates.set(key, fractionRate.cost_rate);
          // 自動推算來源金額並完成
          const sourceAmount = autoCalculateSourceAmount(pending.paymentInfo.amount, pending.sellRate, pending.sourceCurrency);
          if (!sourceAmount) {
            // fallback：推算失敗，回退詢問
            pending.state = "awaiting_source_amount";
            pending.expireAt = Date.now() + 10 * 60 * 1000;
            if (WA_SEND_REPLY) {
              await msg.reply(`✅ 賣出 ${fractionRate.sell_rate}｜底價 ${fractionRate.cost_rate} 已記錄\n📝 *請回覆兌換前 ${inferred.from} 金額*，例：300w`);
            }
            return;
          }
          await recordTransactionFromPending(pending, sourceAmount, msg);
          return;
        }
      }

      // 防止將公式誤當成匯率數字（如 "2000w/7.01=285307usd" → parseFloat 得 2000）
      const asFormula = parseConversionLine(trimmedText) || findConversionInText(trimmedText);
      if (asFormula) {
        const pInfo = pending.paymentInfo || pending.partialPaymentInfo;
        if (!pInfo) { return; }
        // 先檢查公式結果金額是否匹配付款金額
        if (!amountsMatch(asFormula.result_amount, pInfo.amount)) {
          if (WA_SEND_REPLY) {
            await msg.reply(`⚠️ 公式結果金額與付款金額不符\n公式結果：${(asFormula.result_amount || 0).toLocaleString()} ≠ 付款金額：${pInfo.amount.toLocaleString()}\n💡 請檢查公式或回覆賣出匯率數字`);
          }
          return;
        }
        // 金額匹配 → 嘗試 resolve
        const convResult = await resolveConversion(pInfo, text, pending.toCurrency);
        if (convResult && convResult.auto_inferred) {
          pendingExchanges.delete(msg.from);
          const pd = pInfo.payment_details_dict || {};
          if (convResult.conversion) {
            pd.conversion = convResult.conversion;
            pInfo.payment_details = JSON.stringify(pd);
          }
          let replyMsg = `✅ 已檢測付款：${pending.customerName}\n金額：${pInfo.amount.toLocaleString()} ${pending.toCurrency}`;
          if (pd.bank_name) replyMsg += `\n銀行：${pd.bank_name}`;
          if (pd.account_number) replyMsg += `\n戶口：${pd.account_number}`;
          replyMsg += `\n${convResult.note}`;
          if (WA_SEND_REPLY) { await msg.reply(replyMsg); }
          const resolvedFrom = convResult.from_currency || "CNY";
          await createTransaction({
            agent_name: pending.agentName, customer_name: pending.customerName,
            amount: pInfo.amount, currency: pInfo.currency,
            raw_message: pInfo.raw_message, source: "whatsapp",
            group_id: msg.from,
            payment_details: pInfo.payment_details,
            from_currency: resolvedFrom,
            to_currency: pending.toCurrency,
            remarks: pInfo.remarks || "",
            insured_person: pInfo.insured_person || "",
            timestamp: msg.timestamp ? new Date(msg.timestamp * 1000).toISOString() : new Date().toISOString()
          }, msg);
          return;
        }
        // resolveConversion 失敗（公式內部算術錯誤）→ 提示
        if (WA_SEND_REPLY) {
          const isMultiply = asFormula.operator === "*";
          const expected = isMultiply
            ? Math.round(asFormula.source_amount * asFormula.rate)
            : Math.round(asFormula.source_amount / asFormula.rate);
          await msg.reply(`⚠️ 公式結果金額與付款金額匹配，但公式內部算術不一致\n${asFormula.source_amount.toLocaleString()} ${isMultiply ? "×" : "÷"} ${asFormula.rate} 應為 ${expected.toLocaleString()}，而非 ${asFormula.result_amount.toLocaleString()}\n💡 請檢查公式或回覆賣出匯率數字`);
        }
        return;
      }

      const rateNum = parseFloat(trimmedText);
      if (isNaN(rateNum) || rateNum <= 0) {
        if (WA_SEND_REPLY) {
          const circ = ["①", "②", "③", "④", "⑤"];
          const optList = options.map((o, i) => `${circ[i]}${o.label}`).join("  ");
          await msg.reply(`❌ 請回覆數字，例：7.01\n幣種：${optList}  ${circ[options.length]}取消`);
        }
        return;
      }

      // 推斷來源幣種
      pending.sellRate = rateNum;
      const inferred = await inferSourceCurrency(rateNum, pending.toCurrency);
      if (inferred) {
        pending.sourceCurrency = inferred.from;
      } else {
        // 無法推斷，讓用戶手動選擇
        if (WA_SEND_REPLY) {
          const circ = ["①", "②", "③", "④", "⑤"];
          const optList = options.map((o, i) => `${circ[i]}${o.label}`).join("  ");
          await msg.reply(`⚠️ 無法推斷幣種，請回覆數字：\n${optList}  ${circ[options.length]}取消`);
        }
        return;
      }

      // ── 已獲得賣出匯率和來源幣種，檢查底價匯率 ──
      let baseRate = await getBaseRate(pending.sourceCurrency, pending.toCurrency);
      // USDT → USD：若無當天收集底價，從 preset 或預設 0.99 取，不追問
      if (baseRate === null && pending.sourceCurrency === "USDT" && pending.toCurrency === "USD") {
        const presetRates = getSetting("preset_exchange_rates", {});
        baseRate = presetRates["USDT:USD"] !== undefined ? presetRates["USDT:USD"] : 0.99;
      }
      if (baseRate) {
        // 有底價匯率，自動推算來源金額並完成
        pending.baseRate = baseRate;
        const sourceAmount = autoCalculateSourceAmount(pending.paymentInfo.amount, pending.sellRate, pending.sourceCurrency);
        if (!sourceAmount) {
          // fallback：推算失敗
          pending.state = "awaiting_source_amount";
          pending.expireAt = Date.now() + 10 * 60 * 1000;
          if (WA_SEND_REPLY) {
            await msg.reply(`✅ 賣出 ${rateNum}\n📝 *請回覆兌換前 ${pending.sourceCurrency} 金額*，例：300w`);
          }
          return;
        }
        await recordTransactionFromPending(pending, sourceAmount, msg);
        return;
      } else {
        // 無底價匯率，追問
        pending.state = "awaiting_base_rate";
        pending.expireAt = Date.now() + 10 * 60 * 1000;
        const fromLabel = `${pending.sourceCurrency} → ${pending.toCurrency}`;
        if (WA_SEND_REPLY) {
          await msg.reply(`✅ 賣出 ${rateNum}\n📝 *請回覆今天底價匯率*，例：0.99（0=跳過）`);
        }
      }
      return;
    }

    // ═══════════════════════════════════════════
    //  State 2: awaiting_base_rate — 等待底價匯率
    // ═══════════════════════════════════════════
    if (pending.state === "awaiting_base_rate") {
      const rateNum = parseFloat(trimmedText);
      if (isNaN(rateNum) || rateNum < 0) {
        if (WA_SEND_REPLY) {
          await msg.reply(`❌ 請回覆數字，例：0.99（或 0 跳過）`);
        }
        return;
      }

      if (rateNum === 0) {
        pending.baseRate = null;  // 跳過底價
      } else {
        pending.baseRate = rateNum;
        // 存入 collectedBaseRates 供當天後續使用
        const today = new Date().toISOString().split("T")[0];
        const key = `${today}:${pending.sourceCurrency}:${pending.toCurrency}`;
        collectedBaseRates.set(key, rateNum);
        // 同時更新 preset_exchange_rates
        try {
          const presetRates = getSetting("preset_exchange_rates", {});
          const pair = `${pending.sourceCurrency}:${pending.toCurrency}`;
          presetRates[pair] = rateNum;
          await axios.put(`${API_BASE_URL}/settings`, { preset_exchange_rates: presetRates }, { headers: getHeaders() });
          console.log(`   📌 已更新預設匯率：${pair} = ${rateNum}`);
        } catch (e) { /* 非關鍵 */ }
      }

      // 自動推算來源金額並完成
      const sourceAmount = autoCalculateSourceAmount(pending.paymentInfo.amount, pending.sellRate, pending.sourceCurrency);
      if (!sourceAmount) {
        // fallback：推算失敗
        pending.state = "awaiting_source_amount";
        pending.expireAt = Date.now() + 10 * 60 * 1000;
        if (WA_SEND_REPLY) {
          await msg.reply(`✅ 底價已記錄\n📝 *請回覆兌換前 ${pending.sourceCurrency} 金額*，例：300w`);
        }
        return;
      }
      await recordTransactionFromPending(pending, sourceAmount, msg);
      return;
    }

    // ═══════════════════════════════════════════
    //  State 3: awaiting_source_amount — 等待兌換前金額
    // ═══════════════════════════════════════════
    if (pending.state === "awaiting_source_amount") {
      const sourceAmount = parseSourceAmount(text);
      if (!sourceAmount) {
        if (WA_SEND_REPLY) {
          await msg.reply(`❌ 請回覆數字，例：300w 或 3000000`);
        }
        return;
      }

      pendingExchanges.delete(msg.from);

      const pd = pending.paymentInfo.payment_details_dict || {};
      const conversion = {
        source_amount: sourceAmount,
        rate: pending.sellRate,
        source_currency: pending.sourceCurrency,
        operator: (pending.sourceCurrency === "USDT" || pending.sourceCurrency === "USD") ? "*" : "/",
        matched: pending.baseRate ? true : false,
        daily_rate: pending.baseRate || null,
        rate_source: pending.baseRate ? "manual_collected" : "manual_skip",
      };
      pd.conversion = conversion;
      pending.paymentInfo.payment_details = JSON.stringify(pd);

      const success = await createTransaction({
        agent_name: pending.agentName,
        customer_name: pending.customerName,
        amount: pending.paymentInfo.amount,
        currency: pending.paymentInfo.currency,
        raw_message: pending.paymentInfo.raw_message,
        source: "whatsapp",
        group_id: msg.from,
        payment_details: pending.paymentInfo.payment_details,
        from_currency: pending.sourceCurrency,
        to_currency: pending.toCurrency,
        remarks: pending.paymentInfo.remarks || "",
        insured_person: pending.paymentInfo.insured_person || "",
        timestamp: msg.timestamp ? new Date(msg.timestamp * 1000).toISOString() : new Date().toISOString()
      }, msg);

      if (success && WA_SEND_REPLY) {
        let replyMsg = `✅ 已紀錄收款：${pending.customerName}\n兌換：${pending.sourceCurrency} → ${pending.toCurrency}\n金額：${pending.paymentInfo.amount.toLocaleString()} ${pending.toCurrency}`;
        replyMsg += `\n📐 賣出匯率：${pending.sellRate} | 來源金額：${sourceAmount.toLocaleString()} ${pending.sourceCurrency}`;
        if (pending.baseRate) replyMsg += `\n📉 底價匯率：${pending.baseRate}`;
        if (pending.paymentInfo.remarks) replyMsg += `\n備註：${pending.paymentInfo.remarks}`;
        if (pending.paymentInfo.insured_person) replyMsg += `\n投保人：${pending.paymentInfo.insured_person}`;
        await msg.reply(replyMsg);
      }
      return;
    }

    // ═══════════════════════════════════════════
    //  State 4: awaiting_completion — 等待補全付款資訊
    // ═══════════════════════════════════════════
    if (pending.state === "awaiting_completion") {
      completed = false;
      const completion = parseCompletionFields(text);
      const hasNewFields = Object.keys(completion.fields).length > 0 || Object.keys(completion.pd).length > 0;

      if (hasNewFields) {
        const pInfo = pending.partialPaymentInfo;
        if (!pInfo.payment_details_dict) {
          try { pInfo.payment_details_dict = JSON.parse(pInfo.payment_details || "{}"); } catch (_) { pInfo.payment_details_dict = {}; }
        }
        const pd = pInfo.payment_details_dict || {};
        if (completion.fields.amount) pInfo.amount = completion.fields.amount;
        if (completion.fields.currency) pInfo.currency = completion.fields.currency;
        if (completion.fields.customer_name) pInfo.customer_name = completion.fields.customer_name;
        for (const [k, v] of Object.entries(completion.pd)) { if (v) pd[k] = v; }
        pInfo.payment_details = JSON.stringify(pd);

        const newWarnings = [];
        if (!pd.account_number) newWarnings.push("❌ 缺少戶口號碼");
        if (!pd.account_name && !pInfo.customer_name) newWarnings.push("❌ 缺少戶口全名");
        if (!pd.swift && !pd.bank_name && !pd.bank_code) newWarnings.push("❌ 缺少銀行識別資訊");
        if (!pInfo.amount || pInfo.amount <= 0) newWarnings.push("❌ 缺少或無法解析交易金額");

        if (newWarnings.length === 0) {
          pendingExchanges.delete(msg.from);
          const pd2 = pInfo.payment_details_dict || {};
          text = `戶口全名：${pInfo.customer_name || pd2.account_name || ""}\n銀行名稱：${pd2.bank_name || ""}\n戶口號碼：${pd2.account_number || ""}\n金額：${pInfo.amount} ${pInfo.currency || "USD"}`;
          if (pd2.swift) text += `\nSWIFT：${pd2.swift}`;
          if (pd2.bank_code) text += `\n銀行代碼：${pd2.bank_code}`;
          console.log("   ✅ 付款資訊已補全，重新進入主流程");
          completed = true;
        } else {
          pending.partialPaymentInfo = pInfo;
          pending.expireAt = Date.now() + 10 * 60 * 1000;
          if (WA_SEND_REPLY) {
            await msg.reply(`⚠️ 仍缺少以下欄位，請繼續補充：\n${newWarnings.join("\n")}\n💡 也可重新發送完整交易信息\n💡 回覆「取消」可取消`);
          }
          return;
        }
      }

      if (!completed) {
        if (WA_SEND_REPLY) {
          const errWarnings = (pending.partialPaymentInfo.warnings || []).filter(w => w.startsWith("❌"));
          await msg.reply(`⚠️ 未檢測到有效欄位，請補充：\n${errWarnings.join("\n")}\n💡 例：戶口全名：CHAN TAI MAN\n💡 回覆「取消」可取消`);
        }
        return;
      }
      // completed=true → fall through 到主付款解析
      if (completed) {
        // pending 已清除，reconstructed text 已在 text 變數中，繼續往下走到主付款解析
      } else {
        return;
      }
    }

    // 未知 state → 忽略（除非是 awaiting_completion 補全成功 fall through）
    if (!completed) return;
  }
  }  // close if (pending) after else block

  // ── 第二層：Per-Agent skip_payment_parsing ──
  if (agentParserOverrides?.skip_payment_parsing) {
    console.log(`   🔒 agent 已設定 skip_payment_parsing，跳過付款解析`);
    return;
  }

  // ── 第一層：帳戶查找消息過濾（防止客戶轉款帳號被誤判為付款）──
  if (isAccountLookupMessage(text)) {
    console.log("   🔒 檢測到帳戶查找消息，跳過付款解析");
    return;
  }

  // ── 優先檢查是否為結構化付款資訊 ──
  let paymentInfo = parsePaymentInfo(text, agentParserOverrides);

  // ── AI 兜底：正則失敗、不完整、或有阻斷錯誤時嘗試 AI ──
  let regexHadErrors = false;
  if (paymentInfo) {
    regexHadErrors = (paymentInfo.warnings || []).some(w => w.startsWith("❌"));
  }
  if (!paymentInfo || paymentInfo.amount <= 0 || (paymentInfo.customer_name || "") === "Unknown" || regexHadErrors) {
    try {
      const aiResult = await extractPaymentInfo(text);
      if (aiResult && aiResult.amount > 0 && aiResult.customer_name && aiResult.customer_name !== "Unknown") {
        // 事後校驗：檢查關鍵字段完整性，補 warnings
        const aiWarnings = [];
        const pd = aiResult.payment_details_dict || {};
        if (!pd.account_number) aiWarnings.push("❌ 缺少戶口號碼");
        if (!pd.account_name) aiWarnings.push("❌ 缺少戶口全名");
        if (!pd.swift && !pd.bank_name && !pd.bank_code) aiWarnings.push("❌ 缺少銀行識別資訊（SWIFT、銀行名稱或銀行代碼至少需要一項）");
        aiResult.warnings = aiWarnings;
        paymentInfo = aiResult;
        console.log("   🤖 AI 支付解析兜底成功");
      }
    } catch (e) {
      console.log(`   ⚠️ AI 支付解析異常：${e.message}`);
    }
  }

  if (paymentInfo) {
    const customerName = paymentInfo.customer_name || "Unknown";
    console.log(`🏦 檢測到付款資訊: 客戶=${customerName} ${paymentInfo.amount} ${paymentInfo.currency}`);
    const warnings = paymentInfo.warnings || [];
    const hasErrors = warnings.some(w => w.startsWith("❌"));
    const hasWarnings = warnings.some(w => w.startsWith("⚠️"));

    // ── KYC 預填訊息檢測：MSO 金額為 "xxx" / "xxxx" 佔位符 → 僅記錄客戶帳戶，不創建交易 ──
    if (/(?:Mso[- ]?Pobo|MSO)\s*[：:]\s*x{2,}/i.test(paymentInfo.raw_message || msgText)) {
      console.log("   📋 檢測到 KYC 預填訊息，僅記錄客戶帳戶資訊");
      if (customerName !== "Unknown" && paymentInfo.payment_details) {
        try {
          await axios.post(`${API_BASE_URL}/customer-accounts/record`, {
            customer_name: customerName,
            payment_details: paymentInfo.payment_details,
            group_id: msg.from
          }, { headers: getHeaders() });
          console.log("   📋 客戶帳戶資訊已記錄");
        } catch (e) {
          if (e.response?.status === 401) { await login(); }
          console.log("   ⚠️ 記錄客戶帳戶失敗：", e.message);
        }
      }
      return;
    }

    // ── 有嚴重錯誤（缺必填欄位）→ 暫存並讓 agent 補全 ──
    if (hasErrors) {
      const toCurrency = (paymentInfo.currency || "HKD").toUpperCase();
      // 只顯示 ❌ 的錯誤，不顯示 ⚠️
      const errWarnings = warnings.filter(w => w.startsWith("❌"));
      if (WA_SEND_REPLY) {
        await msg.reply(
          `⚠️ 付款資訊不完整，請補充：\n${errWarnings.join("\n")}\n💡 可直接回覆缺失欄位（例：戶口全名：CHEN XIA）\n💡 也可重新發送完整交易信息\n💡 回覆「取消」可取消`
        );
      }
      // 提醒同群舊 pending 被覆蓋
      if (pendingExchanges.has(msg.from) && WA_SEND_REPLY) {
        const old = pendingExchanges.get(msg.from);
        const oldName = old.customerName || old.agentName || "Unknown";
        const oldAmt = ((old.paymentInfo || old.partialPaymentInfo || {}).amount || 0).toLocaleString();
        await msg.reply(`⚠️ 上一筆待處理交易已被新交易取代：${oldName} ${oldAmt}`);
      }
      pendingExchanges.set(msg.from, {
        agentName: senderDisplayName,
        customerName: customerName !== "Unknown" ? customerName : "",
        toCurrency,
        state: "awaiting_completion",
        expireAt: Date.now() + 10 * 60 * 1000,
        chat: msg.from,
      });
      scheduleSaveState();
      return;
    }

    if (paymentInfo.amount > 0 && customerName !== "Unknown") {
      const toCurrency = (paymentInfo.currency || "HKD").toUpperCase();
      const options = EXCHANGE_OPTIONS[toCurrency];

      const pd = paymentInfo.payment_details_dict || {};
      let replyMsg = `✅ 已檢測付款：${customerName}\n金額：${paymentInfo.amount.toLocaleString()} ${toCurrency}`;
      if (pd.bank_name) replyMsg += `\n銀行：${pd.bank_name}`;
      if (pd.account_number) replyMsg += `\n戶口：${pd.account_number}`;

      // ── 三層搜索換匯公式：引用消息 → 公式緩衝區 → 上一條消息 ──
      let formulaText = null;

      // 1. 檢查引用消息中是否有換匯公式
      let quotedText = null;
      if (msg.from.endsWith("@g.us") && msg.hasQuotedMsg) {
        try {
          const quotedMsg = await msg.getQuotedMessage();
          quotedText = quotedMsg.body || "";
          if (quotedText && (parseConversionLine(quotedText) || findConversionInText(quotedText))) {
            formulaText = quotedText;
          }
        } catch (e) { /* ignore */ }
      }

      // 2. 從公式緩衝區搜尋
      if (!formulaText) {
        formulaText = findFormulaInBuffer(msg.from, paymentInfo.amount);
      }

      // 3. 向後兼容：上一條消息
      if (!formulaText) {
        formulaText = prevText;
      }

      const conversionResult = await resolveConversion(paymentInfo, formulaText, toCurrency);

      if (conversionResult && conversionResult.auto_inferred) {
        // 自動推斷成功，跳過兌換選單直接記錄
        if (conversionResult.conversion) {
          pd.conversion = conversionResult.conversion;
          paymentInfo.payment_details = JSON.stringify(pd);
        }
        replyMsg += `\n${conversionResult.note}`;
        if (hasWarnings) replyMsg += "\n\n⚠️ 請注意：\n" + warnings.join("\n");
        if (WA_SEND_REPLY) { await msg.reply(replyMsg); }
        const inferredFrom = conversionResult.from_currency || "CNY";
        await createTransaction({
          agent_name: senderDisplayName, customer_name: customerName,
          amount: paymentInfo.amount, currency: paymentInfo.currency,
          raw_message: paymentInfo.raw_message, source: "whatsapp",
          group_id: msg.from,
          payment_details: paymentInfo.payment_details,
          from_currency: inferredFrom,
          to_currency: toCurrency,
          remarks: paymentInfo.remarks || "",
          insured_person: paymentInfo.insured_person || "",
          timestamp: msg.timestamp ? new Date(msg.timestamp * 1000).toISOString() : new Date().toISOString()
        }, msg);
        console.log(`💾 付款資訊已記錄（自動推斷 ${inferredFrom}→${toCurrency}，代理: ${senderDisplayName}, 客戶: ${customerName}）`);
      } else if (options && options.length > 0) {
        // 有兌換選項，先追問賣出匯率
        if (conversionResult && conversionResult.note) {
          replyMsg += `\n${conversionResult.note}`;
        }
        replyMsg += `\n\n📝 *請回覆賣出匯率*\n例：7.01（人→美）或 0.982（USDT→美）\n💡 也可發送換匯公式，例：200w / 7.01 = 285,307 USD\n💡 回覆「取消」可取消`;
        if (hasWarnings) replyMsg += "\n\n⚠️ 請注意：\n" + warnings.join("\n");

        if (WA_SEND_REPLY) { await msg.reply(replyMsg); }
        // 暫存，等待代理回覆賣出匯率或換匯公式
        if (pendingExchanges.has(msg.from) && WA_SEND_REPLY) {
          const old = pendingExchanges.get(msg.from);
          const oldName = old.customerName || old.agentName || "Unknown";
          const oldAmt = ((old.paymentInfo || old.partialPaymentInfo || {}).amount || 0).toLocaleString();
          await msg.reply(`⚠️ 上一筆待處理交易已被新交易取代：${oldName} ${oldAmt}`);
        }
        pendingExchanges.set(msg.from, {
          paymentInfo, agentName: senderDisplayName,
          customerName, toCurrency,
          conversionInfo: conversionResult ? conversionResult.conversion : null,
          state: "awaiting_sell_rate",
          sellRate: null,
          sourceCurrency: null,
          baseRate: null,
          expireAt: Date.now() + 10 * 60 * 1000,
          chat: msg.from,
        });
        scheduleSaveState();
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
          group_id: msg.from,
          payment_details: paymentInfo.payment_details,
          from_currency: conversionResult ? conversionResult.from_currency || "" : "",
          to_currency: toCurrency,
          remarks: paymentInfo.remarks || "",
          insured_person: paymentInfo.insured_person || "",
          timestamp: msg.timestamp ? new Date(msg.timestamp * 1000).toISOString() : new Date().toISOString()
        }, msg);
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
        const lastTx = await getLastTransaction(null, msg.from);
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
        const lastTx = await getLastTransaction(cancellation.agent_name, msg.from);
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
  scheduleSaveState();
  } catch (err) {
    console.error("❌ 消息處理異常：", err.message, err.stack);
  }
}

// 處理斷線重連（指數退避）
client.on("disconnected", (reason) => {
  if (isReconnecting) return;
  console.warn("⚠️ WhatsApp 已斷線：", reason);
  isReconnecting = true;
  reconnectAttempts++;
  const delay = Math.min(5000 * Math.pow(2, reconnectAttempts - 1), MAX_RECONNECT_DELAY_MS);
  console.log(`🔄 ${Math.round(delay / 1000)} 秒後嘗試重連（第 ${reconnectAttempts} 次）...`);
  setTimeout(async () => {
    try { await reconnect(`disconnected:${reason}`); } catch (err) {
      console.error("❌ 重連失敗：", err.message);
    }
    isReconnecting = false;
  }, delay);
});

// 啟動
client.initialize();