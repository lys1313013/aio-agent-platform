import { cn } from '@/lib/utils';

export default function BrandLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn('h-6 w-6', className)} aria-hidden>
      <defs>
        <linearGradient id="brand-bg" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#8b5cf6" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="7" fill="url(#brand-bg)" />
      <g fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <line x1="16" y1="5.5" x2="16" y2="9" />
        <circle cx="16" cy="4.5" r="1.4" fill="#fff" stroke="none" />
        <rect x="7.5" y="9" width="17" height="13" rx="4.5" />
        <line x1="5.5" y1="13.5" x2="7.5" y2="13.5" />
        <line x1="24.5" y1="13.5" x2="26.5" y2="13.5" />
        <line x1="5.5" y1="17.5" x2="7.5" y2="17.5" />
        <line x1="24.5" y1="17.5" x2="26.5" y2="17.5" />
        <circle cx="12.5" cy="14.5" r="1.7" fill="#fff" stroke="none" />
        <circle cx="19.5" cy="14.5" r="1.7" fill="#fff" stroke="none" />
        <line x1="12.5" y1="18.5" x2="19.5" y2="18.5" />
      </g>
    </svg>
  );
}
