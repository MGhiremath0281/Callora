import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  ApiError,
  UNAUTHORIZED_EVENT,
  clearStoredApiKey,
  readStoredApiKey,
  storeApiKey,
} from "@/lib/api";
import { getBusiness } from "@/services/callora";
import type { Business } from "@/types/api";

type AuthStatus = "loading" | "anonymous" | "authenticated" | "error";

type AuthContextValue = {
  status: AuthStatus;
  apiKey: string | null;
  business: Business | null;
  error: string | null;
  login: (key: string) => Promise<void>;
  logout: () => void;
  refreshBusiness: () => Promise<Business | null>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [apiKey, setApiKey] = useState<string | null>(() => readStoredApiKey());
  const [business, setBusiness] = useState<Business | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  const logout = useCallback(() => {
    clearStoredApiKey();
    setApiKey(null);
    setBusiness(null);
    setError(null);
    setStatus("anonymous");
  }, []);

  const loadBusiness = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const profile = await getBusiness();
      setBusiness(profile);
      setStatus("authenticated");
      return profile;
    } catch (err) {
      if (err instanceof ApiError && err.isUnauthorized) {
        logout();
        return null;
      }
      const message =
        err instanceof Error ? err.message : "Could not load the workspace.";
      setError(message);
      setStatus("error");
      return null;
    }
  }, [logout]);

  useEffect(() => {
    if (!apiKey) {
      setStatus("anonymous");
      return;
    }
    void loadBusiness();
    // The API key is the only dependency: re-validating on every render
    // would hammer the backend.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey]);

  useEffect(() => {
    const handler = () => logout();
    window.addEventListener(UNAUTHORIZED_EVENT, handler);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handler);
  }, [logout]);

  const login = useCallback(
    async (key: string) => {
      const trimmed = key.trim();
      if (!trimmed) throw new Error("Enter your Callora API key.");
      storeApiKey(trimmed);
      setApiKey(trimmed);
      setStatus("loading");
      setError(null);
      try {
        const profile = await getBusinessWithKey(trimmed);
        setBusiness(profile);
        setStatus("authenticated");
      } catch (err) {
        clearStoredApiKey();
        setApiKey(null);
        setBusiness(null);
        setStatus("anonymous");
        throw err;
      }
    },
    [],
  );

  const refreshBusiness = useCallback(async () => {
    try {
      const profile = await getBusiness();
      setBusiness(profile);
      setStatus("authenticated");
      setError(null);
      return profile;
    } catch (err) {
      if (err instanceof ApiError && err.isUnauthorized) {
        logout();
        return null;
      }
      return null;
    }
  }, [logout]);

  const value = useMemo(
    () => ({
      status,
      apiKey,
      business,
      error,
      login,
      logout,
      refreshBusiness,
    }),
    [status, apiKey, business, error, login, logout, refreshBusiness],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/**
 * Validate a candidate key before adopting it.
 *
 * `getBusiness` reads the stored key, so the key is stored first and removed
 * again when the API rejects it (see `login`).
 */
async function getBusinessWithKey(key: string) {
  return getBusiness().catch((error) => {
    if (error instanceof ApiError) throw error;
    throw new Error(
      "Could not reach the Callora API. Check the backend URL and try again.",
    );
  });
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside <AuthProvider>.");
  }
  return context;
}

export function useBusiness() {
  const { business } = useAuth();
  if (!business) {
    throw new Error("useBusiness requires an authenticated workspace.");
  }
  return business;
}
