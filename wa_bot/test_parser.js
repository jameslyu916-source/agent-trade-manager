// wa_bot/test_parser.js
const testMessages = [
  "【成交】代理A 完成交易 金額1000元",
  "成交：代理B 500元",
  "代理C 今日成交 2000元",
  "Transaction: AgentD 3000",
  "今天天氣真好",           // 應該返回 null
  "代理E 今日交易 999,999元",
];

// 直接複製 wa_bot.js 裡的 parseTransaction 函數
function parseTransaction(messageText) {
  let text = messageText
    .trim()
    .replace(/，/g, "").replace(/元/g, "")
    .replace(/HKD/g, "").replace(/成交/g, "交易")
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
        return { agent_name: agentName, amount, raw_message: messageText, source: "whatsapp" };
      }
    }
  }
  return null;
}

// 執行測試
console.log("========== 解析函數測試 ==========\n");
testMessages.forEach((msg, i) => {
  const result = parseTransaction(msg);
  const status = result ? "✅ 解析成功" : "⚪ 無匹配";
  console.log(`[${i + 1}] 輸入：「${msg}」`);
  console.log(`    ${status}`, result ? JSON.stringify(result) : "");
  console.log();
});