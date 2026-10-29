import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { NAV_GROUPS } from "@/components/layout/sidebar";
import { ToastProvider } from "@/components/ui/toast";
import CampaignsPage from "@/pages/campaigns";

vi.mock("@/app/auth", async () => {
  const { businessFixture } = await import("@/test/fixtures");
  return {
    useBusiness: () => businessFixture,
  };
});

function renderWithProviders(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("primary navigation", () => {
  it("exposes every product area exactly once", () => {
    const links = NAV_GROUPS.flatMap((group) =>
      group.items.map((item) => item.to),
    );

    expect(links).toEqual([
      "/",
      "/agents",
      "/leads",
      "/calls",
      "/handoffs",
      "/campaigns",
      "/analytics",
      "/compliance",
      "/settings",
    ]);
  });
});

describe("Campaigns Management", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("renders campaign listing with metrics and action buttons", async () => {
    renderWithProviders(<CampaignsPage />);

    expect(await screen.findByRole("heading", { name: "Campaigns" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create campaign/i })).toBeInTheDocument();
    expect(screen.getByText(/Total Campaigns/i)).toBeInTheDocument();
  });

  it("requires explicit confirmation before launching a campaign", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CampaignsPage />);

    // Wait for the seeded campaigns to render
    const startButtons = await screen.findAllByRole("button", { name: /start/i });
    expect(startButtons.length).toBeGreaterThan(0);

    // Click Start on the draft campaign
    await user.click(startButtons[0]);

    // An explicit confirmation dialog must appear (PRD Section 6.4 requirement)
    expect(
      await screen.findByRole("heading", { name: /confirm campaign launch/i }),
    ).toBeInTheDocument();

    expect(
      screen.getByText(/Server Compliance Assurance/i),
    ).toBeInTheDocument();

    // Confirm button is disabled until the compliance checkbox is checked
    const confirmButton = screen.getByRole("button", {
      name: /confirm & start campaign/i,
    });
    expect(confirmButton).toBeDisabled();

    // Check the compliance confirmation checkbox
    const checkbox = screen.getByRole("checkbox");
    await user.click(checkbox);

    // Confirm button is now enabled
    expect(confirmButton).not.toBeDisabled();
  });
});
