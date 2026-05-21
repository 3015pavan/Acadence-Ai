const ACCESS_TOKEN_KEY = "acadence_access_token";
const REFRESH_TOKEN_KEY = "acadence_refresh_token";
const USER_KEY = "acadence_user";


export function getStoredAccessToken() {
  if (typeof window === "undefined") {
    return "";
  }
  return window.localStorage.getItem(ACCESS_TOKEN_KEY) || "";
}


export function getStoredRefreshToken() {
  if (typeof window === "undefined") {
    return "";
  }
  return window.localStorage.getItem(REFRESH_TOKEN_KEY) || "";
}


export function getStoredUser() {
  if (typeof window === "undefined") {
    return null;
  }

  const rawValue = window.localStorage.getItem(USER_KEY);
  if (!rawValue) {
    return null;
  }

  try {
    return JSON.parse(rawValue);
  } catch {
    return null;
  }
}


export function setAuthSession(session) {
  if (typeof window === "undefined") {
    return;
  }

  if (session?.access_token) {
    window.localStorage.setItem(ACCESS_TOKEN_KEY, session.access_token);
  }
  if (session?.refresh_token) {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, session.refresh_token);
  }
  if (session?.user) {
    window.localStorage.setItem(USER_KEY, JSON.stringify(session.user));
  }
}


export function clearAuthSession() {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}