import { Component, type ReactNode } from "react";

/** A crash on one page shows a message and a way back instead of a blank screen. */
export default class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null; key?: string }> {
  state: { error: Error | null; key?: string } = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  static getDerivedStateFromProps(p: { resetKey?: string }, s: { error: Error | null; key?: string }) { return p.resetKey !== s.key ? { error: null, key: p.resetKey } : null; }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="mx-auto mt-10 max-w-lg rounded-xl border border-red-200 bg-red-50 p-5 text-sm">
        <h2 className="font-semibold text-red-800">This page hit an error</h2>
        <p className="mt-1 text-red-700">{this.state.error.message}</p>
        <button className="mt-3 rounded-md border border-red-300 bg-white px-3 py-1.5 text-red-800 hover:bg-red-100" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    );
  }
}
