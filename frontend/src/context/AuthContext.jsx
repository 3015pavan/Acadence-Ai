import { createContext, useContext, useEffect, useMemo, useState } from "react";
import api from "../api";
import { clearAuthSession, getStoredAccessToken, getStoredUser, setAuthSession } from "../auth/session";


const AuthContext = createContext(null);


export function AuthProvider({ children }) {
  const [user, setUser] = useState(getStoredUser());
  const [loading, setLoading] = useState(Boolean(getStoredAccessToken()));

  useEffect(() => {
    let active = true;

    async function syncSession() {
      const token = getStoredAccessToken();
      if (!token) {
        setLoading(false);
        return;
      }

      try {
        const response = await api.get("/auth/me");
        if (!active) {
          return;
        }
        setUser(response.data);
        setAuthSession({ user: response.data, access_token: token });
      } catch {
        if (!active) {
          return;
        }
        clearAuthSession();
        setUser(null);
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    syncSession();

    return () => {
      active = false;
    };
  }, []);

  async function login(email, password) {
    const response = await api.post("/auth/login", { email, password });
    setAuthSession(response.data);
    setUser(response.data.user);
    return response.data.user;
  }

  async function signup(payload) {
    const response = await api.post("/auth/signup", payload);
    setAuthSession(response.data);
    setUser(response.data.user);
    return response.data.user;
  }

  async function refreshSession() {
    const refreshToken = window.localStorage.getItem("acadence_refresh_token");
    if (!refreshToken) {
      return null;
    }

    const response = await api.post("/auth/refresh", { refresh_token: refreshToken });
    const nextUser = getStoredUser();
    setAuthSession({ ...response.data, user: nextUser });
    return response.data;
  }

  async function logout() {
    try {
      await api.post("/auth/logout");
    } catch {
      // Ignore logout errors and clear the local session anyway.
    } finally {
      clearAuthSession();
      setUser(null);
    }
  }

  const value = useMemo(
    () => ({ user, loading, login, signup, logout, refreshSession, setUser }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}


export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}