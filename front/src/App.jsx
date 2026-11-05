import { Navigate, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import ClinicForm from './pages/clinics/ClinicForm'
import ClinicList from './pages/clinics/ClinicList'
import Login from './pages/Login'
import VerifyPrescription from './pages/VerifyPrescription'
import MyAccount from './pages/MyAccount'
import MyAgenda from './pages/MyAgenda'
import MyCard from './pages/MyCard'
import MyCommissions from './pages/MyCommissions'
import MyRegion from './pages/MyRegion'
import MyTeam from './pages/MyTeam'
import MySecurity from './pages/MySecurity'
import MyProfile from './pages/MyProfile'
import Notifications from './pages/Notifications'
import Queue from './pages/Queue'
import Shell from './pages/Shell'
import { SimpleForm, SimpleList } from './pages/SimpleCrud'
import AppointmentForm from './pages/appointments/AppointmentForm'
import AppointmentList from './pages/appointments/AppointmentList'
import AppointmentView from './pages/appointments/AppointmentView'
import CustomerForm from './pages/customers/CustomerForm'
import CustomerList from './pages/customers/CustomerList'
import DependentForm from './pages/customers/DependentForm'
import AgreementForm from './pages/partners/AgreementForm'
import AgreementList from './pages/partners/AgreementList'
import PartnerForm from './pages/partners/PartnerForm'
import PartnerList from './pages/partners/PartnerList'
import PortalAgreement from './pages/portal/PortalAgreement'
import PortalAgreements from './pages/portal/PortalAgreements'
import PortalEmployeeForm from './pages/portal/PortalEmployeeForm'
import PortalEmployees from './pages/portal/PortalEmployees'
import PortalEnroll from './pages/portal/PortalEnroll'
import CategoryForm from './pages/plans/CategoryForm'
import CategoryList from './pages/plans/CategoryList'
import PlanForm from './pages/plans/PlanForm'
import PlanList from './pages/plans/PlanList'
import ProfessionalAgenda from './pages/professionals/ProfessionalAgenda'
import ProfessionalForm from './pages/professionals/ProfessionalForm'
import ProfessionalList from './pages/professionals/ProfessionalList'
import MyPayouts from './pages/reports/MyPayouts'
import MyPayments from './pages/MyPayments'
import CustomerPayments from './pages/customers/CustomerPayments'
import PartnerStatement from './pages/partners/PartnerStatement'
import PortalStatement from './pages/portal/PortalStatement'
import PayoutsAdmin from './pages/payouts/PayoutsAdmin'
import Receipt from './pages/payouts/Receipt'
import Reports from './pages/reports/Reports'
import RegionForm from './pages/sales/RegionForm'
import RegionList from './pages/sales/RegionList'
import SellerForm from './pages/sales/SellerForm'
import SellerList from './pages/sales/SellerList'
import SubscriptionForm from './pages/subscriptions/SubscriptionForm'
import SubscriptionList from './pages/subscriptions/SubscriptionList'
import CompanyForm from './pages/taxes/CompanyForm'
import TaxRuleForm from './pages/taxes/TaxRuleForm'
import TaxRuleList from './pages/taxes/TaxRuleList'
import UserForm from './pages/users/UserForm'
import UserList from './pages/users/UserList'
import { RequireAccess, useSession } from './session'

// Specialties use the generic list/form pages. Clinics (address + map) and
// plans (discount editor) have their own pages.
const catalog = [
  {
    title: 'Specialties',
    singular: 'specialty',
    path: '/specialties',
    hint: 'The specialty price is added to every appointment of that specialty; on-call services use the on-call price and remote appointments the remote price, when set.',
    fields: [
      { name: 'name', label: 'Name' },
      { name: 'price_cents', label: 'Price', money: true },
      {
        name: 'on_call_price_cents',
        label: 'On-call price',
        money: true,
        optional: true,
        placeholder: 'Same as the price',
        hint: 'Charged instead of the price on on-call services; never below it. Leave blank to charge the same.',
      },
      {
        name: 'remote_price_cents',
        label: 'Remote price',
        money: true,
        optional: true,
        placeholder: 'Same as the price',
        hint: 'Charged instead of the price on remote appointments (video call). Leave blank to charge the same.',
      },
    ],
    columns: [
      { key: 'name', label: 'Name' },
      { key: 'price_cents', label: 'Price', money: true },
      { key: 'on_call_price_cents', label: 'On-call price', money: true, blank: 'same as price' },
      { key: 'remote_price_cents', label: 'Remote price', money: true, blank: 'same as price' },
    ],
  },
]

const staff = (s) => s.isStaff
const pricing = (s) => s.canEditPricing
const users = (s) => s.canManageUsers
const reports = (s) => s.canSeeBalanceReports || s.canSeeAttendantReport
const admin = (s) => s.role === 'admin'
const booking = (s) => s.canBookAppointments
const professional = (s) => s.isProfessional
const client = (s) => s.isClient
const payouts = (s) => s.canManagePayouts
const receipts = (s) => s.canManagePayouts || s.isProfessional
const sales = (s) => s.canSeeSales
const manageSales = (s) => s.canManageSales
const seller = (s) => s.isSeller
const appointments = (s) => !s.isSeller && !s.isPartner
const customers = (s) => s.canManageCustomers
const subscriptions = (s) => s.canSeeSubscriptions
const selling = (s) => s.canSellPlans || s.isClient
const partners = (s) => s.canSeePartners
const managePartners = (s) => s.canManagePartners
const company = (s) => s.isPartner

/** Route element wrapped in an access check (the API enforces the same rules). */
const guard = (check, element) => <RequireAccess check={check}>{element}</RequireAccess>

/** Professionals get a read-only page with the signature; everyone else the form. */
function AppointmentPage() {
  const session = useSession()
  return session.isProfessional ? <AppointmentView /> : <AppointmentForm />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      {/* Public: pharmacies check a prescription and its .p7s without an account. */}
      <Route path="/verify-prescription" element={<VerifyPrescription />} />
      <Route
        element={
          <RequireAccess>
            <Shell />
          </RequireAccess>
        }
      >
        <Route path="/" element={<Dashboard />} />

        {/* Every role but sellers: clients and professionals are scoped to their own records by the API. */}
        <Route path="/appointments" element={guard(appointments, <AppointmentList />)} />
        <Route path="/my-agenda" element={guard(professional, <MyAgenda />)} />
        <Route path="/appointments/new" element={guard(booking, <AppointmentForm />)} />
        <Route path="/appointments/:id" element={guard(appointments, <AppointmentPage />)} />
        <Route path="/subscriptions" element={guard(subscriptions, <SubscriptionList />)} />
        <Route path="/subscriptions/new" element={guard(selling, <SubscriptionForm />)} />
        <Route path="/subscriptions/:id" element={guard(subscriptions, <SubscriptionForm />)} />
        <Route path="/notifications" element={<Notifications />} />

        {/* Professionals */}
        <Route path="/my-payouts" element={guard(professional, <MyPayouts />)} />
        <Route path="/my-security" element={guard(professional, <MySecurity />)} />
        <Route path="/my-profile" element={guard(professional, <MyProfile />)} />

        {/* Clients: the virtual card (its code is shown only to the patient's own login) */}
        <Route path="/my-card" element={guard(client, <MyCard />)} />
        <Route path="/my-payments" element={guard(client, <MyPayments />)} />

        {/* Sellers: their region's rules, their commissions, their account */}
        <Route path="/my-region" element={guard(seller, <MyRegion />)} />
        <Route path="/my-commissions" element={guard(seller, <MyCommissions />)} />
        <Route path="/my-team" element={guard(seller, <MyTeam />)} />
        <Route path="/my-account" element={guard(seller, <MyAccount />)} />

        {/* Staff, and sellers for their own portfolio (the API scopes it) */}
        <Route path="/customers" element={guard(customers, <CustomerList />)} />
        <Route path="/customers/new" element={guard(customers, <CustomerForm />)} />
        <Route path="/customers/:id" element={guard(customers, <CustomerForm />)} />
        <Route path="/customers/:id/payments" element={guard(staff, <CustomerPayments />)} />
        <Route path="/customers/:customerId/dependents/new" element={guard(customers, <DependentForm />)} />
        <Route path="/customers/:customerId/dependents/:dependentId" element={guard(customers, <DependentForm />)} />

        {/* Staff */}
        <Route path="/professionals" element={guard(staff, <ProfessionalList />)} />
        <Route path="/professionals/:id/agenda" element={guard(staff, <ProfessionalAgenda />)} />
        <Route path="/professionals/:id" element={guard(staff, <ProfessionalForm />)} />
        <Route path="/clinics" element={guard(staff, <ClinicList />)} />
        <Route path="/clinics/:id" element={guard(staff, <ClinicForm />)} />
        <Route path="/modalities" element={guard(staff, <PlanList />)} />
        <Route path="/modalities/:id" element={guard(staff, <PlanForm />)} />
        <Route path="/plan-categories" element={guard(staff, <CategoryList />)} />
        <Route path="/plan-categories/:id" element={guard(staff, <CategoryForm />)} />
        {catalog.map((entity) => (
          <Route key={entity.path} path={entity.path} element={guard(staff, <SimpleList {...entity} />)} />
        ))}

        {/* Admin and supervisor: anything that moves prices */}
        <Route path="/professionals/new" element={guard(pricing, <ProfessionalForm />)} />
        <Route path="/clinics/new" element={guard(pricing, <ClinicForm />)} />
        <Route path="/modalities/new" element={guard(pricing, <PlanForm />)} />
        <Route path="/plan-categories/new" element={guard(pricing, <CategoryForm />)} />
        {catalog.map((entity) => (
          <Route key={`${entity.path}-form`}>
            <Route path={`${entity.path}/new`} element={guard(pricing, <SimpleForm {...entity} />)} />
            <Route path={`${entity.path}/:id`} element={guard(pricing, <SimpleForm {...entity} />)} />
          </Route>
        ))}

        {/* Sales: staff read regions and sellers; admins and supervisors edit them */}
        <Route path="/regions" element={guard(sales, <RegionList />)} />
        <Route path="/regions/new" element={guard(manageSales, <RegionForm />)} />
        <Route path="/regions/:id" element={guard(sales, <RegionForm />)} />
        <Route path="/sellers" element={guard(sales, <SellerList />)} />
        <Route path="/sellers/new" element={guard(manageSales, <SellerForm />)} />
        <Route path="/sellers/:id" element={guard(sales, <SellerForm />)} />

        {/* Corporate (B2B): staff read partners and agreements (and enroll employees); admins and supervisors negotiate them */}
        <Route path="/partners" element={guard(partners, <PartnerList />)} />
        <Route path="/partners/new" element={guard(managePartners, <PartnerForm />)} />
        <Route path="/partners/:id" element={guard(partners, <PartnerForm />)} />
        <Route path="/partners/:id/statement" element={guard(partners, <PartnerStatement />)} />
        <Route path="/agreements" element={guard(partners, <AgreementList />)} />
        <Route path="/agreements/new" element={guard(managePartners, <AgreementForm />)} />
        <Route path="/agreements/:id" element={guard(partners, <AgreementForm />)} />

        {/* Partner companies: their self-service portal (employees, agreements, invoices) */}
        <Route path="/my-employees" element={guard(company, <PortalEmployees />)} />
        <Route path="/my-employees/new" element={guard(company, <PortalEnroll />)} />
        <Route path="/my-employees/:id" element={guard(company, <PortalEmployeeForm />)} />
        <Route path="/my-agreements" element={guard(company, <PortalAgreements />)} />
        <Route path="/my-agreements/:id" element={guard(company, <PortalAgreement />)} />
        <Route path="/my-statement" element={guard(company, <PortalStatement />)} />

        {/* Management */}
        <Route path="/reports" element={guard(reports, <Reports />)} />
        <Route path="/users" element={guard(users, <UserList />)} />
        <Route path="/users/new" element={guard(users, <UserForm />)} />
        <Route path="/users/:id" element={guard(users, <UserForm />)} />
        <Route path="/queue" element={guard(admin, <Queue />)} />
        <Route path="/payouts" element={guard(payouts, <PayoutsAdmin />)} />
        <Route path="/receipts/:id" element={guard(receipts, <Receipt />)} />
        <Route path="/tax-rules" element={guard(staff, <TaxRuleList />)} />
        <Route path="/tax-rules/new" element={guard(admin, <TaxRuleForm />)} />
        <Route path="/tax-rules/:id" element={guard(staff, <TaxRuleForm />)} />
        <Route path="/company" element={guard(staff, <CompanyForm />)} />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
