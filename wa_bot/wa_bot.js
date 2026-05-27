// wa_bot/wa_bot.js
const { Client, LocalAuth } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const axios = require("axios");
require("dotenv").config();

// ==================== 配置 ====================
const API_BASE_URL = process.env.API_BASE_URL || "http://localhost:8000";
const API_USERNAME = process.env.API_USERNAME || "admin";
const API_PASSWORD = process.env.API_PASSWORD || "admin123";
const WATCH_GROUP_NAMES = (process.env.WATCH_GROUP_NAMES || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

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
    const res = await axios.post(`${API_BASE_URL}/transactions/`, data, {
      headers: getHeaders(),
    });
    return res.status === 200;
  } catch (err) {
    // 令牌過期則重新登錄後重試
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
function parseTransaction(messageText) {
  // 標準化文本：移除常見標點與關鍵詞
  let text = messageText
    .trim()
    .replace(/，/g, "")
    .replace(/元/g, "")
    .replace(/HKD/g, "")
    .replace(/成交/g, "交易")
    .replace(/完成交易/g, "交易");

  const patterns = [
    /【交易】(.+?)\s+交易\s+(\d[\d,]*)/,
    /交易[:：]\s*(.+?)\s+(\d[\d,]*)/,
    /(.+?)\s+今日交易\s+(\d[\d,]*)/,
    /Transaction[:：]\s*(.+?)\s+(\d[\d,]*)/i,
    /(.+?)\s+transaction\s+(\d[\d,]*)/i,
  ];

  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) {
      const agentName = match[1].trim();
      const amount = parseInt(match[2].replace(/,/g, ""), 10);
      if (!isNaN(amount) && amount > 0) {
        return {
          agent_name: agentName,
          amount: amount,
          raw_message: messageText,
          source: "whatsapp",
        };
      }
    }
  }

  return null;
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

  const chat = await msg.getChat();
  const groupName = chat.name;

  if (
    WATCH_GROUP_NAMES.length > 0 &&
    !WATCH_GROUP_NAMES.includes(groupName)
  ) {
    console.log(`   ⚠️ 群組「${groupName}」不在監控列表，已跳過`);
    return;
  }

  const text = msg.body;
  const parsed = parseTransaction(text);

  if (!parsed) {
    console.log("   ⚪ 消息格式無法解析");
    return;
  }

  console.log(`   📨 解析成功：${parsed.agent_name} ${parsed.amount} HKD`);

  const allowed = await isAgentAllowed(parsed.agent_name);
  if (!allowed) {
    console.log(`   ⚠️ 代理「${parsed.agent_name}」不在白名單`);
    return;
  }

  const success = await createTransaction(parsed);
  if (success) {
    console.log(`   ✅ 交易已記錄！`);
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