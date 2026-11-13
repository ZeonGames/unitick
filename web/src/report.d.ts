declare module "@render/report.js" {
  /** Draws a full match report into `root`; returns a function that removes it. */
  export function mountReport(root: HTMLElement, payload: unknown, opts?: { embedded?: boolean }): () => void;
}
