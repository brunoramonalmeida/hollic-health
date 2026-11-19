import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { GuestRoute, ProtectedRoute } from './components/ProtectedRoute'
import { SessionRedirect } from './components/SessionRedirect'
import { SplashPage } from './pages/SplashPage'
import { WelcomePage } from './pages/WelcomePage'
import { LoginPage } from './pages/LoginPage'
import { RecoveryPage } from './pages/RecoveryPage'
import { HomeBase } from './pages/HomeBase'
import { HomePage } from './pages/HomePage'
import { AppointmentsPage } from './pages/AppointmentsPage'
import { ProfilePage } from './pages/ProfilePage'
import { EditProfilePage } from './pages/EditProfilePage'
import { EditAddressPage } from './pages/EditAddressPage'
import { PasswordUpdatePage } from './pages/PasswordUpdatePage'
import { NotificationsPage } from './pages/NotificationsPage'
import { AccreditedPage } from './pages/AccreditedPage'
import { PartnersPage } from './pages/PartnersPage'
import { SchedulePage } from './pages/SchedulePage'
import { AppointmentViewPage } from './pages/AppointmentViewPage'
import { InstallAppPage } from './pages/InstallAppPage'

export default function App() {
  return (
    <AuthProvider>
      <HashRouter>
        <SessionRedirect />
        <div className="app-shell">
          <div className="phone-frame">
            <Routes>
              <Route path="/" element={<SplashPage />} />

              <Route element={<GuestRoute />}>
                <Route path="/welcome" element={<WelcomePage />} />
                <Route path="/login" element={<LoginPage />} />
                <Route path="/recover" element={<RecoveryPage />} />
              </Route>

              <Route element={<ProtectedRoute />}>
                <Route path="/app" element={<HomeBase />}>
                  <Route index element={<HomePage />} />
                  <Route path="appointments" element={<AppointmentsPage />} />
                  <Route path="profile" element={<ProfilePage />} />
                </Route>
                <Route path="/app/notifications" element={<NotificationsPage />} />
                <Route path="/app/network" element={<AccreditedPage />} />
                <Route path="/app/partners" element={<PartnersPage />} />
                <Route path="/app/schedule" element={<SchedulePage />} />
                <Route path="/app/install" element={<InstallAppPage />} />
                <Route path="/app/appointments/:id" element={<AppointmentViewPage />} />
                <Route path="/app/profile/details" element={<EditProfilePage />} />
                <Route path="/app/profile/address" element={<EditAddressPage />} />
                <Route path="/app/profile/password" element={<PasswordUpdatePage />} />
              </Route>

              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </div>
      </HashRouter>
    </AuthProvider>
  )
}
