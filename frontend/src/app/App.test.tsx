import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import App from "@/app/App";
import { AuthProvider } from "@/app/auth";
import { ToastProvider } from "@/components/ui/toast";

function renderApp(initialEntries: string[]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <AuthProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("protected routing", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("shows the API-key login when no session exists", async () => {
    renderApp(["/login"]);

    expect(
      await screen.findByRole("heading", { name: /connect your workspace/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/api key/i)).toBeInTheDocument();
  });

  it("redirects an anonymous visitor away from a protected page", async () => {
    renderApp(["/calls"]);

    expect(
      await screen.findByRole("heading", { name: /connect your workspace/i }),
    ).toBeInTheDocument();
  });
});
