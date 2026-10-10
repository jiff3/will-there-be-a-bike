import type { CSSProperties } from 'react';

const paths = {
  bike: 'M5 17a4 4 0 1 0 0 .01M19 17a4 4 0 1 0 0 .01M5 17l5-9 5 9H5m5-9h6l3 9M8 5h4m3 0h3l1 3',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  search: 'M10.5 18a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15m5.5-2 5 5',
  pin: 'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  close: 'M6 6l12 12M6 18 18 6',
  user: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M4 21v-3a8 8 0 0 1 16 0v3',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18m0 4v5l3 2',
  refresh: 'M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3',
  dock: 'M6 4v16h12V4M3 20h18M9 8h6m-6 4h6',
  check: 'M5 12l4 4L19 6',
  walk: 'M14 4a1 1 0 1 0 0 .01M12 7l-3 5H5m7-5 3 5h4m-7-5-1 8-5 6m5-6 5 6',
  swap: 'M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4',
} satisfies Record<string, string>;
export function Icon({
  name,
  size = 20,
  style,
}: {
  name: keyof typeof paths;
  size?: number;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}
    >
      <path d={paths[name]} />
    </svg>
  );
}
