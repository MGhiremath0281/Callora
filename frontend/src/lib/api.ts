import type { ApiErrorBody } from "@/types/api";

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(
  /\/$/,
  "",
);

export const API_KEY_STORAGE = "callora.apiKey";

/** Fired when the backend rejects the stored API key. */
export const UNAUTHORIZED_EVENT = "callora:unauthorized";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown[];

  constructor(
    message: string,
    status: number,
    code = "request_failed",
    details: unknown[] = [],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get isUnauthorized() {
    return this.status === 401;
  }

  get isForbidden() {
    return this.status === 403;
  }

  get isNotFound() {
    return this.status === 404;
  }

  get isNetwork() {
    return this.status === 0;
  }
}

export function readStoredApiKey(): string | null {
  try {
    return window.localStorage.getItem(API_KEY_STORAGE);
  } catch {
    return null;
  }
}

export function storeApiKey(key: string) {
  window.localStorage.setItem(API_KEY_STORAGE, key);
}

export function clearStoredApiKey() {
  window.localStorage.removeItem(API_KEY_STORAGE);
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  formData?: FormData;
  signal?: AbortSignal;
  apiKey?: string | null;
  skipAuth?: boolean;
};

function parseErrorBody(body: unknown): {
  message: string;
  code: string;
  details: unknown[];
} {
  const parsed = body as ApiErrorBody | undefined;
  if (parsed?.error?.message) {
    return {
      message: parsed.error.message,
      code: parsed.error.code || "request_failed",
      details: parsed.error.details || [],
    };
  }
  if (typeof parsed?.detail === "string") {
    return { message: parsed.detail, code: "request_failed", details: [] };
  }
  if (Array.isArray(parsed?.detail)) {
    const details = parsed.detail as Array<{ msg?: string; loc?: unknown[] }>;
    return {
      message:
        details
          .map((entry) => entry.msg || "Invalid value")
          .filter(Boolean)
          .join("; ") || "The request could not be completed.",
      code: "validation_error",
      details: details as unknown[],
    };
  }
  return {
    message: "The request could not be completed.",
    code: "request_failed",
    details: [],
  };
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const {
    method = "GET",
    body,
    formData,
    signal,
    apiKey = readStoredApiKey(),
    skipAuth = false,
  } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (!skipAuth && apiKey) headers["X-API-Key"] = apiKey;
  if (body !== undefined && !formData) headers["Content-Type"] = "application/json";

  const url = path.startsWith("http")
    ? path
    : `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
      signal,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(
      "The Callora API could not be reached. Check that the backend is running and the API URL is correct.",
      0,
      "network_error",
    );
  }

  if (response.status === 401 && !skipAuth) {
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
  }

  if (!response.ok) {
    let parsedBody: unknown = null;
    try {
      parsedBody = await response.json();
    } catch {
      // A non-JSON body is reported with the status text below.
    }
    const parsed = parseErrorBody(parsedBody);
    throw new ApiError(
      parsed.message || `Request failed with HTTP ${response.status}.`,
      response.status,
      parsed.code,
      parsed.details,
    );
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(
      "The Callora API returned a response that was not valid JSON.",
      response.status,
      "invalid_response",
    );
  }
}
