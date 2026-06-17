// frontend/utils.js
// Base URL for API requests
const API_BASE_URL = "http://localhost:8000";

// ==================== Toast Notification ====================
function showToast(message, type = "info") {
    const colors = {
        success: "bg-emerald-500",
        error: "bg-red-500",
        info: "bg-blue-500",
        warning: "bg-amber-500"
    };
    const icons = {
        success: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>`,
        error: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>`,
        info: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
        warning: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>`
    };

    const container = document.getElementById("toast-container") || createToastContainer();
    const toast = document.createElement("div");
    toast.className = `toast ${colors[type] || colors.info} text-white px-4 py-3 rounded-xl shadow-lg flex items-center space-x-2.5 animate-toast-in text-sm font-medium`;
    toast.innerHTML = `${icons[type] || icons.info}<span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(60px)";
        toast.style.transition = "all 0.3s ease";
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function createToastContainer() {
    const container = document.createElement("div");
    container.id = "toast-container";
    container.className = "fixed top-5 right-5 z-[9999] flex flex-col space-y-2";
    document.body.appendChild(container);

    const style = document.createElement("style");
    style.textContent = `
        @keyframes toastIn {
            from { opacity: 0; transform: translateX(60px); }
            to   { opacity: 1; transform: translateX(0); }
        }
        .animate-toast-in { animation: toastIn 0.35s ease-out; }
    `;
    document.head.appendChild(style);
    return container;
}

// ==================== JWT token management ====================
function getToken() {
    return localStorage.getItem("token");
}

function setToken(token) {
    localStorage.setItem("token", token);
}

function removeToken() {
    localStorage.removeItem("token");
}

function isLoggedIn() {
    return getToken() !== null;
}

// ==================== API request encapsulation ====================
async function apiRequest(url, method = "GET", data = null) {
    const headers = {
        "Authorization": `Bearer ${getToken()}`
    };

    const options = {
        method: method,
        headers: headers
    };

    if (data && method !== "GET") {
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(data);
    }

    try {
        const response = await fetch(`${API_BASE_URL}${url}`, options);

        if (response.status === 401) {
            removeToken();
            window.location.href = "index.html";
            return null;
        }

        if (!response.ok) {
            const error = await response.text();
            throw new Error(error);
        }

        return await response.json();
    } catch (error) {
        console.error("API請求失敗：", error);
        showToast("請求失敗，請稍後重試", "error");
        throw error;
    }
}

// ==================== Format functions ====================
function formatHKD(amount) {
    return amount.toLocaleString("zh-HK") + " HKD";
}

function formatCurrency(amount, currency) {
    const c = (currency || "USD").toUpperCase();
    return amount.toLocaleString("zh-HK") + " " + c;
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function formatCurrencyBreakdown(breakdown, field = 'amount') {
    if (!breakdown || Object.keys(breakdown).length === 0) return '<span class="text-gray-400">-</span>';
    return Object.entries(breakdown).map(([cur, data]) =>
        `<span class="inline-flex items-center gap-1 text-sm font-bold"><span class="text-xs font-medium text-gray-400">${cur}</span> ${(data[field] || 0).toLocaleString('zh-HK')}</span>`
    ).join('<span class="mx-1.5 text-gray-300">|</span>');
}

function formatEarnings(earnings) {
    if (!earnings || Object.keys(earnings).length === 0) return '<span class="text-gray-400">-</span>';
    return Object.entries(earnings)
        .sort(([a], [b]) => (a !== "USD" ? 1 : -1))
        .map(([cur, amt]) => `<span class="text-xs font-medium text-gray-400">${cur}</span> ${amt.toLocaleString('zh-HK')}`)
        .join('<span class="mx-1.5 text-gray-300">|</span>');
}

function renderPaymentDetails(paymentDetailsJson) {
    if (!paymentDetailsJson) return "";
    try {
        const pd = typeof paymentDetailsJson === "string" ? JSON.parse(paymentDetailsJson) : paymentDetailsJson;
        const items = [];
        if (pd.swift) items.push(`<span class="text-gray-400">SWIFT:</span> ${escapeHtml(pd.swift)}`);
        if (pd.bank_name) items.push(`<span class="text-gray-400">銀行:</span> ${escapeHtml(pd.bank_name)}`);
        if (pd.bank_address) items.push(`<span class="text-gray-400">地址:</span> ${escapeHtml(pd.bank_address)}`);
        if (pd.bank_code) items.push(`<span class="text-gray-400">代碼:</span> ${escapeHtml(pd.bank_code)}`);
        if (pd.account_number) items.push(`<span class="text-gray-400">戶口:</span> ${escapeHtml(pd.account_number)}`);

        // 換匯信息
        if (pd.conversion && pd.conversion.source_amount) {
            const conv = pd.conversion;
            const srcAmt = conv.source_amount.toLocaleString("zh-HK");
            const srcCur = conv.source_currency || "CNY";
            const opSymbol = conv.operator === "*" ? " × " : " / ";
            let convHtml = `<span class="text-amber-400">兌換:</span> ${srcAmt} ${srcCur}${opSymbol}${conv.rate}`;
            if (conv.matched) {
                convHtml += ` <span class="text-green-400">(≈${conv.daily_rate})</span>`;
            }
            if (conv.autocorrected) {
                convHtml += ` <span class="text-orange-400">[已補全萬位]</span>`;
            }
            items.push(convHtml);
        }

        if (!items.length) return "";
        return `<div class="text-xs text-gray-500 mt-1 space-x-2">${items.join(" <span class='text-gray-300'>|</span> ")}</div>`;
    } catch (e) {
        return "";
    }
}

function utcToHKTime(utcTimestamp) {
    const date = new Date(utcTimestamp);
    return date.toLocaleString("zh-HK", {
        timeZone: "Asia/Hong_Kong",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false
    });
}

// ==================== Page permission check ====================
function checkAuth() {
    if (!isLoggedIn()) {
        window.location.href = "index.html";
    }
}

// ==================== Logout function ====================
function logout() {
    removeToken();
    window.location.href = "index.html";
}

// ==================== Shared UI: loading skeleton ====================
function renderSkeleton(rows, heightClass = "h-10") {
    let html = '<div class="space-y-3">';
    for (let i = 0; i < rows; i++) {
        html += `<div class="skeleton ${heightClass} w-full"></div>`;
    }
    html += "</div>";
    return html;
}

// ==================== Shared UI: empty state ====================
function renderEmptyState(title, description = "") {
    return `
        <div class="flex flex-col items-center justify-center py-12 text-center">
            <div class="w-16 h-16 bg-gray-100 rounded-2xl flex items-center justify-center mb-4">
                <svg class="w-7 h-7 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"/>
                </svg>
            </div>
            <p class="text-sm font-medium text-gray-500">${title}</p>
            ${description ? `<p class="text-xs text-gray-400 mt-1">${description}</p>` : ""}
        </div>
    `;
}
