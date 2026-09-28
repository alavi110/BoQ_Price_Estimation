/**
 * Vitest setup: the jest-dom matchers, and the browser APIs jsdom does not
 * implement but Recharts and the ResizeObserver-based layout code call.
 *
 * `matchMedia` is stubbed rather than left absent because a missing
 * `window.matchMedia` throws on access, not on call - so a chart that queries it
 * fails the test with a ReferenceError that says nothing about the chart.
 * `ResizeObserver` is polyfilled as a no-op for the same reason: Recharts
 * subscribes to it during render and a test that renders a chart would otherwise
 * fail before reaching any assertion about it.
 */
import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

if (!('ResizeObserver' in window)) {
  class NoopResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver =
    NoopResizeObserver;
}

// jsdom reports every element as 0x0. `getBoundingClientRect` is deliberately
// left as jsdom provides it: stubbing it would let a component that reads real
// layout pass a test it would fail in a browser.

afterEach(() => {
  cleanup();
});
