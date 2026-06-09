"""
付款/收款信息解析器 — 從結構化的銀行轉帳訊息中提取交易資料。
支援香港主要銀行的 SWIFT 代碼匹配、多種欄位名稱變體、以及多幣種金額提取。
"""
import re
import json
from datetime import datetime, timezone


# ═══════════════════════════════════════════
#  香港主要銀行及其 SWIFT/BIC 代碼
# ═══════════════════════════════════════════
HK_BANKS = [
    {"name": "Standard Chartered Bank (Hong Kong) Limited", "code": "003",
     "swift": ["SCBLHKHHXXX", "SCBLHKHH"], "aliases": ["渣打銀行", "渣打银行", "渣打", "Standard Chartered"]},
    {"name": "The Hongkong and Shanghai Banking Corporation Limited", "code": "004",
     "swift": ["HSBCHKHHXXX", "HSBCHKHHHKH", "HSBCHKHH"], "aliases": ["匯豐銀行", "汇丰银行", "匯豐", "汇丰", "HSBC", "Hongkong Bank"]},
    {"name": "Bank of China (Hong Kong) Limited", "code": "012",
     "swift": ["BKCHHKHHXXX", "BKCHHKHH"], "aliases": ["中國銀行(香港)", "中国银行(香港)", "中銀香港", "中银香港", "中銀", "中银", "BOCHK", "BOC"]},
    {"name": "Hang Seng Bank Limited", "code": "024",
     "swift": ["HASEHKHHXXX", "HASEHKHH"], "aliases": ["恒生銀行", "恒生银行", "恆生銀行", "恒生", "Hang Seng"]},
    {"name": "Citibank (Hong Kong) Limited", "code": "006",
     "swift": ["CITIHKAXXXX", "CITIHKAX", "CITIHKA"], "aliases": ["花旗銀行", "花旗银行", "花旗", "Citibank", "Citi"]},
    {"name": "The Bank of East Asia, Limited", "code": "015",
     "swift": ["BEASHKHHXXX", "BEASHKHH"], "aliases": ["東亞銀行", "东亚银行", "東亞", "东亚", "BEA"]},
    {"name": "DBS Bank (Hong Kong) Limited", "code": "016",
     "swift": ["DHBKHKHHXXX", "DHBKHKHH"], "aliases": ["星展銀行", "星展银行", "星展", "DBS"]},
    {"name": "Industrial and Commercial Bank of China (Asia) Limited", "code": "029",
     "swift": ["ICBKHKHHXXX", "ICBKHKHH"], "aliases": ["中國工商銀行(亞洲)", "中国工商银行(亚洲)", "工銀亞洲", "工银亚洲", "工行", "ICBC"]},
    {"name": "China Construction Bank (Asia) Corporation Limited", "code": "009",
     "swift": ["CCBQHKHHXXX", "CCBQHKHH"], "aliases": ["中國建設銀行(亞洲)", "中国建设银行(亚洲)", "建銀亞洲", "建银亚洲", "CCB"]},
    {"name": "Bank of Communications (Hong Kong) Limited", "code": "027",
     "swift": ["COMMHKHHXXX", "COMMHKHH"], "aliases": ["交通銀行", "交通银行", "交行", "BOCOM"]},
    {"name": "OCBC Wing Hang Bank Limited", "code": "035",
     "swift": ["WIHBHKHHXXX", "WIHBHKHH"], "aliases": ["華僑永亨銀行", "华侨永亨银行", "永亨", "OCBC"]},
    {"name": "Dah Sing Bank Limited", "code": "040",
     "swift": ["DSBAHKHHXXX", "DSBAHKHH"], "aliases": ["大新銀行", "大新银行", "大新", "Dah Sing"]},
    {"name": "Chong Hing Bank Limited", "code": "041",
     "swift": ["CHBKHKHHXXX", "LCHBHKHH"], "aliases": ["創興銀行", "创兴银行", "創興", "创兴", "Chong Hing"]},
    {"name": "Nanyang Commercial Bank Limited", "code": "043",
     "swift": ["NYCBHKHHXXX", "NYCBHKHH"], "aliases": ["南洋商業銀行", "南洋商业银行", "南洋", "NCB"]},
    {"name": "Shanghai Commercial Bank Limited", "code": "025",
     "swift": ["SCBKHKHHXXX", "SCBKHKHH"], "aliases": ["上海商業銀行", "上海商业银行", "上商", "Shanghai Commercial"]},
    {"name": "China Merchants Bank (Hong Kong Branch)", "code": "238",
     "swift": ["CMBCHKHHXXX", "CMBCHKHH"], "aliases": ["招商銀行", "招商银行", "招行", "CMB"]},
    {"name": "Fubon Bank (Hong Kong) Limited", "code": "128",
     "swift": ["FUBOHKHHXXX", "FUBOHKHH"], "aliases": ["富邦銀行", "富邦银行", "富邦", "Fubon"]},
    {"name": "Public Bank (Hong Kong) Limited", "code": "028",
     "swift": ["PBHKHKHHXXX", "PBHKHKHH"], "aliases": ["大眾銀行", "大众银行", "大众", "Public Bank"]},
    {"name": "Agricultural Bank of China Limited, Hong Kong Branch", "code": "031",
     "swift": ["ABOCHKHHXXX", "ABOCHKHH"], "aliases": ["中國農業銀行", "中国农业银行", "農行", "农行", "ABC"]},
    {"name": "Chiyu Banking Corporation Limited", "code": "039",
     "swift": ["CIYUHKHHXXX", "CIYUHKHH"], "aliases": ["集友銀行", "集友银行", "集友", "Chiyu"]},
]


def _build_swift_lookup():
    """構建 SWIFT 代碼 → 銀行資料的查找表"""
    lookup = {}
    for bank in HK_BANKS:
        for swift in bank["swift"]:
            lookup[swift.upper()] = bank
    return lookup


SWIFT_LOOKUP = _build_swift_lookup()


# ═══════════════════════════════════════════
#  欄位名稱變體匹配規則
#  每個欄位一組正則，按優先級排列
# ═══════════════════════════════════════════
FIELD_PATTERNS = {
    "swift": [
        r"(?:收款銀行\s*)?SWIFT\s*(?:代[號号碼码]|代碼|コード|Code|CODE|code|編號|编号)",
        r"(?:收款銀行\s*)?BIC\s*(?:代[號号碼码]|Code|CODE|code|編號|编号)?",
        r"Beneficiary\s*BIC",
        r"SWIFT\s*",
        r"BIC\s*",
    ],
    "bank_name": [
        r"Beneficiary\s*Bank",
        r"收款銀行\s*(?:名稱|名称|名)\s*",
        r"銀行\s*(?:名稱|名称|名)\s*",
        r"^(?:銀行\s*)?名稱\s*",
        r"^(?:银行\s*)?名称\s*",
        r"(?:Bank|BANK)\s*(?:Name|NAME|name)\s*",
        r"^收款銀行\s*$",
        r"^(?:Bank|BANK)\s*$",
    ],
    "bank_address": [
        r"收款銀行\s*(?:地址|位址)\s*",
        r"銀行\s*(?:地址|位址)\s*",
        r"(?:Bank|BANK)\s*(?:Address|ADDRESS|address|Addr|ADDR)\s*",
        r"Bank\s*Add",
        r"Branch\s*Address",
        r"開戶行\s*地址",
        r"开户行\s*地址",
        r"银行\s*(?:地址|位址)",
        r"分行\s*",
    ],
    "bank_code": [
        r"銀行\s*(?:代[碼码號号]|Code|CODE|code)\s*",
        r"(?:Bank|BANK)\s*(?:Code|CODE|code)\s*",
        r"金融機構\s*(?:代[碼码號号]|Code)",
    ],
    "routing_number": [
        r"Routing\s*Number",
        r"ABA\s*Routing",
    ],
    "account_number": [
        r"(?:綜合|综合)?\s*(?:戶口|户口|帳戶|账户|帳號|账号|賬戶|账户)\s*(?:號碼|号码|號|号|編號|编号|Number|No|NO|num)",
        r"美金\s*(?:賬戶|账户|帳戶|账户)",
        r"\$?\s*USD\s*Account",
        r"收款\s*(?:戶口|户口)\s*(?:號碼|号码)",
        r"(?:Account|ACCOUNT|account)\s*(?:Number|No|NO|num|Nbr)\s*",
        r"收款人\s*(?:帳號|账号)\s*(?:\([A-Za-z]{3}\))?",
        r"A/C\s*(?:No|Number|num)?\s*",
        r"收款\s*(?:账号|帳號)",
    ],
    "account_name": [
        r"(?:戶口|户口|帳戶|账户|賬戶)\s*(?:全名|名稱|名称|姓名|戶名|户名)",
        r"(?:Account|ACCOUNT|account)\s*(?:Name|NAME|name|Holder|HOLDER)",
        r"(?:Beneficiary|BENEFICIARY)\s*(?:Name|NAME|name)",
        r"收款人\s*(?:名稱|名称|姓名|全名|名字)",
        r"^Beneficiary\s*$",
        r"^收款人\s*$",
        r"收款\s*(?:账户名|帳戶名|戶口名)",
    ],
    "amount": [
        r"Mso[- ]?Pobo",
        r"(?:Amount|AMOUNT|amount)\s*",
        r"(?:金額|金额|交易金額|交易金额)\s*",
        r"(?:收款金額|收款金额|入金金額|入金金额)\s*",
    ],
    "remarks": [
        r"備註\s*",
        r"备注\s*",
        r"(?:Remarks|REMARKS|remarks)\s*",
    ],
    "insured_person": [
        r"投保人\s*",
    ],
}


# 中文貨幣詞 → ISO 代碼
_CN_CURRENCY_MAP = {
    "美金": "USD", "美元": "USD",
    "港幣": "HKD", "港元": "HKD", "港币": "HKD",
    "人民幣": "CNY", "人民币": "CNY",
}
_CN_CURRENCY_RE = re.compile("|".join(re.escape(k) for k in _CN_CURRENCY_MAP))

# 支援的貨幣單位
_CURRENCY_PATTERN = re.compile(
    r'(USD|HKD|CNY|RMB|EUR|GBP|JPY|AUD|SGD|CAD|CHF|NZD|THB|MYR|PHP|IDR|TWD|KRW|INR)\s*$',
    re.IGNORECASE
)

# 金額提取（帶貨幣後綴，如 222456USD）
_AMOUNT_WITH_CURRENCY = re.compile(r'([\d,]+(?:\.\d+)?)\s*([A-Za-z]{2,4})\s*$')
# 純數字金額
_AMOUNT_ONLY = re.compile(r'([\d,]+(?:\.\d+)?)')


# ═══════════════════════════════════════════
#  主要解析函數
# ═══════════════════════════════════════════

def _match_field(line: str):
    """
    嘗試將一行文字匹配到一個已知欄位，返回 (field_key, raw_value)
    或 (None, None)。
    """
    # 預處理：去除行首尾 * 標記
    line = line.strip().strip("*").strip()

    # 先處理分隔符：找到第一個冒號或等號的位置
    for sep in [":", "：", "="]:
        idx = line.find(sep)
        if idx > 0:
            key_part = line[:idx]
            value_part = line[idx + 1:].strip()
            break
    else:
        # 沒有分隔符，用空格分割
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            return None, None
        key_part, value_part = parts[0], parts[1].strip()

    # 去除值中的 * 包裹（如 *CHASUS33XXX*）
    value_part = value_part.strip("*").strip()
    key_part_clean = key_part.strip().rstrip(":：= ")

    # 按優先級嘗試匹配每個欄位類型（使用 search 而非 fullmatch 以兼容複合標籤）
    for field_key, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, key_part_clean, re.IGNORECASE):
                return field_key, value_part

    return None, None


def _parse_amount_with_currency(raw_value: str) -> tuple[int | None, str]:
    """從金額字串中分離數字和貨幣單位，例如 '222,456USD' → (222456, 'USD')"""
    v = raw_value.strip().replace(",", "")

    # 中文貨幣後綴: 194,525 美金
    m_cn = re.search(rf"([\d,]+(?:\.\d+)?)\s*({_CN_CURRENCY_RE.pattern})\s*$", v)
    if m_cn:
        try:
            amount = int(float(m_cn.group(1)))
        except (ValueError, TypeError):
            amount = None
        currency = _CN_CURRENCY_MAP.get(m_cn.group(2), "USD")
        return amount, currency

    m = _AMOUNT_WITH_CURRENCY.search(v)
    if m:
        try:
            amount = int(float(m.group(1)))
        except (ValueError, TypeError):
            amount = None
        currency = m.group(2).upper()
        # 標準化常見貨幣別名
        if currency == "RMB":
            currency = "CNY"
        return amount, currency

    # 沒找到貨幣後綴，嘗試只用數字
    m2 = _AMOUNT_ONLY.search(v)
    if m2:
        try:
            return int(float(m2.group(1))), "USD"  # 默認 USD
        except (ValueError, TypeError):
            pass

    return None, "USD"


def _validate_swift(swift: str) -> tuple[bool, bool]:
    """
    驗證 SWIFT/BIC 代碼，返回 (格式正確, 資料異常應阻擋記錄)

    - 格式正確：標準 8 或 11 位字母數字
    - 資料異常：值明顯不是 SWIFT（太長/太短/含特殊字符/含中文/含空格），應阻擋記錄
    """
    v = swift.strip()

    # 標準 SWIFT 格式：4字母(銀行) + 2字母(國家) + 2字母數字(地區) + 可選3字母數字(分行)
    if re.match(r'^[A-Za-z]{4}[A-Za-z]{2}[A-Za-z0-9]{2}([A-Za-z0-9]{3})?$', v):
        return True, False

    # ── 判斷是否「明顯不是 SWIFT 代碼」→ 應阻擋記錄 ──
    looks_broken = False

    # 含中文、全角字符、冒號、逗號（通常是解析錯誤/缺換行）
    if re.search(r'[一-鿿　-〿＀-￯：:，,、。]', v):
        looks_broken = True

    # 含空格 → 合併了多個欄位
    if ' ' in v:
        looks_broken = True

    # 長度異常（SWIFT 必須是 8 或 11 位）
    if len(v) < 8 or len(v) > 11:
        looks_broken = True

    # 含非字母數字字符（特殊符號如 @#$% 等）
    if re.search(r'[^A-Za-z0-9]', v):
        looks_broken = True

    return False, looks_broken


def _validate_bank_code(code: str) -> bool:
    """驗證香港銀行代碼格式（通常為 3 位數字）"""
    return bool(re.match(r'^\d{3}$', code.strip()))


def _lookup_bank_by_swift(swift: str) -> dict | None:
    """根據 SWIFT 代碼查找銀行資料"""
    swift_upper = swift.strip().upper()
    # 精確匹配
    if swift_upper in SWIFT_LOOKUP:
        return SWIFT_LOOKUP[swift_upper]
    # 前 8 位匹配
    swift_8 = swift_upper[:8]
    if swift_8 in SWIFT_LOOKUP:
        return SWIFT_LOOKUP[swift_8]
    # 模糊匹配（遍歷所有銀行）
    for bank in HK_BANKS:
        for s in bank["swift"]:
            if s.upper().startswith(swift_8):
                return bank
    return None


def _lookup_bank_by_name_or_alias(name: str) -> dict | None:
    """根據銀行名稱或別名查找銀行資料"""
    name_lower = name.strip().lower()
    for bank in HK_BANKS:
        if name_lower == bank["name"].lower():
            return bank
        for alias in bank["aliases"]:
            if name_lower == alias.lower():
                return bank
        # 部分匹配（名稱包含或包含名稱）
        if name_lower in bank["name"].lower() or bank["name"].lower() in name_lower:
            return bank
        for alias in bank["aliases"]:
            if name_lower in alias.lower() or alias.lower() in name_lower:
                return bank
    return None


def parse_payment_info(message_text: str) -> dict | None:
    """
    解析結構化的付款/收款訊息。

    支援的格式範例：
        收款銀行SWIFT代號： SCBLHKHHXXX
        收款銀行名稱： Standard Chartered Bank (Hong Kong) Limited
        收款銀行地址： DES VOEUX ROAD, 4-4A,STANDARD CHARTERED BANK BUILDING
        銀行代碼：003
        戶口號碼：290-8-888999-9
        戶口全名：CHAN TAI MAN
        Mso-Pobo: 222456USD

    回傳格式：
        {
            "customer_name": "CHAN TAI MAN",  # 戶口全名即為客戶名稱
            "amount": 222456,
            "currency": "USD",
            "raw_message": <原始消息>,
            "payment_details": { ... },       # JSON 字串
            "warnings": [...],                # 缺漏或無法匹配的提示
            "matched_bank": { ... }           # 匹配到的銀行資料
        }

    若完全無法識別為付款資訊，回傳 None。
    """
    if not message_text or not message_text.strip():
        return None

    raw = message_text.strip()
    lines = raw.split("\n")

    # 收集解析結果
    extracted = {}
    warnings = []
    unmatched_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        field_key, value = _match_field(line)
        if field_key and value:
            if field_key in extracted:
                # 如果已有同名欄位，保留第一個（或是合併？保留較長的）
                if len(value) > len(extracted[field_key]):
                    extracted[field_key] = value
            else:
                extracted[field_key] = value
        else:
            unmatched_lines.append(line)

    # ── 判斷是否為付款資訊 ──
    # 至少需要有銀行相關欄位 + 金額 才算付款資訊
    banking_fields = {"swift", "bank_name", "bank_code", "account_number", "account_name", "routing_number"}
    found_banking = banking_fields & set(extracted.keys())
    has_amount = "amount" in extracted

    # 至少需要 2 個銀行欄位或 1 個銀行欄位 + 金額
    if len(found_banking) < 2 and not (len(found_banking) >= 1 and has_amount):
        return None

    # ── 提取金額和貨幣 ──
    amount = None
    currency = "USD"

    if "amount" in extracted:
        amount, currency = _parse_amount_with_currency(extracted["amount"])
        if amount is None:
            warnings.append(f"⚠️ 無法解析金額：{extracted['amount']}")
        del extracted["amount"]

    # ── 備用金額提取：掃描未匹配的行末尾找「數字 + 貨幣」 ──
    if amount is None:
        matched_keys = set(extracted.keys())
        for line in reversed(lines):
            fk, _ = _match_field(line)
            if fk and fk in matched_keys:
                continue  # 已是已知欄位
            cleaned = line.strip().strip("*").strip()
            has_currency = bool(re.search(r'[A-Za-z]{2,4}\s*$', cleaned)) or bool(_CN_CURRENCY_RE.search(cleaned))
            if re.search(r'\d', cleaned) and has_currency and not fk:
                fallback_amt, fallback_cur = _parse_amount_with_currency(cleaned)
                if fallback_amt is not None:
                    amount = fallback_amt
                    currency = fallback_cur
                    break

    # ── 匹配銀行資料 ──
    matched_bank = None
    if "swift" in extracted:
        swift_val = extracted["swift"].strip()
        is_valid, looks_broken = _validate_swift(swift_val)
        if is_valid:
            matched_bank = _lookup_bank_by_swift(swift_val)
            if matched_bank:
                if "bank_name" not in extracted:
                    extracted["bank_name"] = matched_bank["name"]
                if "bank_code" not in extracted:
                    extracted["bank_code"] = matched_bank["code"]
                if "bank_name" in extracted:
                    bank_by_name = _lookup_bank_by_name_or_alias(extracted["bank_name"])
                    if bank_by_name and bank_by_name["name"] != matched_bank["name"]:
                        warnings.append(f"⚠️ SWIFT 代碼與銀行名稱不一致：SWIFT 對應「{matched_bank['name']}」，名稱給出「{extracted['bank_name']}」")
            elif currency != "CNY":
                pass  # 格式有效的國際 SWIFT 無需在 HK 列表中
        elif currency != "CNY":
            # CNY 交易通常無 SWIFT 代碼，不報錯
            if looks_broken:
                warnings.append(f"❌ SWIFT 欄位格式異常（可能缺少換行）：{swift_val}")
            else:
                warnings.append(f"⚠️ SWIFT 代碼格式不正確：{swift_val}")
    elif "bank_name" in extracted:
        matched_bank = _lookup_bank_by_name_or_alias(extracted["bank_name"])
        if not matched_bank:
            warnings.append(f"⚠️ 無法識別的銀行名稱：{extracted['bank_name']}")

    # ── 驗證銀行識別資訊 ──
    has_swift = extracted.get("swift", "").strip()
    has_bank_name = extracted.get("bank_name", "").strip()
    has_bank_code = extracted.get("bank_code", "").strip()
    if not has_swift and not has_bank_name and not has_bank_code:
        warnings.append("❌ 缺少銀行識別資訊（SWIFT、銀行名稱或銀行代碼至少需要一項）")

    # ── 驗證銀行地址 ──
    if not extracted.get("bank_address", "").strip():
        warnings.append("⚠️ 缺少銀行地址")

    # ── 驗證必填欄位 ──
    if "account_number" not in extracted:
        warnings.append("❌ 缺少戶口號碼")
    if "account_name" not in extracted:
        warnings.append("❌ 缺少戶口全名")
    if amount is None:
        warnings.append("❌ 缺少或無法解析交易金額（Mso-Pobo）")

    # ── 驗證銀行代碼格式 ──
    if "bank_code" in extracted and not _validate_bank_code(extracted["bank_code"]):
        warnings.append(f"⚠️ 銀行代碼格式異常（通常為3位數字）：{extracted['bank_code']}")

    # ── 構建回傳結果 ──
    agent_name = extracted.get("account_name", "Unknown").strip()

    payment_details = {
        "swift": extracted.get("swift", ""),
        "bank_name": extracted.get("bank_name", ""),
        "bank_address": extracted.get("bank_address", ""),
        "bank_code": extracted.get("bank_code", ""),
        "routing_number": extracted.get("routing_number", ""),
        "account_number": extracted.get("account_number", ""),
        "account_name": agent_name,
        "remarks": extracted.get("remarks", ""),
        "insured_person": extracted.get("insured_person", ""),
    }
    if matched_bank:
        payment_details["bank_matched"] = matched_bank["name"]

    return {
        "customer_name": agent_name,
        "amount": amount or 0,
        "currency": currency,
        "raw_message": raw,
        "source": "telegram",
        "payment_details": json.dumps(payment_details, ensure_ascii=False),
        "payment_details_dict": payment_details,
        "remarks": extracted.get("remarks", ""),
        "insured_person": extracted.get("insured_person", ""),
        "warnings": warnings,
        "matched_bank": matched_bank,
    }


# ═══════════════════════════════════════════
#  换汇公式行解析器
#  格式: "50w / 7.04 = 71,023 USD"
#        "1,375,292 / 7.07 = 194,525 USD"
#        "50w / 0.896 = 558,036 HKD"
# ═══════════════════════════════════════════

# 支援 / 和 * 兩種運算符
_CONVERSION_LINE_RE = re.compile(
    r'^([\d,]+(?:\.\d+)?(?:w|万|萬)?)\s*[/*]\s*([\d.]+)\s*=\s*([\d,]+(?:\.\d+)?(?:w|万|萬)?)(?:\s*(USD|HKD|CNY|RMB))?\s*$',
    re.IGNORECASE
)

# 不加 anchor 的版本，用於在引用消息/長文本中搜尋公式
_CONVERSION_SEARCH_RE = re.compile(
    r'([\d,]+(?:\.\d+)?(?:w|万|萬)?)\s*[/*]\s*([\d.]+)\s*=\s*([\d,]+(?:\.\d+)?(?:w|万|萬)?)(?:\s*(USD|HKD|CNY|RMB))?\s*',
    re.IGNORECASE
)

import unicodedata


def _strip_decorators(text: str) -> str:
    """去除貨幣裝飾符號（emoji 和獨立貨幣符號，避免干擾數字捕獲）"""
    # 去除常見獨立貨幣符號
    for ch in "$£¥€￥":
        text = text.replace(ch, "")
    # 去除 emoji（Unicode category So 和 Sk）
    result = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat not in ("So", "Sk"):
            result.append(ch)
    return "".join(result)


def _parse_match(m: re.Match) -> dict:
    source_str = m.group(1).replace(",", "")
    rate = float(m.group(2))
    result_str = m.group(3).replace(",", "")
    result_currency = (m.group(4) or "").upper()
    if result_currency == "RMB":
        result_currency = "CNY"
    if not result_currency:
        result_currency = None

    source_has_wan = source_str.endswith(("w", "万", "萬"))
    source_amount = float(source_str.rstrip("w万萬"))
    if source_has_wan:
        source_amount *= 10000

    result_has_wan = result_str.endswith(("w", "万", "萬"))
    result_amount = int(float(result_str.rstrip("w万萬")))
    if result_has_wan:
        result_amount *= 10000

    operator = "*" if "*" in m.group(0) else "/"

    return {
        "source_amount": source_amount,
        "rate": rate,
        "result_amount": result_amount,
        "result_currency": result_currency,
        "operator": operator,
    }


def parse_conversion_line(text: str) -> dict | None:
    """解析换汇公式行，返回 {source_amount, rate, result_amount, result_currency} 或 None"""
    if not text or not text.strip():
        return None

    cleaned = _strip_decorators(text.strip())
    m = _CONVERSION_LINE_RE.match(cleaned)
    if not m:
        return None

    return _parse_match(m)


def find_conversion_in_text(text: str) -> dict | None:
    """在任意文本中搜尋換匯公式（用於引用消息、長文本等場景）"""
    if not text or not text.strip():
        return None

    cleaned = _strip_decorators(text)
    m = _CONVERSION_SEARCH_RE.search(cleaned)
    if not m:
        return None

    return _parse_match(m)
