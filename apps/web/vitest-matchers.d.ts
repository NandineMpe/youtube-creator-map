import "vitest";

/**
 * Types for the matchers `vitest.setup.ts` registers at runtime.
 *
 * `expect.extend` adds them to the runtime object but tells TypeScript
 * nothing, so without this every `toBeInTheDocument` is a type error in a
 * file that runs perfectly. Declaring them here keeps the type checker
 * and the runtime describing the same thing.
 */
declare module "vitest" {
  interface Assertion<T = unknown> {
    // @testing-library/jest-dom
    toBeInTheDocument(): T;
    toHaveAttribute(name: string, value?: unknown): T;
    toHaveTextContent(text: string | RegExp): T;
    toHaveAccessibleName(name?: string | RegExp): T;
    toBeVisible(): T;
    toBeDisabled(): T;
    toHaveFocus(): T;
    toHaveClass(...classNames: string[]): T;
    // vitest-axe
    toHaveNoViolations(): T;
  }

  interface AsymmetricMatchersContaining {
    toBeInTheDocument(): unknown;
    toHaveNoViolations(): unknown;
  }
}
