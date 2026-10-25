import { useId } from "react";

/** The RepoLens mark: a lens over a small graph of connected repository nodes. Same drawing as public/favicon.svg. */
export default function Logo({ className = "h-6 w-6" }: { className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 64 64" className={className} role="img" aria-label="RepoLens AI logo">
      <defs><linearGradient id={id} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#6366f1" /><stop offset="1" stopColor="#3730a3" /></linearGradient></defs>
      <rect width="64" height="64" rx="15" fill={`url(#${id})`} />
      <circle cx="28" cy="28" r="16" fill="#fff" fillOpacity="0.14" stroke="#fff" strokeWidth="4" />
      <path d="M40 40 L53 53" stroke="#fff" strokeWidth="6" strokeLinecap="round" />
      <path d="M21 25 L33 21 M33 21 L29 35 M21 25 L29 35" stroke="#fff" strokeWidth="2" strokeLinecap="round" fill="none" />
      <circle cx="21" cy="25" r="3.4" fill="#fff" /><circle cx="29" cy="35" r="3.4" fill="#fff" /><circle cx="33" cy="21" r="3.9" fill="#fbbf24" />
    </svg>
  );
}
