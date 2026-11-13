import type { ReactNode } from "react";

export function Spinner({ label }: { label: string }) {
  return (
    <div className="status" role="status">
      <span className="spin" aria-hidden />
      <span>{label}</span>
    </div>
  );
}

export function Problem({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="problem" role="alert">
      <b>{title}</b>
      {children && <div>{children}</div>}
    </div>
  );
}
