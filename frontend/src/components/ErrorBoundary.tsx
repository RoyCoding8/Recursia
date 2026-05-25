"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  private handleReset = (): void => {
    this.setState({ hasError: false, error: undefined });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div style={{ padding: 24, textAlign: "center" }}>
            <h2>Something went wrong</h2>
            <p style={{ color: "#888", margin: "8px 0 16px" }}>
              {this.state.error?.message ?? "An unexpected error occurred."}
            </p>
            <button onClick={this.handleReset} style={{ padding: "6px 16px", cursor: "pointer" }}>
              Try again
            </button>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
