// Stroke icons drawn for this app (24px grid, 1.75 stroke, currentColor).
const base = {
  width: 20,
  height: 20,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
  focusable: 'false',
}

const paths = {
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></>,
  polygon: <><path d="M5 17 4 7l8-3 8 6-4 9Z" /><circle cx="4" cy="7" r="1.4" /><circle cx="12" cy="4" r="1.4" /><circle cx="20" cy="10" r="1.4" /><circle cx="16" cy="19" r="1.4" /><circle cx="5" cy="17" r="1.4" /></>,
  rectangle: <><rect x="4" y="6" width="16" height="12" rx="0.5" strokeDasharray="3 2.2" /><circle cx="4" cy="6" r="1.4" /><circle cx="20" cy="18" r="1.4" /></>,
  clear: <><path d="M4 7h16M9 7V4.8h6V7M6.5 7l1 12.5h9l1-12.5" /></>,
  upload: <><path d="M12 15V4m0 0L7.5 8.5M12 4l4.5 4.5" /><path d="M5 14v4.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V14" /></>,
  file: <><path d="M14 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8Z" /><path d="M14 3.5V8h4.5M8.5 13c1.5-1.6 3-1.6 4.5 0s3 1.6 4.5 0M8.5 16.5c1.5-1.6 3-1.6 4.5 0" /></>,
  drop: <><path d="M12 3.5s6 6.4 6 10.5a6 6 0 0 1-12 0c0-4.1 6-10.5 6-10.5Z" /><path d="M9 14.5a3 3 0 0 0 3 3" /></>,
  pin: <><path d="M12 21s6.5-5.6 6.5-11A6.5 6.5 0 0 0 5.5 10c0 5.4 6.5 11 6.5 11Z" /><circle cx="12" cy="10" r="2.3" /></>,
  save: <><path d="M5 4.5h11l3 3v11a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 18.5Z" /><path d="M8.5 4.5v4h6v-4M8 20v-5.5h8V20" /></>,
  download: <><path d="M12 4v11m0 0 4.5-4.5M12 15l-4.5-4.5" /><path d="M5 19.5h14" /></>,
  target: <><circle cx="12" cy="12" r="7" /><circle cx="12" cy="12" r="2" /><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3" /></>,
  copy: <><rect x="8.5" y="8.5" width="11" height="11" rx="1.5" /><path d="M15.5 8.5V6A1.5 1.5 0 0 0 14 4.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5" /></>,
  close: <><path d="m6 6 12 12M18 6 6 18" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5.5M12 7.6v.2" /></>,
  warning: <><path d="M12 4 2.8 19.5h18.4Z" /><path d="M12 10v4.5M12 17.2v.2" /></>,
  book: <><path d="M4.5 5.5A1.5 1.5 0 0 1 6 4h13.5v14H6a1.5 1.5 0 0 0-1.5 1.5Z" /><path d="M4.5 19.5A1.5 1.5 0 0 0 6 21h13.5v-3M8.5 8h7M8.5 11h5" /></>,
  pulse: <><path d="M3 12h4l2.5-6 4 12 2.5-6H21" /></>,
  code: <><path d="m8.5 8-4 4 4 4M15.5 8l4 4-4 4M13.5 5.5l-3 13" /></>,
  layers: <><path d="m12 4 8.5 4.5L12 13 3.5 8.5Z" /><path d="m3.5 12.5 8.5 4.5 8.5-4.5M3.5 16.5 12 21l8.5-4.5" /></>,
  sliders: <><path d="M5 20v-6M5 10V4M12 20v-8M12 8V4M19 20v-4M19 12V4M3 14h4M10 8h4M17 16h4" /></>,
  check: <><path d="m5 12.5 4.5 4.5L19 7.5" /></>,
  undo: <><path d="M9 14 4.5 9.5 9 5" /><path d="M4.5 9.5h9a5.5 5.5 0 0 1 0 11H11" /></>,
  menu: <><path d="M4 7h16M4 12h16M4 17h16" /></>,
}

export default function Icon({ name, size = 20, ...rest }) {
  return (
    <svg {...base} width={size} height={size} {...rest}>
      {paths[name]}
    </svg>
  )
}
