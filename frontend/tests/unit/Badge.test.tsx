import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge } from "@/components/ui/Badge";

describe("Badge", () => {
  it("renders content", () => {
    render(<Badge>hello</Badge>);
    expect(screen.getByText("hello")).toBeInTheDocument();
  });
  it("applies tone classes", () => {
    const { container } = render(<Badge tone="warn">warn</Badge>);
    const span = container.querySelector("span");
    expect(span?.className).toMatch(/amber/);
  });
});
