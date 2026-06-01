// wa_bot/wa_bot.js
const { Client, LocalAuth } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const axios = require("axios");
const { parsePaymentInfo } = require("./payment_parser");
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
      console.log(`🔄 系統設置已刷新（TG: ${tg} | WA: ${wa}）`);
      return true;
    }
    return false;
  } catch (err) {
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
      amount: data.amount,
      currency: data.currency || "USD",
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
        return { agent_name: agent, amount, raw_message: raw, source: "whatsapp" };
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
        return { agent_name: agent, amount, raw_message: raw, source: "whatsapp" };
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
      return { agent_name: agent, amount, raw_message: raw, source: "whatsapp" };
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
  if (!remainder || ["上一筆", "上一笔", "上一单", "last", "上一條", "上一"].includes(remainder)) {
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
    agent = tempParsed.agent_name;
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

// ==================== WhatsApp 客戶端 ====================
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

  // ── 優先檢查是否為結構化付款資訊 ──
  const paymentInfo = parsePaymentInfo(text);
  if (paymentInfo) {
    console.log(`🏦 檢測到付款資訊: ${paymentInfo.agent_name} ${paymentInfo.amount} ${paymentInfo.currency}`);
    const warnings = paymentInfo.warnings || [];
    const hasErrors = warnings.some(w => w.startsWith("❌"));
    const hasWarnings = warnings.some(w => w.startsWith("⚠️"));

    // ── 有嚴重錯誤（缺必填欄位）→ 阻擋記錄，只回報錯誤 ──
    if (hasErrors) {
      if (WA_SEND_REPLY) {
        await msg.reply("❌ 付款資訊不完整，請修正後重新發送：\n\n" + warnings.join("\n"));
      }
      return;
    }

    if (paymentInfo.amount > 0 && paymentInfo.agent_name !== "Unknown") {
      const allowed = await isAgentAllowed(paymentInfo.agent_name);
      if (!allowed) {
        console.log(`   ⚠️ 戶口全名「${paymentInfo.agent_name}」不在白名單`);
        if (WA_SEND_REPLY) {
          await msg.reply(`⚠️ 戶口全名「${paymentInfo.agent_name}」不在白名單中，付款未記錄`);
        }
        return;
      }

      const success = await createTransaction({
        agent_name: paymentInfo.agent_name,
        amount: paymentInfo.amount,
        currency: paymentInfo.currency,
        raw_message: paymentInfo.raw_message,
        source: "whatsapp",
        payment_details: paymentInfo.payment_details
      });

      if (success) {
        console.log(`   ✅ 付款資訊已記錄！`);
        if (WA_SEND_REPLY) {
          const pd = paymentInfo.payment_details_dict || {};
          let replyMsg = `✅ 已紀錄收款：${paymentInfo.agent_name}\n金額：${paymentInfo.amount.toLocaleString()} ${paymentInfo.currency}`;
          if (pd.bank_name) replyMsg += `\n銀行：${pd.bank_name}`;
          if (pd.account_number) replyMsg += `\n戶口：${pd.account_number}`;
          if (hasWarnings) replyMsg += "\n\n⚠️ 請注意以下問題：\n" + warnings.join("\n");
          await msg.reply(replyMsg);
        }
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
          console.log(`   ✅ 已取消上一筆 [${src}]：${lastTx.agent_name} ${lastTx.amount} ${cur}`);
          if (WA_SEND_REPLY) {
            await msg.reply(`✅ 已取消上一筆 WhatsApp 交易：${lastTx.agent_name} ${lastTx.amount.toLocaleString()} ${cur}`);
          }
        } else {
          if (WA_SEND_REPLY) await msg.reply("⚠️ 沒有找到可取消的 WhatsApp 交易記錄");
        }
      } else if (cancellation.target === "agent") {
        const lastTx = await getLastTransaction(cancellation.agent_name);
        if (lastTx) {
          await deleteTransactionById(lastTx.id);
          const cur = lastTx.currency || "USD";
          console.log(`   ✅ 已取消 ${cancellation.agent_name} 的交易`);
          if (WA_SEND_REPLY) {
            await msg.reply(`✅ 已取消 ${cancellation.agent_name} 的最近一筆 WhatsApp 交易：${lastTx.amount.toLocaleString()} ${cur}`);
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

  // ── 解析交易 ──
  const parsed = parseTransaction(text);
  if (!parsed) {
    console.log("   ⚪ 消息格式無法解析");
    return;
  }

  console.log(`   📨 解析成功：${parsed.agent_name} ${parsed.amount} HKD`);

  const allowed = await isAgentAllowed(parsed.agent_name);
  if (!allowed) {
    console.log(`   ⚠️ 代理「${parsed.agent_name}」不在白名單`);
    if (WA_SEND_REPLY) {
      await msg.reply(`⚠️ 代理「${parsed.agent_name}」不在白名單中，交易未記錄`);
    }
    return;
  }

  const success = await createTransaction(parsed);
  if (success) {
    console.log(`   ✅ 交易已記錄！`);
    if (WA_SEND_REPLY) {
      await msg.reply(`✅ 已紀錄交易：${parsed.agent_name} 成交 ${parsed.amount.toLocaleString()} HKD`);
    }
  }
});

// 處理斷線重連
client.on("disconnected", (reason) => {
  console.warn("⚠️ WhatsApp 已斷線：", reason);
  console.log("🔄 5秒後嘗試重連...");
  setTimeout(() => client.initialize(), 5000);
});

// 啟動
client.initialize();