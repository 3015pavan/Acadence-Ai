import axios from "axios";

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
  nextConfig.headers = { ...(config.headers || {}), "X-User-Role": resolveUserRole() };
  return nextConfig;
});

export default api;
