// wa_bot/payment_parser.js
// 付款/收款信息解析器 — 與 Python 版 payment_parser.py 邏輯一致

// ═══════════════════════════════════════════
//  香港主要銀行及其 SWIFT/BIC 代碼
// ═══════════════════════════════════════════
const HK_BANKS = [
  { name: "Standard Chartered Bank (Hong Kong) Limited", code: "003",
    swift: ["SCBLHKHHXXX", "SCBLHKHH"], aliases: ["渣打銀行", "渣打银行", "渣打", "渣打銀行（香港）有限公司", "渣打银行（香港）有限公司", "Standard Chartered", "Standard Chartered Bank (Hong Kong) Limited"] },
  { name: "The Hongkong and Shanghai Banking Corporation Limited", code: "004",
    swift: ["HSBCHKHHXXX", "HSBCHKHHHKH", "HSBCHKHH"], aliases: ["匯豐銀行", "汇丰银行", "匯豐", "汇丰", "香港上海滙豐銀行有限公司", "香港上海汇丰银行有限公司", "HSBC", "Hongkong Bank"] },
  { name: "Bank of China (Hong Kong) Limited", code: "012",
    swift: ["BKCHHKHHXXX", "BKCHHKHH"], aliases: ["中國銀行(香港)", "中国银行(香港)", "中國銀行（香港）有限公司", "中国银行（香港）有限公司", "中銀香港", "中银香港", "中銀", "中银", "BOCHK", "BOC"] },
  { name: "Hang Seng Bank Limited", code: "024",
    swift: ["HASEHKHHXXX", "HASEHKHH"], aliases: ["恒生銀行", "恒生银行", "恆生銀行", "恒生", "恒生銀行有限公司", "Hang Seng"] },
  { name: "Citibank (Hong Kong) Limited", code: "006",
    swift: ["CITIHKAXXXX", "CITIHKAX", "CITIHKA"], aliases: ["花旗銀行", "花旗银行", "花旗", "Citibank", "Citi"] },
  { name: "The Bank of East Asia, Limited", code: "015",
    swift: ["BEASHKHHXXX", "BEASHKHH"], aliases: ["東亞銀行", "东亚银行", "東亞", "东亚", "BEA"] },
  { name: "DBS Bank (Hong Kong) Limited", code: "016",
    swift: ["DHBKHKHHXXX", "DHBKHKHH"], aliases: ["星展銀行", "星展银行", "星展", "星展銀行（香港）有限公司", "DBS", "DBS Bank (Hong Kong) Limited"] },
  { name: "Industrial and Commercial Bank of China (Asia) Limited", code: "029",
    swift: ["ICBKHKHHXXX", "ICBKHKHH"], aliases: ["中國工商銀行(亞洲)", "中国工商银行(亚洲)", "工銀亞洲", "工银亚洲", "工行", "ICBC"] },
  { name: "China Construction Bank (Asia) Corporation Limited", code: "009",
    swift: ["CCBQHKHHXXX", "CCBQHKHH"], aliases: ["中國建設銀行(亞洲)", "中国建设银行(亚洲)", "建銀亞洲", "建银亚洲", "CCB"] },
  { name: "Bank of Communications (Hong Kong) Limited", code: "027",
    swift: ["COMMHKHHXXX", "COMMHKHH"], aliases: ["交通銀行", "交通银行", "交行", "BOCOM"] },
  { name: "OCBC Wing Hang Bank Limited", code: "035",
    swift: ["WIHBHKHHXXX", "WIHBHKHH"], aliases: ["華僑永亨銀行", "华侨永亨银行", "永亨", "OCBC"] },
  { name: "Dah Sing Bank Limited", code: "040",
    swift: ["DSBAHKHHXXX", "DSBAHKHH"], aliases: ["大新銀行", "大新银行", "大新", "Dah Sing"] },
  { name: "Chong Hing Bank Limited", code: "041",
    swift: ["CHBKHKHHXXX", "LCHBHKHH"], aliases: ["創興銀行", "创兴银行", "創興", "创兴", "Chong Hing"] },
  { name: "Nanyang Commercial Bank Limited", code: "043",
    swift: ["NYCBHKHHXXX", "NYCBHKHH"], aliases: ["南洋商業銀行", "南洋商业银行", "南洋", "NCB"] },
  { name: "Shanghai Commercial Bank Limited", code: "025",
    swift: ["SCBKHKHHXXX", "SCBKHKHH"], aliases: ["上海商業銀行", "上海商业银行", "上商", "Shanghai Commercial"] },
  { name: "China Merchants Bank (Hong Kong Branch)", code: "238",
    swift: ["CMBCHKHHXXX", "CMBCHKHH"], aliases: ["招商銀行", "招商银行", "招行", "CMB"] },
  { name: "Fubon Bank (Hong Kong) Limited", code: "128",
    swift: ["FUBOHKHHXXX", "FUBOHKHH"], aliases: ["富邦銀行", "富邦银行", "富邦", "Fubon"] },
  { name: "Public Bank (Hong Kong) Limited", code: "028",
    swift: ["PBHKHKHHXXX", "PBHKHKHH"], aliases: ["大眾銀行", "大众银行", "大众", "Public Bank"] },
  { name: "Agricultural Bank of China Limited, Hong Kong Branch", code: "031",
    swift: ["ABOCHKHHXXX", "ABOCHKHH"], aliases: ["中國農業銀行", "中国农业银行", "農行", "农行", "ABC"] },
  { name: "Chiyu Banking Corporation Limited", code: "039",
    swift: ["CIYUHKHHXXX", "CIYUHKHH"], aliases: ["集友銀行", "集友银行", "集友", "Chiyu"] },
];

// 構建 SWIFT 查找表
function buildSwiftLookup() {
  const lookup = {};
  for (const bank of HK_BANKS) {
    for (const s of bank.swift) {
      lookup[s.toUpperCase()] = bank;
    }
  }
  return lookup;
}
const SWIFT_LOOKUP = buildSwiftLookup();

// ═══════════════════════════════════════════
//  欄位名稱變體匹配規則
// ═══════════════════════════════════════════
const FIELD_PATTERNS = {
  swift: [
    /(?:收款銀行\s*)?SWIFT\s*(?:代[號号碼码]|代碼|コード|Code|CODE|code|編號|编号)/i,
    /(?:收款銀行\s*)?BIC\s*(?:代[號号碼码]|Code|CODE|code|編號|编号)?/i,
    /Beneficiary\s*BIC/i,
    /銀行國際代[碼码]/i,
    /银行国际代[碼码]/i,
    /SWIFT\s*/i,
    /BIC\s*/i,
  ],
  bank_name: [
    /Beneficiary\s*Bank/i,
    /收款銀行\s*(?:名稱|名称|名)\s*/i,
    /銀行\s*(?:名稱|名称|名)\s*/i,
    /银行\s*(?:名稱|名称|名)\s*/i,
    /^(?:銀行\s*)?名稱\s*/i,
    /^(?:银行\s*)?名称\s*/i,
    /(?:Bank|BANK)\s*(?:Name|NAME|name)\s*/i,
    /^收款銀行\s*$/i,
    /^(?:Bank|BANK)\s*$/i,
  ],
  bank_address: [
    /收款銀行\s*(?:地址|位址)\s*/i,
    /銀行\s*(?:地址|位址)\s*/i,
    /(?:Bank|BANK)\s*(?:Address|ADDRESS|address|Addr|ADDR)\s*/i,
    /Bank\s*Add/i,
    /Branch\s*Address/i,
    /開戶行\s*地址/i,
    /开户行\s*地址/i,
    /分行\s*/i,
    /银行\s*(?:地址|位址)/i,
  ],
  bank_code: [
    /銀行\s*(?:代[碼码號号]|Code|CODE|code)\s*/i,
    /银行\s*(?:代[碼码號号]|Code|CODE|code)\s*/i,
    /(?:Bank|BANK)\s*(?:Code|CODE|code)\s*/i,
    /金融機構\s*(?:代[碼码號号]|Code)/i,
  ],
  routing_number: [
    /Routing\s*Number/i,
    /ABA\s*Routing/i,
  ],
  account_number: [
    /(?:綜合|综合)?\s*(?:戶口|户口|帳戶|账户|帳號|账号|賬戶|账户)\s*(?:號碼|号码|號|号|編號|编号|Number|No|NO|num)/i,
    /美金\s*(?:賬戶|账户|帳戶|账户)/i,
    /\$?\s*USD\s*Account/i,
    /收款\s*(?:戶口|户口)\s*(?:號碼|号码)/i,
    /(?:Account|ACCOUNT|account)\s*(?:Number|No|NO|num|Nbr)\s*/i,
    /收款人\s*(?:帳號|账号)\s*(?:\([A-Za-z]{3}\))?/i,
    /A\/C\s*(?:No|Number|num)?\s*/i,
    /收款\s*(?:账号|帳號)/i,
  ],
  account_name: [
    /(?:戶口|户口|帳戶|账户|帐户|賬戶)\s*(?:全名|名稱|名称|姓名|戶名|户名|名字)/i,
    /(?:Account|ACCOUNT|account)\s*(?:Name|NAME|name|Holder|HOLDER)/i,
    /(?:Beneficiary|BENEFICIARY)\s*(?:Name|NAME|name)/i,
    /收款人\s*(?:名稱|名称|姓名|全名|名字)/i,
    /^Beneficiary\s*$/i,
    /^收款人\s*$/i,
    /收款\s*(?:账户名|帐户名|帳戶名|戶口名)/i,
  ],
  amount: [
    /Mso[- ]?Pobo/i,
    /^MSO\b/i,
    /(?:Amount|AMOUNT|amount)\s*/i,
    /(?:金額|金额|交易金額|交易金额)\s*/i,
    /(?:收款金額|收款金额|入金金額|入金金额)\s*/i,
  ],
  remarks: [
    /備註\s*/i,
    /备注\s*/i,
    /(?:Remarks|REMARKS|remarks)\s*/i,
  ],
  insured_person: [
    /投保人\s*/i,
  ],
};

// ═══════════════════════════════════════════
//  輔助函數
// ═══════════════════════════════════════════

function matchField(line, patterns = FIELD_PATTERNS) {
  // 預處理：去除行首尾 * 標記
  let cleanLine = line.trim().replace(/^\*+|\*+$/g, "").trim();
  let keyPart, valuePart;

  // 找分隔符
  const colonIdx = Math.min(
    ...["：", ":", "="].map(s => cleanLine.indexOf(s)).filter(i => i > 0),
    Infinity
  );

  if (colonIdx < Infinity) {
    keyPart = cleanLine.substring(0, colonIdx);
    valuePart = cleanLine.substring(colonIdx + 1).trim();
  } else {
    const spaceIdx = cleanLine.indexOf(" ");
    if (spaceIdx < 0) return [null, null];
    keyPart = cleanLine.substring(0, spaceIdx);
    valuePart = cleanLine.substring(spaceIdx + 1).trim();
  }

  // 去除值中的 * 包裹（如 *CHASUS33XXX*）
  valuePart = valuePart.replace(/^\*+|\*+$/g, "").trim();
  const keyPartClean = keyPart.trim().replace(/[:：= ]+$/, "");

  for (const [fieldKey, fieldPatterns] of Object.entries(patterns)) {
    for (const pattern of fieldPatterns) {
      if (pattern.test(keyPartClean)) {
        return [fieldKey, valuePart];
      }
    }
  }

  return [null, null];
}

const CN_CURRENCY_MAP = {
  "美金": "USD", "美元": "USD",
  "港幣": "HKD", "港元": "HKD", "港币": "HKD",
  "人民幣": "CNY", "人民币": "CNY",
};
const CN_CURRENCY_RE = new RegExp(Object.keys(CN_CURRENCY_MAP).join("|"));

function parseAmountWithCurrency(rawValue, currencyMap = CN_CURRENCY_MAP) {
  const v = rawValue.trim().replace(/,/g, "");

  // 中文貨幣後綴: 194,525 美金
  const cnCurrencyRe = new RegExp(Object.keys(currencyMap).join("|"));
  const mCN = v.match(new RegExp(`([\\d,]+(?:\\.\\d+)?)\\s*(${cnCurrencyRe.source})\\s*$`));
  if (mCN) {
    const amount = parseInt(mCN[1], 10);
    const currency = currencyMap[mCN[2]] || "USD";
    return [isNaN(amount) ? null : amount, currency];
  }

  // 貨幣前綴: HKD 2,247,207
  const mPre = v.match(/^([A-Za-z]{2,4})\s+([\d,]+(?:\.\d+)?)\s*$/);
  if (mPre) {
    let currency = mPre[1].toUpperCase();
    if (currency === "RMB") currency = "CNY";
    const amount = parseInt(mPre[2].replace(/,/g, ""), 10);
    return [isNaN(amount) ? null : amount, currency];
  }

  // 字母貨幣後綴: 222456USD
  const m1 = v.match(/([\d,]+(?:\.\d+)?)\s*([A-Za-z]{2,4})\s*$/);
  if (m1) {
    const amount = parseInt(m1[1], 10);
    let currency = m1[2].toUpperCase();
    if (currency === "RMB") currency = "CNY";
    return [isNaN(amount) ? null : amount, currency];
  }

  // 純數字
  const m2 = v.match(/([\d,]+(?:\.\d+)?)/);
  if (m2) {
    const amount = parseInt(m2[1].replace(/,/g, ""), 10);
    return [isNaN(amount) ? null : amount, "USD"];
  }

  return [null, "USD"];
}

function validateSwift(swift) {
  // 返回 { valid: bool, looksBroken: bool }
  // looksBroken = 明顯不是 SWIFT（太長/太短/含特殊字符/含中文/含空格），應阻擋記錄
  const v = swift.trim();
  if (/^[A-Za-z]{4}[A-Za-z]{2}[A-Za-z0-9]{2}([A-Za-z0-9]{3})?$/.test(v)) {
    return { valid: true, looksBroken: false };
  }

  let looksBroken = false;

  // 含中文、全角字符、冒號、逗號（通常是解析錯誤/缺換行）
  if (/[一-鿿　-〿＀-￯：:，,、。]/.test(v)) looksBroken = true;

  // 含空格 → 合併了多個欄位
  if (v.includes(' ')) looksBroken = true;

  // 長度異常（SWIFT 必須是 8 或 11 位）
  if (v.length < 8 || v.length > 11) looksBroken = true;

  // 含非字母數字字符（特殊符號如 @#$% 等）
  if (/[^A-Za-z0-9]/.test(v)) looksBroken = true;

  return { valid: false, looksBroken };
}

function validateBankCode(code) {
  return /^\d{3}$/.test(code.trim());
}

function buildSwiftLookupFromBanks(banks) {
  const lookup = {};
  for (const bank of banks) {
    for (const s of bank.swift) {
      lookup[s.toUpperCase()] = bank;
    }
  }
  return lookup;
}

function lookupBankBySwift(swift, swiftLookup = SWIFT_LOOKUP, banks = HK_BANKS) {
  const swiftUpper = swift.trim().toUpperCase();
  if (swiftLookup[swiftUpper]) return swiftLookup[swiftUpper];
  const swift8 = swiftUpper.substring(0, 8);
  if (swiftLookup[swift8]) return swiftLookup[swift8];
  for (const bank of banks) {
    for (const s of bank.swift) {
      if (s.toUpperCase().startsWith(swift8)) return bank;
    }
  }
  return null;
}

function lookupBankByNameOrAlias(name, banks = HK_BANKS) {
  const nameLower = name.trim().toLowerCase();
  for (const bank of banks) {
    if (nameLower === bank.name.toLowerCase()) return bank;
    for (const alias of bank.aliases) {
      if (nameLower === alias.toLowerCase()) return bank;
    }
    if (bank.name.toLowerCase().includes(nameLower) || nameLower.includes(bank.name.toLowerCase())) return bank;
    for (const alias of bank.aliases) {
      if (alias.toLowerCase().includes(nameLower) || nameLower.includes(alias.toLowerCase())) return bank;
    }
  }
  return null;
}

// ═══════════════════════════════════════════
//  主要解析函數
// ═══════════════════════════════════════════

function parsePaymentInfo(messageText, agentOverrides = null) {
  if (!messageText || !messageText.trim()) return null;

  // 根據 agent 配置合併自定義欄位規則
  const fieldPatterns = agentOverrides?.field_patterns
    ? { ...FIELD_PATTERNS, ...agentOverrides.field_patterns }
    : FIELD_PATTERNS;
  const currencyMap = agentOverrides?.currency_map
    ? { ...CN_CURRENCY_MAP, ...agentOverrides.currency_map }
    : CN_CURRENCY_MAP;
  const banks = agentOverrides?.banks || HK_BANKS;

  const raw = messageText.trim();
  const lines = raw.split("\n");

  const extracted = {};
  const warnings = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const [fieldKey, value] = matchField(trimmed, fieldPatterns);
    if (fieldKey && value) {
      if (fieldKey in extracted) {
        if (value.length > extracted[fieldKey].length) {
          extracted[fieldKey] = value;
        }
      } else {
        extracted[fieldKey] = value;
      }
    }
  }

  // 判斷是否為付款資訊
  const bankingFields = new Set(["swift", "bank_name", "bank_code", "account_number", "account_name", "routing_number"]);
  let foundBanking = 0;
  for (const k of bankingFields) {
    if (k in extracted) foundBanking++;
  }
  const hasAmount = "amount" in extracted;

  if (foundBanking < 2 && !(foundBanking >= 1 && hasAmount)) {
    return null;
  }

  // 提取金額和貨幣
  let amount = null;
  let currency = "USD";

  if ("amount" in extracted) {
    [amount, currency] = parseAmountWithCurrency(extracted["amount"], currencyMap);
    if (amount === null) {
      warnings.push(`⚠️ 無法解析金額：${extracted["amount"]}`);
    }
    delete extracted["amount"];
  }

  // 備用金額提取：掃描未匹配的行末尾找「數字 + 貨幣」
  if (amount === null) {
    const cnCurrencyRe = new RegExp(Object.keys(currencyMap).join("|"));
    const matchedKeys = new Set(Object.keys(extracted));
    for (let i = lines.length - 1; i >= 0; i--) {
      const [fk] = matchField(lines[i], fieldPatterns);
      if (fk && matchedKeys.has(fk)) continue; // 已是已知欄位
      const cleaned = lines[i].trim().replace(/^\*+|\*+$/g, "").trim();
      const hasCurrency = /[A-Za-z]{2,4}\s*$/.test(cleaned) || /^[A-Za-z]{2,4}\s+/.test(cleaned) || cnCurrencyRe.test(cleaned);
      if (/\d/.test(cleaned) && hasCurrency && !fk) {
        const [fallbackAmt, fallbackCur] = parseAmountWithCurrency(cleaned, currencyMap);
        if (fallbackAmt !== null) {
          amount = fallbackAmt;
          currency = fallbackCur;
          break;
        }
      }
    }
  }

  // 匹配銀行資料（使用可覆蓋的 banks 列表）
  const bankSwiftLookup = buildSwiftLookupFromBanks(banks);
  let matchedBank = null;
  if ("swift" in extracted) {
    const swiftVal = extracted["swift"].trim();
    const { valid, looksBroken } = validateSwift(swiftVal);
    if (valid) {
      matchedBank = lookupBankBySwift(swiftVal, bankSwiftLookup, banks);
      if (matchedBank) {
        if (!("bank_name" in extracted)) extracted["bank_name"] = matchedBank.name;
        if (!("bank_code" in extracted)) extracted["bank_code"] = matchedBank.code;
        if ("bank_name" in extracted) {
          const bankByName = lookupBankByNameOrAlias(extracted["bank_name"], banks);
          if (bankByName && bankByName.name !== matchedBank.name) {
            warnings.push(`⚠️ SWIFT 代碼與銀行名稱不一致：SWIFT 對應「${matchedBank.name}」，名稱給出「${extracted["bank_name"]}」`);
          }
        }
      }
    } else if (currency !== "CNY") {
      // CNY 交易通常無 SWIFT 代碼，不報錯
      if (looksBroken) {
        warnings.push(`❌ SWIFT 欄位格式異常（可能缺少換行）：${swiftVal}`);
      } else {
        warnings.push(`⚠️ SWIFT 代碼格式不正確：${swiftVal}`);
      }
    }
  } else if ("bank_name" in extracted) {
    matchedBank = lookupBankByNameOrAlias(extracted["bank_name"], banks);
    if (!matchedBank) {
      warnings.push(`⚠️ 無法識別的銀行名稱：${extracted["bank_name"]}`);
    }
  }

  // ── 驗證銀行識別資訊 ──
  const hasSwift = (extracted["swift"] || "").trim();
  const hasBankName = (extracted["bank_name"] || "").trim();
  const hasBankCode = (extracted["bank_code"] || "").trim();
  if (!hasSwift && !hasBankName && !hasBankCode) {
    warnings.push("❌ 缺少銀行識別資訊（SWIFT、銀行名稱或銀行代碼至少需要一項）");
  }

  // ── 銀行地址為可選項，不顯示警告 ──

  // 驗證必填欄位
  if (!("account_number" in extracted)) warnings.push("❌ 缺少戶口號碼");
  if (!("account_name" in extracted)) warnings.push("❌ 缺少戶口全名");
  if (amount === null) warnings.push("❌ 缺少或無法解析交易金額（Mso-Pobo）");

  // 驗證銀行代碼格式
  if ("bank_code" in extracted && !validateBankCode(extracted["bank_code"])) {
    warnings.push(`⚠️ 銀行代碼格式異常（通常為3位數字）：${extracted["bank_code"]}`);
  }

  const agentName = (extracted["account_name"] || "Unknown").trim();

  const paymentDetails = {
    swift: extracted["swift"] || "",
    bank_name: extracted["bank_name"] || "",
    bank_address: extracted["bank_address"] || "",
    bank_code: extracted["bank_code"] || "",
    routing_number: extracted["routing_number"] || "",
    account_number: extracted["account_number"] || "",
    account_name: agentName,
    remarks: extracted["remarks"] || "",
    insured_person: extracted["insured_person"] || "",
  };
  if (matchedBank) {
    paymentDetails.bank_matched = matchedBank.name;
  }

  return {
    customer_name: agentName,
    amount: amount || 0,
    currency: currency,
    raw_message: raw,
    source: "whatsapp",
    payment_details: JSON.stringify(paymentDetails),
    payment_details_dict: paymentDetails,
    remarks: extracted["remarks"] || "",
    insured_person: extracted["insured_person"] || "",
    warnings: warnings,
    matched_bank: matchedBank,
  };
}

// ═══════════════════════════════════════════
//  换汇公式行解析器
// ═══════════════════════════════════════════

// 支援 / 和 * 兩種運算符
// 支援括號加法：例如 (200,000+405,958) / 0.897 = 675,538 HKD
const SRC_AMT_RE = /\(?[\d,]+(?:\.\d+)?(?:\s*\+\s*[\d,]+(?:\.\d+)?)*\)?(?:[十百千]?[万萬]|[万萬]|w|億|[十百千])?/;
const CONV_LINE_SRC = SRC_AMT_RE.source;
const CONV_LINE_DST = /[\d,]+(?:\.\d+)?(?:[十百千]?[万萬]|[万萬]|w|億|[十百千])?/.source;

	// 支援兩種手續費扣除格式：
	//   等號後：202500 / 7.01 = 28,887 - 30 = 28,857 USD
	//   等號前：310589 / 6.99 - 30 = 44,403 USD
	const CONV_FEE_BEFORE_EQ = `(?:\\s*-\\s*([\\d,]+)\\s*)?`;
	const CONV_FEE_AFTER_EQ = `(?:\\s*-\\s*([\\d,]+)\\s*=\\s*(${CONV_LINE_DST}))?`;
	const CONVERSION_LINE_RE = new RegExp(
	  `^(${CONV_LINE_SRC})(?:\\s+[A-Z]{3,5})?\\s*[\\/\\*]\\s*([\\d.]+)\\s*${CONV_FEE_BEFORE_EQ}=\\s*(${CONV_LINE_DST})${CONV_FEE_AFTER_EQ}(?:\\s*(USD|HKD|CNY|RMB))?\\s*$`, "i");
	const CONVERSION_SEARCH_RE = new RegExp(
	  `(${CONV_LINE_SRC})(?:\\s+[A-Z]{3,5})?\\s*[\\/\\*]\\s*([\\d.]+)\\s*${CONV_FEE_BEFORE_EQ}=\\s*(${CONV_LINE_DST})${CONV_FEE_AFTER_EQ}(?:\\s*(USD|HKD|CNY|RMB))?\\s*`, "gi");

// 去除貨幣裝飾符號（emoji 和獨立貨幣符號，避免干擾數字捕獲）
function _stripDecorators(text) {
  return text.replace(/\p{Extended_Pictographic}/gu, "").replace(/[\$£¥€￥]/g, "");
}

// 去除換匯公式中常見的非結構化填充詞（如 agent 備註的「現金」等），避免干擾正則匹配
const FORMULA_FILLER_WORDS = /現金|现金|\bcash\b/gi;
function _cleanFormulaText(text) {
  return text.replace(FORMULA_FILLER_WORDS, "").trim();
}

function _parseAmount(str) {
  // 解析带中文单位的金额字符串 → number（从大到小匹配，避免 千万 被 万 先捕获）
  const units = [
    { re: /億$/, mul: 100000000 },
    { re: /千[万萬]$/, mul: 10000000 },
    { re: /百[万萬]$/, mul: 1000000 },
    { re: /十[万萬]$/, mul: 100000 },
    { re: /[wW万萬]$/, mul: 10000 },
    { re: /千$/, mul: 1000 },
    { re: /百$/, mul: 100 },
  ];
  for (const u of units) {
    if (u.re.test(str)) {
      const val = parseFloat(str.replace(u.re, ""));
      if (!isNaN(val)) return Math.round(val * u.mul);
      return null;
    }
  }
  const val = parseFloat(str);
  return isNaN(val) ? null : Math.round(val);
}

function _parseMatch(m) {
  // 處理 source 中的括號加法，如 "(200,000+405,958)" → 605,958
  const sourceStrRaw = m[1].replace(/,/g, "");
  let sourceAmount;
  if (sourceStrRaw.includes("+")) {
    sourceAmount = sourceStrRaw
      .replace(/[()]/g, "")
      .split("+")
      .map(p => _parseAmount(p.trim()))
      .reduce((a, b) => (a !== null && b !== null ? a + b : null), 0);
  } else {
    sourceAmount = _parseAmount(sourceStrRaw);
  }

  const rate = parseFloat(m[2]);

  // 判斷手續費格式（group index 已重排，見檔案頂部註解）
  //   m[3] = 等號前手續費（新格式）  m[4] = 等號後結果/毛額
  //   m[5] = 等號後手續費（舊格式）  m[6] = 等號後淨額（舊格式）  m[7] = 幣種
  const hasFeeBeforeEq = m[3] !== undefined;   // 新：SOURCE / RATE - FEE = RESULT
  const hasFeeAfterEq = m[5] !== undefined && m[6] !== undefined;  // 舊：= GROSS - FEE = NET

  let resultStr, resultAmount, grossAmount;

  if (hasFeeBeforeEq) {
    // 新格式：手續費在等號前 → result_amount 直接取 m[4]，gross 由 source/rate 算出
    resultStr = m[4].replace(/,/g, "");
    resultAmount = _parseAmount(resultStr);
    grossAmount = (rate !== 0 && sourceAmount !== null)
      ? Math.round(sourceAmount / rate)
      : null;
  } else if (hasFeeAfterEq) {
    // 舊格式：手續費在等號後 → net = m[6], gross = m[4]
    resultStr = m[6].replace(/,/g, "");
    resultAmount = _parseAmount(resultStr);
    grossAmount = _parseAmount(m[4].replace(/,/g, ""));
  } else {
    // 無手續費
    resultStr = m[4].replace(/,/g, "");
    resultAmount = _parseAmount(resultStr);
    grossAmount = null;
  }

  let resultCurrency = (m[7] || "").toUpperCase();
  if (resultCurrency === "RMB") resultCurrency = "CNY";
  if (!resultCurrency) resultCurrency = null;

  const operator = m[0].includes("*") ? "*" : "/";

  return {
    source_amount: sourceAmount,
    rate: rate,
    result_amount: resultAmount,
    result_currency: resultCurrency,
    operator: operator,
    gross_amount: grossAmount,
  };
}

function parseConversionLine(text) {
  if (!text || !text.trim()) return null;

  const cleaned = _cleanFormulaText(_stripDecorators(text.trim()).trim());
  const m = cleaned.match(CONVERSION_LINE_RE);
  if (!m) return null;

  return _parseMatch(m);
}

// 在任意文本中搜尋換匯公式（用於引用消息、長文本等場景）
// 若有多個公式，優先取帶幣種後綴（USD/HKD/CNY/RMB）的最後一個
function findConversionInText(text) {
  if (!text || !text.trim()) return null;

  const cleaned = _cleanFormulaText(_stripDecorators(text));
  const matches = [];
  let m;
  while ((m = CONVERSION_SEARCH_RE.exec(cleaned)) !== null) {
    matches.push(m);
  }
  CONVERSION_SEARCH_RE.lastIndex = 0;

  if (matches.length === 0) return null;

  // 優先取帶幣種後綴的匹配
  const withCurrency = matches.filter(m => m[7] && m[7].trim());
  const best = withCurrency.length > 0 ? withCurrency[withCurrency.length - 1] : matches[matches.length - 1];

  return _parseMatch(best);
}

// ═══════════════════════════════════════════
//  分數匯率解析器 — "0.99/0.982" 格式（成本價/賣出價）
// ═══════════════════════════════════════════

const FRACTION_RATE_RE = /^([\d]+(?:\.[\d]+)?)\s*\/\s*([\d]+(?:\.[\d]+)?)$/;

function parseFractionRate(text) {
  if (!text || !text.trim()) return null;
  const trimmed = text.trim();
  const m = trimmed.match(FRACTION_RATE_RE);
  if (!m) return null;
  const costRate = parseFloat(m[1]);
  const sellRate = parseFloat(m[2]);
  if (isNaN(costRate) || isNaN(sellRate) || costRate <= 0 || sellRate <= 0) return null;
  if (costRate > 20 || sellRate > 20) return null;
  return { type: "fraction_rate", cost_rate: costRate, sell_rate: sellRate };
}

module.exports = { parsePaymentInfo, parseConversionLine, findConversionInText, parseFractionRate };
