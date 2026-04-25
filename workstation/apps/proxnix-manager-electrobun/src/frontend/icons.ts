export type IconName =
  | "back"
  | "box"
  | "branch"
  | "chevron"
  | "edit"
  | "folder"
  | "gear"
  | "health"
  | "home"
  | "key"
  | "lock"
  | "open"
  | "publish"
  | "refresh"
  | "server"
  | "spark"
  | "trash";

export function icon(name: IconName): string {
  const paths: Record<IconName, string> = {
    back: '<path d="M19 12H5" /><path d="m12 5-7 7 7 7" />',
    box: '<path d="M3 7.5 12 3l9 4.5-9 4.5-9-4.5Z" /><path d="M3 7.5V16.5L12 21L21 16.5V7.5" /><path d="M12 12v9" />',
    branch:
      '<circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="18" cy="18" r="2.5" /><path d="M8.5 6H15.5" /><path d="M18 8.5V15.5" /><path d="M8.5 6V10.5C8.5 12.71 10.29 14.5 12.5 14.5H18" />',
    chevron: '<path d="m9 6 6 6-6 6" />',
    edit:
      '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5Z" />',
    folder:
      '<path d="M3 8a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8Z" /><path d="M3 10h18" />',
    gear:
      '<circle cx="12" cy="12" r="3.5" /><path d="M12 2.8v2.3M12 18.9v2.3M4.3 7.1l2 1.2M17.7 14.7l2 1.2M2.8 12h2.3M18.9 12h2.3M4.3 16.9l2-1.2M17.7 9.3l2-1.2" />',
    health:
      '<path d="M12 5v14M5 12h14" /><circle cx="12" cy="12" r="9" />',
    home:
      '<path d="M3 11.5 12 4l9 7.5" /><path d="M5.5 10.5V20h13v-9.5" /><path d="M9.5 20v-5h5v5" />',
    key:
      '<circle cx="8" cy="12" r="4" /><path d="M12 12h9" /><path d="M17 12v3" /><path d="M20 12v2" />',
    lock:
      '<rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V7.5A4 4 0 0 1 12 3.5A4 4 0 0 1 16 7.5V10" />',
    open: '<path d="M14 4h6v6" /><path d="M10 14L20 4" /><path d="M20 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5" />',
    publish: '<path d="M12 16V4" /><path d="M7 9l5-5 5 5" /><path d="M4 20h16" />',
    refresh:
      '<path d="M20 6v5h-5" /><path d="M4 18v-5h5" /><path d="M7 17a7 7 0 0 0 11-3" /><path d="M17 7A7 7 0 0 0 6 10" />',
    server:
      '<rect x="3" y="4" width="18" height="7" rx="2" /><rect x="3" y="13" width="18" height="7" rx="2" /><path d="M7 8h.01" /><path d="M7 17h.01" /><path d="M11 8h6" /><path d="M11 17h6" />',
    spark:
      '<path d="M12 3l1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7L12 3Z" />',
    trash:
      '<path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M6 6l1 15h10l1-15" /><path d="M10 10v7" /><path d="M14 10v7" />',
  };

  return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]}</svg>`;
}
