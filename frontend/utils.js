// frontend/utils.js
// Base URL for API requests
const API_BASE_URL = "http://localhost:8000";

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
        
        // 401未授權，自動跳轉到登錄頁
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
        alert("請求失敗，請稍後重試");
        throw error;
    }
}

// ==================== Format functions ====================
function formatHKD(amount) {
    // 格式化HKD金額為千分位
    return amount.toLocaleString("zh-HK") + " HKD";
}

function utcToHKTime(utcTimestamp) {
    /**
     * 正確將UTC時間戳轉換為香港時間字符串
     * @param {string} utcTimestamp - 後端返回的UTC ISO格式時間戳（例如：2026-05-26T08:00:00.000Z）
     * @returns {string} 香港時間格式字符串（例如：2026-05-26 16:00:00）
     */
    const date = new Date(utcTimestamp);
    return date.toLocaleString("zh-HK", {
        timeZone: "Asia/Hong_Kong",  // 強制指定香港時區
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false  // 使用24小時制，避免顯示上午/下午
    });
}

// ==================== Page permission check ====================
function checkAuth() {
    // If not logged in, redirect to login page
    if (!isLoggedIn()) {
        window.location.href = "index.html";
    }
}

// ==================== Logout function ====================
function logout() {
    removeToken();
    window.location.href = "index.html";
}