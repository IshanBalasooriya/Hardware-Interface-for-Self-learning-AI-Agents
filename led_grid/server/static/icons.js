const paths = {
  arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
  play: '<path d="m8 5 11 7-11 7Z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  grid: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="3" width="6" height="6" rx="1"/><rect x="3" y="15" width="6" height="6" rx="1"/><rect x="15" y="15" width="6" height="6" rx="1"/>',
  moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2m9-10h-2M5 12H3m15.5-6.5-1.4 1.4M6.9 17.1l-1.4 1.4m13.2 0-1.4-1.4M6.9 6.9 5.5 5.5"/>',
  code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18"/>',
  prompt: '<path d="m4 7 5 5-5 5m8 0h8"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3"/>',
  flask: '<path d="M9 3h6m-5 0v6L4 19a1.3 1.3 0 0 0 1 2h14a1.3 1.3 0 0 0 1-2L14 9V3M7 15h10"/>',
  link: '<path d="m10 13 4-4m-6 6-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 3 2-2a4 4 0 1 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(1 0)"/>',
  save: '<path d="M5 3h12l4 4v14H3V3h2Zm2 0v6h10V3M7 21v-7h10v7"/>',
  enter: '<path d="M19 5v9H5m5-5-5 5 5 5"/>'
};
export function icon(name, className = "") {
  return `<svg class="icon ${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] ?? paths.grid}</svg>`;
}
export function brandMark() {
  return `<svg class="brand-mark" viewBox="0 0 28 28" aria-hidden="true">${Array.from({ length: 16 }, (_, i) => `<rect x="${(i % 4) * 7}" y="${Math.floor(i / 4) * 7}" width="4.5" height="4.5" rx="0.8" fill="currentColor" opacity="${[0, 3, 12, 15].includes(i) ? ".3" : "1"}"/>`).join("")}</svg>`;
}