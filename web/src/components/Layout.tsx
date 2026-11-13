import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

export default function Layout() {
  const path = useLocation().pathname;
  const onUpload = path.startsWith("/upload");
  return (
    <div className="shell">
      <header className={`topbar${path.startsWith("/match/") ? " wide" : ""}`}>
        <NavLink to="/" className="wordmark" aria-label="unitick home">
          uni<i>tick</i>
        </NavLink>
        {!onUpload && (
          <Link to="/upload" className="btn primary"><span>Analyze a replay</span></Link>
        )}
      </header>
      <Outlet />
    </div>
  );
}
