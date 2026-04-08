import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RunInput } from "../../src/components/RunInput";

describe("RunInput", () => {
  it("submits the objective with the selected base persona", async () => {
    const onSubmit = vi.fn();

    render(
      <RunInput
        onSubmit={onSubmit}
        personas={[
          {
            personaId: "python_developer",
            name: "Python Developer",
            description: "Builds Python services",
          },
          {
            personaId: "sql_developer",
            name: "SQL Developer",
            description: "Designs SQL systems",
          },
        ]}
        defaultPersonaId="python_developer"
      />,
    );

    fireEvent.change(screen.getByLabelText(/Describe the root object/i), {
      target: { value: "Create a tiny API" },
    });
    fireEvent.change(screen.getByLabelText("Base persona"), {
      target: { value: "sql_developer" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start Run" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("Create a tiny API", "sql_developer");
  });

  it("shows a fallback option when no personas are available", () => {
    render(<RunInput onSubmit={vi.fn()} personas={[]} personasError="Failed to load personas" />);

    expect(screen.getByLabelText("Base persona")).toHaveAttribute("disabled");
    expect(screen.getByText("No personas found")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Failed to load personas");
  });
});
