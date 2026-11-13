import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import MatchesPage from "./pages/MatchesPage";
import MatchPage from "./pages/MatchPage";
import UploadPage from "./pages/UploadPage";
import NotFound from "./pages/NotFound";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<MatchesPage />} />
        <Route path="upload" element={<UploadPage />} />
        <Route path="match/:id" element={<MatchPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
