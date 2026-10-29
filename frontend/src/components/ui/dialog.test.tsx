import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ConfirmDialog } from "./dialog";

describe("ConfirmDialog", () => {
  it("blocks a typed confirmation until the exact phrase is entered", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    render(
      <ConfirmDialog
        open
        onClose={() => undefined}
        onConfirm={onConfirm}
        title="Activate the voice agent?"
        description="Outbound calling will be enabled for this workspace."
        confirmLabel="Activate agent"
        requireText="ACTIVATE"
        tone="danger"
      />,
    );

    const confirm = screen.getByRole("button", { name: "Activate agent" });
    expect(confirm).toBeDisabled();

    await user.type(screen.getByRole("textbox"), "active");
    expect(confirm).toBeDisabled();

    await user.clear(screen.getByRole("textbox"));
    await user.type(screen.getByRole("textbox"), "ACTIVATE");
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("allows a normal confirmation without typed text", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    render(
      <ConfirmDialog
        open
        onClose={() => undefined}
        onConfirm={onConfirm}
        title="Cancel this call?"
        description="The call cannot be resumed."
        confirmLabel="Cancel call"
      />,
    );

    const confirm = screen.getByRole("button", { name: "Cancel call" });
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
