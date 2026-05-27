// wa_bot/test_api.js
const axios = require("axios");
require("dotenv").config();

const API_BASE_URL = process.env.API_BASE_URL || "http://localhost:8000";
const API_USERNAME = process.env.API_USERNAME || "admin";
const API_PASSWORD = process.env.API_PASSWORD || "admin123";

async function runTest() {
  console.log("========== API 提交流程測試 ==========\n");

  // 1. 登錄
  console.log("【步驟1】登錄後端...");
  const params = new URLSearchParams();
  params.append("username", API_USERNAME);
  params.append("password", API_PASSWORD);
  const loginRes = await axios.post(`${API_BASE_URL}/auth/login`, params);
  const token = loginRes.data.access_token;
  const headers = { Authorization: `Bearer ${token}` };
  console.log("✅ 登錄成功\n");

  // 2. 查詢代理白名單，取第一個代理來測試
  console.log("【步驟2】獲取代理白名單...");
  const agentsRes = await axios.get(`${API_BASE_URL}/agents/`, { headers });
  const agents = agentsRes.data;

  if (agents.length === 0) {
    console.log("⚠️ 白名單為空，請先在後端新增代理再測試");
    return;
  }

  const testAgent = agents[0].agent_name;
  console.log(`✅ 找到代理：${agents.map(a => a.agent_name).join(", ")}`);
  console.log(`   使用「${testAgent}」進行測試\n`);

  // 3. 提交模擬交易
  console.log("【步驟3】提交模擬WhatsApp交易...");
  const mockTransaction = {
    agent_name: testAgent,
    amount: 8888,
    raw_message: `【成交】${testAgent} 完成交易 金額8888元`,
    source: "whatsapp",
  };

  const txRes = await axios.post(
    `${API_BASE_URL}/transactions/`,
    mockTransaction,
    { headers }
  );
  console.log("✅ 交易提交成功：", JSON.stringify(txRes.data, null, 2), "\n");

  // 4. 查詢今日統計確認已記錄
  console.log("【步驟4】查詢今日統計確認...");
  const statsRes = await axios.get(`${API_BASE_URL}/transactions/daily`, { headers });
  console.log("✅ 今日統計：", JSON.stringify(statsRes.data, null, 2));
}

runTest().catch(err => {
  console.error("❌ 測試失敗：", err.response?.data || err.message);
});