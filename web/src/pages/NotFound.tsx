import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <main className="page">
      <div className="empty">
        <b>This page does not exist.</b>
        <p><Link to="/">Go to your matches</Link></p>
      </div>
    </main>
  );
}
