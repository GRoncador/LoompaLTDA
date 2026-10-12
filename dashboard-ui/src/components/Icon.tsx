// Ícones de traço no estilo da biblioteca Lucide (ISC), desenhados à mão para não
// depender de pacote novo. Herdam a cor do texto (currentColor) e o tamanho da classe.
const PATHS: Record<IconName, React.ReactNode> = {
  lightbulb: <><path d="M9 18h6" /><path d="M10 21h4" /><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z" /></>,
  calendar: <><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /></>,
  clipboard: <><rect x="8" y="2" width="8" height="4" rx="1" /><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /></>,
  wrench: <path d="M14.7 6.3a4 4 0 0 0 5 5L21 13l-7.5 7.5a2.1 2.1 0 0 1-3-3L17.9 10a4 4 0 0 0-3.2-3.7z M4 20l3-3" />,
  search: <><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></>,
  pin: <><path d="M12 17v5" /><path d="M9 3h6l-1 6 3 3H7l3-3z" /></>,
  inbox: <><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 5.1L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z" /></>,
  pause: <><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></>,
  stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
  play: <path d="M7 4.5v15l12-7.5z" />,
  mic: <><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10a7 7 0 0 0 14 0M12 19v3" /></>,
  check: <path d="M4 12.5l5 5L20 6.5" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  palette: <><path d="M12 22a10 10 0 1 1 10-10c0 2-1.5 3-3.5 3H16a2 2 0 0 0-1 3.7c.6.4.5 1.3-.5 1.3z" /><circle cx="7.5" cy="10.5" r="1" /><circle cx="12" cy="7" r="1" /><circle cx="16.5" cy="10.5" r="1" /></>,
  puzzle: <path d="M19.4 13.5h-1.9V10a2 2 0 0 0-2-2h-3.5V6.1a2.1 2.1 0 1 0-4.2 0V8H4.2a2 2 0 0 0-2 2v3.5h1.9a2.2 2.2 0 1 1 0 4.4H2.2V21a2 2 0 0 0 2 2h14.7" />,
  lock: <><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>,
  unlock: <><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 7.8-1.2" /></>,
  flag: <><path d="M4 22V4" /><path d="M4 4h13l-2 4 2 4H4" /></>,
  wallet: <><path d="M20 7H5a2 2 0 0 1 0-4h13v4" /><rect x="3" y="7" width="18" height="14" rx="2" /><circle cx="16.5" cy="14" r="1" /></>,
  compass: <><circle cx="12" cy="12" r="10" /><path d="M16.2 7.8l-2.1 5.6-5.6 2.1 2.1-5.6z" /></>,
  megaphone: <><path d="M3 11v2a1 1 0 0 0 1 1h2l5 4V6L6 10H4a1 1 0 0 0-1 1z" /><path d="M15 9a4 4 0 0 1 0 6M18.5 6a8 8 0 0 1 0 12" /></>,
  image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="9" cy="9" r="2" /><path d="M21 15l-5-5L5 21" /></>,
  building: <><rect x="4" y="2" width="16" height="20" rx="1" /><path d="M9 6h2M13 6h2M9 10h2M13 10h2M9 14h2M13 14h2M10 22v-4h4v4" /></>,
  message: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  bug: <><rect x="8" y="6" width="8" height="14" rx="4" /><path d="M8 11H3M21 11h-5M8 16H3M21 16h-5M9 6l-2-3M15 6l2-3M12 6v0" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  smile: <><circle cx="12" cy="12" r="10" /><path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>,
  flask: <path d="M9 3h6M10 3v6L4.5 18.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-2.5L14 9V3" />,
  brain: <><path d="M9.5 2A3.5 3.5 0 0 0 6 5.5v.5a3 3 0 0 0-2 5.2A3.5 3.5 0 0 0 7.5 18H9V2z" /><path d="M14.5 2A3.5 3.5 0 0 1 18 5.5v.5a3 3 0 0 1 2 5.2A3.5 3.5 0 0 1 16.5 18H15V2z" /></>,
  refresh: <><path d="M21 12a9 9 0 1 1-2.6-6.4L21 8" /><path d="M21 3v5h-5" /></>,
  rocket: <><path d="M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2c.8-1 .8-2.3 0-3.1A2.2 2.2 0 0 0 5 15z" /><path d="M12 15l-3-3a22 22 0 0 1 2-3.9A12.9 12.9 0 0 1 22 2c0 2.7-.8 7.5-6 11a22 22 0 0 1-4 2z" /><circle cx="17" cy="7" r="1.5" /></>,
  ban: <><circle cx="12" cy="12" r="10" /><path d="M4.9 4.9l14.2 14.2" /></>,
  sparkles: <><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" /><path d="M19 17l.7 1.8L21.5 19.5 19.7 20.2 19 22l-.7-1.8-1.8-.7 1.8-.7z" /></>,
  barChart: <><path d="M3 3v18h18" /><path d="M7 15v3M12 10v8M17 6v12" /></>,
  globe: <><circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z" /></>,
  key: <><circle cx="7.5" cy="15.5" r="4.5" /><path d="M10.7 12.3L21 2M17 6l3 3M14 9l2 2" /></>,
  edit: <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />,
  star: <path d="M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.3l-5.9 3.3 1.3-6.6-4.9-4.6 6.6-.8z" />,
  arrowUp: <path d="M12 19V5M5 12l7-7 7 7" />,
  square: <rect x="4" y="4" width="16" height="16" rx="2" />,
  checkSquare: <><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M8 12.5l3 3 5-6" /></>,
  landmark: <><path d="M3 21h18M5 21V10M10 21V10M14 21V10M19 21V10M12 3l9 5H3z" /></>,
  radio: <><circle cx="12" cy="12" r="2" /><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2" /></>,
};

export type IconName = "lightbulb" | "calendar" | "clipboard" | "wrench" | "search" | "pin" | "inbox" | "pause" | "stop" | "play" | "mic" | "check" | "x" | "palette" | "puzzle" | "lock" | "unlock" | "flag" | "wallet" | "compass" | "megaphone" | "image" | "building" | "message" | "bug" | "user" | "smile" | "settings" | "flask" | "brain" | "refresh" | "rocket" | "ban" | "sparkles" | "barChart" | "globe" | "key" | "edit" | "star" | "arrowUp" | "square" | "checkSquare" | "landmark" | "radio";

export function Icon({ name, className = "h-4 w-4" }: { name: IconName; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`inline-block shrink-0 align-[-0.125em] ${className}`}
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
