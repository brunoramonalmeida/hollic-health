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
import { PlanPage } from './pages/PlanPage'
import { WalletPage } from './pages/WalletPage'
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
                <Route path="/recuperar" element={<RecoveryPage />} />
              </Route>

              <Route element={<ProtectedRoute />}>
                <Route path="/app" element={<HomeBase />}>
                  <Route index element={<HomePage />} />
                  <Route path="agendamentos" element={<AppointmentsPage />} />
                  <Route path="plano" element={<PlanPage />} />
                  <Route path="carteira" element={<WalletPage />} />
                  <Route path="perfil" element={<ProfilePage />} />
                </Route>
                <Route path="/app/notificacoes" element={<NotificationsPage />} />
                <Route path="/app/rede" element={<AccreditedPage />} />
                <Route path="/app/parceiros" element={<PartnersPage />} />
                <Route path="/app/agendar" element={<SchedulePage />} />
                <Route path="/app/instalar" element={<InstallAppPage />} />
                <Route path="/app/agendamentos/:id" element={<AppointmentViewPage />} />
                <Route path="/app/perfil/dados" element={<EditProfilePage />} />
                <Route path="/app/perfil/endereco" element={<EditAddressPage />} />
                <Route path="/app/perfil/senha" element={<PasswordUpdatePage />} />
              </Route>

              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </div>
      </HashRouter>
    </AuthProvider>
  )
}
