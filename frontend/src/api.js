import axios from "axios";
import { clearAuthSession, getStoredAccessToken, getStoredRefreshToken, setAuthSession } from "./auth/session";

function resolveApiBaseUrl() {
  const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();

  if (configuredBaseUrl) {
    return configuredBaseUrl.replace(/\/$/, "");
  }

  if (
    typeof window !== "undefined" &&
    ["localhost", "127.0.0.1"].includes(window.location.hostname)
  ) {
    return "http://127.0.0.1:8000";  // Matches backend port and Gmail redirect URI
  }

  return "/";
}

function resolveUserRole() {
  const configuredRole = import.meta.env.VITE_USER_ROLE?.trim();
  if (configuredRole) {
    return configuredRole;
  }

  if (typeof window !== "undefined") {
    const storedRole = window.localStorage.getItem("userRole")?.trim();
    if (storedRole) {
      return storedRole;
    }
  }

  return "admin";
}

const api = axios.create({
  baseURL: resolveApiBaseUrl(),
});

api.interceptors.request.use((config) => {
  const nextConfig = { ...config };
  const token = getStoredAccessToken();
  nextConfig.headers = {
    ...(config.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  return nextConfig;
});

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (error?.response?.status === 401 && !originalRequest?._retry) {
      const refreshToken = getStoredRefreshToken();
      if (!refreshToken) {
        clearAuthSession();
        return Promise.reject(error);
      }

      originalRequest._retry = true;
      try {
        const refreshResponse = await axios.post(`${resolveApiBaseUrl()}/auth/refresh`, { refresh_token: refreshToken });
        const nextToken = refreshResponse.data?.access_token;
        if (nextToken) {
          const existingUser = window.localStorage.getItem("acadence_user");
          setAuthSession({ ...refreshResponse.data, user: existingUser ? JSON.parse(existingUser) : null });
          originalRequest.headers = { ...(originalRequest.headers || {}), Authorization: `Bearer ${nextToken}` };
          return api(originalRequest);
        }
      } catch {
        clearAuthSession();
      }
    }

    return Promise.reject(error);
  },
);

export default api;
