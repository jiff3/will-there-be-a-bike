import { Route, Routes } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { HomePage } from './pages/HomePage';
import { StatusPage } from './pages/StatusPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { AccountPage } from './pages/AccountPage';
import { AuthProvider } from './auth/AuthProvider';
import { CommutesPage } from './pages/CommutesPage';
import { NotificationsPage } from './pages/NotificationsPage';
import { ReplayPage } from './pages/ReplayPage';

export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<HomePage />} />
          <Route path="status" element={<StatusPage />} />
          <Route path="account" element={<AccountPage />} />
          <Route path="commutes" element={<CommutesPage />} />
          <Route path="notifications" element={<NotificationsPage />} />
          <Route path="replay" element={<ReplayPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}
